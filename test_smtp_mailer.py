import os
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

import facturacion_bp as billing
import smtp_mailer


class SmtpMailerTests(unittest.TestCase):
    def test_sends_pdf_and_xml_through_ssl(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        env = {
            "SMTP_HOST": "mailc75.carrierzone.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "hectorsc@hscrefrigeracion.com",
            "SMTP_PASSWORD": "secret",
            "SMTP_FROM": "hectorsc@hscrefrigeracion.com",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(smtp_mailer.smtplib, "SMTP_SSL", return_value=smtp) as client:
            result = smtp_mailer.send_cfdi_email(
                recipient="cliente@example.com", subject="Factura 10", body="Adjuntos",
                pdf_bytes=b"%PDF", xml_bytes=b"<xml/>", folio="10",
            )
        client.assert_called_once()
        smtp.login.assert_called_once_with("hectorsc@hscrefrigeracion.com", "secret")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "cliente@example.com")
        self.assertEqual({part.get_filename() for part in message.iter_attachments()}, {"Factura-10.pdf", "Factura-10.xml"})
        self.assertEqual(result["from"], "hectorsc@hscrefrigeracion.com")

    def test_rejects_invalid_recipient(self):
        with patch.object(smtp_mailer, "smtp_config", return_value={"configured": True}):
            with self.assertRaisesRegex(ValueError, "correo electrónico válido"):
                smtp_mailer.send_cfdi_email(
                    recipient="no-es-correo", subject="x", body="x",
                    pdf_bytes=b"pdf", xml_bytes=b"xml", folio="1",
                )


class InvoiceEmailEndpointTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(billing.facturacion_bp)
        self.client = app.test_client()

    def test_wrong_send_key_does_not_download_or_send(self):
        with (
            patch.object(billing, "smtp_config", return_value={"configured": True}),
            patch.object(billing, "authorized_to_send", return_value=False),
            patch.object(billing, "_fm_request") as download,
        ):
            response = self.client.post("/api/invoices/abc/email", json={"email": "x@example.com", "send_key": "bad"})
        self.assertEqual(response.status_code, 403)
        download.assert_not_called()

    def test_authorized_send_uses_both_facturama_files(self):
        with (
            patch.object(billing, "smtp_config", return_value={"configured": True}),
            patch.object(billing, "authorized_to_send", return_value=True),
            patch.object(billing, "_provider", return_value="facturama"),
            patch.object(billing, "_fm_request", side_effect=[{"Content": "JVBERg=="}, {"Content": "PHhtbC8+"}]),
            patch.object(billing, "send_cfdi_email", return_value={"recipient": "x@example.com"}) as send,
        ):
            response = self.client.post("/api/invoices/abc/email", json={
                "email": "x@example.com", "send_key": "correct", "folio": "1587",
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(send.call_args.kwargs["pdf_bytes"], b"%PDF")
        self.assertEqual(send.call_args.kwargs["xml_bytes"], b"<xml/>")


if __name__ == "__main__":
    unittest.main()
