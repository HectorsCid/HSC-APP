import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from operaciones_store import OperationsStore


class TaskAssigneesTests(unittest.TestCase):
    def test_shared_task_and_legacy_assignment(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'tasks.db')
            ids=[]
            for n in range(2):
                store.create_invite({'token_hash':f'token{n}','name':f'Técnico {n}','email':f't{n}@example.com','role':'technician','expires_at':'2099-01-01T00:00:00+00:00'})
                ids.append(store.accept_invite(f'token{n}','hash')['id'])
            body={'id':'MISSION','title':'Revisar equipos','scheduled_date':'2026-09-14','assigned_user_ids':ids+ids[:1]}
            for _ in range(2):task=store.save_task(body)
            self.assertEqual(set(task['assigned_user_ids']),set(ids))
            self.assertEqual(len(store.snapshot()['tasks']),1)
            reminders=store.tasks_for_reminders('2026-09-14')
            self.assertEqual(set(reminders[0]['assigned_user_ids']),set(ids))
            self.assertEqual(store.complete_task('MISSION')['status'],'Terminada')
            self.assertEqual(store.tasks_for_reminders('2026-09-14'),[])
            legacy=store.save_task({'title':'Anterior','scheduled_date':'2026-09-14','assigned_user_id':ids[0]})
            self.assertEqual(legacy['assigned_user_ids'],ids[:1])
            with self.assertRaises(ValueError):store.save_task(dict(body,assigned_user_ids=['missing']))
            with self.assertRaises(ValueError):store.save_task(dict(body,assigned_user_ids=[]))

    def test_only_assigned_technicians_can_complete(self):
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_operaciones_complete_task');fn.decorator_list=[]
        for user,allowed in [('T1',True),('T2',True),('OTHER',False)]:
            store=Mock();store.snapshot.return_value={'tasks':[{'id':'M','assigned_user_id':'T1','assigned_user_ids':['T1','T2']}]}
            def abort(code):raise PermissionError(code)
            ns={'_operations_forbidden':lambda *args:None,'_operations_role':lambda:'technician','OPERACIONES_STORE':store,
                'session':{'hsc_user_id':user},'abort':abort,'_invalidate_operations_cache':Mock(),'jsonify':lambda v:v}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
            if allowed:ns['api_operaciones_complete_task']('M');store.complete_task.assert_called_once_with('M',actor_id=user)
            else:
                with self.assertRaises(PermissionError):ns['api_operaciones_complete_task']('M')
                store.complete_task.assert_not_called()


if __name__=='__main__':unittest.main()
