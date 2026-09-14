"""Retirar el lector no debe retirar SMTP, cadenas ni avisos operativos."""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import smtp_mailer


class MailRetirementTests(unittest.TestCase):
    def test_reader_removed_but_operational_worker_remains(self):
        source = Path('app.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertTrue({'mail_bp', 'mail_idle', 'mail_client'}.isdisjoint(imports))
        worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'ensure_notifications_worker')
        namespace = {'start_notifications': MagicMock(), 'app': object()}
        worker.decorator_list = []
        exec(compile(ast.Module(body=[worker], type_ignores=[]), 'app.py', 'exec'), namespace)
        namespace['ensure_notifications_worker']()
        namespace['start_notifications'].assert_called_once_with(namespace['app'])
        self.assertNotIn('href="/correo"', Path('templates/inicio_app.html').read_text(encoding='utf-8'))
        notices = ast.parse(Path('notification_delivery.py').read_text(encoding='utf-8-sig'))
        accepts = next(node for node in notices.body if isinstance(node, ast.FunctionDef) and node.name == 'accepts_notice')
        options = next(node for node in notices.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'NOTICE_OPTIONS' for t in node.targets))
        ns = {'notification_preferences': lambda user: {}}
        exec(compile(ast.Module(body=[options, accepts], type_ignores=[]), 'notification_delivery.py', 'exec'), ns)
        self.assertFalse(ns['accepts_notice']('owner', 'correo'))
        self.assertTrue(ns['accepts_notice']('owner', 'fallas'))
        self.assertNotIn('correo', ns['NOTICE_OPTIONS']['admin'])

    def test_document_sending_keeps_thread_headers(self):
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch.dict(os.environ, {'SMTP_USER':'hsc@example.com', 'SMTP_PASSWORD':'secret', 'SMTP_FROM':'hsc@example.com', 'SMTP_PORT':'465'}), patch.object(smtp_mailer.smtplib, 'SMTP_SSL', return_value=smtp), patch.object(smtp_mailer, '_save_sent_copy', return_value=True):
            smtp_mailer.send_quote_email(recipient='client@example.com', subject='Cotización', body='Seguimiento', pdf_bytes=b'%PDF', folio='10', in_reply_to='<parent@example.com>', references=['<first@example.com>'])
        message = smtp.send_message.call_args.args[0]
        self.assertTrue(message['Message-ID'])
        self.assertEqual(message['In-Reply-To'], '<parent@example.com>')
        self.assertEqual(message['References'], '<first@example.com> <parent@example.com>')
        self.assertEqual(len(list(message.iter_attachments())), 1)


if __name__ == '__main__':
    unittest.main()
