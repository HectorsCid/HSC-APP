import os
import unittest
from unittest.mock import patch

import app as hsc
import reportes_bp as reports


class ManualReportsTests(unittest.TestCase):
    def setUp(self):
        hsc.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = hsc.app.test_client()
        self.env = patch.dict(os.environ, {"HSC_APP_PASSWORD": "test-password"}, clear=False)
        self.env.start()
        with self.client.session_transaction() as session:
            session["hsc_authenticated"] = True

    def tearDown(self):
        self.env.stop()

    def test_panel_offers_both_manual_report_types(self):
        with patch.object(reports, "_diag_read_records", return_value=[]):
            response = self.client.get("/reportes")
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Reporte de trabajo", text)
        self.assertIn("Reporte de refrigeración", text)

    def test_refrigeration_form_has_technical_fields(self):
        with patch.object(reports, "_diag_clientes_catalogo", return_value=[]):
            response = self.client.get("/reportes/diag/nuevo?tipo=refrigeracion")
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="report_type" value="refrigeracion"', text)
        self.assertIn('name="equipo"', text)
        self.assertIn('name="presion_cto1"', text)
        self.assertIn('name="obs_electronico"', text)

    def test_autosave_assigns_work_report_folio(self):
        with (
            patch.object(reports, "_diag_read_records", return_value=[]),
            patch.object(reports, "_diag_write_records", return_value=True),
        ):
            response = self.client.post("/reportes/diag/borrador", json={
                "report_type": "trabajo",
                "datos": {"cliente": "CLIENTE PRUEBA", "fecha": "2026-09-10"},
                "partes": [{"cantidad": "1", "descripcion": "Servicio"}],
            })
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["folio"], "RT-2026-0001")
        self.assertEqual(len(data["token"]), 32)

    def test_finalize_saves_pdf_and_marks_report_completed(self):
        token = "a" * 32
        payload = {
            "token": token, "folio": "RF-2026-0001", "report_type": "refrigeracion",
            "datos": {"cliente": "CLIENTE PRUEBA", "fecha": "2026-09-10"},
            "partes": [], "fotos": [], "total_partes": 0,
        }
        completed = {**payload, "status": "completed", "pdf_url": "https://drive/pdf"}
        with (
            patch.object(reports, "_diag_payload_temporal", return_value=payload),
            patch.object(reports, "_diag_render", return_value="<html></html>"),
            patch.object(reports, "render_pdf_bytes", return_value=b"%PDF-test"),
            patch.object(reports, "_ensure_folder", side_effect=["client-folder", "report-folder"]),
            patch.object(reports, "_upsert_pdf", return_value="pdf-id"),
            patch.object(reports, "_upsert_bytes", return_value="json-id"),
            patch.object(reports, "_file_web_link", return_value="https://drive/pdf"),
            patch.object(reports, "_folder_web_link", return_value="https://drive/folder"),
            patch.object(reports, "_diag_store_payload", return_value=completed) as store,
            patch.object(reports, "_log_pdf_historial"),
        ):
            response = self.client.get(f"/reportes/diag/pdf/{token}")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/reportes"))
        self.assertEqual(store.call_args.kwargs["status"], "completed")


if __name__ == "__main__":
    unittest.main()
