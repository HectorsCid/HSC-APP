import os
import smtplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from flask import Flask, session
from diagnostics_bp import create_diagnostics_blueprint, check_smtp, check_facturama
from operaciones_store import OperationsStore


class DiagnosticsPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = OperationsStore(local_path=Path(self.temp.name)/'test.sqlite3')
        self.app = Flask(__name__)
        self.app.secret_key = 'test'
        self.mail, self.billing = MagicMock(return_value=dict(ok=True,status='authenticated',message='Sin envío')), MagicMock(return_value=dict(ok=True,status='connected',message='Sin timbrado'))
        def forbidden(*roles):
            return None if session.get('role') in roles else ('No autorizado', 403)
        self.app.register_blueprint(create_diagnostics_blueprint(self.store, forbidden, smtp_probe=self.mail, billing_probe=self.billing))
        self.client = self.app.test_client()

    def owner(self):
        with self.client.session_transaction() as sess:
            sess['role'] = 'admin'
        self.client.get('/diagnostico')
        with self.client.session_transaction() as sess:
            return sess['hsc_diagnostics_csrf']

    def test_owner_only_and_initial_page_never_probes(self):
        for role in ('', 'client', 'technician'):
            with self.client.session_transaction() as sess:
                sess['role'] = role
            self.assertEqual(self.client.get('/diagnostico').status_code, 403)
            self.assertEqual(self.client.get('/api/diagnostico/estado').status_code, 403)
            self.assertEqual(self.client.post('/api/diagnostico/probar/correo', json={}).status_code, 403)
        self.owner()
        result = self.client.get('/api/diagnostico/estado')
        self.assertTrue(result.get_json()['database']['ok'])
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.mail.assert_not_called()
        self.billing.assert_not_called()

    def test_manual_checks_csrf_rate_limit_and_failure_recovery(self):
        csrf = self.owner()
        self.assertEqual(self.client.post('/api/diagnostico/probar/correo', json={}).status_code, 403)
        response = self.client.post('/api/diagnostico/probar/correo', json=dict(csrf=csrf))
        self.assertTrue(response.get_json()['ok'])
        self.mail.assert_called_once()
        self.assertEqual(self.client.post('/api/diagnostico/probar/correo', json=dict(csrf=csrf)).status_code, 429)
        self.billing.side_effect = RuntimeError('PRIVATE_PASSWORD')
        result = self.client.post('/api/diagnostico/probar/facturama', json=dict(csrf=csrf))
        self.assertFalse(result.get_json()['ok'])
        self.assertNotIn('PRIVATE_PASSWORD', result.get_data(as_text=True))
        self.assertEqual(self.client.post('/api/diagnostico/probar/unknown', json=dict(csrf=csrf)).status_code, 400)

    def test_smtp_authentication_only_no_send_and_secret_redaction(self):
        with patch.dict(os.environ, dict(SMTP_USER='test@example.com',SMTP_PASSWORD='PRIVATE_PASSWORD')), patch('diagnostics_bp.smtplib.SMTP_SSL') as smtp:
            server = smtp.return_value.__enter__.return_value
            self.assertTrue(check_smtp()['ok'])
            server.login.assert_called_once_with('test@example.com','PRIVATE_PASSWORD')
            server.send_message.assert_not_called()
            server.sendmail.assert_not_called()
            server.mail.assert_not_called()
            server.login.side_effect = smtplib.SMTPAuthenticationError(535, b'PRIVATE_PASSWORD')
            result = check_smtp()
            self.assertEqual(result['status'], 'authentication_failed')
            self.assertNotIn('PRIVATE_PASSWORD', str(result))

    def test_facturama_is_read_only_and_never_returns_profile_or_secret(self):
        with patch('facturacion_bp._facturama_config', return_value=dict(configured=True)), patch('facturacion_bp._fm_request', return_value={'Csd': {'PrivateKey': 'PRIVATE_KEY'}}) as request:
            result = check_facturama()
            request.assert_called_once_with('GET', '/TaxEntity', timeout=8)
            self.assertTrue(result['ok'])
            self.assertNotIn('PRIVATE_KEY', str(result))

    def test_owner_can_stop_monitoring_without_altering_data(self):
        from memory_diagnostics import MemoryMonitor
        monitor = MemoryMonitor()
        csrf = self.owner()
        with patch('diagnostics_bp.MONITOR', monitor):
            response = self.client.post('/api/diagnostico/probar/detener', json=dict(csrf=csrf))
            self.assertTrue(response.get_json()['ok'])
            self.assertTrue(monitor.paused)


if __name__ == '__main__':
    unittest.main()
