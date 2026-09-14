import ast
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from operaciones_store import OperationsStore
from test_operaciones_store import _payload


class FaultAttentionTests(unittest.TestCase):
    def test_review_then_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'store.db')
            store.import_matrix_snapshot(_payload())
            reviewed=store.resolve_fault('F-UVM-1',status='Revisada',resolved_by='Técnico',resolution_notes='Requiere pieza')
            self.assertEqual(reviewed['status'],'Revisada')
            self.assertFalse(reviewed['resolved_at'])
            repaired=store.resolve_fault('F-UVM-1',status='Reparada',resolution_notes='Se cambió pieza')
            self.assertEqual(repaired['status'],'Reparada')
            self.assertTrue(repaired['resolved_at'])
            with self.assertRaises(ValueError):store.resolve_fault('F-UVM-1',status='Cualquier cosa')

    def test_each_status_notifies_assigned_client_and_excludes_actor(self):
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_operaciones_resolve_fault')
        fn.decorator_list=[]
        for status in ['Revisada','Reparada']:
            fault={'id':'F1','client_id':'A','equipment_id':'A1','description':'No enfría','status':status,'resolution_notes':'Trabajo documentado'}
            store=Mock(enabled=True);store.snapshot.return_value={'faults':[fault]};store.resolve_fault.return_value=fault
            ns={'_operations_forbidden':lambda *a:None,'OPERACIONES_STORE':store,'request':SimpleNamespace(get_json=lambda **k:{'status':status}),
                '_operations_role':lambda:'admin','session':{},'jsonify':lambda v:v,'_invalidate_operations_cache':Mock(),'_schedule_operations_sync':Mock(),'current_app':SimpleNamespace(logger=Mock())}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
            enqueue=Mock()
            with patch.dict('sys.modules',{'notification_delivery':SimpleNamespace(enqueue=enqueue)}):
                result=ns['api_operaciones_resolve_fault']('F1')
            self.assertTrue(result['ok'])
            partner=enqueue.call_args_list[-1]
            self.assertIn(status.lower(),partner.args[0])
            self.assertIn('Trabajo documentado',partner.args[1])
            self.assertEqual(partner.kwargs['client_id'],'A')
            self.assertEqual(partner.kwargs['audience'],'client')
            self.assertEqual(partner.kwargs['exclude_user_id'],'owner')


if __name__=='__main__':unittest.main()
