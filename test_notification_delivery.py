import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from flask import Flask

with patch.dict('sys.modules', {'cfdi_drive':SimpleNamespace(backup_json_file=Mock(),load_json_file=lambda *a,**k:{})}):
    import notification_center as notices
    import notification_delivery as delivery


class NotificationsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=patch.object(notices,'PATH',Path(self.temp.name)/'notices.json')
        self.path.start();self.addCleanup(self.path.stop)
        self.backup=patch.object(notices,'backup_json_file',Mock())
        self.backup.start();self.addCleanup(self.backup.stop)
        self.app=Flask(__name__);self.app.config.update(TESTING=True,SECRET_KEY='test')
        self.app.register_blueprint(delivery.bp);self.client=self.app.test_client()

    def login(self, role='admin', user='owner', company=''):
        with self.client.session_transaction() as session:
            session.update(hsc_authenticated=True,hsc_role=role,hsc_user_id=user,hsc_client_id=company)

    def subscribe(self, endpoint='https://fcm.googleapis.com/test'):
        return self.client.post('/api/operaciones/avisos/subscribe',json={'subscription':{
            'endpoint':endpoint,'keys':{'auth':'test','p256dh':'test'}}})

    def test_inbox_and_mark_read_are_scoped(self):
        notices.publish('Admin','private',key='admin')
        notices.publish('Client A','a',key='a',audience='client',client_id='A')
        self.login('client','user-a','A')
        result=self.client.get('/api/operaciones/avisos').get_json()
        self.assertEqual([r['title'] for r in result['items']],['Client A'])
        admin_id=notices.snapshot()['items'][0]['id']
        self.assertEqual(self.client.post(f'/api/operaciones/avisos/{admin_id}/read').status_code,404)

    def test_legacy_authenticated_owner_and_anonymous(self):
        self.assertEqual(self.client.get('/api/operaciones/avisos').status_code,401)
        with self.client.session_transaction() as session:
            session['hsc_authenticated']=True
        self.assertEqual(self.client.get('/api/operaciones/avisos').status_code,200)
        self.subscribe()
        self.assertEqual(notices._read()['push_devices'][0]['user_id'],'owner')

    def test_subscription_identity_cannot_be_forged(self):
        self.login('client','user-a','A');self.subscribe()
        device=notices._read()['push_devices'][0]
        self.assertEqual((device['role'],device['client_id']),('client','A'))
        delivery.enqueue('Admin','private','/inicio-app','admin')
        self.assertEqual(notices._read()['push_queue'][0]['targets'],[])

    def test_retries_and_success_not_resent(self):
        self.login();self.subscribe()
        delivery.enqueue('Falla','body','/inicio-app','fault:1')
        push=Mock(side_effect=[RuntimeError('offline'),None])
        with patch.object(delivery,'configured',return_value=True),patch.dict('sys.modules',{'pywebpush':SimpleNamespace(webpush=push)}):
            delivery.drain()
            state=notices._read();target=state['push_queue'][0]['targets'][0]
            self.assertEqual(target['status'],'pending');target['next_at']=0;notices._write_local(state)
            delivery.drain();delivery.drain()
        self.assertEqual(push.call_count,2)
        self.assertEqual(notices._read()['push_queue'][0]['targets'][0]['status'],'accepted')

    def test_changed_account_cancels_pending_admin_delivery(self):
        self.login();self.subscribe();delivery.enqueue('Admin','secret','/inicio-app','admin')
        self.login('client','user-a','A');self.subscribe()
        push=Mock()
        with patch.object(delivery,'configured',return_value=True),patch.dict('sys.modules',{'pywebpush':SimpleNamespace(webpush=push)}):
            delivery.drain()
        push.assert_not_called()
        self.assertEqual(notices._read()['push_queue'][0]['targets'][0]['status'],'cancelled')

    def test_real_test_requires_own_device(self):
        self.login();self.subscribe()
        self.assertTrue(self.client.post('/api/operaciones/avisos/test',json={'endpoint':'https://fcm.googleapis.com/test'}).get_json()['queued'])
        self.login('client','other','B')
        self.assertEqual(self.client.post('/api/operaciones/avisos/test',json={'endpoint':'https://fcm.googleapis.com/test'}).status_code,400)

    def test_queue_persists_and_duplicate_event_is_not_requeued(self):
        self.login();self.subscribe()
        delivery.enqueue('Falla','body','/inicio-app','fault:1')
        delivery.enqueue('Falla','body','/inicio-app','fault:1')
        self.assertEqual(len(notices._read()['push_queue']),1)
        self.assertTrue(notices.PATH.exists())

    def test_disabled_category_blocks_push_and_inbox(self):
        self.login();self.subscribe()
        state=notices._read();state['notification_preferences']={'owner':{'fallas':False}};notices._write_local(state)
        delivery.enqueue('Falla','body','/inicio-app','fault-muted',category='fallas')
        self.assertEqual(notices._read()['push_queue'][0]['targets'],[])
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['items'],[])

    def test_read_and_delete_do_not_affect_other_users(self):
        notices.publish('Falla','body',key='shared-tech',audience='technician')
        self.login('technician','T1')
        item=self.client.get('/api/operaciones/avisos').get_json()['items'][0]
        self.client.post(f"/api/operaciones/avisos/{item['id']}/read")
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['unread'],0)
        self.login('technician','T2')
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['unread'],1)
        self.login('technician','T1')
        self.client.delete(f"/api/operaciones/avisos/{item['id']}")
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['items'],[])
        self.login('technician','T2')
        self.assertEqual(len(self.client.get('/api/operaciones/avisos').get_json()['items']),1)
        self.login('client','C1','OTHER')
        self.client.post('/api/operaciones/avisos/delete-many',json={'ids':[item['id']]})
        self.login('technician','T2')
        self.assertEqual(len(self.client.get('/api/operaciones/avisos').get_json()['items']),1)

    def test_resolution_excludes_actor_on_all_devices_and_inbox(self):
        self.login('technician','T1');self.subscribe('https://push.example/t1-phone');self.subscribe('https://push.example/t1-tablet')
        self.login('technician','T2');self.subscribe('https://push.example/t2')
        delivery.enqueue('Resuelta','body','/hsc-tecnico/','resolved-test',category='fallas',audience='technician',exclude_user_id='T1')
        self.assertEqual([t['user_id'] for t in notices._read()['push_queue'][0]['targets']],['T2'])
        self.login('technician','T1')
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['items'],[])
        self.login('technician','T2')
        response=self.client.get('/api/operaciones/avisos').get_json()
        self.assertEqual(len(response['items']),1)
        self.assertNotIn('facturas',response['categories'])
        self.login();self.subscribe('https://push.example/admin')
        delivery.enqueue('Resuelta','body','/inicio-app','resolved-self',category='fallas',exclude_user_id='owner')
        self.assertEqual(notices._read()['push_queue'][-1]['targets'],[])
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['items'],[])

    def test_technician_payment_targets_only_its_owner(self):
        self.login('technician','T1');self.subscribe('https://push.example/one')
        self.login('technician','T2');self.subscribe('https://push.example/two')
        delivery.enqueue('Pagado','body','/hsc-tecnico/','paid:1',category='pagos',audience='technician',user_id='T1')
        targets=notices._read()['push_queue'][0]['targets']
        self.assertEqual([t['user_id'] for t in targets],['T1'])
        self.assertEqual(self.client.get('/api/operaciones/avisos').get_json()['items'],[])

    def test_daily_task_reminders_are_scoped_and_not_repeated(self):
        from datetime import datetime, timezone, timedelta
        store=Mock()
        store.list_users.return_value=[{'id':'T1','role':'technician','status':'active'},{'id':'T2','role':'technician','status':'active'},
            {'id':'C1','role':'client','status':'active','client_id':'A'},{'id':'C2','role':'client','status':'active','client_id':'B'}]
        store.tasks_for_reminders.return_value=[{'id':'M1','title':'Mantenimiento','scheduled_time':'09:00','client_id':'A','assigned_user_ids':['T1'],'created_by':'owner'},
            {'id':'M2','title':'Reparación','scheduled_time':'12:30','client_id':'A','assigned_user_ids':['T1'],'created_by':'owner'}]
        now=datetime(2026,9,18,7,59,tzinfo=timezone(timedelta(hours=-6)))
        delivery.send_task_reminders(store,now)
        store.tasks_for_reminders.assert_not_called()
        delivery.send_task_reminders(store,now.replace(hour=8,minute=0))
        delivery.send_task_reminders(store,now.replace(hour=9))
        jobs=notices._read()['push_queue']
        self.assertEqual(len(jobs),3)
        self.assertEqual(len(notices._read()['task_reminder_days']),3)
        for role,user,company,count in [('technician','T1','',1),('technician','T2','',0),('client','C1','A',1),('client','C2','B',0),('admin','owner','',1)]:
            self.login(role,user,company)
            rows=self.client.get('/api/operaciones/avisos').get_json()['items']
            agenda=[r for r in rows if r['category']=='agenda']
            self.assertEqual(len(agenda),count)
            if count:self.assertIn('12:30',agenda[0]['body'])

    def test_quote_notifications_only_target_own_partner(self):
        self.login('client','user-a','A');self.subscribe('https://fcm.googleapis.com/a')
        self.login('client','user-b','B');self.subscribe('https://fcm.googleapis.com/b')
        self.login('technician','tech');self.subscribe('https://fcm.googleapis.com/tech')
        delivery.observe_partner_documents('A',{'quotes':[]})
        delivery.observe_partner_documents('A',{'quotes':[{'id':'quote-A','status':'Disponible'}]})
        job=notices._read()['push_queue'][-1]
        self.assertEqual([t['user_id'] for t in job['targets']],['user-a'])
        for role,user,company,count in [('client','user-a','A',1),('client','user-b','B',0),('technician','tech','',0)]:
            self.login(role,user,company)
            rows=self.client.get('/api/operaciones/avisos').get_json()['items']
            self.assertEqual(len([r for r in rows if r['category']=='cotizaciones']),count)

    def test_documents_baseline_new_item_and_change_without_duplicates(self):
        delivery.observe_partner_documents('A',{'quotes':[{'id':'old','status':'Disponible'}]})
        self.assertEqual(notices._read().get('items',[]),[])
        payload={'quotes':[{'id':'old','status':'Disponible'},{'id':'new','status':'Disponible'}]}
        delivery.observe_partner_documents('A',payload)
        delivery.observe_partner_documents('A',payload)
        self.assertEqual(len(notices._read()['items']),1)
        self.assertEqual(notices._read()['items'][0]['client_id'],'A')
        payload['quotes'][1]['status']='Aceptada'
        delivery.observe_partner_documents('A',payload)
        self.assertEqual(len(notices._read()['items']),2)

    def test_only_admin_can_change_notification_settings(self):
        self.login('technician','T1')
        self.assertEqual(self.client.post('/api/operaciones/avisos/preferences/owner',json={'notifications':{}}).status_code,403)


if __name__=='__main__':unittest.main()
