import os
import unittest
from unittest.mock import patch

from flask import Flask

import facturacion_bp as billing
import pagos_facturama_bp as payments


class FacturamaPaymentTests(unittest.TestCase):
    INVOICE_UUID = "c94c8af3-c774-4d4c-802e-781411934a6e"
    REP_UUID = "215cec43-7e57-44ac-9d63-b54bbc4745bd"

    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(payments.pagos_bp)
        self.client = self.app.test_client()
        self.env = patch.dict(os.environ, {
            "FACTURAMA_USER": "sandbox-user",
            "FACTURAMA_PASSWORD": "sandbox-password",
            "FACTURAMA_SANDBOX": "true",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def invoice(self):
        return {
            "Id": "invoice-id",
            "CfdiType": "ingreso",
            "Serie": "A",
            "Folio": "10",
            "PaymentMethod": "PPD - Pago en parcialidades o diferido",
            "Total": 116,
            "Receiver": {
                "Rfc": "URE180429TM6",
                "Name": "UNIVERSIDAD ROBOTICA ESPAÑOLA",
                "FiscalRegime": "601",
                "TaxZipCode": "86991",
            },
            "Taxes": [{"Name": "IVA", "Rate": 16, "Total": 16, "Type": "transferred"}],
            "Complement": {"TaxStamp": {"Uuid": self.INVOICE_UUID}},
        }

    def test_builds_cfdi_40_payment_complement(self):
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            cfdi, invoice, unpaid = payments._build_facturama_payment(self.invoice(), {
                "amount": 58, "previous_balance": 116, "partiality_number": 1,
                "payment_form": "03", "date": "2026-09-07T10:00:00",
            })
        self.assertEqual(cfdi["CfdiType"], "P")
        self.assertEqual(cfdi["Receiver"]["CfdiUse"], "CP01")
        self.assertNotIn("PaymentMethod", cfdi)
        related = cfdi["Complemento"]["Payments"][0]["RelatedDocuments"][0]
        self.assertEqual(related["PaymentMethod"], "PPD")
        self.assertEqual(related["PreviousBalanceAmount"], 116.0)
        self.assertEqual(related["AmountPaid"], 58.0)
        self.assertEqual(related["ImpSaldoInsoluto"], 58.0)
        self.assertEqual(related["Taxes"][0]["Rate"], .16)
        self.assertEqual(related["Taxes"][0]["Base"], 50.0)
        self.assertEqual(related["Taxes"][0]["Total"], 8.0)
        self.assertEqual(unpaid, 58)

    def test_rejects_non_ppd_invoice(self):
        invoice = self.invoice()
        invoice["PaymentMethod"] = "PUE - Pago en una sola exhibición"
        with self.assertRaisesRegex(ValueError, "no es PPD"):
            payments._build_facturama_payment(invoice, {"amount": 116})

    def test_info_reads_facturama_detail(self):
        with patch.object(payments, "_facturama_detail", return_value=self.invoice()):
            response = self.client.get("/api/pagos/info/invoice-id")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["payment_method"], "PPD")
        self.assertEqual(data["customer"]["tax_system"], "601")

    def test_create_posts_to_facturama_and_returns_download_links(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "GET":
                return self.invoice()
            return {
                "Id": "rep-id",
                "Complement": {"TaxStamp": {"Uuid": self.REP_UUID}},
            }

        with (
            patch.object(billing, "_fm_request", side_effect=fake_request),
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_read_index", return_value={}),
            patch.object(billing, "_write_index") as write_index,
            patch.object(billing, "_backup_facturama_cfdi", return_value={"ok": True}) as backup,
            patch.object(payments, "_local_customer_fiscal_data", return_value={}),
        ):
            response = self.client.post("/api/pagos/crear", json={
                "invoice_id": "invoice-id", "amount": 116, "payment_form": "03",
            })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["uuid"], self.REP_UUID)
        self.assertEqual(data["pdf_url"], "/api/invoices/rep-id/pdf")
        self.assertEqual(calls[-1][0:2], ("POST", "/3/cfdis"))
        write_index.assert_called_once()
        backup.assert_called_once_with(
            "rep-id", self.REP_UUID, "UNIVERSIDAD ROBOTICA ESPAÑOLA",
            "10", "Complemento-Pago-P1",
        )

    def test_info_reports_next_partiality_and_remaining_balance(self):
        index = {self.INVOICE_UUID: {"payments": [{
            "rep_id": "rep-one", "status": "active", "amount": 40,
            "remaining_balance": 76, "partiality_number": 1,
        }]}}
        with (
            patch.object(payments, "_facturama_detail", return_value=self.invoice()),
            patch.object(billing, "_read_index", return_value=index),
        ):
            response = self.client.get("/api/pagos/info/invoice-id")
        data = response.get_json()
        self.assertEqual(data["payment_count"], 1)
        self.assertEqual(data["paid_amount"], 40)
        self.assertEqual(data["remaining_balance"], 76)
        self.assertEqual(data["next_partiality_number"], 2)

    def test_create_second_partiality_uses_server_balance(self):
        index = {self.INVOICE_UUID: {"payments": [{
            "rep_id": "rep-one", "status": "active", "amount": 40,
            "remaining_balance": 76, "partiality_number": 1,
        }]}}
        posted = {}

        def fake_request(method, path, **kwargs):
            if method == "GET":
                return self.invoice()
            posted.update(kwargs["json_body"])
            return {"Id": "rep-two", "Complement": {"TaxStamp": {"Uuid": self.REP_UUID}}}

        with (
            patch.object(billing, "_fm_request", side_effect=fake_request),
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_read_index", return_value=index),
            patch.object(billing, "_write_index") as write_index,
            patch.object(billing, "_backup_facturama_cfdi", return_value={"ok": True}),
        ):
            response = self.client.post("/api/pagos/crear", json={
                "invoice_id": "invoice-id", "amount": 30,
                "previous_balance": 999, "partiality_number": 99,
            })
        self.assertEqual(response.status_code, 200)
        related = posted["Complemento"]["Payments"][0]["RelatedDocuments"][0]
        self.assertEqual(related["PreviousBalanceAmount"], 76.0)
        self.assertEqual(related["PartialityNumber"], 2)
        self.assertEqual(related["ImpSaldoInsoluto"], 46.0)
        self.assertEqual(related["Taxes"][0]["Base"], 25.86)
        self.assertEqual(related["Taxes"][0]["Total"], 4.14)
        saved = write_index.call_args.args[0][self.INVOICE_UUID]
        self.assertEqual(len(saved["payments"]), 2)

    def test_rejects_payment_above_remaining_balance(self):
        index = {self.INVOICE_UUID: {"payments": [{
            "rep_id": "rep-one", "status": "active", "amount": 100,
            "remaining_balance": 16,
        }]}}
        with (
            patch.object(billing, "_fm_request", return_value=self.invoice()),
            patch.object(billing, "_read_index", return_value=index),
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
        ):
            response = self.client.post("/api/pagos/crear", json={
                "invoice_id": "invoice-id", "amount": 20,
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("saldo anterior", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
