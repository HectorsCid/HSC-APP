import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as module


class FakeOperationsStore:
    enabled = True

    def snapshot(self):
        return {"clients": [
            {"id": "HDI", "name": "Holiday Inn Diamante"},
            {"id": "TT", "name": "Travers Tool"},
        ]}


class PartnerDocumentsTest(unittest.TestCase):
    def setUp(self):
        module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = module.app.test_client()
        self.temp = tempfile.TemporaryDirectory()
        self.quotes = Path(self.temp.name) / "cotizaciones.json"
        self.quotes.write_text(json.dumps([
            {"id": "COT-HDI", "cliente": "Holiday Inn Diamante", "receptor": {"rfc": "HDI010101AA1"}, "total": 100},
            {"id": "COT-TT", "cliente": "Travers Tool", "receptor": {"rfc": "TTO010101AA1"}, "total": 200},
        ]), encoding="utf-8")
        self.patches = [
            patch.object(module, "OPERACIONES_STORE", FakeOperationsStore()),
            patch.object(module, "_ruta_cotizaciones", return_value=self.quotes),
            patch.object(module, "_resolver_cliente_catalogo", side_effect=self.resolve_client),
            patch.object(module, "billing_client_groups", return_value=([{
                "name": "Holiday Inn Diamante", "rfc": "HDI010101AA1", "pending_total": 25,
                "invoices": [{"id": "INV-HDI", "folio": "18", "date": "2026-09-01", "total": 116,
                              "active": True, "paid": False, "payment_method": "PPD", "remaining_balance": 25}],
                "complements": [{"id": "REP-HDI", "uuid": "UUID-REP", "invoice_folio": "18",
                                 "date": "2026-09-02", "amount": 91, "partiality_number": 1}],
            }], {})),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    @staticmethod
    def resolve_client(name="", rfc=""):
        if rfc == "HDI010101AA1" or "Holiday" in str(name):
            return "Holiday Inn Diamante", {"rfc": "HDI010101AA1"}
        return "Travers Tool", {"rfc": "TTO010101AA1"}

    def login(self, role, client_id=""):
        with self.client.session_transaction() as session:
            session["hsc_authenticated"] = True
            session["hsc_role"] = role
            session["hsc_client_id"] = client_id

    def test_filters_all_document_types_by_assigned_client(self):
        self.login("client", "HDI")
        response = self.client.get("/api/operaciones/partner-documents?client_id=HDI")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual([item["id"] for item in data["quotes"]], ["COT-HDI"])
        self.assertEqual([item["id"] for item in data["invoices"]], ["INV-HDI"])
        self.assertEqual([item["id"] for item in data["complements"]], ["REP-HDI"])

    def test_partner_cannot_request_another_client(self):
        self.login("client", "HDI")
        response = self.client.get("/api/operaciones/partner-documents?client_id=TT")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
