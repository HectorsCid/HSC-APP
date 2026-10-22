import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from equipment_retirement import removal, create_retirement_blueprint
from operaciones_store import OperationsStore


class EquipmentRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationsStore(local_path=Path(self.temp.name)/'test.sqlite3')
        self.matrix = {'clients': [{'id': 'C', 'name': 'Cliente'}],
                       'equipment': [{'id': 'E1', 'client_id': 'C', 'name': 'Equipo uno', 'status': 'Activo'},
                                     {'id': 'E2', 'client_id': 'C', 'name': 'Equipo dos', 'status': 'Activo'}],
                       'reports': [], 'faults': []}
        self.store.import_matrix_snapshot(deepcopy(self.matrix))

    def apply(self):
        plan = removal(self.store, 'E1')
        return removal(self.store, 'E1', fingerprint=plan['fingerprint'], actor_id='owner')

    def test_empty_delete_preview_backup_and_idempotency(self):
        plan = removal(self.store, 'E1')
        self.assertEqual(plan['action'], 'delete')
        self.assertEqual(len(self.store.snapshot()['equipment']), 2)
        result = self.apply()
        self.assertTrue(result['applied'])
        self.assertEqual([e['id'] for e in self.store.snapshot()['equipment']], ['E2'])
        self.assertTrue(removal(self.store, 'E1', fingerprint=plan['fingerprint'], actor_id='owner')['already_done'])
        with self.store.connection() as conn:
            saved = conn.execute("SELECT action,backup_json FROM operations_equipment_retirements WHERE id='E1'").fetchone()
        self.assertEqual(saved[0], 'delete')
        self.assertEqual(json.loads(saved[1])['equipment']['name'], 'Equipo uno')

    def test_deleted_not_reimported_or_recreated(self):
        self.apply()
        self.store.import_matrix_snapshot(deepcopy(self.matrix))
        self.assertEqual([e['id'] for e in self.store.snapshot()['equipment']], ['E2'])
        with self.assertRaises(ValueError):
            self.store.save_equipment([dict(self.matrix['equipment'][0], active=True)])
        with self.assertRaises(ValueError):
            self.store.save_equipment_photo('E1', 'photo')
        self.assertIsNone(self.store.queue_sync('equipment', 'E1', 'sheets', 'upsert'))

    def test_reports_archive_and_sync_never_reactivates(self):
        self.matrix['reports'] = [{'id': 'R1', 'client_id': 'C', 'equipment_id': 'E1', 'completed': True,
                                   '_evidence_refs': ['photo.jpg']}]
        self.store.import_matrix_snapshot(deepcopy(self.matrix))
        before = self.store.snapshot()
        self.assertEqual(self.apply()['action'], 'archive')
        self.store.import_matrix_snapshot(deepcopy(self.matrix))
        after = self.store.snapshot()
        equipment = next(e for e in after['equipment'] if e['id'] == 'E1')
        self.assertEqual(equipment['status'], 'Inactivo')
        self.assertTrue(equipment['retired'])
        self.assertEqual(before['reports'], after['reports'])
        self.assertEqual(after['stats']['evidence_photos'], 1)
        with self.assertRaises(ValueError):
            self.store.save_equipment([dict(self.matrix['equipment'][0], active=True)])

    def test_photo_list_repair_fault_task_expense_and_pending_preserved(self):
        statements = [
            ("UPDATE operations_equipment SET photo_ref='photo' WHERE id='E1'", ()),
            ('INSERT INTO operations_worklists(id,revision,payload_json) VALUES (?,?,?)', ('W1', 1, json.dumps({'items': [{'equipment_id': 'E1'}]}))),
            ('INSERT INTO operations_repairs(id,revision,owner_id,client_key,equipment_key,service_date,state,payload_json) VALUES (?,?,?,?,?,?,?,?)', ('REP1', 1, 'owner', 'client:C', 'equipment:E1', '2026-10-22', 'draft', '{}')),
        ]
        for sql, params in statements:
            with self.store.connection() as conn:
                conn.execute(sql, params)
        self.store.save_fault({'id': 'F1', 'client_id': 'C', 'equipment_id': 'E1', 'description': 'Falla'})
        self.store.save_task({'title': 'Visita', 'equipment_id': 'E1', 'scheduled_date': '2026-10-22'})
        self.store.save_expense({'user_id': 'owner', 'equipment_id': 'E1', 'concept': 'Compra', 'expense_date': '2026-10-22', 'amount': 1})
        self.store.queue_sync('equipment', 'E1', 'sheets', 'upsert')
        plan = removal(self.store, 'E1')
        self.assertEqual(plan['action'], 'archive')
        self.assertTrue(set(('Reportes o borradores',)).isdisjoint(plan['references']))
        self.assertEqual(len(plan['references']), 7)
        self.apply()
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM operations_worklists").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM operations_repairs").fetchone()[0], 1)
        self.assertEqual(len(self.store.snapshot()['expenses']), 1)

    def test_new_history_requires_new_confirmation(self):
        plan = removal(self.store, 'E1')
        self.store.save_task({'title': 'Nueva visita', 'equipment_id': 'E1', 'scheduled_date': '2026-10-22'})
        with self.assertRaises(ValueError):
            removal(self.store, 'E1', fingerprint=plan['fingerprint'], actor_id='owner')
        self.assertEqual(removal(self.store, 'E1')['action'], 'archive')
        self.assertEqual(len(self.store.snapshot()['equipment']), 2)

    def test_new_relations_to_deleted_are_rejected(self):
        self.apply()
        with self.assertRaises(ValueError):
            self.store.save_report_draft({'client_id': 'C', 'equipment_id': 'E1', 'round': '1'})
        with self.assertRaises(ValueError):
            self.store.save_task({'title': 'Visita', 'equipment_id': 'E1', 'scheduled_date': '2026-10-22'})
        with self.assertRaises(ValueError):
            self.store.save_fault({'client_id': 'C', 'equipment_id': 'E1', 'description': 'Falla'})

    def test_concurrent_import_cannot_resurrect_deleted(self):
        from concurrent.futures import ThreadPoolExecutor
        plan = removal(self.store, 'E1')
        def refresh():
            for _ in range(4):
                self.store.import_matrix_snapshot(deepcopy(self.matrix))
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(refresh)
            second = pool.submit(removal, self.store, 'E1', fingerprint=plan['fingerprint'], actor_id='owner')
            first.result()
            # Import refresh updates timestamps: re-review is required if it won the race.
            try:
                second.result()
            except ValueError:
                self.apply()
        self.store.import_matrix_snapshot(deepcopy(self.matrix))
        self.assertNotIn('E1', [e['id'] for e in self.store.snapshot()['equipment']])

    def test_api_admin_confirm_and_errors(self):
        from flask import Flask, session
        app = Flask(__name__)
        app.secret_key = 'test'
        invalidated = []
        def forbidden(*roles):
            return None if session.get('role') in roles else ({'ok': False}, 403)
        app.register_blueprint(create_retirement_blueprint(self.store, forbidden, lambda: invalidated.append(True)))
        client = app.test_client()
        base = '/api/operaciones/equipment/E1/'
        for role in ('client', 'technician', ''):
            with client.session_transaction() as sess:
                sess['role'] = role
            self.assertEqual(client.post(base+'removal-preview', json={}).status_code, 403)
            self.assertEqual(client.post(base+'remove', json={'confirm': True, 'fingerprint': 'bad'}).status_code, 403)
        with client.session_transaction() as sess:
            sess['role'] = 'admin'
            sess['hsc_user_id'] = 'owner'
        plan = client.post(base+'removal-preview', json={}).get_json()
        self.assertEqual(client.post(base+'remove', json={}).status_code, 400)
        self.assertEqual(client.post(base+'remove', json={'confirm': True, 'fingerprint': 'bad'}).status_code, 409)
        self.assertEqual(client.post(base+'remove', json={'confirm': True, 'fingerprint': plan['fingerprint']}).status_code, 200)
        self.assertEqual(invalidated, [True])


if __name__ == '__main__':
    unittest.main()
