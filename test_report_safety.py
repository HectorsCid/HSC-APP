"""No external services: loss of acknowledgements, atomic saves and ownership."""
import ast
import hashlib
import io
from datetime import datetime
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

    def test_edit_can_replace_legacy_evidence_without_mutation_id(self):
        original_draft = self.draft()
        self.store.save_report_evidence(original_draft['id'], 1, 'drive-legacy')
        original = self.store.finalize_report(original_draft['id'])
        self.assertEqual('', original['evidence'][0]['mutation_id'])

        edit = self.draft(edit_report_id=original['id'])
        inherited = self.store.get_report_detail(edit['id'])['evidence'][0]
        self.assertEqual('legacy-original-1', inherited['mutation_id'])
        removed = self.store.delete_report_evidence(
            edit['id'], inherited['mutation_id'], actor_id='another-technician'
        )
        self.assertEqual(1, removed['position'])
        self.assertEqual([], self.store.get_report_detail(edit['id'])['evidence'])

    def test_outbox_failure_rolls_back_report_and_receipt(self):
        draft = self.draft()
        with patch.object(self.store, '_queue_sync_in_transaction', side_effect=RuntimeError('disk error')):
            with self.assertRaises(RuntimeError):
                self.store.finalize_report(draft['id'])
        self.assertEqual(self.store.get_report_detail(draft['id'])['state'], 'draft')
        self.assertIsNone(self.store.get_report_submission(draft['id'], 'T1'))
        self.assertEqual(self.store.pending_sync(), [])
        self.assertTrue(self.store.finalize_report(draft['id'])['completed'])

    def test_concurrent_technicians_confirm_the_same_shared_report(self):
        drafts = [self.draft('T1'), self.draft('T2')]
        self.assertEqual(drafts[0]['id'], drafts[1]['id'])
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
        self.assertEqual(len(success), 2)
        self.assertEqual(failures, [])
        self.assertEqual(success[0]['id'], success[1]['id'])
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
            _scope_operaciones_payload=lambda p: p, _OPERACIONES_IMPORT_STATUS={'error': ''},
            read_operaciones_matrix=lambda *args, **kwargs: self.store.snapshot(),
            get_sheets_service=lambda **kwargs: object(), SHEET_ID='test',
            _complete_legacy_operations_relations=lambda payload: payload,
            _queue_matrix_duplicate_repairs=lambda stats: 0,
            reset_thread_google_services=Mock(), datetime=datetime, hashlib=hashlib,
            store_operations_evidence=lambda *args, **kwargs: {
                'drive_ref': 'drive-shared', 'storage_ref': 'storage-shared'
            })
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
        client.audit_namespace['_schedule_operations_sync'].assert_not_called()

    def test_api_allows_authorized_technicians_to_collaborate_on_shared_draft(self):
        draft = self.draft('T2')
        client = self.api()
        self.assertEqual(client.get('/api/operaciones/reports/' + draft['id']).status_code, 200)
        reset = client.post('/api/operaciones/reports/' + draft['id'] + '/evidence/reset')
        self.assertEqual(reset.status_code, 200)
        self.assertTrue(reset.get_json()['preserved'])
        response = client.post('/api/operaciones/reports/' + draft['id'] + '/evidence/1',
            data={'file': (io.BytesIO(b'test'), 'test.jpg', 'image/jpeg'), 'mutation_id': 'shared-photo'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()['evidence']['position'], 1)

    def test_api_retry_of_edit_resolves_original_folio(self):
        original = self.store.finalize_report(self.draft()['id'])
        edit = self.draft(edit_report_id=original['id'])
        body = dict(edit, payload=dict(edit['payload']), submission_id='retry-edit', photo_mutation_ids=[])
        client = self.api()
        with patch.dict('sys.modules', {'notification_delivery': SimpleNamespace(enqueue=Mock())}):
            for _ in range(2):
                response = client.post('/api/operaciones/reports/finalize', json=body)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(response.get_json()['report']['id'], original['id'])
        recovered = client.get('/api/operaciones/reports/' + edit['id'])
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.get_json()['report']['id'], original['id'])

    def test_api_does_not_acknowledge_another_technicians_submission(self):
        draft = self.draft()
        body = dict(draft, payload=dict(draft['payload']), changed_fields={},
                    submission_id='T1-submission', photo_mutation_ids=[])
        client = self.api()
        with patch.dict('sys.modules', {'notification_delivery': SimpleNamespace(enqueue=Mock())}):
            first = client.post('/api/operaciones/reports/finalize', json=body)
            self.assertEqual(first.status_code, 200, first.get_json())
            with client.session_transaction() as saved:
                saved['hsc_user_id'] = 'T2'
            other = dict(body, submission_id='T2-submission')
            other['payload'] = dict(body['payload'], notas='Unsent offline work')
            other['changed_fields'] = {'notas': 'Unsent offline work'}
            rejected = client.post('/api/operaciones/reports/finalize', json=other)
            self.assertEqual(rejected.status_code, 409)
            self.assertEqual(rejected.get_json()['code'], 'report_completed')
            forged_retry = client.post('/api/operaciones/reports/finalize', json=body)
            self.assertEqual(forged_retry.status_code, 409)

    def test_api_waits_for_all_reserved_photographs(self):
        draft = self.draft()
        self.store.reserve_report_evidence(draft['id'], 1, 'photo-uploading', actor_id='T2')
        body = dict(draft, payload=dict(draft['payload']), changed_fields={},
                    submission_id='waiting-for-photos', photo_mutation_ids=[])
        client = self.api()
        response = client.post('/api/operaciones/reports/finalize', json=body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['code'], 'photos_uploading')
        self.assertEqual(self.store.pending_sync(), [])


if __name__ == '__main__':
    unittest.main()
