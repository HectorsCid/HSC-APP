"""Real owner routes with synthetic SQL and Google failure injection."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_operaciones_sync import _store_with_report


class SyncRecoveryApiTests(unittest.TestCase):
    def setUp(self):
        import app
        self.module = app
        temp = tempfile.TemporaryDirectory(prefix='hsc-recovery-api-')
        self.addCleanup(temp.cleanup)
        self.store, self.report_id = _store_with_report(Path(temp.name))
        self.operation = self.store.pending_sync()[0]
        with self.assertRaises(TimeoutError):
            with self.store.sheet_write_guard(self.operation, 'synthetic-sheet', self.store.get_report_detail(self.report_id)['revision']) as delivery:
                delivery['sent'] = True
                raise TimeoutError('Respuesta perdida')
        self.state = self.store.sheet_delivery_status('synthetic-sheet')
        for key, value in [('OPERACIONES_STORE', self.store), ('SHEET_ID', 'synthetic-sheet'),
                           ('OPERACIONES_MATRIX_AUTO_SYNC', False), ('OPERACIONES_SHEETS_SYNC_ENABLED', True)]:
            p = patch.object(app, key, value); p.start(); self.addCleanup(p.stop)
        self.scheduler = patch.object(app, '_schedule_operations_sync', return_value=True).start()
        self.addCleanup(patch.stopall)
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session.update(hsc_authenticated=True,hsc_role='admin',hsc_user_id='owner-A',hsc_user_name='Ana Pruebas')
        self.body = dict(operation_id=self.state['operation_id'],attempt_id=self.state['attempt_id'],confirm_no_request_in_flight=True)

    def test_diagnostic_remains_visible_when_google_read_fails(self):
        with patch.object(self.module, 'get_sheets_service', side_effect=TimeoutError('offline')):
            response = self.client.get('/api/operaciones/diagnostics?live=1')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['sheet_delivery']['requires_confirmation'])
        self.assertEqual(payload['sheet_delivery']['entity_id'], self.report_id)
        self.assertTrue(payload['google_error'])

    def test_confirm_route_audits_session_actor_and_restarts_queue(self):
        before = self.store.pending_sync()
        response = self.client.post('/api/operaciones/sync/resolve-uncertain', json=dict(self.body, actor_id='forged'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['queue_state'], 'started')
        self.assertEqual(response.get_json()['recovery']['actor_id'], 'owner-A')
        self.scheduler.assert_called_with(force=True)
        self.assertEqual(before, self.store.pending_sync())
        self.assertEqual(self.client.post('/api/operaciones/sync/resolve-uncertain', json=self.body).status_code, 409)

    def test_confirmation_and_attempt_reference_are_required(self):
        for patch_body in [dict(confirm_no_request_in_flight=False),dict(attempt_id='old-attempt'),dict(operation_id='old-op')]:
            response = self.client.post('/api/operaciones/sync/resolve-uncertain', json=dict(self.body, **patch_body))
            self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.store.pending_sync()), 1)

    def test_technician_cannot_view_or_resume(self):
        with self.client.session_transaction() as session:
            session['hsc_role'] = 'technician'
        self.assertEqual(self.client.get('/api/operaciones/diagnostics').status_code, 403)
        self.assertEqual(self.client.get('/api/operaciones/sync-status').status_code, 403)
        self.assertEqual(self.client.post('/api/operaciones/sync/resolve-uncertain', json=self.body).status_code, 403)

    def test_busy_worker_schedules_followup_instead_of_losing_resume(self):
        patch.stopall()
        module = self.module
        with module.app.app_context(), patch.object(module,'OPERACIONES_STORE',self.store), \
                patch.object(module,'OPERACIONES_SHEETS_SYNC_ENABLED',True), \
                patch.object(module,'_request_operations_sync_retry') as retry:
            module._OPERACIONES_SYNC_LOCK.acquire()
            try:
                self.assertFalse(module._schedule_operations_sync(force=True))
                retry.assert_called_once()
            finally:
                module._OPERACIONES_SYNC_LOCK.release()

    def test_worker_automatically_schedules_safe_failure_retry(self):
        from test_operaciones_sync import FakeSheets
        self.store.resolve_sheet_delivery('synthetic-sheet',self.state['operation_id'],
            attempt_id=self.state['attempt_id'],external_quiescent=True,actor_id='owner-test')
        with patch.object(self.module,'get_sheets_write_service',return_value=FakeSheets(fail_completion_once=True)), \
                patch.object(self.module,'_request_operations_sync_retry') as retry:
            self.module._OPERACIONES_SYNC_LOCK.acquire()
            self.module._operations_sync_worker(self.module.app)
            retry.assert_called_once()
            self.assertEqual(self.store.sheet_delivery_status('synthetic-sheet')['state'],'idle')
            self.assertEqual(len(self.store.pending_sync()),1)

    def test_worker_keeps_unknown_delivery_paused_without_automatic_release(self):
        from test_operaciones_sync import FakeSheets
        with patch.object(self.module,'get_sheets_write_service',return_value=FakeSheets()), \
                patch.object(self.module,'_request_operations_sync_retry') as retry:
            self.module._OPERACIONES_SYNC_LOCK.acquire()
            self.module._operations_sync_worker(self.module.app)
            retry.assert_not_called()
            self.assertTrue(self.store.sheet_delivery_status('synthetic-sheet')['requires_confirmation'])
