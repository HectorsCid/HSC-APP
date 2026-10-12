"""Sustitución de tres REP: sólo fixtures y proveedor simulado."""
import copy
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch
from test_rep_replacement import ReplacementTests
import facturacion_bp as billing
import pagos_facturama_bp as payments


class ConsolidationTests(unittest.TestCase):
    OLD_UUID = ReplacementTests.OLD_UUID
    NEW_UUID = ReplacementTests.NEW_UUID
    save = ReplacementTests.save
    def setUp(self):
        ReplacementTests.setUp(self)
        self.invoices={}; self.old_reps={}; self.index={}
        for n in (1,2,3):
            invoice=copy.deepcopy(self.invoice)
            invoice.update(Id=f'invoice-{n}',Folio=str(n))
            uuid=f'c94c8af3-c774-4d4c-802e-781411934a6{n}'
            old_uuid=f'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa{n}'
            invoice['Complement']['TaxStamp']['Uuid']=uuid
            self.invoices[invoice['Id']]=invoice
            self.old_reps[f'old-{n}']={'Id':f'old-{n}','Uuid':old_uuid,'CfdiType':'P','Status':'active','Receiver':invoice['Receiver']}
            self.index[uuid]={'payments':[dict(rep_id=f'old-{n}',rep_uuid=old_uuid,status='active',amount=116,remaining_balance=0,partiality_number=1)]}

    def run_group(self, confirmation=True, mutate=None):
        posted=[]
        if mutate: mutate()
        def provider(method,path,**kwargs):
            self.assertEqual((method,path),('POST','/3/cfdis'))
            posted.append(kwargs['json_body'])
            return {'Id':'new','Uuid':self.NEW_UUID}
        def payment(rep_id):
            return ET.Element('Pago',FechaPago='2026-09-17T12:00:00',FormaDePagoP='03',MonedaP='MXN')
        with (patch.object(payments,'_facturama_detail',side_effect=lambda id:(self.old_reps | self.invoices)[id]),
              patch.object(payments,'_replacement_payment',side_effect=payment),patch.object(billing,'_fm_request',side_effect=provider)):
            response=self.client.post('/api/pagos/crear',json={'replacement_ids':list(self.old_reps),'confirmed_same_payment':confirmation,
                'documents':[{'invoice_id':id,'amount':116} for id in self.invoices],'payment_form':'03'})
        return response,posted

    def test_three_paid_invoices_create_one_related_rep(self):
        response,posted=self.run_group()
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(len(posted),1)
        self.assertEqual(len(posted[0]['Relations']['Cfdis']),3)
        self.assertEqual(posted[0]['Complemento']['Payments'][0]['Amount'],348)
        self.assertEqual(len(response.get_json()['replaces']),3)
        for record in self.index.values():
            self.assertEqual(billing._payment_summary(record,116)['paid_amount'],116)
            self.assertEqual(len(record['payments']),2)

    def test_individual_cancellations_never_double_count(self):
        self.run_group()
        for id in self.old_reps:
            with patch.object(billing,'_fm_request',return_value={'Status':'canceled'}):
                response=self.client.post(f'/api/invoices/{id}/cancel',json={'motive':'01','substitution_folio':self.NEW_UUID})
            self.assertEqual(response.status_code,200)
            for record in self.index.values():
                self.assertEqual(billing._payment_summary(record,116)['paid_amount'],116)
        for record in self.index.values():
            self.assertEqual([p['rep_id'] for p in billing._payment_entries(record)],['new'])

    def test_requires_same_payment_confirmation(self):
        response,posted=self.run_group(confirmation=False)
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_duplicate_source_is_rejected(self):
        with patch.object(billing,'_fm_request') as provider:
            response=self.client.post('/api/pagos/crear',json={'replacement_ids':['old-1','old-1'],'documents':[{'invoice_id':'invoice-1','amount':116}]})
        self.assertEqual(response.status_code,400)
        provider.assert_not_called()

    def test_different_receiver_rejected_before_stamp(self):
        response,posted=self.run_group(mutate=lambda:self.old_reps['old-3'].update(Receiver={'Rfc':'XXX010101XXX'}))
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_different_payment_dates_rejected(self):
        with (patch.object(payments,'_facturama_detail',side_effect=lambda id:self.old_reps[id]),
              patch.object(payments,'_replacement_payment',side_effect=lambda id:ET.Element('Pago',FechaPago='2026-09-18' if id=='old-3' else '2026-09-17',FormaDePagoP='03',MonedaP='MXN'))):
            with self.assertRaisesRegex(ValueError,'distintos'):
                payments._replacement_sources(list(self.old_reps),self.index)

    def test_covered_invoices_are_returned_in_context(self):
        def provider(method,path,**kwargs):
            uuid=kwargs['params']['uuid']
            return [inv for inv in self.invoices.values() if payments._facturama_uuid(inv)==uuid]
        with (patch.object(payments,'_facturama_detail',side_effect=lambda id:(self.old_reps | self.invoices)[id]),
              patch.object(payments,'_replacement_payment',return_value=ET.Element('Pago',FechaPago='2026-09-17T12:00:00',FormaDePagoP='03',MonedaP='MXN')),
              patch.object(billing,'_sync_facturama_payment_rows'),patch.object(billing,'_fm_request',side_effect=provider)):
            response=self.client.get('/api/pagos/replacement-context?id=old-1&id=old-2&id=old-3')
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(len(response.get_json()['documents']),3)
        self.assertEqual(len(response.get_json()['originals']),3)
        self.assertTrue(all(doc['invoice']['remaining_balance']==116 for doc in response.get_json()['documents']))

    def test_no_other_payment_while_any_replacement_pending(self):
        self.run_group()
        with (patch.object(payments,'_facturama_detail',return_value=self.invoices['invoice-2']),patch.object(billing,'_fm_request') as provider):
            response=self.client.post('/api/pagos/crear',json={'invoice_id':'invoice-2','amount':10,'payment_form':'03'})
        self.assertEqual(response.status_code,409)
        provider.assert_not_called()

    def test_rebuild_from_xml_preserves_all_three_links(self):
        def xml(rep_uuid,uuids,links=()):
            relations='<cfdi:CfdiRelacionados TipoRelacion="04">'+''.join(f'<cfdi:CfdiRelacionado UUID="{uuid}"/>' for uuid in links)+'</cfdi:CfdiRelacionados>' if links else ''
            docs=''.join(f'<p:DoctoRelacionado IdDocumento="{uuid}" NumParcialidad="1" ImpPagado="116.00" ImpSaldoAnt="116.00" ImpSaldoInsoluto="0.00"/>' for uuid in uuids)
            return f'<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:p="http://www.sat.gob.mx/Pagos20" xmlns:t="http://www.sat.gob.mx/TimbreFiscalDigital" TipoDeComprobante="P">{relations}<cfdi:Complemento><p:Pagos><p:Pago FechaPago="2026-09-17T12:00:00" FormaDePagoP="03" MonedaP="MXN">{docs}</p:Pago></p:Pagos><t:TimbreFiscalDigital UUID="{rep_uuid}"/></cfdi:Complemento></cfdi:Comprobante>'.encode()
        invoice_uuids=list(self.index)
        old_xml={id:xml(rep['Uuid'],[invoice_uuids[n]]) for n,(id,rep) in enumerate(self.old_reps.items())}
        new_xml=xml(self.NEW_UUID,invoice_uuids,[rep['Uuid'] for rep in self.old_reps.values()])
        self.index={}
        def provider(method,path,**kwargs):
            if path=='/api/cfdi':return [rep for rep in self.old_reps.values() if rep['Uuid']==kwargs['params']['uuid']]
            id=path.rsplit('/',1)[-1]
            return new_xml if id=='new' else old_xml[id]
        with (patch.object(billing,'_fm_request',side_effect=provider),patch.object(billing,'_decode_facturama_file',side_effect=lambda value:value)):
            result=billing._sync_facturama_payment_rows([{'Id':'new','Uuid':self.NEW_UUID,'CfdiType':'P','Status':'active'}])
        self.assertEqual(result['errors'],0)
        self.assertEqual(len(self.index),3)
        for uuid,record in self.index.items():
            self.assertEqual(len(record['payments']),2)
            self.assertEqual(billing._payment_summary(record,116)['paid_amount'],116)
        self.index.pop(invoice_uuids[-1])
        with (patch.object(billing,'_fm_request',side_effect=provider),patch.object(billing,'_decode_facturama_file',side_effect=lambda value:value)):
            recovered=billing._sync_facturama_payment_rows([{'Id':'new','Uuid':self.NEW_UUID,'CfdiType':'P','Status':'active'}])
        self.assertEqual(recovered['errors'],0)
        self.assertEqual(len(self.index),3)
        for id in self.old_reps:
            billing._drop_confirmed_rep(self.index,id)
            self.assertEqual(sum(billing._payment_summary(record,116)['paid_amount'] for record in self.index.values()),348)
