import tempfile
import unittest
from pathlib import Path
from operaciones_store import OperationsStore
from task_notices import plan_task_notices


class EquipmentCreationNotices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationsStore(local_path=Path(self.temp.name)/'equipment.sqlite')
        self.store.import_matrix_snapshot({'clients':[{'id':'C1','name':'Empresa Uno'}], 'equipment':[], 'reports':[], 'faults':[]})
        self.item = {'id':'C1_1','client_id':'C1','name':'Gerencia planta'}

    def test_new_technician_equipment_and_retry(self):
        self.store.save_equipment([self.item],actor_id='T1',actor_role='technician')
        events=self.store.pending_notice_events()
        self.assertEqual(len(events),1)
        payload=dict(events[0]['payload'],actor_name='Técnico Uno')
        notice=plan_task_notices(events[0]['id'],payload)[0]
        self.assertEqual(notice['audience'],'admin')
        self.assertEqual(notice['category'],'equipos')
        for text in ['Técnico Uno','Empresa Uno','C1_1','Gerencia planta']:
            self.assertIn(text,notice['body'])
        self.store.save_equipment([self.item],actor_id='T1',actor_role='technician')
        self.store.save_equipment([dict(self.item,name='Editado')],actor_id='T1',actor_role='technician')
        self.assertEqual(len(self.store.pending_notice_events()),1)

    def test_admin_and_failed_batch_do_not_notify(self):
        self.store.save_equipment([self.item],actor_id='owner',actor_role='admin')
        self.assertEqual(self.store.pending_notice_events(),[])
        with self.assertRaises(ValueError):
            self.store.save_equipment([dict(self.item,id='C1_2'),dict(self.item,id='C1_3',client_id='missing')],actor_id='T1',actor_role='technician')
        self.assertEqual(self.store.pending_notice_events(),[])
        self.assertEqual(len(self.store.snapshot()['equipment']),1)


if __name__=='__main__':
    unittest.main()
