import copy
import unittest
from unittest.mock import patch
from test_pagos_facturama import FacturamaPaymentTests
import pagos_facturama_bp as payments
import facturacion_bp as billing


class MultiPaymentTests(FacturamaPaymentTests):
    def run_batch(self, documents, mutate=None):
        invoices = {}
        for n in (1, 2):
            inv = copy.deepcopy(self.invoice())
            inv['Id'] = f'invoice-{n}'
            inv['Folio'] = str(n)
            inv['Complement']['TaxStamp']['Uuid'] = f'c94c8af3-c774-4d4c-802e-781411934a6{n}'
            invoices[inv['Id']] = inv
        if mutate:
            mutate(invoices['invoice-2'])
        posted = []
        def provider(method, path, **kwargs):
            self.assertEqual(method, 'POST')
            posted.append(kwargs['json_body'])
            return {'Id':'rep-batch', 'Uuid':self.REP_UUID}
        with (patch.object(payments,'_facturama_detail',side_effect=lambda id:invoices[id]),
              patch.object(billing,'_read_index',return_value={}),
              patch.object(billing,'_write_index') as save,
              patch.object(billing,'_fm_request',side_effect=provider),
              patch.object(billing,'_facturama_issuer_locations',return_value=('42501','42501')),
              patch.object(billing,'_backup_facturama_cfdi',return_value={'ok':True}),
              patch.object(billing.notices,'publish')):
            response=self.client.post('/api/pagos/crear',json={'documents':documents,'payment_form':'03'})
        return response,posted,save

    def test_one_rep_for_two_invoices(self):
        response,posted,save=self.run_batch([{'invoice_id':'invoice-1','amount':58},{'invoice_id':'invoice-2','amount':116}])
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(posted),1)
        pago=posted[0]['Complemento']['Payments'][0]
        self.assertEqual(pago['Amount'],174)
        self.assertEqual(len(pago['RelatedDocuments']),2)
        self.assertEqual(pago['RelatedDocuments'][0]['ImpSaldoInsoluto'],58)
        self.assertEqual(len(save.call_args.args[0]),2)

    def test_rejects_duplicate_before_stamp(self):
        response,posted,_=self.run_batch([{'invoice_id':'invoice-1','amount':58}]*2)
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_rejects_different_customer_before_stamp(self):
        response,posted,_=self.run_batch([{'invoice_id':'invoice-1','amount':58},{'invoice_id':'invoice-2','amount':58}],lambda inv:inv['Receiver'].update(Rfc='AAA010101AAA'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_rejects_excess_amount_before_stamp(self):
        response,posted,_=self.run_batch([{'invoice_id':'invoice-1','amount':117}])
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_rejects_cancelled_invoice_before_stamp(self):
        response,posted,_=self.run_batch([{'invoice_id':'invoice-2','amount':58}],lambda inv:inv.update(Status='canceled'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)

    def test_rejects_foreign_currency(self):
        response,posted,_=self.run_batch([{'invoice_id':'invoice-2','amount':58}],lambda inv:inv.update(Currency='USD'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(posted)
