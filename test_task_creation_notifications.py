import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime
from operaciones_store import OperationsStore
from task_notices import plan_task_notices
from test_notification_delivery import NotificationsTest, notices, delivery

class TaskCreationNotifications(unittest.TestCase):
    def setUp(self):
        NotificationsTest.setUp(self)
        self.store = OperationsStore(local_path=Path(self.temp.name)/'operations.sqlite')
        self.store.get_user_by_id = lambda uid: {'id':uid,'name':uid,'status':'active','role':'technician'}
        self.body = dict(id='TASK_TEST', title='Revisar bomba', scheduled_date='2026-09-20',
                         scheduled_time='09:30', client_id='UDA', assigned_user_ids=['T1'], created_by='owner')

    def drain(self, fail=False):
        with patch.dict('sys.modules', {'app':SimpleNamespace(OPERACIONES_STORE=self.store)}):
            if fail:
                with patch.object(delivery, 'enqueue', side_effect=RuntimeError('offline')):
                    delivery.drain_operation_events(SimpleNamespace(logger=Mock()))
            else:delivery.drain_operation_events(SimpleNamespace(logger=Mock()))

    def test_atomic_queue_and_retry_are_idempotent(self):
        self.store.save_task(self.body);self.store.save_task(self.body)
        self.assertEqual(len(self.store.pending_notice_events()),1)
        self.drain(fail=True)
        self.assertEqual(len(self.store.pending_notice_events()),1)
        self.drain()
        self.assertEqual(self.store.pending_notice_events(),[])
        self.assertEqual(len(notices._read()['push_queue']),2)

    def test_task_and_event_rollback_together(self):
        with patch.object(self.store,'_queue_notice_event',side_effect=RuntimeError('DB full')):
            with self.assertRaises(RuntimeError):self.store.save_task(self.body)
        self.assertEqual(self.store.snapshot()['tasks'],[])

    def test_assignment_change_cancellation_and_completion(self):
        first=self.store.save_task(dict(self.body,notify_client=True))
        self.assertTrue(first['notify_client'])
        changed=self.store.save_task(dict(self.body,assigned_user_ids=['T2'],scheduled_time='12:00'))
        self.assertTrue(changed['notify_client'])
        event=self.store.pending_notice_events()[-1]
        planned=plan_task_notices(event['id'],event['payload'])
        self.assertTrue(any(n['user_id']=='T1' and 'Ya no' in n['title'] for n in planned))
        self.assertTrue(any(n['user_id']=='T2' and 'actualizada' in n['title'] for n in planned))
        self.store.complete_task(first['id'],actor_id='T2')
        last=self.store.pending_notice_events()[-1]
        planned=plan_task_notices(last['id'],last['payload'])
        self.assertFalse(any(n['user_id']=='T2' for n in planned))
        self.assertTrue(any(n['audience']=='admin' and n['title']=='Actividad terminada' for n in planned))
        self.assertTrue(any(n['client_id']=='UDA' for n in planned))
        count=len(self.store.pending_notice_events());self.store.complete_task(first['id'],actor_id='T2')
        self.assertEqual(len(self.store.pending_notice_events()),count)
        self.store.save_task(dict(self.body,status='Cancelada'))
        last=self.store.pending_notice_events()[-1]
        self.assertEqual(plan_task_notices(last['id'],last['payload'])[0]['title'],'Actividad cancelada')

    def test_partner_choice_and_confirmation(self):
        self.store.save_task(self.body)
        event=self.store.pending_notice_events()[0]
        self.assertFalse(any(n['audience']=='client' for n in plan_task_notices(event['id'],event['payload'])))
        self.store.save_task(dict(self.body,status='Solicitada',created_by='C1'))
        self.store.save_task(dict(self.body,notify_client=True,actor_id='owner'))
        event=self.store.pending_notice_events()[-1]
        self.assertTrue(any(n['audience']=='client' and n['title']=='Visita confirmada' for n in plan_task_notices(event['id'],event['payload'])))

    def test_late_and_early_reminders_and_internal_tasks(self):
        self.store.save_task(dict(self.body,scheduled_time='07:00',created_by='T1'))
        self.store.list_users=lambda:[{'id':'T1','role':'technician','status':'active'},
                                     {'id':'C1','client_id':'UDA','role':'client','status':'active'}]
        delivery.send_task_reminders(self.store,datetime(2026,9,20,7,0))
        self.assertEqual(len(notices._read()['push_queue']),2)
        delivery.send_task_reminders(self.store,datetime(2026,9,20,8,0))
        self.assertEqual(len(notices._read()['push_queue']),2)
        self.store.save_task(dict(self.body,id='LATE',notify_client=True))
        delivery.send_task_reminders(self.store,datetime(2026,9,20,10,0))
        self.assertEqual(len(notices._read()['push_queue']),5)
        delivery.send_task_reminders(self.store,datetime(2026,9,20,10,1))
        self.assertEqual(len(notices._read()['push_queue']),5)

    def test_expense_events_ownership_and_payment(self):
        expense=dict(id='E1',user_id='T1',technician_name='Uno',created_by='T1',amount=100,
                     expense_date='2026-09-20',concept='Material')
        self.store.save_expense(expense);self.store.save_expense(expense)
        self.assertEqual(len(self.store.pending_notice_events()),1)
        with self.assertRaisesRegex(ValueError,'otra cuenta'):
            self.store.save_expense(dict(expense,user_id='T2'))
        self.store.update_expense_status('E1','Liquidado',actor_id='owner')
        events=self.store.pending_notice_events()
        self.assertEqual(len(events),2)
        plans=plan_task_notices(events[-1]['id'],events[-1]['payload'])
        self.assertEqual(plans[0]['user_id'],'T1')
        self.assertEqual(plans[0]['category'],'pagos')
        self.store.update_expense_status('E1','Liquidado',actor_id='owner')
        self.assertEqual(len(self.store.pending_notice_events()),2)

if __name__=='__main__':unittest.main()
