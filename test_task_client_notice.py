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
            # Delivery now belongs to the transactional task outbox, tested in
            # test_task_creation_notifications, not a second inline push.
            self.assertEqual(store.save_task.call_args.args[0]['notify_client'],enabled)
            enqueue.assert_not_called()


if __name__=='__main__':unittest.main()
