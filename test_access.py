import os
import unittest
from unittest.mock import patch

import app as hsc


class AppAccessTests(unittest.TestCase):
    def setUp(self):
        hsc.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = hsc.app.test_client()
        self.env = patch.dict(os.environ, {"HSC_APP_PASSWORD": "clave-segura-pruebas"}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_pages_and_api_require_login_but_health_does_not(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        page = self.client.get("/facturacion")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/acceso", page.headers["Location"])
        api = self.client.get("/api/ping")
        self.assertEqual(api.status_code, 401)

    def test_login_persists_until_logout(self):
        wrong = self.client.post("/acceso", data={"password": "incorrecta"})
        self.assertIn("no es correcta", wrong.get_data(as_text=True))
        login = self.client.post("/acceso", data={
            "password": "clave-segura-pruebas", "next": "/facturacion",
        })
        self.assertEqual(login.status_code, 302)
        self.assertTrue(login.headers["Location"].endswith("/facturacion"))
        self.assertEqual(self.client.get("/facturacion").status_code, 200)
        with self.client.session_transaction() as saved:
            self.assertTrue(saved.permanent)
            self.assertTrue(saved["hsc_authenticated"])
        self.client.get("/cerrar-sesion")
        self.assertEqual(self.client.get("/facturacion").status_code, 302)

    def test_notifications_and_report_auto_header_do_not_bypass_login(self):
        self.assertEqual(self.client.get("/api/notifications").status_code, 401)
        self.assertEqual(self.client.post("/api/notifications/example/read").status_code, 401)
        response = self.client.post("/reportes/pdf_json/UDA38_R%201", headers={"X-HSC-Auto-PDF": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/acceso", response.headers["Location"])

    def test_login_does_not_allow_external_return_url(self):
        response = self.client.post("/acceso", data={
            "password": "clave-segura-pruebas", "next": "https://example.com",
        })
        self.assertTrue(response.headers["Location"].endswith("/inicio-app"))

    def test_sensitive_pages_include_security_and_no_cache_headers(self):
        response = self.client.get("/api/ping")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])


if __name__ == "__main__":
    unittest.main()
