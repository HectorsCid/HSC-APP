import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import abort, current_app, jsonify, request, session
from test_notification_delivery import NotificationsTest, notices, delivery


class TaskCreationNotifications(unittest.TestCase):
    def setUp(self):
        NotificationsTest.setUp(self)
        modules = patch.dict('sys.modules', {'notification_delivery': delivery})
        modules.start()
        self.addCleanup(modules.stop)
        store = SimpleNamespace(
            snapshot=lambda: {'tasks': []},
            get_user_by_id=lambda user_id: {'name': 'Luis', 'id': user_id},
            save_task=lambda body: dict(body, id=body.get('id') or 'TASK_TEST'),
        )
        module = ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        route = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'api_operaciones_save_task')
        route.decorator_list = []
        ns = {'request': request, 'session': session, 'jsonify': jsonify, 'abort': abort,
              'current_app': current_app, '_operations_role': lambda: session.get('hsc_role'),
              '_operations_forbidden': lambda *roles: None, 'OPERACIONES_STORE': store,
              '_invalidate_operations_cache': Mock()}
        exec(compile(ast.Module(body=[route], type_ignores=[]), 'app.py', 'exec'), ns)
        self.app.add_url_rule('/tasks', view_func=ns[route.name], methods=['POST'])
        self.body = {'id': 'TASK_TEST', 'title': 'Revisar bomba', 'scheduled_date': '2026-09-20',
                     'scheduled_time': '09:30', 'client_id': 'UDA'}

    login = NotificationsTest.login
    subscribe = NotificationsTest.subscribe

    def test_technician_creation_notifies_admin_without_notify_client(self):
        self.login(); self.subscribe('https://fcm.googleapis.com/admin')
        self.login('technician', 'tech1'); self.subscribe('https://fcm.googleapis.com/tech')
        response = self.client.post('/tasks', json=self.body)
        self.assertEqual(response.status_code, 201)
        queue = notices._read()['push_queue']
        self.assertEqual(len(queue), 1)
        self.assertEqual([t['user_id'] for t in queue[0]['targets']], ['owner'])
        self.assertIn('Luis', queue[0]['body'])
        self.assertIn('09:30', queue[0]['body'])
        self.assertIn('open=calendar', queue[0]['url'])
        self.client.post('/tasks', json=self.body)
        self.assertEqual(len(notices._read()['push_queue']), 1)

    def test_admin_creation_does_not_send_self_notice(self):
        self.login(); self.subscribe()
        self.assertEqual(self.client.post('/tasks', json=self.body).status_code, 201)
        self.assertEqual(notices._read().get('push_queue', []), [])

    def test_optional_partner_notice_stays_scoped(self):
        self.login('technician', 'tech1')
        self.client.post('/tasks', json=dict(self.body, notify_client=True))
        items = notices._read()['items']
        self.assertEqual({i['audience'] for i in items}, {'admin', 'client'})
        partner = next(i for i in items if i['audience'] == 'client')
        self.assertEqual(partner['client_id'], 'UDA')


if __name__ == '__main__':
    unittest.main()
