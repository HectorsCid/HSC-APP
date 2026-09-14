"""Ejecuta la ruta de fallas sin cargar los módulos de PDF o Google."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class FaultNoticeTests(unittest.TestCase):
    def test_saved_partner_fault_creates_admin_notice_with_client_link(self):
        source = ast.parse(Path('app.py').read_text(encoding='utf-8'))
        names = {'_notify_operations_fault', 'api_operaciones_save_fault'}
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
        for node in functions:
            node.decorator_list = []
        fault = {'id':'F1','client_id':'B & C','equipment_id':'B1','description':'No enfría'}
        store = Mock(enabled=True)
        store.save_fault.return_value = fault
        push = Mock()
        namespace = {
            '_operations_forbidden':lambda *args:None,
            '_operations_permission_forbidden':lambda *args:None,
            '_operations_role':lambda:'client',
            'session':{'hsc_client_id':'B & C'},
            'request':SimpleNamespace(get_json=lambda **kw:dict(fault)),
            'jsonify':lambda value:value,
            'OPERACIONES_STORE':store,
            '_invalidate_operations_cache':Mock(), '_schedule_operations_sync':Mock(),
            'current_app':SimpleNamespace(logger=Mock()),
        }
        exec(compile(ast.Module(body=functions,type_ignores=[]),'app.py','exec'),namespace)
        with patch.dict('sys.modules',{'facturacion_bp':SimpleNamespace(_send_push_notifications=push)}):
            result,status = namespace['api_operaciones_save_fault']()
            self.assertEqual(status,201)
            self.assertTrue(result['ok'])
            self.assertEqual(push.call_args.kwargs['category'],'fallas')
            self.assertEqual(push.call_args.kwargs['url'],'/hsc-tecnico/?client=B+%26+C&fault=F1')
            push.side_effect = RuntimeError('Push no disponible')
            result,status = namespace['api_operaciones_save_fault']()
            self.assertEqual(status,201,'Un error de push no debe provocar un registro duplicado')


if __name__ == '__main__':
    unittest.main()
