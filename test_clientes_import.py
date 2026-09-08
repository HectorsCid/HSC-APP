import io
import unittest
from unittest.mock import patch

from openpyxl import Workbook
from werkzeug.datastructures import FileStorage

import app as app_module


class CustomerImportTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    @staticmethod
    def xlsx_upload(rows):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Nombre legal", "RFC", "Acciones"])
        for row in rows:
            sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return FileStorage(stream=stream, filename="clientes.xlsx")

    def test_reads_and_groups_duplicate_konta_rfc(self):
        upload = self.xlsx_upload([
            ["CLIENTE CORTO", "AAA010101AAA", ""],
            ["CLIENTE CORTO SA DE CV", "AAA010101AAA", ""],
            ["OTRO CLIENTE", "BBB010101BBB", ""],
        ])
        rows = app_module._leer_clientes_konta(upload)
        self.assertEqual(len(rows), 2)
        first = next(row for row in rows if row["rfc"] == "AAA010101AAA")
        self.assertEqual(first["nombre_legal"], "CLIENTE CORTO SA DE CV")
        self.assertEqual(first["apariciones"], 2)

    def test_plan_matches_existing_name_without_accents_or_case(self):
        rows = [{"nombre_legal": "GERRESHEIMER QUERETARO", "rfc": "GQU381122UF6"}]
        plan = app_module._plan_importacion_clientes(rows, {"Gerresheimer Querétaro": {}})
        self.assertEqual(plan[0]["destino"], "Gerresheimer Querétaro")
        self.assertEqual(plan[0]["tipo"], "nombre")

    def test_plan_suggests_commercial_name_when_legal_suffix_differs(self):
        rows = [{"nombre_legal": "BITICINO DE MEXICO SA DE CV", "rfc": "BME8604039G0"}]
        plan = app_module._plan_importacion_clientes(rows, {"Bticino": {}})
        self.assertEqual(plan[0]["destino"], "Bticino")
        self.assertEqual(plan[0]["tipo"], "sugerido")

    def test_invoice_fiscal_data_is_saved_to_selected_customer(self):
        original = app_module.clientes_predefinidos
        app_module.clientes_predefinidos = {"Cliente prueba": {"atencion": []}}
        try:
            with patch.object(app_module, "guardar_clientes") as save_mock:
                response = self.client.post("/api/clientes/fiscales", json={
                    "cliente": "Cliente prueba",
                    "rfc": "ure180429tm6",
                    "razon_social": "Universidad Robotica Española",
                    "cp": "86991",
                    "regimen_fiscal": "601",
                    "uso_cfdi": "G03",
                    "correo_facturacion": "facturas@example.com",
                })
            self.assertEqual(response.status_code, 200)
            saved = app_module.clientes_predefinidos["Cliente prueba"]
            self.assertEqual(saved["rfc"], "URE180429TM6")
            self.assertEqual(saved["cp"], "86991")
            self.assertEqual(saved["uso_cfdi"], "G03")
            save_mock.assert_called_once()
        finally:
            app_module.clientes_predefinidos = original

    def test_current_customer_fiscal_record_overrides_old_quote_receiver(self):
        quote = {
            "cliente": "Bticino",
            "receptor": {"nombre": "Bticino", "rfc": "", "cp": ""},
        }
        current = {
            "razon_social": "BTICINO DE MEXICO SA DE CV",
            "rfc": "BME8604039G0",
            "cp": "76120",
            "regimen_fiscal": "601",
            "uso_cfdi": "G03",
        }
        receiver = app_module._combinar_receptor_cotizacion(quote, current)
        self.assertEqual(receiver["nombre"], "BTICINO DE MEXICO SA DE CV")
        self.assertEqual(receiver["rfc"], "BME8604039G0")
        self.assertEqual(receiver["cp"], "76120")


if __name__ == "__main__":
    unittest.main()
