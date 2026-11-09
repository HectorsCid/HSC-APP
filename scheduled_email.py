"""Persistent one-off delivery of already prepared commercial documents."""

import base64
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, current_app, jsonify, request

from email_tracking import email_thread_headers, record_email_delivery
from smtp_mailer import (authorized_to_send, parse_optional_recipients, parse_recipients,
                         send_cfdi_email, send_quote_email, trusted_device_token)


ZONE = ZoneInfo('America/Mexico_City')
UTC = timezone.utc
bp = Blueprint('scheduled_email', __name__)


def scheduled_time(value, now=None):
    now = now or datetime.now(UTC)
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', value or ''):
            raise ValueError
        local = datetime.fromisoformat(value).replace(tzinfo=ZONE)
        instant = local.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Selecciona una fecha y hora válidas para el envío.') from None
    if not now < instant <= now + timedelta(days=90):
        raise ValueError('Elige una hora futura dentro de los próximos 90 días (Ciudad de México).')
    return int(instant.timestamp())


def _label(ts):
    return datetime.fromtimestamp(ts, ZONE).strftime('%d/%m/%Y a las %H:%M (Ciudad de México)')


class Queue:
    def __init__(self, store):
        self.store = store
        self.p = store.placeholder
        self.ready = False
        self.lock = threading.Lock()

    def initialize(self):
        with self.lock:
            if self.ready:
                return
            with self.store.connection() as conn:
                conn.execute('''CREATE TABLE IF NOT EXISTS scheduled_document_mail (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, document_id TEXT NOT NULL,
                    due BIGINT NOT NULL, state TEXT NOT NULL, claimed BIGINT NOT NULL DEFAULT 0,
                    recipient TEXT NOT NULL, payload TEXT NOT NULL)''')
                conn.execute('''CREATE INDEX IF NOT EXISTS scheduled_document_mail_due
                    ON scheduled_document_mail (state, due)''')
                conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS scheduled_document_mail_active
                    ON scheduled_document_mail (kind, document_id) WHERE state IN ('pending', 'sending')''')
            self.ready = True

    def add(self, kind, doc, due, fields):
        self.initialize()
        job = uuid.uuid4().hex
        with self.store.connection() as conn:
            row = conn.execute(f'''INSERT INTO scheduled_document_mail
                (id,kind,document_id,due,state,recipient,payload)
                VALUES ({self.p},{self.p},{self.p},{self.p},'pending',{self.p},{self.p})
                ON CONFLICT DO NOTHING RETURNING id''',
                (job, kind, doc, due, fields['recipient'], json.dumps(fields))).fetchone()
            if not row:
                raise ValueError('Ya hay un envío pendiente para este folio. Puedes cambiar su hora o cancelarlo.')
        return job

    def list(self, kind, doc):
        self.initialize()
        message_expr = ("payload::jsonb->>'body'" if self.store.dialect == 'postgres'
                        else "json_extract(payload, '$.body')")
        with self.store.connection() as conn:
            rows = conn.execute(f'''SELECT id,due,state,recipient,
                CASE WHEN state IN ('pending','sending') THEN {message_expr} END
                FROM scheduled_document_mail
                WHERE kind={self.p} AND document_id={self.p}
                ORDER BY CASE WHEN state IN ('pending','sending') THEN 0 ELSE 1 END, due DESC LIMIT 10''', (kind, doc)).fetchall()
        items = []
        for job, due, state, recipient, message in rows:
            item = dict(id=job, when=_label(due), state=state, recipient=recipient)
            if message is not None:
                item['message'] = message
            items.append(item)
        return items

    def change(self, kind, doc, job, due=None):
        self.initialize()
        if due is None:
            # A retry after losing the response should still confirm the saved cancellation.
            updates, args, states = "state='cancelled',payload=''", (), "('pending','cancelled')"
        else:
            updates, args, states = f'due={self.p}', (due,), "('pending')"
        with self.store.connection() as conn:
            return bool(conn.execute(f'''UPDATE scheduled_document_mail SET {updates}
                WHERE kind={self.p} AND document_id={self.p} AND id={self.p} AND state IN {states}''',
                (*args, kind, doc, job)).rowcount)

    def edit_message(self, kind, doc, job, message):
        self.initialize()
        update = ("jsonb_set(payload::jsonb, '{body}', to_jsonb(CAST(%s AS text)))::text"
                  if self.store.dialect == 'postgres' else "json_set(payload, '$.body', ?)")
        with self.store.connection() as conn:
            return bool(conn.execute(f'''UPDATE scheduled_document_mail SET payload={update}
                WHERE kind={self.p} AND document_id={self.p} AND id={self.p} AND state='pending' ''',
                (message, kind, doc, job)).rowcount)

    def claim(self, now):
        self.initialize()
        with self.store.connection() as conn:
            # After interruption we cannot know whether SMTP accepted it. Never resend it blindly.
            conn.execute(f"UPDATE scheduled_document_mail SET state='uncertain',payload='' WHERE state='sending' AND claimed<{self.p}", (now-900,))
            return conn.execute(f'''UPDATE scheduled_document_mail SET state='sending',claimed={self.p}
                WHERE id=(SELECT id FROM scheduled_document_mail WHERE state='pending' AND due<={self.p}
                          ORDER BY due,id LIMIT 1) AND state='pending'
                RETURNING id,kind,document_id,payload''', (now, now)).fetchone()

    def finish(self, job, state):
        with self.store.connection() as conn:
            conn.execute(f"UPDATE scheduled_document_mail SET state={self.p},payload='' WHERE id={self.p} AND state='sending'", (state, job))


def enqueue(kind, doc, when, *, recipient, cc, subject, body, folio, pdf_bytes, extra_attachments, xml_bytes=None):
    due = scheduled_time(when)
    queue = current_app.extensions['scheduled_email_queue']
    if not queue.store.enabled:
        raise ValueError('El envío programado requiere una base de datos persistente.')
    recipients = parse_recipients(recipient)
    recipient_keys = {email.casefold() for email in recipients}
    copies = [email for email in parse_optional_recipients(cc) if email.casefold() not in recipient_keys]
    if len(recipients) + len(copies) > 15:
        raise ValueError('Puedes enviar a un máximo de 15 destinatarios entre Para y CC.')
    if not subject.strip() or not body.strip():
        raise ValueError('Completa el asunto y el mensaje.')
    recipient, cc = ', '.join(recipients), ', '.join(copies)
    if not pdf_bytes or (kind == 'factura' and not xml_bytes):
        raise ValueError('No se encontraron los documentos necesarios para programar el correo.')
    if any(not a.get('data') for a in extra_attachments):
        raise ValueError('Uno de los archivos adjuntos está vacío.')
    if len(pdf_bytes) + len(xml_bytes or b'') + sum(len(a['data']) for a in extra_attachments) > 25 * 1024 * 1024:
        raise ValueError('El total de los archivos supera 25 MB para un envío programado.')
    encode = lambda data: base64.b64encode(data).decode('ascii')
    fields = dict(recipient=recipient, cc=cc, subject=subject, body=body, folio=folio,
                  pdf_bytes=encode(pdf_bytes), extra_attachments=[{**a, 'data':encode(a['data'])} for a in extra_attachments])
    if xml_bytes is not None:
        fields['xml_bytes'] = encode(xml_bytes)
    job = queue.add(kind, doc, due, fields)
    response = jsonify(ok=True, scheduled=True, id=job, message=f'Correo programado para el {_label(due)}. Destino: {recipient}.')
    token = trusted_device_token()
    if token:
        response.set_cookie('hsc_mail_trusted', token, max_age=315360000, secure=True,
                            httponly=True, samesite='Strict', path='/api')
    return response


@bp.route('/api/scheduled-email/<kind>/<doc>', methods=['GET', 'POST'])
def manage(kind, doc):
    if kind not in {'cotizacion', 'factura'}:
        return jsonify(ok=False, error='Tipo de documento inválido.'), 404
    queue = current_app.extensions['scheduled_email_queue']
    if not queue.store.enabled:
        return jsonify(ok=True, available=False, items=[])
    if request.method == 'GET':
        return jsonify(ok=True, available=True, items=queue.list(kind, doc))
    if not request.is_json:
        return jsonify(ok=False, error='Solicitud inválida.'), 415
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict) or body.get('action') not in ('cancel', 'reschedule', 'edit_message'):
        return jsonify(ok=False, error='Acción inválida.'), 400
    if not authorized_to_send(body.get('send_key'), request.cookies.get('hsc_mail_trusted', '')):
        return jsonify(ok=False, error='Escribe la clave privada de envío para modificar el correo.'), 403
    job = str(body.get('id') or '')
    if body['action'] == 'edit_message':
        message = body.get('message')
        if not isinstance(message, str) or not message.strip() or '\x00' in message:
            return jsonify(ok=False, error='Escribe un mensaje válido para el correo.'), 400
        changed = queue.edit_message(kind, doc, job, message)
    else:
        try:
            due = scheduled_time(body.get('when')) if body['action'] == 'reschedule' else None
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        changed = queue.change(kind, doc, job, due)
    if not changed:
        return jsonify(ok=False, error='El envío ya no está pendiente y no se puede modificar.',
                       items=queue.list(kind, doc)), 409
    return jsonify(ok=True, available=True, items=queue.list(kind, doc))


def deliver_one(queue, logger):
    row = queue.claim(int(datetime.now(UTC).timestamp()))
    if not row:
        return
    job,kind,doc,raw = row
    try:
        fields = json.loads(raw)
        for key in ('pdf_bytes', 'xml_bytes'):
            if key in fields:
                fields[key] = base64.b64decode(fields[key])
        for attachment in fields['extra_attachments']:
            attachment['data'] = base64.b64decode(attachment['data'])
        sender = send_cfdi_email if kind == 'factura' else send_quote_email
        result = sender(**fields, **email_thread_headers(kind, doc))
    except Exception:
        logger.exception('No se confirmó el correo programado %s', job)
        queue.finish(job, 'uncertain')
        return
    queue.finish(job, 'sent')
    record_email_delivery(kind, doc, recipients=result.get('recipients'), cc=result.get('cc'),
                          folio=fields['folio'], message_id=result.get('message_id') or '')


def install(app, store):
    queue = Queue(store)
    app.extensions['scheduled_email_queue'] = queue
    app.register_blueprint(bp)
    lock = threading.Lock()
    worker = None

    def run():
        delay = threading.Event()
        while True:
            try:
                with app.app_context():
                    deliver_one(queue, app.logger)
            except Exception:
                app.logger.exception('No se pudo revisar la cola de correos programados')
            delay.wait(30)

    @app.before_request
    def ensure_scheduled_mail_worker():
        nonlocal worker
        if app.testing or not store.enabled or (worker and worker.is_alive()):
            return
        with lock:
            if not worker or not worker.is_alive():
                worker = threading.Thread(target=run, name='scheduled-document-mail', daemon=True)
                worker.start()
