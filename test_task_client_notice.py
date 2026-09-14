import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch


class TaskClientNoticeTests(unittest.TestCase):
    def test_notice_is_optional_and_scoped(self):
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_operaciones_save_task');fn.decorator_list=[]
        for enabled in (False,True):
            store=Mock();store.save_task.return_value={'id':'M','client_id':'A','title':'Visita','scheduled_date':'2026-09-18','scheduled_time':'10:30'}
            ns={'_operations_forbidden':lambda *a:None,'_operations_role':lambda:'admin','session':{},'OPERACIONES_STORE':store,'request':SimpleNamespace(get_json=lambda **k:{'notify_client':enabled}), 'jsonify':lambda v:v,'_invalidate_operations_cache':Mock()}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
            enqueue=Mock()
            with patch.dict('sys.modules',{'notification_delivery':SimpleNamespace(enqueue=enqueue)}):
                response,status=ns['api_operaciones_save_task']()
            self.assertEqual(status,201)
            self.assertEqual(enqueue.call_count,int(enabled))
            if enabled:
                self.assertEqual(enqueue.call_args.kwargs['client_id'],'A')
                self.assertEqual(enqueue.call_args.kwargs['audience'],'client')
                self.assertIn('10:30',enqueue.call_args.args[1])
                self.assertIn('open=calendar',enqueue.call_args.args[2])


if __name__=='__main__':unittest.main()
