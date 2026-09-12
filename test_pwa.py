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
        self.assertEqual(manifest["id"], "/hsc-panel")
        self.assertEqual(manifest["name"], "HSC Panel")
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

    def test_operations_apps_have_independent_manifests(self):
        technician = self.client.get("/manifest-hsc-tecnico.webmanifest")
        partner = self.client.get("/manifest-hsc-partner.webmanifest")
        self.assertEqual(technician.status_code, 200)
        self.assertEqual(partner.status_code, 200)
        technician_manifest = technician.get_json()
        partner_manifest = partner.get_json()
        self.assertEqual(technician_manifest["id"], "/hsc-tecnico/")
        self.assertEqual(technician_manifest["scope"], "/hsc-tecnico/")
        self.assertEqual(technician_manifest["start_url"], "/hsc-tecnico/?origen=app")
        self.assertEqual(partner_manifest["id"], "/hsc-partner/")
        self.assertEqual(partner_manifest["scope"], "/hsc-partner/")
        self.assertEqual(partner_manifest["start_url"], "/hsc-partner/?origen=app")
        self.assertNotEqual(technician_manifest["id"], partner_manifest["id"])

    def test_operations_apps_expose_their_own_install_identity(self):
        technician = self.client.get("/hsc-tecnico/").get_data(as_text=True)
        partner = self.client.get("/hsc-partner/").get_data(as_text=True)
        self.assertIn('/manifest-hsc-tecnico.webmanifest', technician)
        self.assertIn('data-app-kind="technician"', technician)
        self.assertIn('Instalar HSC Técnico', technician)
        self.assertIn('/manifest-hsc-partner.webmanifest', partner)
        self.assertIn('data-app-kind="partner"', partner)
        self.assertIn('Instalar HSC Partner', partner)


if __name__ == "__main__":
    unittest.main()
