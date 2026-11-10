import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import Flask, jsonify
from operaciones_store import OperationsStore
import pagos_facturama_bp as payments


class QuickPaymentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Flask(__name__)
        self.app.register_blueprint(payments.pagos_bp)
        self.app.extensions['operations_store'] = OperationsStore(local_path=Path(tmp.name)/'queue.sqlite')
        self.client = self.app.test_client()
        self.fields = dict(invoice_id='inv', rfc='RFC', amount='120', payment_form='03',
                           email='pagos@example.com', expected_date=datetime.now(ZoneInfo('America/Mexico_City')).date().isoformat())

    def test_invalid_email_or_key_never_reaches_stamp(self):
        with (patch.object(payments,'smtp_config',return_value={'configured':True}),
              patch.object(payments,'authorized_to_send',side_effect=[False, True]),
              patch.object(payments,'_create_multiple_payment') as stamp):
            self.assertEqual(self.client.post('/api/pagos/crear-rapido',json=self.fields).status_code,403)
            self.assertEqual(self.client.post('/api/pagos/crear-rapido',json={**self.fields,'email':'incorrecto'}).status_code,400)
        stamp.assert_not_called()

    def test_uncertain_attempt_is_not_stamped_twice(self):
        with (patch.object(payments,'smtp_config',return_value={'configured':True}),
              patch.object(payments,'authorized_to_send',return_value=True),
              patch.object(payments,'_create_multiple_payment',side_effect=lambda *a,**kw:(jsonify(ok=False),502)) as stamp):
            self.assertEqual(self.client.post('/api/pagos/crear-rapido',json=self.fields).status_code,502)
            self.assertEqual(self.client.post('/api/pagos/crear-rapido',json=self.fields).status_code,409)
        stamp.assert_called_once()
        self.assertEqual(stamp.call_args.kwargs['quick_rfc'],'RFC')
        self.assertTrue(stamp.call_args.args[0]['date'].startswith(self.fields['expected_date']+'T'))

    def test_changed_balance_does_not_stamp(self):
        from test_pagos_facturama import FacturamaPaymentTests
        invoice = FacturamaPaymentTests().invoice()
        with (self.app.app_context(),patch.object(payments.billing,'_provider',return_value='facturama'),
              patch.object(payments.billing,'_read_index',return_value={}),
              patch.object(payments,'_facturama_detail',return_value=invoice),
              patch.object(payments.billing,'_fm_request') as provider):
            data, status = payments._create_multiple_payment({'documents':[{'invoice_id':'inv','amount':1}]},
                quick_rfc=payments._complete_customer(invoice)['customer']['tax_id'])
        self.assertEqual(status,400)
        self.assertIn('Cambió el saldo',data.get_json()['error'])
        provider.assert_not_called()
