import unittest
from unittest.mock import patch

from flask import Flask

from mail_bp import mail_bp


class MailBlueprintTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder="templates", static_folder="static")
        app.config.update(TESTING=True, SECRET_KEY="test")
        app.add_url_rule("/manifest.webmanifest", endpoint="pwa_manifest", view_func=lambda: "{}")
        app.register_blueprint(mail_bp)
        self.client = app.test_client()

    def test_mail_page_renders(self):
        response = self.client.get("/correo")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Correo HSC", response.get_data(as_text=True))

    @patch("mail_bp.mail_client.list_folders")
    def test_folders_are_returned_from_imap_client(self, folders):
        folders.return_value = [{"name": "INBOX", "label": "INBOX", "role": "inbox"}]
        response = self.client.get("/api/correo/folders")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["folders"][0]["name"], "INBOX")

    @patch("mail_bp.mail_client.move_message")
    def test_move_rejects_unknown_destination(self, move):
        response = self.client.post(
            "/api/correo/message/12/move",
            json={"folder": "INBOX", "destination": "external-folder"},
        )

        self.assertEqual(response.status_code, 400)
        move.assert_not_called()

    @patch("mail_bp.mail_client.list_folders")
    def test_imap_failure_does_not_break_the_page(self, folders):
        folders.side_effect = RuntimeError("Servidor no disponible")
        response = self.client.get("/api/correo/folders")

        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
