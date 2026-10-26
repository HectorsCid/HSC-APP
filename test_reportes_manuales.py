import os
import time
import io
import tempfile
from pathlib import Path
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

    def test_operations_report_prefills_client_equipment_and_technician(self):
        with (
            patch.object(reports, "_diag_clientes_catalogo", return_value=[]),
            self.client.session_transaction() as session,
        ):
            session["hsc_role"] = "technician"
            session["hsc_user_id"] = "test-technician"
            session["hsc_account_checked_at"] = time.time()
            session["hsc_user_name"] = "Carlos Técnico"
        with patch.object(reports, "_diag_clientes_catalogo", return_value=[]):
            response = self.client.get(
                "/reportes/diag/nuevo?tipo=refrigeracion&origin=operations&"
                "cliente=Cliente+Ocasional&direccion=Parque+1&equipo=Cámara+1&"
                "marca=Bohn&modelo=M1&no_serie=S1"
            )
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('value="Cliente Ocasional"', text)
        self.assertIn('value="Cámara 1"', text)
        self.assertIn('value="Carlos Técnico"', text)
        self.assertIn('name="return_to" value="/hsc-tecnico/"', text)

    def test_technician_cannot_open_admin_report_history(self):
        with self.client.session_transaction() as session:
            session["hsc_role"] = "technician"
            session["hsc_user_id"] = "test-technician"
            session["hsc_account_checked_at"] = time.time()
        response = self.client.get("/reportes")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/hsc-tecnico/"))

    def test_both_forms_use_the_unified_clients_catalog(self):
        catalog = {
            "CLIENTE NUEVO": {
                "direccion": "Parque Industrial 10",
                "atencion": ["Ing. Ana"],
                "contactos": [{"nombre": "Sr. Luis", "correo": "luis@example.com"}],
            }
        }
        old_sheet_client = {"ID-1": {"NombreCliente": "CLIENTE VIEJO DE APPSHEET"}}
        with (
            patch.dict(hsc.app.config, {"HSC_CLIENTES_PROVIDER": lambda: catalog}),
            patch.dict(reports._clientes_cache, {"by_id": old_sheet_client}, clear=False),
        ):
            general = self.client.get("/reportes/diag/nuevo?tipo=trabajo").get_data(as_text=True)
            refrigeration = self.client.get("/reportes/diag/nuevo?tipo=refrigeracion").get_data(as_text=True)
        for html in (general, refrigeration):
            self.assertIn("CLIENTE NUEVO", html)
            self.assertIn("Ing. Ana", html)
            self.assertIn("Sr. Luis", html)
            self.assertNotIn("CLIENTE VIEJO DE APPSHEET", html)

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

    def test_preview_redirects_to_get_and_back_keeps_data_and_six_photos(self):
        records={}
        def save(payload,**kwargs):
            records[payload['token']]={**payload,'folio':'RT-2026-0001','status':'draft'}
            return records[payload['token']]
        with tempfile.TemporaryDirectory() as folder:
            paths=[Path(folder)/name for name in ('uploads','pdfs','tmp')]
            for path in paths:path.mkdir()
            with (
                patch.object(reports,'_diag_paths',return_value=paths),
                patch.object(reports,'_diag_payload_temporal',side_effect=lambda token:records.get(token)),
                patch.object(reports,'_diag_store_payload',side_effect=save) as persist,
                patch.object(reports,'_optimize_photo_bytes',return_value=(b'photo','image/jpeg')) as compress,
                patch.object(reports,'_diag_render',return_value='<html>Vista previa</html>') as render,
                patch.object(reports,'_diag_clientes_catalogo',return_value=[]),
                patch.object(reports,'render_pdf_bytes') as pdf,
            ):
                response=self.client.post('/reportes/diag/prev',data={
                    'report_type':'trabajo','cliente':'Cliente conservado','fecha':'2026-10-20',
                    'part_desc[]':'Cambio de pieza','part_cant[]':'2','part_precio[]':'0',
                    'fotos':[(io.BytesIO(b'image'),f'foto{i}.jpg') for i in range(6)],
                },content_type='multipart/form-data')
                self.assertEqual(response.status_code,303)
                location=response.headers['Location']
                self.assertIn('/reportes/diag/prev/',location)
                render.assert_not_called()
                self.assertEqual(persist.call_count,1)
                self.assertEqual(compress.call_count,6)
                token=next(iter(records))
                self.assertEqual(len(records[token]['fotos']),6)
                # Back/refresh can retrieve the GET preview repeatedly without submitting again.
                for _ in range(2):self.assertEqual(self.client.get(location).status_code,200)
                self.assertEqual(persist.call_count,1)
                self.assertEqual(compress.call_count,6)
                form=self.client.get('/reportes/diag/nuevo?token='+token).get_data(as_text=True)
                self.assertIn('Cliente conservado',form)
                self.assertIn('Cambio de pieza',form)
                self.assertIn('6 foto(s) conservada(s)',form)
                pdf.assert_not_called()

    def test_preview_navigation_buttons_are_not_part_of_pdf(self):
        for kind in ('trabajo','refrigeracion'):
            with self.subTest(kind=kind),hsc.app.test_request_context('/'):
                payload={'token':'a'*32,'report_type':kind,'datos':{'cliente':'Cliente'},'partes':[],'fotos':[], 'return_to':'/hsc-tecnico/'}
                preview=reports._diag_render(payload,show_toolbar=True,embed_for_pdf=False)
                self.assertIn('← Volver a reportes',preview)
                self.assertIn('href="/inicio-app"',preview)
                self.assertIn('href="/hsc-tecnico/"',preview)
                printable=reports._diag_render(payload,show_toolbar=False,embed_for_pdf=True)
                self.assertNotIn('← Volver a reportes',printable)
                self.assertNotIn('href="/inicio-app"',printable)


if __name__ == "__main__":
    unittest.main()
