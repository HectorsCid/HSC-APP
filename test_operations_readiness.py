import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from itertools import count
from unittest.mock import patch
from pathlib import Path

from operaciones_store import OperationsStore
from operaciones_sync import sync_operations_outbox
from test_operaciones_sync import FakeSheets


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        ticks = count()
        base = datetime(2026, 9, 19, tzinfo=timezone.utc)
        self.clock = patch('operaciones_store._now', side_effect=lambda: (base + timedelta(seconds=next(ticks))).isoformat(timespec='microseconds'))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = OperationsStore(local_path=Path(self.tmp.name) / 'operations.sqlite')
        self.matrix = {'clients': [{'id': 'UDA', 'name': 'Arkansas'}],
                       'equipment': [{'id': 'UDA14', 'client_id': 'UDA', 'name': 'Mesa'}],
                       'reports': [], 'faults': []}
        self.store.import_matrix_snapshot(self.matrix)

    def tearDown(self):
        self.tmp.cleanup()

    def edit(self, name='Nombre HSC'):
        self.store.save_equipment([{'id': 'UDA14', 'client_id': 'UDA', 'name': name}])
        return self.store.pending_sync()[0]

    def draft(self, user=None, **extra):
        data = {'equipment_id': 'UDA14', 'client_id': 'UDA', 'round': '1',
                'payload': {'inicio': '2026-09-19', 'fin': '2026-09-19'}}
        if user:
            data['payload']['_draft_user_id'] = user
        data.update(extra)
        return self.store.save_report_draft(data)

    def test_retry_after_more_than_six_failures(self):
        op = self.edit()
        for _ in range(8):
            self.store.mark_sync_failure(op['id'], 'temporary', 'equipment', 'UDA14')
        result = sync_operations_outbox(self.store, FakeSheets(), 'matrix')
        self.assertEqual(result['synced'], 1)
        self.assertEqual(self.store.pending_sync(), [])

    def test_old_ack_cannot_clear_new_edit(self):
        first = self.edit()
        second = self.edit('Edición nueva')
        self.assertNotEqual(first['id'], second['id'])
        self.assertFalse(self.store.mark_sync_success(first['id'], 'equipment', 'UDA14'))
        self.assertFalse(self.store.mark_sync_failure(first['id'], 'late', 'equipment', 'UDA14'))
        self.assertEqual(self.store.pending_sync()[0]['id'], second['id'])
        self.store.import_matrix_snapshot(self.matrix)
        self.assertEqual(self.store.snapshot()['equipment'][0]['name'], 'Edición nueva')

    def test_import_refreshes_confirmed_edit(self):
        op = self.edit()
        self.store.mark_sync_success(op['id'], 'equipment', 'UDA14')
        self.matrix['equipment'][0]['name'] = 'Cambio en AppSheet'
        self.store.import_matrix_snapshot(self.matrix)
        self.assertEqual(self.store.snapshot()['equipment'][0]['name'], 'Cambio en AppSheet')

    def test_duplicate_policy_is_explicit_and_reversible(self):
        self.assertFalse(self.store.matrix_duplicates_allowed())
        self.assertEqual(self.store.status()['matrix_duplicate_policy'], 'block')
        self.assertEqual(self.store.set_matrix_duplicate_policy(True), 'first_wins')
        self.assertTrue(self.store.matrix_duplicates_allowed())
        self.assertEqual(self.store.status()['matrix_duplicate_policy'], 'first_wins')
        self.assertEqual(self.store.set_matrix_duplicate_policy(False), 'block')
        self.assertFalse(self.store.matrix_duplicates_allowed())

    def test_legacy_duplicate_matrix_report_does_not_block_equipment_refresh(self):
        stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        with self.store.connection() as conn:
            conn.execute(
                "INSERT INTO operations_reports(id,equipment_id,client_id,round_number,completed,"
                "source,matrix_id,report_type,payload_json,state,sync_status,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ('legacy-copy', 'UDA14', 'UDA', '1', 1, 'app', 'UDA14_R 1',
                 'refrigeration', '{}', 'completed', 'synced', stamp),
            )
            conn.execute(
                "INSERT INTO operations_reports(id,equipment_id,client_id,round_number,completed,"
                "source,matrix_id,report_type,payload_json,state,sync_status,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ('UDA14_R 1', 'UDA14', 'UDA', '1', 1, 'sheets', 'UDA14_R 1',
                 'refrigeration', '{}', 'imported', 'synced', stamp),
            )
        self.matrix['equipment'][0]['name'] = 'Cambio desde AppSheet'
        self.matrix['reports'] = [{'id': 'UDA14_R 1', 'equipment_id': 'UDA14',
            'client_id': 'UDA', 'round': '1', 'completed': True}]
        self.store.import_matrix_snapshot(self.matrix)
        self.assertEqual(self.store.snapshot()['equipment'][0]['name'], 'Cambio desde AppSheet')

    def test_snapshot_older_than_confirmation_keeps_local_edit(self):
        op = self.edit()
        from operaciones_store import _now as now
        self.matrix['_read_started_at'] = now()
        self.store.mark_sync_success(op['id'], 'equipment', 'UDA14')
        self.store.import_matrix_snapshot(self.matrix)
        self.assertEqual(self.store.snapshot()['equipment'][0]['name'], 'Nombre HSC')

    def test_drafts_isolated_by_account(self):
        first, second = self.draft('tech1'), self.draft('tech2')
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(self.store.get_report_draft('UDA14', '1', user_id='tech1')['id'], first['id'])
        with self.assertRaises(ValueError):
            self.draft('tech2', id=first['id'])
        with self.assertRaises(ValueError):
            self.store.delete_report_draft(first['id'], user_id='tech2')
        self.store.finalize_report(first['id'])
        self.assertIsNone(self.store.get_report_draft('UDA14', '1', user_id='tech1'))
        self.assertEqual(self.store.get_report_draft('UDA14', '1', user_id='tech2')['id'], second['id'])
        with self.assertRaisesRegex(ValueError, 'ya tiene reporte'):
            self.store.finalize_report(second['id'])

    def test_legacy_pending_upload_can_finish_without_discovering_other_drafts(self):
        legacy = self.draft()
        self.assertIsNone(self.store.get_report_draft('UDA14', '1', user_id='tech1'))
        resumed = self.draft('tech1', id=legacy['id'])
        self.assertEqual(resumed['id'], legacy['id'])

    def test_client_photo_persists_and_queues(self):
        self.store.save_entity_photo('client', 'UDA', 'Clientes/UDA/foto.jpg')
        self.assertEqual(self.store.snapshot()['clients'][0]['_photo_ref'], 'Clientes/UDA/foto.jpg')
        self.assertEqual(self.store.pending_sync()[0]['entity_type'], 'client')

    def test_report_keeps_local_id_after_round_trip(self):
        draft = self.draft('tech1')
        self.store.save_report_evidence(draft['id'], 1, 'drive-original', storage_ref='Reportes/foto1.jpg')
        report = self.store.finalize_report(draft['id'])
        op = self.store.pending_sync()[0]
        self.store.mark_sync_success(op['id'], 'report', report['id'])
        self.matrix['reports'] = [{'id': 'UDA14_R 1', 'equipment_id': 'UDA14', 'client_id': 'UDA',
            'round': '1', 'completed': True, '_evidence_refs': ['Reportes/foto1.jpg'],
            '_raw': {'PresionCto1': '25', 'Comentarios': 'Desde matriz'}}]
        self.store.import_matrix_snapshot(self.matrix)
        self.assertEqual(len(self.store.snapshot()['reports']), 1)
        detail = self.store.get_report_detail(report['id'])
        self.assertEqual(detail['payload']['p1'], '25')
        self.assertEqual(detail['payload']['notas'], 'Desde matriz')
        self.assertEqual(len(detail['evidence']), 1)
        self.assertEqual(detail['evidence'][0]['storage_ref'], 'Reportes/foto1.jpg')


if __name__ == '__main__':
    unittest.main()
