import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as cotizador


class QuoteEmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.quote_path = Path(self.tmp.name) / cotizador.COTIZACIONES_FILENAME
        self.quote_path.write_text(
            '[{"id":"1587","folio":"1587","cliente":"Bticino","view_url":"https://drive.google.com/file/d/file-id/view"}]',
            encoding="utf-8",
        )
        self.original_quote_path = cotizador._ruta_cotizaciones
        cotizador._ruta_cotizaciones = lambda: self.quote_path
        self.original_clients = cotizador.clientes_predefinidos
        cotizador.clientes_predefinidos = {"Bticino": {
            "rfc": "BTI010101AAA",
            "razon_social": "BTICINO DE MEXICO SA DE CV",
            "correo_compras": "compras@example.com",
            "correo_cuentas_pagar": "pagos@example.com",
        }}
        self.client = cotizador.app.test_client()

    def tearDown(self):
        cotizador._ruta_cotizaciones = self.original_quote_path
        cotizador.clientes_predefinidos = self.original_clients
        self.tmp.cleanup()

    def test_status_prefills_saved_client_email(self):
        with (
            patch.object(cotizador, "smtp_config", return_value={"configured": True}),
            patch.object(cotizador, "authorized_to_send", return_value=True),
        ):
            response = self.client.get("/api/cotizaciones/1587/email")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["email"], "compras@example.com")
        self.assertEqual(response.get_json()["correos"], {
            "compras": "compras@example.com", "cuentas_pagar": "pagos@example.com",
            "frecuentes": "", "historial": [],
        })
        self.assertTrue(response.get_json()["trusted"])

    def test_selected_invoice_contacts_can_match_by_rfc(self):
        with patch.object(cotizador, "authorized_to_send", return_value=True):
            response = self.client.post("/api/clientes/contactos-seleccionado", json={
                "nombre": "Nombre fiscal distinto", "rfc": "BTI010101AAA",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["correos"]["compras"], "compras@example.com")
        self.assertEqual(response.get_json()["correos"]["cuentas_pagar"], "pagos@example.com")

    def test_client_alias_typo_resolves_to_the_single_master_record(self):
        canonical, data = cotizador._resolver_cliente_catalogo("Biticino de México SA de CV")
        self.assertEqual(canonical, "Bticino")
        self.assertEqual(data["correo_compras"], "compras@example.com")
        with patch.object(cotizador, "authorized_to_send", return_value=True):
            response = self.client.post("/api/clientes/contactos-seleccionado", json={
                "nombre": "Biticino de México SA de CV", "rfc": "",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["correos"]["compras"], "compras@example.com")

    def test_selected_contacts_require_trusted_browser(self):
        with patch.object(cotizador, "authorized_to_send", return_value=False):
            response = self.client.post("/api/clientes/contactos-seleccionado", json={"rfc": "BTI010101AAA"})
        self.assertEqual(response.status_code, 403)

    def test_email_history_learns_only_after_authorized_request_and_can_delete(self):
        with (
            patch.object(cotizador, "authorized_to_send", return_value=True),
            patch.object(cotizador, "guardar_clientes") as save,
        ):
            learned = self.client.post("/api/clientes/correos-historial", json={
                "action": "record",
                "nombre": "BTICINO DE MEXICO SA DE CV",
                "rfc": "BTI010101AAA",
                "emails": "nuevo@example.com; compras@example.com",
            })
            removed = self.client.post("/api/clientes/correos-historial", json={
                "action": "delete",
                "rfc": "BTI010101AAA",
                "emails": "nuevo@example.com",
            })
        self.assertEqual(learned.status_code, 200)
        self.assertEqual({item["email"] for item in learned.get_json()["historial"]}, {
            "nuevo@example.com", "compras@example.com",
        })
        self.assertEqual([item["email"] for item in removed.get_json()["historial"]], ["compras@example.com"])
        self.assertEqual(save.call_count, 2)

    def test_send_includes_quote_and_optional_attachment(self):
        with (
            patch.object(cotizador, "smtp_config", return_value={"configured": True}),
            patch.object(cotizador, "authorized_to_send", return_value=True),
            patch.object(cotizador, "trusted_device_token", return_value="trusted-cookie"),
            patch.object(cotizador, "_pdf_cotizacion_bytes", return_value=b"%PDF"),
            patch.object(cotizador, "send_quote_email", return_value={
                "recipient": "uno@example.com, dos@example.com", "attachments": 2,
            }) as send,
        ):
            response = self.client.post("/api/cotizaciones/1587/email", data={
                "email": "uno@example.com; dos@example.com",
                "cc": "supervisor@example.com",
                "subject": "Cotización HSC No. 1587 – Bticino",
                "message": "Mensaje profesional",
                "send_key": "correct",
                "attachments": (io.BytesIO(b"orden"), "orden-compra.pdf"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(send.call_args.kwargs["recipient"], "uno@example.com; dos@example.com")
        self.assertEqual(send.call_args.kwargs["cc"], "supervisor@example.com")
        self.assertEqual(send.call_args.kwargs["pdf_bytes"], b"%PDF")
        self.assertEqual(send.call_args.kwargs["extra_attachments"][0]["filename"], "orden-compra.pdf")
        self.assertIn("hsc_mail_trusted=trusted-cookie", response.headers.get("Set-Cookie", ""))


if __name__ == "__main__":
    unittest.main()
