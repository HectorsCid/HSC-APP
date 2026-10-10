"""Regresiones locales: ningún timbrado, cancelación ni archivo de producción."""
import copy
import unittest
from test_facturama import FacturamaIntegrationTests
from unittest.mock import patch
from flask import Flask
from test_pagos_facturama import FacturamaPaymentTests
import facturacion_bp as billing
import pagos_facturama_bp as payments


class ReplacementTests(unittest.TestCase):
    OLD_UUID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    NEW_UUID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.app.register_blueprint(billing.facturacion_bp)
        self.app.register_blueprint(payments.pagos_bp)
        self.client = self.app.test_client()
        self.invoice = FacturamaPaymentTests().invoice()
        self.uuid = FacturamaPaymentTests.INVOICE_UUID
        self.index = {self.uuid: {'payments': [dict(rep_id='old',rep_uuid=self.OLD_UUID,status='active',amount=58,remaining_balance=58,partiality_number=1)]}}
        self.patches = [patch.object(billing,'_read_index',side_effect=lambda:copy.deepcopy(self.index)),
                        patch.object(billing,'_write_index',side_effect=self.save),
                        patch.object(billing,'_CFDI_CANCELLATION_LOADED',True),
                        patch.dict(billing._CFDI_CANCELLATION_CACHE,{},clear=True),
                        patch.object(billing,'_persist_cancellation_state'),
                        patch.object(billing,'_facturama_issuer_locations',return_value=('76116','76116')),
                        patch.object(billing,'_backup_facturama_cfdi',return_value={'ok':True}),
                        patch.object(billing.notices,'publish')]
        for patcher in self.patches:
            patcher.start(); self.addCleanup(patcher.stop)

    def save(self, value):
        self.index = copy.deepcopy(value)

    def create(self, status='active', documents=None):
        original={'Id':'old','CfdiType':'P','Status':status,'Uuid':self.OLD_UUID}
        posted=[]
        def provider(method,path,**kwargs):
            self.assertEqual((method,path),('POST','/3/cfdis'))
            posted.append(kwargs['json_body'])
            return {'Id':'new','Uuid':self.NEW_UUID}
        with (patch.object(payments,'_facturama_detail',side_effect=lambda id:original if id=='old' else self.invoice),
              patch.object(payments,'_replacement_payment'),
              patch.object(billing,'_fm_request',side_effect=provider)):
            response=self.client.post('/api/pagos/crear',json={'replacement_id':'old','documents':documents if documents is not None else [{'invoice_id':'invoice-id','amount':60}], 'payment_form':'03'})
        return response,posted

    def test_replacement_relates_old_without_double_counting(self):
        response,posted=self.create()
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(posted[0]['Relations'],{'Type':'04','Cfdis':[{'Uuid':self.OLD_UUID}]})
        related=posted[0]['Complemento']['Payments'][0]['RelatedDocuments'][0]
        self.assertEqual(related['PreviousBalanceAmount'],116)
        self.assertEqual(related['PartialityNumber'],1)
        self.assertEqual(billing._payment_summary(self.index[self.uuid],116)['paid_amount'],58)
        self.assertEqual(len(self.index[self.uuid]['payments']),2)
        self.assertEqual(self.index[self.uuid]['payments'][1]['status'],'replacement_pending')

    def test_confirmed_cancel_promotes_replacement(self):
        self.create()
        with patch.object(billing,'_fm_request',return_value={'Status':'canceled'}):
            response=self.client.post('/api/invoices/old/cancel',json={'motive':'01','substitution_folio':self.NEW_UUID})
        self.assertEqual(response.status_code,200)
        self.assertEqual([p['rep_id'] for p in billing._payment_entries(self.index[self.uuid])],['new'])
        self.assertEqual(billing._payment_summary(self.index[self.uuid],116)['remaining_balance'],56)

    def test_requested_cancel_keeps_original(self):
        self.create()
        with patch.object(billing,'_fm_request',return_value={'Status':'requested'}):
            response=self.client.post('/api/invoices/old/cancel',json={'motive':'01','substitution_folio':self.NEW_UUID})
        self.assertEqual(response.get_json()['cancellation_status'],'pending')
        self.assertEqual(billing._payment_summary(self.index[self.uuid],116)['paid_amount'],58)

    def test_duplicate_replacement_never_stamps(self):
        self.create();response,posted=self.create()
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_pending_original_never_stamps(self):
        response,posted=self.create(status='requested')
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_wrong_cancel_reason_blocked_after_replacement(self):
        self.create()
        with patch.object(billing,'_fm_request') as provider:
            response=self.client.post('/api/invoices/old/cancel',json={'motive':'02'})
        self.assertEqual(response.status_code,409)
        provider.assert_not_called()

    def test_pending_replacement_blocks_normal_new_payment(self):
        self.create()
        with (patch.object(payments,'_facturama_detail',return_value=self.invoice),patch.object(billing,'_fm_request') as provider):
            response=self.client.post('/api/pagos/crear',json={'invoice_id':'invoice-id','amount':10,'payment_form':'03'})
        self.assertEqual(response.status_code,409)
        provider.assert_not_called()

    def test_latest_checked_across_every_invoice(self):
        self.index['another']={'payments':[dict(rep_id='old',status='active'),dict(rep_id='later',status='active')]}
        state=billing._rep_cancellation_state('old')
        self.assertFalse(state['is_latest'])

    def test_query_confirmed_cancellation_reconciles(self):
        self.create()
        with patch.object(billing,'_fm_request',return_value={'Id':'old','CfdiType':'P','Uuid':self.OLD_UUID,'Status':'canceled'}):
            response=self.client.get('/api/invoices/old/cancellation-status')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.get_json()['cancellation_status'],'canceled')
        self.assertEqual(billing._payment_summary(self.index[self.uuid],116)['paid_amount'],60)

    def test_cancel_without_status_does_not_release_balance(self):
        with patch.object(billing,'_fm_request',return_value={}):
            response=self.client.post('/api/invoices/old/cancel',json={'motive':'02'})
        self.assertEqual(response.get_json()['cancellation_status'],'pending')
        self.assertEqual(billing._payment_summary(self.index[self.uuid],116)['paid_amount'],58)

    def test_reconstruct_replacement_from_xml_without_double_counting(self):
        template=FacturamaIntegrationTests.SAMPLE_PAYMENT_XML.decode()
        original=template.replace(FacturamaIntegrationTests.VALID_UUID,self.uuid).replace(FacturamaPaymentTests.REP_UUID,self.OLD_UUID)
        replacement=original.replace(self.OLD_UUID,self.NEW_UUID).replace('<cfdi:Complemento>',f'<cfdi:CfdiRelacionados TipoRelacion="04"><cfdi:CfdiRelacionado UUID="{self.OLD_UUID}"/></cfdi:CfdiRelacionados><cfdi:Complemento>')
        self.index={}
        def provider(method,path,**kwargs):
            if path=='/Cfdi/xml/issued/new': return replacement.encode()
            if path=='/Cfdi/xml/issued/old': return original.encode()
            if path=='/api/cfdi': return [{'Id':'old','CfdiType':'P','Uuid':self.OLD_UUID,'Status':'active'}]
            raise AssertionError(path)
        with (patch.object(billing,'_fm_request',side_effect=provider),patch.object(billing,'_decode_facturama_file',side_effect=lambda value:value)):
            result=billing._sync_facturama_payment_rows([{'Id':'new','CfdiType':'P','Uuid':self.NEW_UUID,'Status':'active'}])
        self.assertEqual(result['errors'],0)
        self.assertEqual([p['rep_id'] for p in billing._payment_entries(self.index[self.uuid])],['old'])
        self.assertEqual(len(self.index[self.uuid]['payments']),2)
        self.assertEqual(billing._payment_summary(self.index[self.uuid],116)['paid_amount'],60)

    def test_new_external_payment_preserves_pending_replacement(self):
        self.create()
        template=FacturamaIntegrationTests.SAMPLE_PAYMENT_XML.decode().replace(FacturamaIntegrationTests.VALID_UUID,self.uuid).replace('NumParcialidad="1"','NumParcialidad="2"')
        with (patch.object(billing,'_fm_request',return_value=template.encode()),patch.object(billing,'_decode_facturama_file',side_effect=lambda value:value)):
            billing._sync_facturama_payment_rows([{'Id':'external','CfdiType':'P','Uuid':'cccccccc-cccc-4ccc-8ccc-cccccccccccc','Status':'active'}])
        self.assertTrue(any(p.get('status')=='replacement_pending' for p in self.index[self.uuid]['payments']))


if __name__=='__main__':
    unittest.main()
