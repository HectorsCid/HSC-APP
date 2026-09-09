import base64
import os
import unittest
from unittest.mock import patch

from flask import Flask

import facturacion_bp as billing


class FacturamaIntegrationTests(unittest.TestCase):
    VALID_UUID = "2c4c8e4a-b337-4bf6-ade2-cd972f8a93bb"
    SAMPLE_XML = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Version="4.0" Serie="HSC" Folio="1370" Fecha="2026-09-09T10:00:00" Moneda="MXN" SubTotal="100.00" Total="116.00" FormaPago="03" MetodoPago="PUE" LugarExpedicion="76240" NoCertificado="00001000000706708942" Sello="SELLODEPRUEBA12345678"><cfdi:Emisor Rfc="SICH930611DV7" Nombre="HECTOR SILVA CID" RegimenFiscal="621"/><cfdi:Receptor Rfc="URE180429TM6" Nombre="UNIVERSIDAD ROBOTICA ESPANOLA" DomicilioFiscalReceptor="86991" RegimenFiscalReceptor="601" UsoCFDI="G03"/><cfdi:Conceptos><cfdi:Concepto ClaveProdServ="85121600" Descripcion="Servicio de prueba" Unidad="Servicio" Cantidad="1" ValorUnitario="100.00" Importe="100.00"/></cfdi:Conceptos><cfdi:Impuestos TotalImpuestosTrasladados="16.00"/><cfdi:Complemento><tfd:TimbreFiscalDigital Version="1.1" UUID="2c4c8e4a-b337-4bf6-ade2-cd972f8a93bb" FechaTimbrado="2026-09-09T10:01:00" NoCertificadoSAT="00001000000500003456" RfcProvCertif="AAA010101AAA" SelloCFD="SELLOCFD" SelloSAT="SELLOSAT"/></cfdi:Complemento></cfdi:Comprobante>'''

    def setUp(self):
        billing._STAMP_RESULTS.clear()
        billing._FISCAL_CATALOG_CACHE.clear()
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
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
            "confirmed": True,
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

    def test_purchase_order_is_sent_to_facturama(self):
        payload = self.payload()
        payload["numero_orden_compra"] = "OC-BTICINO-45872"
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            cfdi = billing._build_facturama_cfdi(payload)
        self.assertEqual(cfdi["OrderNumber"], "OC-BTICINO-45872")

    def test_quote_id_is_not_used_as_purchase_order(self):
        payload = self.payload()
        payload["source_quote_id"] = "cotizacion-interna-1579"
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            cfdi = billing._build_facturama_cfdi(payload)
        self.assertNotIn("OrderNumber", cfdi)

    def test_purchase_order_rejects_more_than_100_characters(self):
        payload = self.payload()
        payload["numero_orden_compra"] = "X" * 101
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            with self.assertRaisesRegex(ValueError, "100 caracteres"):
                billing._build_facturama_cfdi(payload)

    def test_quote_retentions_are_sent_as_federal_withholdings(self):
        payload = self.payload()
        payload["retenciones"] = {
            "aplicar": True,
            "isr": 0.0125,
            "iva": 0.1066666667,
        }
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            cfdi = billing._build_facturama_cfdi(payload)
        item = cfdi["Items"][0]
        taxes = {tax["Name"]: tax for tax in item["Taxes"]}
        self.assertEqual(taxes["ISR"]["Total"], 2.5)
        self.assertEqual(taxes["IVA RET"]["Total"], 21.33)
        self.assertTrue(taxes["ISR"]["IsRetention"])
        self.assertTrue(taxes["IVA RET"]["IsRetention"])
        self.assertEqual(item["Total"], 208.17)

    def test_item_discount_reduces_tax_base(self):
        payload = self.payload()
        payload["items"][0]["descuento"] = 20
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            item = billing._build_facturama_cfdi(payload)["Items"][0]
        self.assertEqual(item["Discount"], 20.0)
        self.assertEqual(item["Taxes"][0]["Base"], 180.0)
        self.assertEqual(item["Total"], 208.8)

    def test_can_apply_only_isr_retention(self):
        payload = self.payload()
        payload["retenciones"] = {"aplicar": True, "isr": 0.0125, "iva": 0}
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            item = billing._build_facturama_cfdi(payload)["Items"][0]
        tax_names = [tax["Name"] for tax in item["Taxes"]]
        self.assertIn("ISR", tax_names)
        self.assertNotIn("IVA RET", tax_names)
        self.assertEqual(item["Total"], 229.5)

    def test_can_apply_only_iva_retention(self):
        payload = self.payload()
        payload["retenciones"] = {"aplicar": True, "isr": 0, "iva": 0.04}
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            item = billing._build_facturama_cfdi(payload)["Items"][0]
        tax_names = [tax["Name"] for tax in item["Taxes"]]
        self.assertNotIn("ISR", tax_names)
        self.assertIn("IVA RET", tax_names)
        self.assertEqual(item["Total"], 224.0)

    def test_obsolete_cfdi_use_is_rejected(self):
        payload = self.payload()
        payload["receptor"]["uso_cfdi"] = "P01"
        with patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")):
            with self.assertRaisesRegex(ValueError, "catálogo vigente"):
                billing._build_facturama_cfdi(payload)

    def test_fiscal_catalogs_have_offline_fallback_and_filter_by_rfc_type(self):
        with patch.object(billing, "_fm_request", side_effect=billing.requests.ConnectionError("offline")):
            response = self.client.get("/api/catalogos/fiscales?rfc=URE180429TM6")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["source"], "respaldo_local")
        self.assertIn("601", {row["value"] for row in data["regimes"]})
        self.assertNotIn("612", {row["value"] for row in data["regimes"]})
        self.assertIn("S01", {row["value"] for row in data["cfdi_uses"]})
        self.assertNotIn("P01", {row["value"] for row in data["cfdi_uses"]})
        self.assertNotIn("D01", {row["value"] for row in data["cfdi_uses"]})
        self.assertEqual(len(data["payment_forms"]), 22)

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
        answer = {"Id": "sandbox-id", "Uuid": self.VALID_UUID, "Status": "active", "Total": 232}
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_facturama_receiver_validation", return_value=({}, {})) as receiver_mock,
            patch.object(billing, "_fm_request", return_value=answer) as request_mock,
            patch.object(billing, "_backup_facturama_cfdi", return_value={"ok": True}),
        ):
            first = self.client.post("/api/facturar", json=self.payload("same-request"))
            second = self.client.post("/api/facturar", json=self.payload("same-request"))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(request_mock.call_count, 1)
        receiver_mock.assert_not_called()
        self.assertTrue(second.get_json()["duplicate_prevented"])
        self.assertEqual(first.get_json()["receiver_validation"], "local_sandbox")

    def test_stamp_requires_explicit_confirmation(self):
        payload = self.payload("unconfirmed-request")
        payload["confirmed"] = False
        with patch.object(billing, "_fm_request") as request_mock:
            response = self.client.post("/api/facturar", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["stage"], "confirmation")
        request_mock.assert_not_called()

    def test_missing_facturama_credentials_never_falls_back(self):
        with (
            patch.dict(os.environ, {"FACTURAMA_USER": "", "FACTURAMA_PASSWORD": ""}, clear=False),
            patch.object(billing, "_fa_post") as fallback_mock,
            patch.object(billing, "_fm_request") as facturama_mock,
        ):
            response = self.client.post("/api/facturar", json=self.payload("missing-config"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["stage"], "configuration")
        fallback_mock.assert_not_called()
        facturama_mock.assert_not_called()

    def test_uncertain_stamp_is_blocked_from_being_sent_twice(self):
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_fm_request", side_effect=billing.requests.Timeout("sin respuesta")) as request_mock,
        ):
            first = self.client.post("/api/facturar", json=self.payload("uncertain-request"))
            second = self.client.post("/api/facturar", json=self.payload("uncertain-request"))
        self.assertEqual(first.status_code, 502)
        self.assertTrue(first.get_json()["uncertain"])
        self.assertEqual(second.status_code, 409)
        self.assertTrue(second.get_json()["uncertain"])
        self.assertEqual(request_mock.call_count, 1)

    def test_stamp_extracts_nested_facturama_uuid(self):
        expected_uuid = self.VALID_UUID
        answer = {
            "Id": "sandbox-id",
            "Complement": {"TaxStamp": {"Uuid": expected_uuid, "CfdiSign": "secret-signature"}},
            "Status": "active",
            "Total": 1.16,
        }
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_facturama_receiver_validation", return_value=({}, {})),
            patch.object(billing, "_fm_request", return_value=answer),
            patch.object(billing, "_backup_facturama_cfdi", return_value={"ok": True}),
        ):
            response = self.client.post("/api/facturar", json=self.payload("nested-request"))
        data = response.get_json()
        self.assertEqual(data["uuid"], expected_uuid)
        self.assertNotIn("secret-signature", str(data))

    def test_stamp_preserves_internal_folio_and_purchase_order_for_pdf(self):
        payload = self.payload("pdf-metadata-request")
        payload.update({"folio": "1370", "numero_orden_compra": "OC-45872"})
        answer = {"Id": "invoice-with-metadata", "Uuid": self.VALID_UUID, "Status": "active", "Total": 232}
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_facturama_receiver_validation", return_value=({}, {})),
            patch.object(billing, "_fm_request", return_value=answer),
            patch.object(billing, "_backup_facturama_cfdi", return_value={"ok": True}),
        ):
            response = self.client.post("/api/facturar", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["internal_folio"], "1370")
        self.assertEqual(response.get_json()["order_number"], "OC-45872")
        with self.app.app_context():
            metadata = billing._invoice_pdf_metadata("invoice-with-metadata")
        self.assertEqual(metadata, {"internal_folio": "1370", "order_number": "OC-45872"})

    def test_stamp_without_valid_uuid_is_rejected_and_not_cached(self):
        answer = {"Id": "invalid-attempt", "Status": "invalid", "Total": 232}
        with (
            patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
            patch.object(billing, "_facturama_receiver_validation", return_value=({}, {})),
            patch.object(billing, "_fm_request", return_value=answer),
        ):
            response = self.client.post("/api/facturar", json=self.payload("invalid-request"))
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["ok"])
        self.assertNotIn("invalid-request", billing._STAMP_RESULTS)

    def test_invoice_list_hides_failed_attempts_and_reads_flat_receiver(self):
        valid_uuid = self.VALID_UUID
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

    def test_invoice_list_normalizes_facturama_labels_for_actions(self):
        rows = [{
            "Id": "ppd-1", "Uuid": self.VALID_UUID, "CfdiType": "ingreso",
            "PaymentMethod": "PPD - Pago en parcialidades o diferido",
            "TaxName": "CLIENTE SAT", "Rfc": "URE180429TM6", "Total": 116,
        }]
        with patch.object(billing, "_fm_request", return_value=rows):
            response = self.client.get("/api/facturas/list")
        invoice = response.get_json()["data"][0]
        self.assertEqual(invoice["type"], "I")
        self.assertEqual(invoice["payment_method"], "PPD")

    def test_received_list_uses_issuer_as_supplier(self):
        rows = [{
            "Id": "received-1", "Uuid": self.VALID_UUID, "CfdiType": "I",
            "Date": "2026-09-08T10:00:00", "Subtotal": 100, "Total": 116,
            "Issuer": {"Name": "PROVEEDOR INDUSTRIAL", "Rfc": "AAA010101AAA"},
            "Receiver": {"Name": "HSC", "Rfc": "EKU9003173C9"},
        }]
        with patch.object(billing, "_fm_request", return_value=rows) as api:
            response = self.client.get("/api/facturas/received?month=2026-09")
        self.assertEqual(response.status_code, 200)
        received = response.get_json()["data"][0]
        self.assertEqual(received["supplier_name"], "PROVEEDOR INDUSTRIAL")
        self.assertEqual(received["supplier_tax_id"], "AAA010101AAA")
        self.assertEqual(received["subtotal"], 100)
        self.assertEqual(api.call_args.kwargs["params"]["type"], "received")
        self.assertEqual(api.call_args.kwargs["params"]["dateStart"], "01/09/2026")
        self.assertEqual(api.call_args.kwargs["params"]["dateEnd"], "30/09/2026")

    def test_received_pdf_uses_received_download_type(self):
        expected = b"%PDF-received"
        encoded = base64.b64encode(expected).decode("ascii")
        with patch.object(billing, "_fm_request", return_value={"Content": encoded}) as api:
            response = self.client.get("/api/invoices/received-1/received/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected)
        self.assertIn("/received/", api.call_args.args[1])

    def test_invoice_template_can_be_saved_without_stamping(self):
        payload = {
            "name": "Mantenimiento mensual",
            "client_name": "Bticino",
            "receiver": {"tax_id": "AAA010101AAA", "name": "CLIENTE"},
            "items": [{"description": "Servicio", "quantity": 1, "unit_price": 100}],
        }
        with (
            patch.object(billing, "_read_invoice_templates", return_value=[]),
            patch.object(billing, "_write_invoice_templates", return_value=True) as save,
            patch.object(billing, "_fm_request") as stamp,
        ):
            response = self.client.post("/api/invoice-templates", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["template"]["name"], "Mantenimiento mensual")
        save.assert_called_once()
        stamp.assert_not_called()

    def test_cannot_cancel_a_payment_when_a_later_partiality_exists(self):
        index = {self.VALID_UUID: {"payments": [
            {"rep_id": "rep-1", "partiality_number": 1, "status": "active", "amount": 40},
            {"rep_id": "rep-2", "partiality_number": 2, "status": "active", "amount": 30},
        ]}}
        with (
            patch.object(billing, "_read_index", return_value=index),
            patch.object(billing, "_fm_request") as api,
        ):
            response = self.client.post("/api/invoices/rep-1/cancel", json={"motive": "02"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Cancela primero el más reciente", response.get_json()["error"])
        api.assert_not_called()

    def test_canceling_latest_payment_reopens_its_invoice_balance(self):
        index = {self.VALID_UUID: {"payments": [
            {"rep_id": "rep-1", "partiality_number": 1, "status": "active", "amount": 40},
            {"rep_id": "rep-2", "partiality_number": 2, "status": "active", "amount": 30},
        ]}}
        with (
            patch.object(billing, "_read_index", return_value=index),
            patch.object(billing, "_fm_request", return_value={"Status": "canceled"}),
            patch.object(billing, "_remove_rep_by_id") as remove,
        ):
            response = self.client.post("/api/invoices/rep-2/cancel", json={"motive": "02"})
        self.assertEqual(response.status_code, 200)
        remove.assert_called_once_with("rep-2")

    def test_receiver_validation_reports_each_mismatched_sat_field(self):
        validation = {
            "ExistRfc": True,
            "MatchName": False,
            "MatchZipCode": True,
            "MatchFiscalRegime": False,
        }
        with patch.object(billing, "_fm_request", return_value=validation):
            _, errors = billing._facturama_receiver_validation(self.payload()["receptor"])
        self.assertEqual(set(errors), {"nombre", "regimen_fiscal"})

    def test_receiver_mismatch_stops_before_stamp(self):
        with patch.dict(os.environ, {"FACTURAMA_SANDBOX": "false"}, clear=False):
            with (
                patch.object(billing, "_facturama_issuer_locations", return_value=("42501", "42501")),
                patch.object(billing, "_facturama_receiver_validation", return_value=({}, {
                    "nombre": "La razón social no coincide con la registrada ante el SAT."
                })),
                patch.object(billing, "_fm_request") as stamp_mock,
            ):
                response = self.client.post("/api/facturar", json=self.payload("bad-receiver"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["stage"], "receiver_validation")
        stamp_mock.assert_not_called()

    def test_download_builds_hsc_pdf_from_facturama_xml(self):
        encoded = base64.b64encode(self.SAMPLE_XML).decode("ascii")
        billing._STAMP_RESULTS["pdf-request"] = {
            "state": "completed",
            "result": {
                "invoice_id": "sandbox-id",
                "internal_folio": "1370",
                "order_number": "OC-BTICINO-45872",
            },
        }
        with patch.object(billing, "_fm_request", return_value={"Content": encoded}):
            response = self.client.get("/api/invoices/sandbox-id/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b"%PDF-"))
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("Factura-HSC-1370.pdf", response.headers["Content-Disposition"])

    def test_payment_cfdi_keeps_facturama_printable_format(self):
        payment_xml = self.SAMPLE_XML.replace(
            b'Version="4.0"', b'Version="4.0" TipoDeComprobante="P"', 1
        )
        expected = b"%PDF-complemento"
        encoded = base64.b64encode(expected).decode("ascii")
        with (
            patch.object(billing, "_hsc_invoice_pdf") as hsc_pdf,
            patch.object(billing, "_fm_request", return_value={"Content": encoded}) as provider_pdf,
        ):
            result = billing._facturama_printable_pdf(payment_xml, "rep-id")
        self.assertEqual(result, expected)
        hsc_pdf.assert_not_called()
        provider_pdf.assert_called_once_with("GET", "/Cfdi/pdf/issued/rep-id")


if __name__ == "__main__":
    unittest.main()
