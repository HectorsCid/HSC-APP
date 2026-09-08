import base64
import os
import unittest
from unittest.mock import patch

from flask import Flask

import facturacion_bp as billing


class FacturamaIntegrationTests(unittest.TestCase):
    def setUp(self):
        billing._STAMP_RESULTS.clear()
        self.app = Flask(__name__)
        self.app.register_blueprint(billing.facturacion_bp)
        self.client = self.app.test_client()
        self.env = patch.dict(os.environ, {
            "FACTURAMA_USER": "sandbox-user",
            "FACTURAMA_PASSWORD": "sandbox-password",
            "FACTURAMA_SANDBOX": "true",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    @staticmethod
    def payload(request_id="test-1"):
        return {
            "request_id": request_id,
            "receptor": {
                "rfc": "URE180429TM6",
                "nombre": "UNIVERSIDAD ROBOTICA ESPAÑOLA",
                "cp": "86991",
                "regimen_fiscal": "601",
                "uso_cfdi": "G03",
            },
            "forma_pago": "03",
            "metodo_pago": "PUE",
            "items": [{
                "descripcion": "Servicio de prueba",
                "cantidad": 2,
                "precio_unitario": 100,
                "clave_prod_serv": "85121600",
                "clave_unidad": "E48",
                "tasa_iva": 0.16,
            }],
        }

    def test_builds_cfdi_40_totals_and_receiver(self):
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            cfdi = billing._build_facturama_cfdi(self.payload())
        self.assertEqual(cfdi["ExpeditionPlace"], "42501")
        self.assertEqual(cfdi["Receiver"]["TaxZipCode"], "86991")
        self.assertEqual(cfdi["Items"][0]["Subtotal"], 200.0)
        self.assertEqual(cfdi["Items"][0]["Taxes"][0]["Total"], 32.0)
        self.assertEqual(cfdi["Items"][0]["Total"], 232.0)
        self.assertEqual(cfdi["Items"][0]["TaxObject"], "02")
        self.assertNotIn("Date", cfdi)
        self.assertNotIn("Serie", cfdi)

    def test_ppd_forces_payment_form_99(self):
        payload = self.payload()
        payload["metodo_pago"] = "PPD"
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            cfdi = billing._build_facturama_cfdi(payload)
        self.assertEqual(cfdi["PaymentForm"], "99")

    def test_issuer_uses_configured_branch_zip(self):
        old_cache = billing._FM_PROFILE_CACHE
        old_branches = billing._FM_BRANCH_CACHE
        billing._FM_PROFILE_CACHE = {
            "Rfc": "EKU9003173C9",
            "FiscalRegime": "601",
            "TaxAddress": {},
            "Csd": {"Certificate": "test.cer", "PrivateKey": "test.key"},
        }
        billing._FM_BRANCH_CACHE = [{
            "IsDefault": True,
            "Address": {"ZipCode": "42501"},
        }]
        try:
            expedition, tax_zip = billing._facturama_issuer_locations()
        finally:
            billing._FM_PROFILE_CACHE = old_cache
            billing._FM_BRANCH_CACHE = old_branches
        self.assertEqual((expedition, tax_zip), ("42501", "42501"))

    def test_official_sandbox_issuer_rejects_wrong_regime(self):
        old_profile = billing._FM_PROFILE_CACHE
        old_branches = billing._FM_BRANCH_CACHE
        billing._FM_PROFILE_CACHE = {
            "Rfc": "EKU9003173C9",
            "FiscalRegime": "605",
            "TaxAddress": {},
        }
        billing._FM_BRANCH_CACHE = [{"Address": {"ZipCode": "42501"}}]
        try:
            with self.assertRaisesRegex(ValueError, "requiere régimen fiscal 601"):
                billing._facturama_issuer_locations()
        finally:
            billing._FM_PROFILE_CACHE = old_profile
            billing._FM_BRANCH_CACHE = old_branches

    def test_stamp_is_idempotent_inside_running_service(self):
        answer = {"Id": "sandbox-id", "Uuid": "2c4c8e4a-b337-4bf6-ade2-cd972f8a93bb", "Status": "active", "Total": 232}
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_fm_request", return_value=answer) as request_mock,
        ):
            first = self.client.post("/api/facturar", json=self.payload("same-request"))
            second = self.client.post("/api/facturar", json=self.payload("same-request"))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(request_mock.call_count, 1)
        self.assertTrue(second.get_json()["duplicate_prevented"])

    def test_stamp_extracts_nested_facturama_uuid(self):
        expected_uuid = "2c4c8e4a-b337-4bf6-ade2-cd972f8a93bb"
        answer = {
            "Id": "sandbox-id",
            "Complement": {"TaxStamp": {"Uuid": expected_uuid, "CfdiSign": "secret-signature"}},
            "Status": "active",
            "Total": 1.16,
        }
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_fm_request", return_value=answer),
        ):
            response = self.client.post("/api/facturar", json=self.payload("nested-request"))
        data = response.get_json()
        self.assertEqual(data["uuid"], expected_uuid)
        self.assertNotIn("secret-signature", str(data))

    def test_stamp_without_valid_uuid_is_rejected_and_not_cached(self):
        answer = {"Id": "invalid-attempt", "Status": "invalid", "Total": 232}
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_fm_request", return_value=answer),
        ):
            response = self.client.post("/api/facturar", json=self.payload("invalid-request"))
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["ok"])
        self.assertNotIn("invalid-request", billing._STAMP_RESULTS)

    def test_invoice_list_hides_failed_attempts_and_reads_flat_receiver(self):
        valid_uuid = "2c4c8e4a-b337-4bf6-ade2-cd972f8a93bb"
        rows = [
            {"Id": "ok-1", "Uuid": valid_uuid, "TaxName": "CLIENTE SAT", "Rfc": "URE180429TM6", "Total": 116, "IsActive": True},
            {"Id": "failed-1", "Uuid": "", "TaxName": "INTENTO FALLIDO", "Status": "invalid"},
        ]
        with patch.object(billing, "_fm_request", return_value=rows):
            response = self.client.get("/api/facturas/list")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["customer_name"], "CLIENTE SAT")
        self.assertEqual(data[0]["customer_tax_id"], "URE180429TM6")

    def test_download_decodes_facturama_base64(self):
        expected = b"%PDF-sandbox"
        encoded = base64.b64encode(expected).decode("ascii")
        with patch.object(billing, "_fm_request", return_value={"Content": encoded}):
            response = self.client.get("/api/invoices/sandbox-id/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected)
        self.assertEqual(response.mimetype, "application/pdf")


if __name__ == "__main__":
    unittest.main()
