import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as module
from operaciones_store import OperationsStore
from test_operaciones_store import _payload


class FaultQuoteAccess(unittest.TestCase):
    def test_link_and_visibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OperationsStore(local_path=Path(tmp) / 'ops.db')
            store.import_matrix_snapshot(_payload())
            store.save_fault(dict(id='F1', client_id='UVMQ', equipment_id='UVMQ1', description='Revisión'))
            path = Path(tmp) / 'quotes.json'
            path.write_text(json.dumps([dict(id='Q', folio='101', rfc='AAA'), dict(id='X', folio='102', rfc='BBB')]))
            with patch.object(module, 'OPERACIONES_STORE', store), patch.object(module, '_ruta_cotizaciones', return_value=path), patch.object(module, '_partner_documents_identity', return_value={'rfc':'AAA','names':set()}), patch.object(module, '_pdf_cotizacion_bytes', return_value=b'%PDF-test'), patch.object(module, '_app_access_password', return_value=''), patch.dict(module.app.config, TESTING=True):
                c = module.app.test_client()
                url = '/api/operaciones/faults/F1/quote'
                def login(role, uid, client=''):
                    with c.session_transaction() as sess:
                        sess.update(hsc_authenticated=True, hsc_role=role, hsc_user_id=uid, hsc_client_id=client)
                login('admin','owner')
                self.assertEqual(c.get(url+'?folio=101').json['quote']['id'], 'Q')
                self.assertEqual(c.post(url,json=dict(folio='102',quote_id='X')).status_code,404)
                self.assertEqual(c.post(url,json=dict(folio='101',quote_id='Q')).status_code,200)
                self.assertNotIn('quote_id',store.snapshot()['faults'][0])
                for role,uid,client,status in [('client','C','UVMQ',200),('technician','T','UVMQ',403),('client','B','OTHER',404),('admin','other','',403)]:
                    login(role,uid,client)
                    self.assertEqual(c.get(url).status_code,status)
                    self.assertEqual(c.get(url+'/pdf').status_code,status)
                    self.assertEqual(c.post(url,json={'unlink':True}).status_code,403)
                    self.assertEqual(c.get(url+'?folio=101').status_code,403)
                login('admin','owner')
                self.assertEqual(c.post(url,json={'unlink':True}).status_code,200)
                self.assertEqual(c.get(url+'/pdf').status_code,404)
