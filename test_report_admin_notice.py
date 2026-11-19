import ast
from pathlib import Path
import tempfile
import unittest
from operaciones_store import OperationsStore
from task_notices import plan_task_notices


class ReportAdminNoticeTests(unittest.TestCase):
    def test_notice_commits_with_report_and_retry_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'reports.sqlite')
            store.import_matrix_snapshot({'clients':[{'id':'C','name':'Client'}],
                'equipment':[{'id':'E','client_id':'C','name':'Equipment'}], 'reports':[], 'faults':[]})
            draft=store.save_report_draft({'client_id':'C','equipment_id':'E','round':'1',
                'payload':{'inicio':'2026-11-18','fin':'2026-11-18','_draft_user_id':'erick'}})
            store.finalize_report(draft['id'])
            store.finalize_report(draft['id'])
            events=[event for event in store.pending_notice_events() if event['payload']['kind']=='report_completed']
        self.assertEqual(len(events),1)
        payload={**events[0]['payload'],'actor_name':'Erick'}
        first=plan_task_notices(events[0]['id'],payload)
        self.assertEqual(first,plan_task_notices(events[0]['id'],payload))
        self.assertEqual(first[0]['audience'],'admin')
        self.assertEqual(first[0]['user_id'],'owner')
        self.assertIn('Erick · C · E · R1',first[0]['body'])

    def test_finalize_route_does_not_deliver_notice_synchronously(self):
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        route=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_operaciones_finalize_report')
        names=[n.func.id for n in ast.walk(route) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertNotIn('enqueue',names)
        self.assertIn('wake_delivery',names)
