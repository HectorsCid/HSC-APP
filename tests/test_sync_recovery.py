"""Owner recovery regressions; synthetic data, no external services."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from operaciones_store import OperationsStore
from operaciones_sync import sync_operations_outbox, _plan_operation, _get_sheet_titles, _write_plan
from test_operaciones_sync import FakeSheets, _store_with_report


class SyncRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hsc-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.store, self.report_id = _store_with_report(Path(self.temp.name))
        self.operation = self.store.pending_sync()[0]
        self.revision = self.store.get_report_detail(self.report_id)['revision']

    def restarted(self):
        return OperationsStore(local_path=self.store.local_path)

    def uncertain(self):
        with self.assertRaises(TimeoutError):
            with self.store.sheet_write_guard(self.operation, 'test-sheet', self.revision) as delivery:
                delivery['sent'] = True
                raise TimeoutError('Respuesta de Google perdida')
        return self.store.sheet_delivery_status('test-sheet')

    def resume(self, delivery, actor='owner-A', confirmed=True):
        return self.store.resolve_sheet_delivery('test-sheet', delivery['operation_id'],
            attempt_id=delivery['attempt_id'], external_quiescent=confirmed,
            actor_id=actor, actor_name=actor)

    def test_uncertain_has_frozen_entity_revision_reason_and_exact_count(self):
        state = self.uncertain()
        for n in range(70):
            self.store.queue_sync('equipment', f'TEST-{n}', 'sheets', 'upsert', {'test': n})
        state = self.store.sheet_delivery_status('test-sheet')
        self.assertTrue(state['requires_confirmation'])
        self.assertEqual(state['entity_id'], self.report_id)
        self.assertEqual(state['revision'], self.revision)
        self.assertEqual(state['waiting_changes'], 71)
        self.assertIn('perdida', state['last_error'])
        self.assertTrue(state['attempt_id'])
        self.assertTrue(state['started_at'])

    def test_confirmation_required_and_pending_bytes_preserved_and_audited(self):
        state = self.uncertain()
        before = self.store.pending_sync(200)
        with self.assertRaises(ValueError):
            self.resume(state, confirmed=False)
        self.resume(state)
        self.assertEqual(before, self.store.pending_sync(200))
        last = self.store.sheet_delivery_status('test-sheet')['last_recovery']
        self.assertEqual(last['actor_id'], 'owner-A')
        self.assertEqual(last['attempt_id'], state['attempt_id'])
        self.assertTrue(last['resumed_at'])
        result = sync_operations_outbox(self.store, FakeSheets(), 'test-sheet')
        self.assertEqual(result['synced'], 1)
        self.assertEqual(self.store.pending_sync(), [])

    def test_stale_reference_cannot_release_new_attempt_of_same_operation(self):
        old = self.uncertain()
        self.resume(old)
        current = self.uncertain()
        self.assertNotEqual(old['attempt_id'], current['attempt_id'])
        with self.assertRaises(ValueError):
            self.resume(old)
        self.assertTrue(self.store.sheet_delivery_status('test-sheet')['requires_confirmation'])

    def test_two_owners_exactly_one_can_resume(self):
        state = self.uncertain()
        barrier = threading.Barrier(2)
        def run(actor):
            barrier.wait()
            try:
                self.resume(state, actor)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(run, ['owner-A', 'owner-B']))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.store.pending_sync()), 1)

    def test_active_request_cannot_be_released(self):
        entered, finish = threading.Event(), threading.Event()
        def run():
            with self.store.sheet_write_guard(self.operation, 'test-sheet', self.revision):
                entered.set()
                finish.wait(10)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(run)
            self.assertTrue(entered.wait(5))
            try:
                state = self.store.sheet_delivery_status('test-sheet')
                self.assertTrue(state['in_flight'])
                self.assertFalse(state['requires_confirmation'])
                with self.assertRaises(ValueError):
                    self.resume(state)
            finally:
                finish.set()
            future.result()

    def test_definite_rejection_recovers_automatically(self):
        from googleapiclient.errors import HttpError
        from httplib2 import Response
        with self.assertRaises(HttpError):
            with self.store.sheet_write_guard(self.operation, 'test-sheet', self.revision) as delivery:
                delivery['sent'] = True
                raise HttpError(Response({'status': '429'}), b'{}')
        self.assertEqual(self.store.sheet_delivery_status('test-sheet')['state'], 'idle')
        self.assertEqual(sync_operations_outbox(self.store, FakeSheets(), 'test-sheet')['synced'], 1)

    def test_local_failure_before_http_never_requires_manual_confirmation(self):
        fake=FakeSheets()
        plan=_plan_operation(self.store,self.operation,fake,'test-sheet',_get_sheet_titles(fake,'test-sheet'),{})
        with patch('operaciones_sync._write_current_plan',side_effect=ValueError('Preparación local fallida')):
            with self.assertRaises(ValueError):_write_plan(fake,'test-sheet',plan)
        self.assertEqual(self.store.sheet_delivery_status('test-sheet')['state'],'idle')
        self.assertEqual(fake.writes,[])

    def test_read_failure_after_acknowledged_write_retries_automatically(self):
        import operaciones_sync
        fake=FakeSheets()
        plan=_plan_operation(self.store,self.operation,fake,'test-sheet',_get_sheet_titles(fake,'test-sheet'),{})
        read_index=operaciones_sync._read_index
        calls=[]
        def read(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise TimeoutError('Lectura posterior sin respuesta')
            return read_index(*args,**kwargs)
        with patch('operaciones_sync._read_index',side_effect=read):
            with self.assertRaises(TimeoutError):_write_plan(fake,'test-sheet',plan)
        self.assertEqual(self.store.sheet_delivery_status('test-sheet')['state'],'idle')
        self.assertEqual(sync_operations_outbox(self.store,fake,'test-sheet')['synced'],1)
        self.assertEqual(sum(row[0]=='append' for row in fake.writes),1)

    def test_orphan_prepared_intent_recovers_automatically_without_losing_changes(self):
        self.store._sheet_delivery('sheets:test-sheet', self.operation['id'], 'active', details={
            'attempt_id': 'prepared-attempt', 'phase': 'prepared', 'entity_id': self.report_id})
        before = self.store.pending_sync()
        restarted = self.restarted()
        self.assertEqual(restarted.sheet_delivery_status('test-sheet')['state'], 'idle')
        self.assertEqual(before, restarted.pending_sync())

    def test_old_schema_intent_gets_stable_reference_and_stays_paused(self):
        self.store._sheet_delivery('sheets:test-sheet', self.operation['id'], 'active')
        first = self.store.sheet_delivery_status('test-sheet')
        second = self.store.sheet_delivery_status('test-sheet')
        self.assertTrue(first['requires_confirmation'])
        self.assertEqual(first['attempt_id'], second['attempt_id'])
        self.assertEqual(len(self.store.pending_sync()), 1)


if __name__ == '__main__':
    unittest.main()
