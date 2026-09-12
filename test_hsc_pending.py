import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from flask import Flask, jsonify, redirect
import facturacion_bp as billing
import reportes_bp as reports
import notification_center as notices
import scheduler_runner


class NotificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.remote = {}
        for mocked in (
            patch.object(notices, "PATH", Path(temp.name) / "notices.json"),
            patch.object(notices, "load_json_file", side_effect=lambda *a, **k: copy.deepcopy(self.remote)),
            patch.object(notices, "backup_json_file", side_effect=self.backup),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def backup(self, name, state):
        self.remote = copy.deepcopy(state)
        return {"ok": True}

    def test_duplicate_month_does_not_reopen_read_notice(self):
        self.assertTrue(notices.publish("Factura", "Septiembre", key="invoice-sept"))
        notice = notices.snapshot()["items"][0]
        self.assertEqual(notices.mark_read(notice["id"]), (True, True))
        self.assertFalse(notices.publish("Factura", "Septiembre", key="invoice-sept"))
        self.assertEqual(notices.snapshot()["unread"], 0)
        self.assertTrue(notices.publish("Factura", "Octubre", key="invoice-oct"))
        self.assertEqual(notices.snapshot()["unread"], 1)

    def test_restores_read_state_and_job_after_restart(self):
        notices.publish("Pago", "Registrado", key="rep-1")
        notices.mark_read(notices.snapshot()["items"][0]["id"])
        notices.record_job("facturas_programadas", "ok", processed=0)
        notices.PATH.unlink()  # Archivo aislado de esta prueba, no datos de HSC.
        state = notices.snapshot()
        self.assertEqual(state["unread"], 0)
        self.assertEqual(state["jobs"]["facturas_programadas"]["status"], "ok")

    def test_backup_failure_keeps_notice_locally(self):
        with patch.object(notices, "backup_json_file", side_effect=RuntimeError("offline")):
            self.assertTrue(notices.publish("Respaldo", "Pendiente"))
        self.assertEqual(notices.snapshot()["unread"], 1)

    def test_external_action_url_is_rejected(self):
        notices.publish("Prueba", "Texto", url="//example.com")
        self.assertEqual(notices.snapshot()["items"][0]["url"], "/inicio-app")

    def test_api_read_state(self):
        app = Flask(__name__)
        app.register_blueprint(billing.facturacion_bp)
        notices.publish("Pago", "Registrado", category="pagos")
        with patch.object(billing, "_read_invoice_schedules", return_value=[]):
            response = app.test_client().get("/api/notifications")
        self.assertEqual(response.status_code, 200)
        item_id = response.get_json()["items"][0]["id"]
        self.assertEqual(app.test_client().post(f"/api/notifications/{item_id}/read").status_code, 200)
        self.assertEqual(notices.snapshot()["unread"], 0)
        self.assertEqual(app.test_client().post("/api/notifications/unknown/read").status_code, 404)


class ReportAndReceivedTests(unittest.TestCase):
    def test_internal_report_does_not_go_through_browser_login(self):
        app = Flask(__name__)
        app.register_blueprint(reports.reportes_bp)
        app.before_request(lambda: redirect("/acceso"))
        with app.test_request_context(), patch.object(reports, "reportes_pdf_json", return_value=jsonify(ok=True)) as generate:
            self.assertTrue(reports._generate_and_store_report_pdf("UDA38_R 1")["ok"])
            generate.assert_called_once_with("UDA38_R 1")
        response = app.test_client().post("/reportes/pdf_json/UDA38_R%201", headers={"X-HSC-Auto-PDF": "1"})
        self.assertEqual(response.status_code, 302)

    def test_internal_report_propagates_generation_failure(self):
        app = Flask(__name__)
        app.register_blueprint(reports.reportes_bp)
        with app.test_request_context(), patch.object(reports, "reportes_pdf_json", return_value=(jsonify(ok=False, error="pdf_busy"), 409)):
            with self.assertRaisesRegex(RuntimeError, "pdf_busy"):
                reports._generate_and_store_report_pdf("UDA38_R 1")

    def test_drafts_do_not_displace_completed_reports(self):
        rows = {"valueRanges": [
            {"values": [["done"], ["draft1"], ["draft2"], ["draft3"]]},
            {"values": [["TRUE"], ["FALSE"], ["FALSE"], ["FALSE"]]},
        ]}
        with patch.object(reports, "_get_headers", return_value=["ID_Reporte", "Realizado"]), patch.object(reports, "_values_batch_get", return_value=rows):
            self.assertEqual(reports._recent_report_ids(1), ["done"])

    def test_received_summary_subtracts_credit_notes_and_separates_currencies(self):
        base = {"status": "active", "type": "I", "currency": "MXN", "subtotal": 100, "discount": 10, "total": 104.4}
        rows = [base, {**base, "type": "E", "subtotal": 20, "discount": 0, "total": 23.2},
                {**base, "type": "P", "total": 1000}, {**base, "status": "cancelado"},
                {**base, "currency": "USD"}]
        totals = billing._received_summary(rows)
        self.assertEqual(totals["MXN"], {"invoices": 1, "credit_notes": 1, "subtotal": 70, "total": 81.2})
        self.assertEqual(totals["USD"]["total"], 104.4)

    def test_received_pagination_does_not_stop_on_small_page(self):
        app = Flask(__name__)
        app.register_blueprint(billing.facturacion_bp)
        one = {"Id": "one", "Uuid": "2c4c8e4a-b337-4bf6-ade2-cd972f8a93bb", "CfdiType": "I", "Date": "2026-09-01", "Total": 100}
        two = {**one, "Id": "two", "Uuid": "215cec43-7e57-44ac-9d63-b54bbc4745bd"}
        with patch.object(billing, "_provider", return_value="facturama"), patch.object(billing, "_fm_request", side_effect=[[one], [two], []]) as api:
            result = app.test_client().get("/api/facturas/received?month=2026-09").get_json()
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(api.call_count, 3)
        self.assertFalse(result["truncated"])

    def test_cron_fails_visibly_when_http_200_contains_failed_invoice(self):
        response = Mock(status_code=200, text="safe test result")
        response.json.return_value = {"ok": True, "processed": [{"status": "error"}]}
        with patch.dict("os.environ", {"HSC_SCHEDULER_KEY": "test-only"}), patch.object(scheduler_runner.requests, "post", return_value=response):
            with self.assertRaises(RuntimeError):
                scheduler_runner.main()

    def test_email_failure_preserves_already_stamped_invoice_id(self):
        from test_invoice_schedules import TEMPLATE
        app = Flask(__name__)
        schedule = {"id": "monthly", "mode": "auto_stamp_email"}
        with app.app_context(), patch.object(billing, "facturar", return_value=jsonify(ok=True, invoice_id="issued-123", uuid="uuid-123")), patch.object(billing, "_fm_request", side_effect=RuntimeError("download unavailable")):
            with self.assertRaises(RuntimeError):
                billing._stamp_scheduled_invoice(schedule, TEMPLATE, "2026-09")
        self.assertEqual(schedule["last_invoice_id"], "issued-123")
        self.assertEqual(schedule["last_uuid"], "uuid-123")


if __name__ == "__main__":
    unittest.main()
