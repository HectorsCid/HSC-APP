import base64
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from operaciones_store import OperationsStore
from scheduled_email import Queue, deliver_one, enqueue, install, scheduled_time


class ScheduledEmailTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = OperationsStore(local_path=Path(temp.name) / 'queue.sqlite3')
        self.app = Flask(__name__)
        self.app.testing = True
        install(self.app, self.store)
        self.queue = self.app.extensions['scheduled_email_queue']
        self.fields = dict(recipient='cliente@example.com', cc='', subject='Documento', body='Mensaje',
                           folio='17', pdf_bytes=base64.b64encode(b'pdf').decode(), extra_attachments=[])

    def test_mexico_time_converts_to_utc_and_rejects_past_or_offset(self):
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.assertEqual(scheduled_time('2026-11-10T07:00', now),
                         int(datetime(2026, 11, 10, 13, tzinfo=timezone.utc).timestamp()))
        for value in ('2026-11-01T07:00', '2026-11-10T07:00Z', '2026-11-31T07:00', '2027-11-10T07:00'):
            with self.assertRaises(ValueError):
                scheduled_time(value, now)

    def test_persistent_reschedule_cancel_and_atomic_claim(self):
        job = self.queue.add('factura', '17', 1000, self.fields)
        reopened = Queue(self.store)
        self.assertIsNone(reopened.claim(999))
        with self.assertRaises(ValueError):
            reopened.add('factura', '17', 1000, self.fields)
        self.assertFalse(reopened.change('factura', '18', job))
        self.assertTrue(reopened.change('factura', '17', job, 1100))
        self.assertIsNone(reopened.claim(1099))
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda queue:queue.claim(1100), [reopened, self.queue]))
        self.assertEqual([row[0] for row in claims if row], [job])
        self.assertFalse(reopened.change('factura', '17', job))
        self.assertIsNone(reopened.claim(2200))
        self.assertEqual(reopened.list('factura', '17')[0]['state'], 'uncertain')
        other = self.queue.add('factura', '18', 3000, self.fields)
        self.assertTrue(reopened.change('factura', '18', other))
        self.assertIsNone(reopened.claim(3100))

    def test_enqueue_preserves_files_and_worker_sends_only_once(self):
        with self.app.app_context(), patch('scheduled_email.scheduled_time', return_value=1), patch('scheduled_email.trusted_device_token', return_value=''):
            result = enqueue('factura', '17', 'local', recipient='cliente@example.com', cc='',
                             subject='Documento', body='Mensaje', folio='17', pdf_bytes=b'pdf', xml_bytes=b'xml',
                             extra_attachments=[dict(filename='orden.pdf', content_type='application/pdf', data=b'orden')])
            self.assertTrue(result.get_json()['scheduled'])
        with (patch('scheduled_email.send_cfdi_email', return_value={}) as send,
              patch('scheduled_email.email_thread_headers', return_value={}),
              patch('scheduled_email.record_email_delivery') as record):
            deliver_one(Queue(self.store), logging.getLogger(__name__))
            deliver_one(self.queue, logging.getLogger(__name__))
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs['pdf_bytes'], b'pdf')
        self.assertEqual(send.call_args.kwargs['xml_bytes'], b'xml')
        self.assertEqual(send.call_args.kwargs['extra_attachments'][0]['data'], b'orden')
        record.assert_called_once()
        self.assertEqual(self.queue.list('factura', '17')[0]['state'], 'sent')

    def test_cancel_requires_mail_authorization(self):
        job = self.queue.add('cotizacion', '17', 1000, self.fields)
        client = self.app.test_client()
        payload = dict(id=job, action='cancel')
        with patch('scheduled_email.authorized_to_send', return_value=False):
            self.assertEqual(client.post('/api/scheduled-email/cotizacion/17', json=payload).status_code, 403)
        with patch('scheduled_email.authorized_to_send', return_value=True):
            response = client.post('/api/scheduled-email/cotizacion/17', json=payload)
        self.assertEqual(response.get_json()['items'][0]['state'], 'cancelled')

    def test_invoice_email_route_queues_existing_documents_only(self):
        from facturacion_bp import facturacion_bp
        from scheduled_email import ZONE
        from datetime import timedelta

        self.app.register_blueprint(facturacion_bp)
        when = (datetime.now(ZONE) + timedelta(days=1)).strftime('%Y-%m-%dT08:00')
        with (patch('facturacion_bp.smtp_config', return_value={'configured':True}),
              patch('facturacion_bp.authorized_to_send', return_value=True),
              patch('facturacion_bp._provider', return_value='facturama'),
              patch('facturacion_bp._fm_request') as provider,
              patch('facturacion_bp._decode_facturama_file', return_value=b'xml'),
              patch('facturacion_bp._facturama_printable_pdf', return_value=b'pdf'),
              patch('facturacion_bp.send_cfdi_email') as send):
            response = self.app.test_client().post('/api/invoices/17/email', data={
                'email':'cliente@example.com', 'scheduled_for':when,
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['scheduled'])
        self.assertEqual(self.queue.list('factura', '17')[0]['state'], 'pending')
        provider.assert_called_once_with('GET', '/Cfdi/xml/issued/17')
        send.assert_not_called()
