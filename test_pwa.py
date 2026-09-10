import json
import unittest

import app as hsc_app


class PwaTests(unittest.TestCase):
    def setUp(self):
        hsc_app.app.config.update(TESTING=True)
        self.client = hsc_app.app.test_client()

    def test_manifest_is_public_and_installable(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json", response.content_type)
        manifest = json.loads(response.get_data(as_text=True))
        self.assertEqual(manifest["start_url"], "/inicio-app?origen=app")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})

    def test_service_worker_is_public_and_has_root_scope(self):
        response = self.client.get("/service-worker.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Service-Worker-Allowed"), "/")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        source = response.get_data(as_text=True)
        self.assertIn("SAFE_PATHS", source)
        self.assertNotIn("/facturacion", source)
        self.assertNotIn("/api/", source)

    def test_main_screen_exposes_manifest_and_installer(self):
        response = self.client.get("/inicio-app")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('rel="manifest"', html)
        self.assertIn("/static/pwa.js", html)
        self.assertIn("apple-mobile-web-app-capable", html)


if __name__ == "__main__":
    unittest.main()
