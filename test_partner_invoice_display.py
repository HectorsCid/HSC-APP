import ast
import copy
import tempfile
import unittest
from pathlib import Path
from flask import Flask, jsonify, request, session, current_app
from operaciones_store import OperationsStore
from partner_invoice_display import apply_display, read_display, save_display


class PartnerInvoiceDisplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=OperationsStore(local_path=Path(self.tmp.name)/'portal.db')
        self.invoice=dict(id='INV',paid=False,remaining_balance=116,cancellation_status='active',total=116)

    def save(self,status='paid',hidden=False,revision=0,client='A'):
        return save_display(self.store,client,'INV',dict(status=status,hidden=hidden,expected_revision=revision),'owner')

    def test_manual_status_does_not_mutate_fiscal_fields(self):
        original=copy.deepcopy(self.invoice)
        self.save()
        projected=apply_display([self.invoice],read_display(self.store,'A'))[0]
        self.assertEqual(projected['portal_payment_status'],'paid')
        for key,value in original.items():self.assertEqual(projected[key],value)
        self.assertEqual(self.invoice,original)
        self.assertEqual(self.store.snapshot()['reports'],[])

    def test_hide_and_restore_are_separate_and_company_scoped(self):
        self.save(hidden=True)
        rows=read_display(self.store,'A')
        self.assertEqual(apply_display([self.invoice],rows),[])
        self.assertTrue(apply_display([self.invoice],rows,is_admin=True)[0]['portal_hidden'])
        self.assertFalse(apply_display([self.invoice],read_display(self.store,'B'))[0]['portal_hidden'])
        self.save(status='automatic',hidden=False,revision=1)
        visible=apply_display([self.invoice],read_display(self.store,'A'))[0]
        self.assertEqual(visible['portal_payment_status'],'automatic')
        self.assertFalse(visible['paid'])

    def test_revision_conflicts_cannot_erase_other_admin_change(self):
        self.save()
        with self.assertRaises(ValueError):self.save('pending')
        self.assertEqual(read_display(self.store,'A')['INV']['status'],'paid')

    def test_invalid_values_are_rejected(self):
        for status,hidden,revision in [('canceled',False,0),('paid','false',0),('paid',False,True)]:
            with self.subTest(status=status,hidden=hidden,revision=revision),self.assertRaises(ValueError):
                save_display(self.store,'A','INV',dict(status=status,hidden=hidden,expected_revision=revision),'owner')

    def test_endpoint_admin_only_and_invoice_membership(self):
        app=Flask(__name__);app.secret_key='test'
        role=['admin']
        def denied(*roles):
            return None if role[0] in roles else (jsonify(ok=False),403)
        tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_operaciones_partner_invoice_display')
        ns=dict(app=app,request=request,jsonify=jsonify,session=session,current_app=current_app,OPERACIONES_STORE=self.store,
                _operations_forbidden=denied,_partner_documents_client=lambda:({'id':'A'},None),
                _partner_documents_payload=lambda client:{'invoices':[self.invoice]})
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'app.py','exec'),ns)
        client=app.test_client();body=dict(status='paid',hidden=True,expected_revision=0)
        for current in ['client','technician']:
            role[0]=current
            self.assertEqual(client.post('/api/operaciones/partner-documents/invoices/INV/display',json=body).status_code,403)
        role[0]='admin'
        self.assertEqual(client.post('/api/operaciones/partner-documents/invoices/OTHER/display',json=body).status_code,404)
        self.assertEqual(client.post('/api/operaciones/partner-documents/invoices/INV/display',json=body).status_code,200)
        self.assertTrue(read_display(self.store,'A')['INV']['hidden'])


if __name__=='__main__':unittest.main()
