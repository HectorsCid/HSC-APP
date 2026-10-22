from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
import json
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
        by_id = {e['id']: e for e in result['protected']}
        self.assertIn('Tiene reportes vinculados', by_id['VC31']['reasons'])
        self.assertIn('Su ficha tiene una fotografía registrada', by_id['VC32']['reasons'])
        self.assertTrue(any('creado o modificado' in r for r in by_id['VC33']['reasons']))
        reconcile(self.store, self.matrix, expected=result['fingerprint'])
        after = reconcile(self.store, self.matrix)
        self.assertTrue(after['done'])
        self.assertEqual(after['protected'], result['protected'])

    def test_bad_count_and_stale_preview_change_nothing(self):
        with self.assertRaises(ValueError):
            reconcile(self.store, dict(self.matrix, equipment=[]))
        plan = reconcile(self.store, self.matrix)
        with self.store.connection() as conn:
            conn.execute("UPDATE operations_equipment SET name='Cambio' WHERE id='VC40'")
        with self.assertRaises(ValueError):
            reconcile(self.store, self.matrix, expected=plan['fingerprint'])
        self.assertEqual(sum(e['status'] == 'Activo' for e in self.store.snapshot()['equipment']), 70)

    def _list_only_setup(self):
        saved = json.dumps({'id': 'L1', 'items': [{'equipment_id': eid} for eid in ('VC31', 'VC32', 'VC33')]})
        with self.store.connection() as conn:
            conn.execute('INSERT INTO operations_worklists(id,revision,payload_json) VALUES (?,?,?)', ('L1', 1, saved))
        plan = reconcile(self.store, self.matrix)
        reconcile(self.store, self.matrix, expected=plan['fingerprint'])
        return saved

    def test_finish_three_preserves_lists_and_is_idempotent(self):
        saved = self._list_only_setup()
        plan = reconcile(self.store, self.matrix)
        self.assertEqual({r['id'] for r in plan['final_candidates']}, {'VC31', 'VC32', 'VC33'})
        result = reconcile(self.store, self.matrix, expected=plan['final_fingerprint'], finish_lists=True)
        self.assertTrue(result['final_applied'])
        self.assertEqual(sum(e['status'] == 'Activo' for e in self.store.snapshot()['equipment']), 30)
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT payload_json FROM operations_worklists WHERE id='L1'").fetchone()[0], saved)
        self.assertNotIn('applied', reconcile(self.store, self.matrix, expected=plan['final_fingerprint'], finish_lists=True))

    def test_new_report_blocks_all_three(self):
        self._list_only_setup()
        plan = reconcile(self.store, self.matrix)
        payload = deepcopy(self.matrix)
        payload['reports'] = [{'id': 'NEW', 'equipment_id': 'VC31', 'client_id': 'VC'}]
        self.store.import_matrix_snapshot(payload)
        with self.assertRaises(ValueError):
            reconcile(self.store, self.matrix, expected=plan['final_fingerprint'], finish_lists=True)
        self.assertEqual(sum(e['status'] == 'Activo' for e in self.store.snapshot()['equipment']), 33)

    def test_finish_form_and_refresh(self):
        from flask import Flask
        self._list_only_setup()
        app = Flask(__name__)
        app.secret_key = 'test-only'
        invalidated = []
        app.register_blueprint(create_cleanup_blueprint(self.store, lambda *_: None, lambda: self.matrix,
                                                       lambda: invalidated.append(True)))
        client = app.test_client()
        page = client.get('/admin/limpieza-equipos-visscher')
        self.assertIn(b'Retirar VC31, VC32 y VC33', page.data)
        with client.session_transaction() as sess:
            csrf = sess['equipment_cleanup_csrf']
        plan = reconcile(self.store, self.matrix)
        page = client.post('/admin/limpieza-equipos-visscher', data={
            'csrf': csrf, 'phase': 'finish_lists', 'fingerprint': plan['final_fingerprint']})
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'VC31, VC32 y VC33 quedaron inactivos', page.data)
        self.assertEqual(invalidated, [True])
        page = client.get('/admin/limpieza-equipos-visscher')
        self.assertNotIn(b'Retirar VC31, VC32 y VC33', page.data)

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
