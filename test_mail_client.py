import unittest
import imaplib
from email.message import EmailMessage
from unittest.mock import Mock, patch

import mail_client


class MailClientParsingTests(unittest.TestCase):
    @patch("mail_client.time.sleep")
    @patch("mail_client.imap_connection")
    def test_fetch_reconnects_after_imap_eof(self, connection, sleep):
        first = Mock()
        first.__enter__ = Mock(return_value=Mock())
        first.__exit__ = Mock(return_value=False)
        first.__enter__.return_value.uid.side_effect = imaplib.IMAP4.abort("socket error: EOF")
        second = Mock()
        second.__enter__ = Mock(return_value=Mock())
        second.__exit__ = Mock(return_value=False)
        second.__enter__.return_value.uid.return_value = ("OK", [(b"meta", b"message")])
        connection.side_effect = [first, second]

        result = mail_client._fetch_with_reconnect("INBOX", "12", "(BODY.PEEK[])", readonly=True)

        self.assertEqual(result[0], "OK")
        self.assertEqual(connection.call_count, 2)
        sleep.assert_called_once()

    def test_folder_roles_support_carrierzone_names(self):
        sent = mail_client._parse_list_row(b'(\\HasNoChildren) "/" "mail/sent-mail"')
        spam = mail_client._parse_list_row(b'(\\HasNoChildren) "/" "mail/spam"')
        inbox = mail_client._parse_list_row(b'(\\HasNoChildren) "/" "INBOX"')

        self.assertEqual(sent["role"], "sent")
        self.assertEqual(spam["role"], "junk")
        self.assertEqual(inbox["role"], "inbox")

    def test_summary_decodes_headers_and_builds_thread_key(self):
        message = EmailMessage()
        message["From"] = "Cliente <cliente@example.com>"
        message["To"] = "HSC <hsc@example.com>"
        message["Subject"] = "Re: RV: Servicio México"
        message["Message-ID"] = "<one@example.com>"
        message.set_content("Buen dia")

        summary = mail_client._summary("42", message.as_bytes(), b"FLAGS (\\Seen)")

        self.assertEqual(summary["uid"], "42")
        self.assertEqual(summary["thread_key"], "servicio méxico")
        self.assertTrue(summary["seen"])
        self.assertEqual(summary["from"][0]["email"], "cliente@example.com")

    def test_summary_accepts_bodystructure_attachment_hint(self):
        message = EmailMessage()
        message["From"] = "cliente@example.com"
        message["Subject"] = "Archivo"
        message.set_content("Adjunto")

        summary = mail_client._summary("8", message.as_bytes(), has_attachments=True)

        self.assertTrue(summary["has_attachments"])

    def test_html_body_is_returned_as_safe_plain_text(self):
        message = EmailMessage()
        message.set_content("Texto alternativo")
        message.add_alternative("<p>Hola <strong>HSC</strong></p><script>alert(1)</script>", subtype="html")
        html_only = list(message.iter_parts())[1]

        self.assertEqual(mail_client._body_text(html_only), "Hola HSC")

    def test_imap_folder_rejects_command_injection(self):
        with self.assertRaises(ValueError):
            mail_client._imap_quote("INBOX\r\nLOGOUT")


if __name__ == "__main__":
    unittest.main()
