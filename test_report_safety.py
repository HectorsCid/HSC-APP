"""No external services: loss of acknowledgements, atomic saves and ownership."""
import ast
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, abort, current_app, jsonify, request, session, url_for
from operaciones_store import OperationsStore


class ReportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperationsStore(local_path=Path(self.tmp.name) / 'reports.sqlite')
        self.store.import_matrix_snapshot({'clients': [{'id': 'A', 'name': 'Test'}],
            'equipment': [{'id': 'A1', 'client_id': 'A', 'name': 'Test'}], 'reports': [], 'faults': []})

    def draft(self, user='T1', **fields):
        return self.store.save_report_draft(dict(client_id='A', equipment_id='A1', round='1',
            payload={'inicio': '2026-09-20', 'fin': '2026-09-20', '_draft_user_id': user}, **fields))

    def test_retry_keeps_folio_and_outbox_operation(self):
        draft = self.draft()
        first = self.store.finalize_report(draft['id'])
        queue = self.store.pending_sync()
        self.assertEqual(self.store.finalize_report(draft['id'])['id'], first['id'])
        self.assertEqual(self.store.pending_sync(), queue)
        self.assertIsNone(self.store.get_report_submission(draft['id'], 'T2'))
        self.assertEqual(self.store.get_report_submission(draft['id'], 'T1')['id'], first['id'])

    def test_edit_receipt_survives_deleted_draft_and_keeps_photos(self):
        original = self.store.finalize_report(self.draft()['id'])
        edit = self.draft(edit_report_id=original['id'])
        self.store.save_report_evidence(edit['id'], 1, 'drive-photo')
        result = self.store.finalize_report(edit['id'])
        self.assertIsNone(self.store.get_report_detail(edit['id']))
        self.assertEqual(self.store.get_report_submission(edit['id'], 'T1')['id'], original['id'])
        self.assertEqual(self.store.finalize_report(edit['id']), result)
        self.assertEqual(result['evidence'][0]['drive_ref'], 'drive-photo')
        self.assertEqual(len(self.store.snapshot()['reports']), 1)

    def test_outbox_failure_rolls_back_report_and_receipt(self):
        draft = self.draft()
        with patch.object(self.store, '_queue_sync_in_transaction', side_effect=RuntimeError('disk error')):
            with self.assertRaises(RuntimeError):
                self.store.finalize_report(draft['id'])
        self.assertEqual(self.store.get_report_detail(draft['id'])['state'], 'draft')
        self.assertIsNone(self.store.get_report_submission(draft['id'], 'T1'))
        self.assertEqual(self.store.pending_sync(), [])
        self.assertTrue(self.store.finalize_report(draft['id'])['completed'])

    def test_concurrent_technicians_cannot_finish_two_reports_for_same_round(self):
        drafts = [self.draft('T1'), self.draft('T2')]
        barrier = threading.Barrier(2)
        success, failures = [], []
        def finish(draft):
            barrier.wait()
            try:
                success.append(self.store.finalize_report(draft['id']))
            except ValueError as exc:
                failures.append(str(exc))
        threads = [threading.Thread(target=finish, args=(draft,)) for draft in drafts]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(success), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn('ya tiene reporte', failures[0])
        self.assertEqual(len(self.store.pending_sync()), 1)

    def test_completed_report_cannot_be_resurrected_as_draft(self):
        draft = self.draft()
        self.store.finalize_report(draft['id'])
        with self.assertRaises(ValueError): self.draft(id=draft['id'])
        self.assertTrue(self.store.get_report_detail(draft['id'])['completed'])

    def api(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY='test')
        names = {'api_operaciones_finalize_report', 'api_operaciones_reset_report_evidence',
                 'api_operaciones_upload_report_evidence', 'api_operaciones_report_detail', 'api_operaciones_bootstrap'}
        tree = ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        functions = [fn for fn in tree.body if isinstance(fn, ast.FunctionDef) and fn.name in names]
        ns = dict(app=app, OPERACIONES_STORE=self.store, request=request, session=session,
            abort=abort, jsonify=jsonify, current_app=current_app, url_for=url_for,
            _operations_forbidden=lambda *args: None, _operations_permission_forbidden=lambda *args: None,
            _operations_role=lambda: 'technician', _invalidate_operations_cache=Mock(),
            _schedule_operations_sync=Mock(), _notify_operations_fault=Mock())
        ns.update(time=SimpleNamespace(monotonic=lambda: 1), _prepare_operaciones_payload=lambda p: p,
            _scope_operaciones_payload=lambda p: p, _OPERACIONES_IMPORT_STATUS={'error': ''})
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'app.py', 'exec'), ns)
        client = app.test_client()
        client.audit_namespace = ns
        with client.session_transaction() as saved: saved['hsc_user_id'] = 'T1'
        return client

    def test_bootstrap_reads_database_without_google_or_shared_cache_lock(self):
        client = self.api()
        for path in ['/api/operaciones/bootstrap', '/api/operaciones/bootstrap?refresh=1']:
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['equipment'][0]['id'], 'A1')
        client.audit_namespace['_schedule_operations_sync'].assert_called_once_with(force=True)

    def test_api_does_not_modify_another_users_draft_or_photos(self):
        draft = self.draft('T2')
        client = self.api()
        self.assertEqual(client.get('/api/operaciones/reports/' + draft['id']).status_code, 404)
        self.assertEqual(client.post('/api/operaciones/reports/' + draft['id'] + '/evidence/reset').status_code, 403)
        response = client.post('/api/operaciones/reports/' + draft['id'] + '/evidence/1',
            data={'file': (io.BytesIO(b'test'), 'test.jpg', 'image/jpeg')})
        self.assertEqual(response.status_code, 403)

    def test_api_retry_of_edit_resolves_original_folio(self):
        original = self.store.finalize_report(self.draft()['id'])
        edit = self.draft(edit_report_id=original['id'])
        body = dict(edit, payload=dict(edit['payload']))
        client = self.api()
        with patch.dict('sys.modules', {'notification_delivery': SimpleNamespace(enqueue=Mock())}):
            for _ in range(2):
                response = client.post('/api/operaciones/reports/finalize', json=body)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()['report']['id'], original['id'])
        recovered = client.get('/api/operaciones/reports/' + edit['id'])
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.get_json()['report']['id'], original['id'])


if __name__ == '__main__':
    unittest.main()
