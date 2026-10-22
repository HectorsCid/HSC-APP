from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from equipment_cleanup import reconcile, create_cleanup_blueprint
from operaciones_store import OperationsStore


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationsStore(local_path=Path(self.temp.name)/'test.sqlite3')
        self.matrix = {'clients': [{'id': 'VC', 'name': 'Visscher Caravelle'}],
                       'equipment': [{'id': f'VC{i}', 'client_id': 'VC', 'name': f'Equipo {i}',
                                      'status': 'Activo'} for i in range(70)], 'reports': [], 'faults': []}
        self.store.import_matrix_snapshot(deepcopy(self.matrix))
        self.matrix['equipment'] = self.matrix['equipment'][:30]

    def test_preview_apply_backup_and_once(self):
        plan = reconcile(self.store, self.matrix)
        self.assertEqual(len(plan['candidates']), 40)
        self.assertEqual(sum(e['status'] == 'Activo' for e in self.store.snapshot()['equipment']), 70)
        result = reconcile(self.store, self.matrix, expected=plan['fingerprint'])
        self.assertEqual(result['applied'], 40)
        rows = self.store.snapshot()['equipment']
        self.assertEqual(len(rows), 70)
        self.assertEqual(sum(e['status'] == 'Activo' for e in rows), 30)
        self.assertTrue(reconcile(self.store, self.matrix, expected=plan['fingerprint'])['done'])

    def test_protect_reports_photos_app_and_pending(self):
        payload = deepcopy(self.matrix)
        payload['reports'] = [{'id': 'R1', 'client_id': 'VC', 'equipment_id': 'VC31'}]
        self.store.import_matrix_snapshot(payload)
        with self.store.connection() as conn:
            conn.execute("UPDATE operations_equipment SET photo_ref='photo' WHERE id='VC32'")
            conn.execute("UPDATE operations_equipment SET source='app' WHERE id='VC33'")
        result = reconcile(self.store, self.matrix)
        self.assertTrue({'VC31', 'VC32', 'VC33'}.issubset({e['id'] for e in result['protected']}))

    def test_bad_count_and_stale_preview_change_nothing(self):
        with self.assertRaises(ValueError):
            reconcile(self.store, dict(self.matrix, equipment=[]))
        plan = reconcile(self.store, self.matrix)
        with self.store.connection() as conn:
            conn.execute("UPDATE operations_equipment SET name='Cambio' WHERE id='VC40'")
        with self.assertRaises(ValueError):
            reconcile(self.store, self.matrix, expected=plan['fingerprint'])
        self.assertEqual(sum(e['status'] == 'Activo' for e in self.store.snapshot()['equipment']), 70)

    def test_admin_only_csrf_and_render(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = 'test-only'
        app.register_blueprint(create_cleanup_blueprint(self.store, lambda *_: None, lambda: self.matrix, lambda: None))
        client = app.test_client()
        page = client.get('/admin/limpieza-equipos-visscher')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'40 espacios', page.data)
        self.assertEqual(client.post('/admin/limpieza-equipos-visscher').status_code, 403)
        denied = Flask('denied')
        denied.register_blueprint(create_cleanup_blueprint(self.store, lambda *_: ('No', 403), lambda: self.matrix, lambda: None))
        self.assertEqual(denied.test_client().get('/admin/limpieza-equipos-visscher').status_code, 403)


if __name__ == '__main__':
    unittest.main()
