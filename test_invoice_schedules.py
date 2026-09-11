import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import Flask

import facturacion_bp as billing


TEMPLATE = {
    "id": "template-1",
    "name": "Mensual Alpha",
    "client_name": "ALPHA",
    "receiver": {
        "tax_id": "XAXX010101000",
        "name": "PUBLICO EN GENERAL",
        "zip": "76000",
        "tax_system": "616",
        "cfdi_use": "S01",
        "email": "cliente@example.com",
    },
    "conditions": {"payment_form": "03", "payment_method": "PUE", "serie": "HSC"},
    "retentions": {"isr": 0, "iva": 0},
    "items": [{"description": "Servicio mensual", "quantity": 1, "unit_price": 100, "tax_rate": 0.16}],
}


class InvoiceScheduleTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test")
        self.app.register_blueprint(billing.facturacion_bp)
        self.client = self.app.test_client()

    def test_next_monthly_run_clamps_day_to_end_of_month(self):
        after = datetime(2027, 2, 1, 8, 0, tzinfo=ZoneInfo("America/Mexico_City"))
        result = billing._next_monthly_run(31, "09:00", after=after)
        self.assertEqual((result.year, result.month, result.day, result.hour), (2027, 2, 28, 9))

    def test_create_confirmation_schedule(self):
        with patch.object(billing, "_read_invoice_templates", return_value=[TEMPLATE]), patch.object(
            billing, "_read_invoice_schedules", return_value=[]
        ), patch.object(billing, "_write_invoice_schedules", return_value=True):
            response = self.client.post("/api/invoice-schedules", json={
                "name": "Factura mensual Alpha", "template_id": "template-1",
                "day": 1, "time": "09:00", "mode": "confirm",
            })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["schedule"]["mode"], "confirm")

    def test_automatic_schedule_requires_explicit_acknowledgement(self):
        with self.app.app_context(), patch.object(billing, "_read_invoice_templates", return_value=[TEMPLATE]):
            with self.assertRaisesRegex(ValueError, "autorizas el timbrado automático"):
                billing._validated_invoice_schedule({
                    "name": "Factura automática", "template_id": "template-1",
                    "day": 1, "time": "09:00", "mode": "auto_stamp_email",
                    "recipient": "cliente@example.com",
                })

    def test_due_confirmation_is_only_notified_once(self):
        key = "scheduler-key-with-20-characters"
        due = datetime.now(ZoneInfo("America/Mexico_City")) - timedelta(minutes=5)
        schedules = [{
            "id": "schedule-1", "name": "Factura mensual Alpha", "template_id": "template-1",
            "day": due.day, "time": due.strftime("%H:%M"), "mode": "confirm", "active": True,
            "next_run": due.isoformat(), "last_run_key": "", "last_status": "pending",
        }]
        written = []
        with patch.dict(os.environ, {"HSC_SCHEDULER_KEY": key}, clear=False), patch.object(
            billing, "_read_invoice_schedules", return_value=schedules
        ), patch.object(billing, "_read_invoice_templates", return_value=[TEMPLATE]), patch.object(
            billing, "_write_invoice_schedules", side_effect=lambda rows: written.append(rows) or True
        ), patch.object(billing, "_send_push_notifications") as notify:
            first = self.client.post("/api/invoice-schedules/run", headers={"X-HSC-Scheduler-Key": key})
            second = self.client.post("/api/invoice-schedules/run", headers={"X-HSC-Scheduler-Key": key})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["processed"][0]["status"], "awaiting_confirmation")
        self.assertEqual(second.get_json()["processed"], [])
        self.assertEqual(notify.call_count, 1)
        self.assertTrue(written)


if __name__ == "__main__":
    unittest.main()
