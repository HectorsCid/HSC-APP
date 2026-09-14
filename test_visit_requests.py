import ast
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from operaciones_store import OperationsStore
from test_operaciones_store import _payload


class VisitRequestTests(unittest.TestCase):
    def test_request_requires_admin_confirmation_and_keeps_single_task(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'visits.db');store.import_matrix_snapshot(_payload())
            role=['client'];body=[{'id':'request-1','title':'Revisión','scheduled_date':'2026-09-20','scheduled_time':'09:00','client_id':'OTHER','status':'Pendiente'}]
            def abort(code):raise PermissionError(code)
            ns={'OPERACIONES_STORE':store,'re':re,'session':{'hsc_user_id':'client-1','hsc_client_id':'UVMQ'},'abort':abort,
                'request':SimpleNamespace(get_json=lambda **k:body[0]),'_operations_forbidden':lambda *roles:None if role[0] in roles else ({'ok':False},403),
                '_invalidate_operations_cache':Mock(),'jsonify':lambda *a,**k:a[0] if a else k}
            tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
            fns=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'api_operaciones_request_visit','api_operaciones_confirm_visit'}]
            for fn in fns:fn.decorator_list=[]
            exec(compile(ast.Module(body=fns,type_ignores=[]),'app.py','exec'),ns)
            enqueue=Mock()
            with patch.dict('sys.modules',{'notification_delivery':SimpleNamespace(enqueue=enqueue)}):
                first,code=ns['api_operaciones_request_visit']();task=first['task']
                self.assertEqual(code,201);self.assertEqual(task['client_id'],'UVMQ');self.assertEqual(task['status'],'Solicitada')
                self.assertEqual(store.tasks_for_reminders('2026-09-20'),[])
                ns['api_operaciones_request_visit']()
                self.assertEqual(ns['api_operaciones_confirm_visit'](task['id'])[1],403)
                role[0]='admin';body[0]={'title':'Revisión confirmada','scheduled_date':'2026-09-21','scheduled_time':'11:00','assigned_user_ids':['owner'],'client_id':'OTHER'}
                confirmed=ns['api_operaciones_confirm_visit'](task['id'])['task']
                self.assertEqual(confirmed['status'],'Pendiente');self.assertEqual(confirmed['client_id'],'UVMQ')
                self.assertEqual(confirmed['scheduled_time'],'11:00');self.assertEqual(len(store.snapshot()['tasks']),1)
                self.assertEqual(enqueue.call_args.kwargs['client_id'],'UVMQ')
                self.assertEqual(enqueue.call_args.args[0],'Visita confirmada')
                ns['api_operaciones_confirm_visit'](task['id'])
                self.assertEqual(len(store.tasks_for_reminders('2026-09-21')),1)


if __name__=='__main__':unittest.main()
