import unittest
from unittest.mock import patch

import app as app_module


class ArticleCatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_creates_complete_active_article(self):
        payload = {
            "codigo": "SERV-01",
            "nombre": "Mantenimiento",
            "descripcion": "Mantenimiento preventivo",
            "categoria": "Refrigeración",
            "clave_sat": "72151200",
            "unidad_sat": "E48",
            "precio": 1250,
            "iva": 0.16,
            "activo": True,
        }
        written = []
        with (
            patch.object(app_module, "_leer_articulos_locales", return_value=[]),
            patch.object(app_module, "descargar_articulos_de_drive", return_value=[]),
            patch.object(app_module, "_escribir_articulos_locales", side_effect=lambda rows: written.extend(rows)),
            patch.object(app_module, "subir_articulos_a_drive", return_value=True),
        ):
            response = self.client.post("/api/articulos", json=payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(written[0]["codigo"], "SERV-01")
        self.assertEqual(written[0]["clave_sat"], "72151200")

    def test_rejects_invalid_sat_code(self):
        with patch.object(app_module, "_leer_articulos_locales", return_value=[]):
            response = self.client.post("/api/articulos", json={
                "codigo": "X", "nombre": "Servicio", "clave_sat": "123", "precio": 0,
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn("8 dígitos", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
