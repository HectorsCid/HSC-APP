import ast
import copy
import tempfile
import unittest
from pathlib import Path
from operaciones_store import OperationsStore


class WorklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperationsStore(local_path=Path(self.tmp.name) / 'lists.db')
        self.store.import_matrix_snapshot({'clients': [{'id':'A','name':'Arkansas'},{'id':'B','name':'Otro'}],
            'equipment':[{'id':'A1','client_id':'A','name':'Uno'},{'id':'A2','client_id':'A','name':'Dos'},
                         {'id':'B1','client_id':'B','name':'Otro'}], 'reports':[], 'faults':[]})
        self.body = dict(id='LIST',mutation_id='CHANGE1',title='Cocina',client_id='A',assigned_user_id='T1',
                         target_date='2026-09-20',items=[{'id':'ITEM1','equipment_id':'A1','round':'1'},
                         {'id':'ITEM2','equipment_id':'A2','round':'2'}],expected_revision=0,status='active')

    def save(self, body=None, actor='T1', admin=False):
        return self.store.save_worklist(body or self.body, actor_id=actor, is_admin=admin)

    def test_retry_order_date_close_and_delete_do_not_touch_reports(self):
        first=self.save();self.assertEqual(first['revision'],1)
        self.assertEqual(self.save()['revision'],1)
        edit={**first,'expected_revision':1,'mutation_id':'CHANGE2','target_date':'2026-09-21','items':list(reversed(first['items']))}
        second=self.save(edit)
        self.assertEqual(second['items'][0]['id'],'ITEM2')
        self.assertEqual(second['items'][0]['added_at'],first['items'][1]['added_at'])
        with self.assertRaises(ValueError):self.save({**self.body,'mutation_id':'CONFLICT'})
        closed=self.save({**second,'expected_revision':2,'mutation_id':'CLOSE','status':'completed'})
        deleted=self.save({**closed,'expected_revision':3,'mutation_id':'DELETE','status':'deleted'})
        self.assertEqual(self.save({**closed,'expected_revision':3,'mutation_id':'DELETE','status':'deleted'}),deleted)
        snapshot=self.store.snapshot()
        self.assertEqual(len(snapshot['equipment']),3)
        self.assertEqual(snapshot['reports'],[])
        self.assertEqual(snapshot['expenses'],[])
        self.assertEqual(snapshot['tasks'],[])
        self.assertEqual(snapshot['worklists'][0]['status'],'deleted')
        with self.store.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM operations_worklist_changes').fetchone()[0],4)

    def test_assignment_and_authorization(self):
        self.save()
        with self.assertRaises(PermissionError):self.save(actor='OTHER')
        with self.assertRaises(PermissionError):self.save({**self.body,'id':'OTHERLIST','assigned_user_id':'OTHER'})
        self.store.create_invite({'token_hash':'token','name':'Dos','email':'two@example.com','role':'technician','expires_at':'2099-01-01T00:00:00+00:00'})
        user=self.store.accept_invite('token','hash')
        current=self.store.snapshot()['worklists'][0]
        assigned=self.save({**current,'mutation_id':'ASSIGN','expected_revision':1,'assigned_user_id':user['id']},actor='owner',admin=True)
        self.assertEqual(assigned['assigned_user_id'],user['id'])
        with self.assertRaises(PermissionError):self.save()
        changed=self.save({**assigned,'mutation_id':'TECH_EDIT','expected_revision':2,'title':'Orden propio'},actor=user['id'])
        self.assertEqual(changed['created_by'],'T1')
        self.assertEqual(changed['updated_by'],user['id'])

    def test_validation(self):
        for changes in [{'items':[]},{'target_date':'2026-02-30'},{'items':[{'equipment_id':'B1','round':'1'}]},
                        {'items':[{'equipment_id':'A1','round':'1'},{'equipment_id':'A1','round':'2'}]},
                        {'items':[{'equipment_id':'A1','round':'5'}]},{'title':''}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.save({**self.body,**changes})
        self.assertEqual(self.store.snapshot()['worklists'],[])

    def test_client_and_technician_scoping(self):
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_scope_operaciones_payload')
        for role,wanted in [('client',[]),('technician',['ONE']),('admin',['ONE','TWO'])]:
            ns={'_operations_role':lambda:role,'session':{'hsc_user_id':'T1','hsc_client_id':'A'}}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
            scoped=ns['_scope_operaciones_payload']({'worklists':[{'id':'ONE','assigned_user_id':'T1'},{'id':'TWO','assigned_user_id':'T2'}]})
            self.assertEqual([item['id'] for item in scoped['worklists']],wanted)

if __name__=='__main__':unittest.main()
