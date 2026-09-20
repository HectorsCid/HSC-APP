import ast
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from operaciones_store import OperationsStore
from operaciones_sync import _entity_snapshot, _plan_operation


class MatrixIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = OperationsStore(local_path=Path(self.tmp.name) / 'test.sqlite')
        self.payload = {'clients': [{'id': 'UDA', 'name': 'Arkansas'}, {'id': 'TT', 'name': 'Travers'}],
                        'equipment': [{'id': 'UDA14', 'client_id': 'UDA', 'name': 'Mesa fría'}],
                        'reports': [], 'faults': []}
        self.store.import_matrix_snapshot(self.payload)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rename_photo_and_report_use_ids(self):
        self.store.save_equipment([{'id': 'UDA14', 'client_id': 'UDA', 'name': 'Refrigerador 1'}])
        self.store.save_equipment_photo('UDA14', 'Equipos/UDA14/nueva.jpg')
        _, key, identity, values = _entity_snapshot(self.store, {'entity_type': 'equipment', 'entity_id': 'UDA14'})
        self.assertEqual((key, identity, values['ID_Cliente']), ('ID_Equipo', 'UDA14', 'UDA'))
        self.assertEqual(values['NombreEquipo'], 'Refrigerador 1')
        self.assertEqual(values['Foto'], 'Equipos/UDA14/nueva.jpg')
        self.payload['reports'] = [{'id': 'UDA14_R 1', 'equipment_id': 'UDA14', 'client_id': 'UDA', 'round': '1', 'completed': True}]
        self.store.import_matrix_snapshot(self.payload)
        snap = self.store.snapshot()
        self.assertEqual(len(snap['equipment']), 1)
        self.assertEqual(snap['equipment'][0]['name'], 'Refrigerador 1')
        self.assertEqual(snap['reports'][0]['equipment_id'], 'UDA14')
        self.store.save_equipment_photo('UDA14', '')
        self.assertFalse(self.store.snapshot()['equipment'][0]['has_photo'])

    def test_no_cross_client_reassignment(self):
        with self.assertRaises(ValueError):
            self.store.save_equipment([{'id': 'UDA14', 'client_id': 'TT', 'name': 'Otro'}])
        self.assertEqual(self.store.snapshot()['equipment'][0]['client_id'], 'UDA')

    def test_duplicate_ids_block_writer(self):
        fake = SimpleNamespace()
        indexes = {('Equipos', 'ID_Equipo'): (['ID_Equipo', 'ID_Cliente', 'NombreEquipo'],
                  {'idequipo': 0, 'idcliente': 1, 'nombreequipo': 2}, {'UDA14': [2, 7]})}
        with self.assertRaisesRegex(ValueError, 'duplicado'):
            _plan_operation(self.store, {'entity_type': 'equipment', 'entity_id': 'UDA14'}, fake, 'test', ['Equipos'], indexes)

    def test_incoming_runs_even_when_outgoing_fails(self):
        module = ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == '_operations_sync_worker')
        calls = []
        class Context:
            def __enter__(self): pass
            def __exit__(self, *args): pass
        logger = SimpleNamespace(exception=lambda *a: None, warning=lambda *a: None, info=lambda *a: None)
        app = SimpleNamespace(app_context=Context, logger=logger)
        def fail(*a, **kw): raise RuntimeError('Google write unavailable')
        ns = {'datetime': datetime, '_OPERACIONES_IMPORT_STATUS': {}, 'OPERACIONES_MATRIX_AUTO_SYNC': True, 'OPERACIONES_STORE': SimpleNamespace(import_matrix_snapshot=lambda p: calls.append('import')),
              'get_sheets_write_service': fail, 'sync_operations_outbox': fail, 'SHEET_ID': 'test',
              'get_sheets_service': lambda **kw: None, 'read_operaciones_matrix': lambda *a, **kw: {'stats': {}},
              '_operations_migration_checks': lambda p: {'ready': True}, '_complete_legacy_operations_relations': lambda p: p,
              '_invalidate_operations_cache': lambda: calls.append('invalidate'), 'reset_thread_google_services': lambda: None,
              '_OPERACIONES_SYNC_LOCK': SimpleNamespace(release=lambda: calls.append('release'))}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), ns)
        ns['_operations_sync_worker'](app)
        self.assertEqual(calls, ['import', 'invalidate', 'release'])


if __name__ == '__main__': unittest.main()
