import copy
import unittest
from unittest.mock import patch
from flask import Flask
import facturacion_bp as billing


class ManualScheduleTests(unittest.TestCase):
    def test_mark_only_pending_without_losing_invoice_or_next_date(self):
        app=Flask(__name__)
        app.register_blueprint(billing.facturacion_bp)
        original=dict(id='s',last_status='awaiting_confirmation',last_run_key='2026-11',next_run='2026-12-01',last_invoice_id='older',last_uuid='uuid',active=True)
        rows=[copy.deepcopy(original)]
        with patch.object(billing,'_read_invoice_schedules',return_value=rows),patch.object(billing,'_write_invoice_schedules',return_value=True) as save:
            response=app.test_client().post('/api/invoice-schedules/s/complete',json=dict(manual=True,expected_run_key='2026-11'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(rows[0]['last_status'],'completed_manual')
        for key in ('next_run','last_invoice_id','last_uuid','active','last_run_key'):
            self.assertEqual(rows[0][key],original[key])
        save.assert_called_once()
        for state,run in [('completed','2026-11'),('email_error','2026-11'),('awaiting_confirmation','2026-10')]:
            with patch.object(billing,'_read_invoice_schedules',return_value=[{**original,'last_status':state}]),patch.object(billing,'_write_invoice_schedules') as save:
                response=app.test_client().post('/api/invoice-schedules/s/complete',json=dict(manual=True,expected_run_key=run))
            self.assertEqual(response.status_code,409)
            save.assert_not_called()
