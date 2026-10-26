"""Owner diagnostics: read-only probes, no document emission or email delivery."""
import secrets
import smtplib
import ssl
import threading
import time
from datetime import datetime, timezone
from memory_diagnostics import MONITOR, read_events, memory_span


def check_smtp():
    from smtp_mailer import smtp_config
    cfg = smtp_config()
    if not cfg['configured']:
        return dict(ok=False, status='not_configured', message='Falta configurar el correo de salida en el servidor.')
    try:
        # Exactly the same TLS/login mode as sending, but no MAIL/RCPT/DATA.
        with smtplib.SMTP_SSL(cfg['host'], cfg['port'], context=ssl.create_default_context(), timeout=8) as server:
            server.login(cfg['username'], cfg['password'])
        return dict(ok=True, status='authenticated', message='El servidor aceptó el usuario y la contraseña. No se envió ningún correo.')
    except smtplib.SMTPAuthenticationError:
        return dict(ok=False, status='authentication_failed', message='El servidor rechazó la autenticación. Revisa contraseña, usuario o restricciones de la cuenta.')
    except ssl.SSLError:
        return dict(ok=False, status='tls_failed', message='No se pudo establecer la conexión segura con el correo.')
    except (TimeoutError, OSError, smtplib.SMTPException):
        return dict(ok=False, status='connection_failed', message='No se pudo completar la conexión con el correo. Puede ser red o disponibilidad del proveedor.')


def check_facturama():
    from facturacion_bp import _facturama_config, _fm_request
    from requests import HTTPError, RequestException
    if not _facturama_config()['configured']:
        return dict(ok=False, status='not_configured', message='Facturama no está configurado en el servidor.')
    try:
        _fm_request('GET', '/TaxEntity', timeout=8)
        return dict(ok=True, status='connected', message='Facturama respondió y aceptó el acceso. No se timbró ni modificó ningún comprobante.')
    except HTTPError as exc:
        code = getattr(exc.response, 'status_code', None)
        return dict(ok=False, status='provider_error', http_status=code,
                    message='Facturama rechazó la consulta; revisa el acceso o el estado del proveedor.')
    except (RequestException, OSError):
        return dict(ok=False, status='connection_failed', message='No se pudo conectar con Facturama dentro del tiempo de prueba.')


def create_diagnostics_blueprint(store, forbidden, *, smtp_probe=check_smtp, billing_probe=check_facturama):
    from flask import Blueprint, jsonify, render_template, request, session
    bp = Blueprint('hsc_diagnostics', __name__)
    lock = threading.Lock()
    last = {}

    @bp.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get('/diagnostico')
    def page():
        denied = forbidden('admin')
        if denied is not None:
            return denied
        session.setdefault('hsc_diagnostics_csrf', secrets.token_urlsafe(32))
        return render_template('diagnostics.html', csrf=session['hsc_diagnostics_csrf'])

    @bp.get('/api/diagnostico/estado')
    def state():
        denied = forbidden('admin')
        if denied is not None:
            return denied
        database = dict(ok=False, message='La base operativa no está configurada.')
        events, persisted = [], False
        try:
            if store.enabled:
                store.initialize()
                with store.connection() as conn:
                    conn.execute('SELECT 1').fetchone()
                database = dict(ok=True, message='La base de datos respondió.')
                events = read_events(store)
                persisted = True
        except Exception:
            database = dict(ok=False, message='La base de datos no respondió. El historial persistente puede no estar disponible.')
        with MONITOR.lock:
            active = [dict(operation=row['operation'], seconds=round(time.monotonic()-row['started'], 1))
                      for row in MONITOR.active.values()]
            recent = list(MONITOR.recent)
        # Merge pending local events with durable records; no growing history in RAM.
        merged = {(e['instance'], e['at'], e['operation'], e['phase'], e.get('span')): e for e in events + recent}
        events = sorted(merged.values(), key=lambda e: e['at'], reverse=True)[:100]
        return jsonify(ok=True, at=datetime.now(timezone.utc).isoformat(), instance=MONITOR.instance,
                       memory=MONITOR.safe_sample(), active=active, events=events, database=database,
                       diagnostics_enabled=MONITOR.enabled(), persisted=persisted,
                       persistence_ok=MONITOR.persistence_ok, dropped=MONITOR.dropped,
                       warning='Los picos son del contenedor completo. Si coinciden tareas, no atribuyen por sí solos la causa a una sola operación.')

    @bp.post('/api/diagnostico/probar/<kind>')
    def probe(kind):
        denied = forbidden('admin')
        if denied is not None:
            return denied
        body = request.get_json(silent=True) if request.is_json else None
        if not isinstance(body, dict) or not secrets.compare_digest(str(body.get('csrf', '')), str(session.get('hsc_diagnostics_csrf', ''))):
            return jsonify(ok=False, error='Recarga el diagnóstico para validar la solicitud.'), 403
        if not session.get('hsc_diagnostics_csrf'):
            return jsonify(ok=False, error='Primero abre el diagnóstico.'), 403
        if kind == 'detener':
            MONITOR.paused = True
            return jsonify(ok=True, status='paused', message='Medición detallada detenida hasta el próximo arranque del servidor. El historial se conserva.', at=datetime.now(timezone.utc).isoformat())
        if kind not in {'correo', 'facturama'}:
            return jsonify(ok=False, error='Prueba no válida.'), 400
        if not lock.acquire(blocking=False):
            return jsonify(ok=False, error='Ya hay una prueba en curso. Espera a que termine.'), 409
        try:
            if time.monotonic() - last.get(kind, float('-inf')) < 30:
                return jsonify(ok=False, error='Espera 30 segundos antes de repetir esta prueba.'), 429
            last[kind] = time.monotonic()
            with memory_span('smtp_check' if kind == 'correo' else 'facturama_check'):
                try:
                    result = (smtp_probe if kind == 'correo' else billing_probe)()
                except Exception:
                    result = dict(ok=False, status='check_failed', message='La prueba no pudo completarse. No se envió ni modificó ningún documento.')
            result['at'] = datetime.now(timezone.utc).isoformat()
            if MONITOR.enabled():
                MONITOR.emit('smtp_check' if kind == 'correo' else 'facturama_check', result['status'])
            return jsonify(result)
        finally:
            lock.release()
    return bp
