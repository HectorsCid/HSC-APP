import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode


class ReportAdminNoticeTests(unittest.TestCase):
    def test_finalization_notifies_only_owner_with_stable_key(self):
        tree = ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'api_operaciones_finalize_report')
        calls = [node for node in ast.walk(route) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'enqueue']
        self.assertEqual(len(calls), 1)
        enqueue = Mock()
        env = dict(enqueue=enqueue, session={'hsc_user_name':'Erick'},
                   report=dict(id='REP1',client_id='UVM',equipment_id='UVM12',round='2'),urlencode=urlencode)
        expression = compile(ast.Expression(calls[0]), '<report notice>', 'eval')
        eval(expression, env)
        eval(expression, env)
        first, second = enqueue.call_args_list
        self.assertEqual(first, second)
        self.assertEqual(first.kwargs['audience'], 'admin')
        self.assertEqual(first.kwargs['user_id'], 'owner')
        self.assertEqual(first.kwargs['category'], 'reportes')
        self.assertNotIn('client_id', first.kwargs)
        self.assertEqual(first.args[3], 'admin-report:REP1')
        self.assertIn('Erick · UVM · UVM12 · R2', first.args[1])
