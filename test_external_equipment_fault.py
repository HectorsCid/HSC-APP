import tempfile
import unittest
from pathlib import Path
from operaciones_store import OperationsStore
from test_operaciones_store import _payload


class ExternalFaultTests(unittest.TestCase):
    def test_external_fault_keeps_company_without_creating_equipment(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationsStore(local_path=Path(folder)/'faults.db')
            store.import_matrix_snapshot(_payload())
            before=store.snapshot()
            body={'id':'OUTSIDE','client_id':'UVMQ','outside_policy':True,'equipment_name':'Refrigerador oficina','equipment_location':'Recepción','description':'No enfría'}
            fault=store.save_fault(body)
            self.assertEqual(fault['client_id'],'UVMQ')
            self.assertEqual(fault['equipment_id'],'')
            self.assertIn('Fuera de póliza',fault['description'])
            self.assertIn('Recepción',fault['description'])
            self.assertEqual(store.save_fault(body)['id'],fault['id'])
            self.assertEqual(store.snapshot()['equipment'],before['equipment'])
            self.assertEqual(store.snapshot()['reports'],before['reports'])
            store.save_fault_evidence('OUTSIDE',1,'photo.jpg')
            self.assertEqual(store.resolve_fault('OUTSIDE',status='Reparada')['photo_count'],1)
            for invalid in [dict(body,id='X',equipment_location=''),dict(body,id='X',equipment_id='UVMQ1'),dict(body,id='X',client_id='missing')]:
                with self.assertRaises(ValueError):store.save_fault(invalid)


if __name__=='__main__':unittest.main()
