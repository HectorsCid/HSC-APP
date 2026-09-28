"""Reproductions from the 5495b12 audit. Synthetic SQLite/Google only."""
import hashlib
import io
import json
import re
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from operaciones_store import OperationsStore, ReportConflictError
from operaciones_sync import _get_sheet_titles, _plan_operation, _write_plan, sync_operations_outbox
from test_operaciones_sync import FakeSheets, _store_with_report


class Request:
    def __init__(self, call):
        self.call = call

    def execute(self):
        return self.call()


class FakeDrive:
    """Stores real metadata; delays lookups to expose search/create races."""
    def __init__(self):
        self.objects = {}
        self.sequence = 0
        self.lock = threading.Lock()
        self.creates = 0

    def files(self):
        return self

    def generateIds(self, **kw):
        def run():
            with self.lock:
                self.sequence += 1
                return {'ids': [f'generated-{self.sequence}']}
        return Request(run)

    def list(self, **kw):
        def run():
            q = kw.get('q', '')
            parent = re.search(r"'([^']+)' in parents", q)
            name = re.search(r"name\s*=\s*'([^']+)'", q)
            contains = re.search(r"name\s+contains\s+'([^']+)'", q)
            with self.lock:
                rows = [dict(v) for v in self.objects.values()
                        if (not parent or parent[1] in v.get('parents', []))
                        and (not name or name[1] == v.get('name'))
                        and (not contains or contains[1] in v.get('name', ''))
                        and not v.get('trashed')]
            time.sleep(.03)
            return {'files': rows}
        return Request(run)

    def get(self, fileId, **kw):
        def run():
            if fileId not in self.objects:
                from googleapiclient.errors import HttpError
                from httplib2 import Response
                raise HttpError(Response({'status': '404'}), b'{}')
            return dict(self.objects[fileId])
        return Request(run)

    def create(self, body, media_body=None, **kw):
        def run():
            with self.lock:
                self.sequence += 1
                ident = body.get('id') or f'created-{self.sequence}'
                if ident in self.objects:
                    from googleapiclient.errors import HttpError
                    from httplib2 import Response
                    raise HttpError(Response({'status': '409'}), b'{}')
                data = dict(body, id=ident, createdTime='2026-09-28T00:00:00Z', permissions=[])
                if media_body:
                    content = media_body.getbytes(0, media_body.size())
                    data['md5Checksum'] = hashlib.md5(content).hexdigest()
                self.objects[ident] = data
                self.creates += 1
                return dict(data)
        return Request(run)

    def update(self, fileId, body=None, addParents=None, removeParents=None, media_body=None, **kw):
        def run():
            item = self.objects[fileId]
            item.update(body or {})
            parents = set(item.get('parents', []))
            parents.difference_update((removeParents or '').split(','))
            parents.update(filter(None, (addParents or '').split(',')))
            item['parents'] = sorted(parents)
            if media_body:
                item['md5Checksum'] = hashlib.md5(media_body.getbytes(0, media_body.size())).hexdigest()
            return dict(item)
        return Request(run)


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hsc-audit-test-')
        self.addCleanup(self.temp.cleanup)
        self.store, self.report_id = _store_with_report(Path(self.temp.name))

    def draft(self, round_number='3'):
        return self.store.save_report_draft({'client_id': 'UVMQ', 'equipment_id': 'UVMQ1',
            'round': round_number, 'payload': {'inicio': '2026-09-28', 'fin': '2026-09-28'}})

    def test_obsolete_sheet_plan_makes_zero_external_writes(self):
        fake = FakeSheets()
        old = self.store.pending_sync()[0]
        plan = _plan_operation(self.store, old, fake, 'synthetic-sheet', _get_sheet_titles(fake, 'synthetic-sheet'), {})
        edit = self.store.save_report_draft({'client_id': 'UVMQ', 'equipment_id': 'UVMQ1', 'round': '2',
            'edit_report_id': self.report_id, 'payload': {'p1': '999'},
            'changed_fields': {'p1': '999'}, 'base_values': {'p1': '120'}})
        self.store.finalize_report(edit['id'])
        self.assertEqual(sync_operations_outbox(self.store, fake, 'synthetic-sheet')['synced'], 1)
        count = len(fake.writes)
        try:
            _write_plan(fake, 'synthetic-sheet', plan)
        except ReportConflictError:
            pass
        self.assertEqual(len(fake.writes), count, 'An obsolete job must not reach Sheets')
        self.assertEqual(self.store.get_report_detail(self.report_id)['payload']['p1'], '999')

    def test_restart_abandoned_reservation_does_not_block_finalization(self):
        draft = self.draft()
        self.store.reserve_report_evidence(draft['id'], 1, 'abandoned')
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.store.connection() as conn:
            conn.execute('UPDATE operations_evidence SET updated_at=? WHERE report_id=?', (past, draft['id']))
        restarted = OperationsStore(local_path=self.store.local_path)
        self.assertTrue(restarted.finalize_report(draft['id'])['completed'])

    def test_confirmed_photo_is_immutable_even_on_duplicate_completion(self):
        draft = self.draft()
        self.store.reserve_report_evidence(draft['id'], 1, 'same-id')
        self.store.complete_report_evidence(draft['id'], 'same-id', 'first-file')
        with self.assertRaises(ReportConflictError):
            self.store.complete_report_evidence(draft['id'], 'same-id', 'different-file')
        self.assertEqual(self.store.get_report_detail(draft['id'])['evidence'][0]['drive_ref'], 'first-file')

    def test_legacy_photo_without_digest_cannot_acknowledge_unknown_replacement_bytes(self):
        draft = self.draft()
        self.store.reserve_report_evidence(draft['id'], 1, 'legacy-photo', actor_id='A')
        self.store.complete_report_evidence(draft['id'], 'legacy-photo', 'original-file')
        with self.assertRaises(ReportConflictError):
            self.store.reserve_report_evidence(draft['id'], 1, 'legacy-photo', actor_id='A', content_hash='different-bytes', attempt_token='retry')

    def drive_context(self):
        from flask import Flask
        app = Flask(__name__)
        app.extensions['operations_store'] = self.store
        return app

    def test_concurrent_drive_creates_reuse_one_folder_and_file(self):
        import reportes_bp as reports
        drive, app = FakeDrive(), self.drive_context()
        def save(_):
            with app.app_context():
                folder = reports._ensure_folder('synthetic-root', 'same-report')
                return folder, reports._upsert_bytes(folder, 'same-hash.jpg', b'same-photo', 'image/jpeg')
        with patch.object(reports, '_drive_files_call', side_effect=lambda fn, **kw: fn(drive)):
            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(save, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(drive.creates, 2, 'Exactly one folder and one file')

    def test_photo_retry_after_slot_reassignment_reuses_the_same_drive_file(self):
        import reportes_bp as reports
        drive, app = FakeDrive(), self.drive_context()
        with app.app_context(), patch.object(reports, 'REPORTES_ROOT_ID', 'root'), \
                patch.object(reports, '_optimize_photo_bytes', return_value=(b'photo', 'image/jpeg')), \
                patch.object(reports, '_drive_files_call', side_effect=lambda fn, **kw: fn(drive)):
            first = reports.store_operations_evidence('client', 'report', 1, b'raw', upload_id='same-mutation')
            second = reports.store_operations_evidence('client', 'report', 2, b'raw', upload_id='same-mutation')
        self.assertEqual(first, second)
        self.assertEqual(drive.creates, 3, 'Two folders and one immutable photo, even after a slot changes')

    def test_existing_duplicate_folders_preserve_and_consolidate_children(self):
        import reportes_bp as reports
        drive, app = FakeDrive(), self.drive_context()
        for ident in ['folder-a', 'folder-b']:
            drive.objects[ident] = dict(id=ident, name='same-report', parents=['root'],
                mimeType='application/vnd.google-apps.folder', createdTime='2026-09-01T00:00:00Z', permissions=[])
        drive.objects['photo-a'] = dict(id='photo-a', name='a.jpg', parents=['folder-a'])
        drive.objects['photo-b'] = dict(id='photo-b', name='b.jpg', parents=['folder-b'])
        with app.app_context(), patch.object(reports, '_drive_files_call', side_effect=lambda fn, **kw: fn(drive)):
            canonical = reports._ensure_folder('root', 'same-report')
        self.assertEqual(canonical, 'folder-a')
        self.assertEqual(set(drive.objects), {'folder-a', 'folder-b', 'photo-a', 'photo-b'})
        self.assertEqual(drive.objects['photo-a']['parents'], [canonical])
        self.assertEqual(drive.objects['photo-b']['parents'], [canonical])

    def test_mutation_requires_account_binding_even_for_an_old_tab(self):
        import app as module
        client = module.app.test_client()
        with client.session_transaction() as session:
            session.update(hsc_authenticated=True, hsc_role='admin', hsc_user_id='ACCOUNT-B')
        with patch.object(module, 'OPERACIONES_STORE', self.store), \
                patch.object(module, '_app_access_password', return_value='synthetic-password'):
            response = client.post('/api/operaciones/reports/draft', json={
                'client_id': 'UVMQ', 'equipment_id': 'UVMQ1', 'round': '4',
                'payload': {'notas': 'Work left open by account A'}})
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(self.store.get_report_draft('UVMQ1', '4'))

    def test_expired_attempt_cannot_complete_or_release_its_replacement(self):
        draft = self.draft()
        kw = dict(actor_id='A', content_hash='hash', attempt_token='old', lease_seconds=-1)
        self.store.reserve_report_evidence(draft['id'], 1, 'mutation', **kw)
        restarted = OperationsStore(local_path=self.store.local_path)
        kw.update(attempt_token='new', lease_seconds=120)
        restarted.reserve_report_evidence(draft['id'], 1, 'mutation', **kw)
        with self.assertRaises(ReportConflictError):
            self.store.complete_report_evidence(draft['id'], 'mutation', 'old-file', lease_token='old')
        self.assertFalse(self.store.release_report_evidence(draft['id'], 'mutation', lease_token='old'))
        restarted.complete_report_evidence(draft['id'], 'mutation', 'new-file', lease_token='new')
        self.assertEqual(restarted.get_report_detail(draft['id'])['evidence'][0]['drive_ref'], 'new-file')

    def test_active_upload_heartbeat_cancel_and_immutable_retry(self):
        from photo_uploads import evidence_lease
        draft = self.draft()
        self.store.reserve_report_evidence(draft['id'], 1, 'mutation', actor_id='A', content_hash='hash', attempt_token='token')
        with patch.object(self.store, 'renew_report_evidence', wraps=self.store.renew_report_evidence) as renew:
            with evidence_lease(self.store, draft['id'], 'mutation', 'token', interval=.01):
                deadline = time.monotonic() + 2
                while not renew.call_count and time.monotonic() < deadline:
                    time.sleep(.02)
            self.assertGreater(renew.call_count, 0)
        with self.assertRaises(ReportConflictError):
            self.store.release_report_evidence(draft['id'], 'mutation', actor_id='B', cancel=True)
        self.assertTrue(self.store.release_report_evidence(draft['id'], 'mutation', actor_id='A', cancel=True))
        for actor, digest in [('B', 'hash'), ('A', 'replacement')]:
            with self.assertRaises(ReportConflictError):
                self.store.reserve_report_evidence(draft['id'], 1, 'mutation', actor_id=actor, content_hash=digest, attempt_token='new')
        self.store.reserve_report_evidence(draft['id'], 1, 'mutation', actor_id='A', content_hash='hash', attempt_token='new')

    def test_six_concurrent_uploads_get_distinct_slots(self):
        draft = self.draft()
        def save(i):
            mutation = 'photo-' + str(i)
            reservation = self.store.reserve_report_evidence(draft['id'], 1, mutation, actor_id='T'+str(i % 3), content_hash=str(i), attempt_token=mutation)
            self.store.complete_report_evidence(draft['id'], mutation, 'file-'+str(i), lease_token=mutation)
            return reservation['position']
        with ThreadPoolExecutor(6) as pool:
            slots = list(pool.map(save, range(6)))
        self.assertEqual(sorted(slots), [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(self.store.finalize_report(draft['id'])['evidence']), 6)

    def test_drive_lost_create_ack_reuses_reserved_id_after_restart(self):
        from drive_registry import ensure_object
        for content in [None, b'synthetic-image']:
            drive = FakeDrive()
            original = drive.create
            def lost_ack(**kw):
                request = original(**kw)
                def run():
                    request.execute()
                    raise TimeoutError('Google accepted the create; reply was lost')
                return Request(run)
            with patch.object(drive, 'create', side_effect=lost_ack):
                with self.assertRaises(TimeoutError):
                    ensure_object(self.store, drive, 'root', 'object-'+str(bool(content)), content=content)
            restarted = OperationsStore(local_path=self.store.local_path)
            ident = ensure_object(restarted, drive, 'root', 'object-'+str(bool(content)), content=content)
            self.assertEqual(drive.creates, 1)
            self.assertEqual(ident, next(iter(drive.objects)))

    def test_duplicate_folders_with_different_access_are_preserved_for_review(self):
        from drive_registry import ensure_object, resource_key
        drive = FakeDrive()
        for ident in ['a', 'b']:
            drive.objects[ident] = dict(id=ident, name='folder', parents=['root'], mimeType='application/vnd.google-apps.folder', permissions=[{'id': ident, 'role': 'reader'}])
        drive.objects['photo'] = dict(id='photo', name='photo.jpg', parents=['b'])
        self.assertEqual(ensure_object(self.store, drive, 'root', 'folder'), 'a')
        self.assertEqual(drive.objects['photo']['parents'], ['b'])
        self.assertEqual(drive.objects['b']['name'], 'folder')
        self.assertEqual(self.store.drive_registry(resource_key('root', 'folder', True))['access_review'], ['b'])

    def test_unknown_sheets_ack_survives_restart_and_blocks_later_writes(self):
        fake = FakeSheets()
        operation = self.store.pending_sync()[0]
        plan = _plan_operation(self.store, operation, fake, 'sheet', _get_sheet_titles(fake, 'sheet'), {})
        def lost_ack(request, delivery):
            delivery['sent'] = True
            raise TimeoutError('ack lost')
        with patch('operaciones_sync._execute_sheet_write', side_effect=lost_ack):
            with self.assertRaises(TimeoutError):
                _write_plan(fake, 'sheet', plan)
        restarted = OperationsStore(local_path=self.store.local_path)
        self.assertEqual(restarted._sheet_delivery('sheets:sheet')['state'], 'uncertain')
        count = len(fake.writes)
        self.assertEqual(sync_operations_outbox(restarted, fake, 'sheet')['synced'], 0)
        self.assertEqual(len(fake.writes), count)
        with self.assertRaises(ValueError):
            restarted.resolve_sheet_delivery('sheet', operation['id'])
        restarted.resolve_sheet_delivery('sheet', operation['id'], attempt_id=restarted.sheet_delivery_status('sheet')['attempt_id'], actor_id='owner-test', external_quiescent=True)
        self.assertEqual(sync_operations_outbox(restarted, fake, 'sheet')['synced'], 1)

    def test_two_sheet_workers_send_one_current_revision(self):
        fake = FakeSheets()
        operation = self.store.pending_sync()[0]
        plans = [_plan_operation(self.store, operation, fake, 'sheet', _get_sheet_titles(fake, 'sheet'), {}) for _ in range(2)]
        def send(plan):
            try:
                _write_plan(fake, 'sheet', plan)
                return True
            except ReportConflictError:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(send, plans))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(sum(write[0] == 'append' for write in fake.writes), 1)

    def child(self, code, *arguments):
        root = Path(__file__).resolve().parents[1]
        env = {key: value for key, value in os.environ.items() if key.upper() in {'PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'WINDIR'}}
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([str(root), str(root / 'venv311/Lib/site-packages')]))
        child = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.store.local_path), *map(str, arguments)],
            cwd=self.temp.name, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
        self.addCleanup(cleanup)
        return child

    def test_process_crash_releases_lock_but_retains_uncertain_sheet_intent(self):
        child = self.child('''import sys
from operaciones_store import OperationsStore
s=OperationsStore(local_path=sys.argv[1]);s.initialize();operation=s.pending_sync()[0]
with s.sheet_write_guard(operation,'crash-sheet') as delivery:
 delivery['sent']=True
 print('READY',flush=True)
 sys.stdin.readline()
''')
        self.assertEqual(child.stdout.readline().strip(), 'READY')
        child.kill()
        child.communicate(timeout=5)
        restarted = OperationsStore(local_path=self.store.local_path)
        with restarted.resource_lock('sheets:crash-sheet', timeout=1):
            intent = restarted.sheet_delivery_status('crash-sheet')
            self.assertEqual(intent['state'], 'active')
        fake = FakeSheets()
        self.assertEqual(sync_operations_outbox(restarted, fake, 'crash-sheet')['synced'], 0)
        self.assertEqual(fake.writes, [])
        restarted.resolve_sheet_delivery('crash-sheet', intent['operation_id'], attempt_id=intent['attempt_id'], actor_id='owner-test', external_quiescent=True)
        self.assertEqual(sync_operations_outbox(restarted, fake, 'crash-sheet')['synced'], 1)

    def test_three_processes_share_one_resource_lock(self):
        counter = Path(self.temp.name) / 'synthetic-remote-counter.txt'
        counter.write_text('0')
        code = '''import sys,time
from pathlib import Path
from operaciones_store import OperationsStore
s=OperationsStore(local_path=sys.argv[1]);target=Path(sys.argv[2])
for _ in range(10):
 with s.resource_lock('shared-remote'):
  value=int(target.read_text());time.sleep(.004);target.write_text(str(value+1))
'''
        children = [self.child(code, counter) for _ in range(3)]
        for child in children:
            _, error = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, error)
        self.assertEqual(counter.read_text(), '30')

    def test_three_workers_can_initialize_one_new_database(self):
        path = Path(self.temp.name) / 'new.sqlite3'
        code = '''import sys
from operaciones_store import OperationsStore
s=OperationsStore(local_path=sys.argv[2]);s.initialize()
with s.connection() as c:
 assert c.execute("SELECT COUNT(*) FROM operations_photo_mutations").fetchone()[0]==0
'''
        children = [self.child(code, path) for _ in range(3)]
        for child in children:
            _, error = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, error)

    def test_real_account_switch_rejects_old_pending_without_changing_technician_scope(self):
        import app as module
        client = module.app.test_client()
        with client.session_transaction() as session:
            session.update(hsc_authenticated=True, hsc_role='technician', hsc_user_id='B', hsc_account_checked_at=time.time(), hsc_permissions={'createReports': True})
        payload = dict(client_id='UVMQ', equipment_id='UVMQ1', round='4', payload={'notas':'synthetic'})
        with patch.object(module, 'OPERACIONES_STORE', self.store), patch.object(module, '_app_access_password', return_value='synthetic'):
            self.assertEqual(client.post('/api/operaciones/reports/draft', json=payload, headers={'X-HSC-Account':'A'}).status_code, 401)
            response = client.post('/api/operaciones/reports/draft', json=payload, headers={'X-HSC-Account':'B'})
            self.assertEqual(response.status_code, 200, response.get_json())


if __name__ == '__main__':
    unittest.main()
