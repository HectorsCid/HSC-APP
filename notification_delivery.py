"""Cola persistente de push y suscripciones vinculadas a la sesión autenticada."""
import json
import os
import threading
import time
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, session
import notification_center as notices

bp = Blueprint('notification_delivery', __name__, url_prefix='/api/operaciones/avisos')
_START_LOCK = threading.Lock()
_DRAIN_LOCK = threading.Lock()
_STARTED = False
_WAKE = threading.Event()


def identity():
    return dict(role=session.get('hsc_role', ''),
                client_id=session.get('hsc_client_id', ''), user_id=session.get('hsc_user_id', ''))


@bp.before_request
def require_identity():
    if not session.get('hsc_authenticated') or identity()['role'] not in {'admin', 'client', 'technician'}:
        return jsonify(ok=False, error='Inicia sesión para consultar tus avisos.'), 401


def configured():
    return bool(os.getenv('VAPID_PUBLIC_KEY') and os.getenv('VAPID_PRIVATE_KEY'))


def enqueue(title, body, url, tag, category='sistema', audience='admin', client_id='', user_id='', endpoint=None):
    if audience == 'client' and not client_id:
        raise ValueError('Los avisos Partner requieren una empresa destinataria.')
    notices.publish(title, body, url=url, key=tag, category=category,
                    audience=audience, client_id=client_id, user_id=user_id)
    with notices.LOCK:
        state = notices._read()
        queue = state.setdefault('push_queue', [])
        if any(row['tag'] == tag for row in queue):
            return {'queued': False, 'configured': configured(), 'sent': 0}
        targets = [device for device in state.get('push_devices', [])
                   if device['role'] == audience
                   and (not client_id or device.get('client_id') == client_id)
                   and (not user_id or device.get('user_id') == user_id)
                   and (not endpoint or device['endpoint'] == endpoint)]
        queue.append(dict(tag=tag, title=str(title)[:160], body=str(body)[:800], url=url,
                          created=time.time(), targets=[dict(endpoint=d['endpoint'], user_id=d['user_id'],
                          role=d['role'], client_id=d.get('client_id',''), status='pending', attempts=0, next_at=0)
                          for d in targets]))
        state['push_queue'] = queue[-2000:]
        notices._write(state)
    _WAKE.set()
    return {'queued': bool(targets), 'devices': len(targets), 'configured': configured(), 'sent': 0}


def drain():
    if not _DRAIN_LOCK.acquire(blocking=False):
        return
    try:
        _drain()
    finally:
        _DRAIN_LOCK.release()


def _drain():
    if not configured():
        return
    from pywebpush import webpush
    with notices.LOCK:
        state = notices._read()
        jobs = json.loads(json.dumps(state.get('push_queue', [])))
    processed = 0
    for job in jobs:
        for target in job['targets']:
            if target['status'] != 'pending' or target['next_at'] > time.time():
                continue
            with notices.LOCK:
                state = notices._read()
                device = next((d for d in state.get('push_devices', []) if d['endpoint'] == target['endpoint']
                               and all(d.get(k,'') == target.get(k,'') for k in ('user_id','role','client_id'))), None)
            status, error = 'accepted', ''
            if device and device['role'] != 'admin':
                from app import OPERACIONES_STORE
                user = OPERACIONES_STORE.get_user_by_id(device['user_id']) if OPERACIONES_STORE.enabled else None
                if not user or user.get('status') != 'active' or user.get('role') != device['role'] or (device['role']=='client' and user.get('client_id')!=device.get('client_id')):
                    device = None
            if not device:
                status, error = 'cancelled', 'El dispositivo cambió de cuenta o fue desvinculado.'
            elif time.time() - job['created'] > 86400:
                status, error = 'failed', 'El aviso venció después de 24 horas.'
            else:
                try:
                    webpush(subscription_info=device['subscription'],
                            data=json.dumps({k:job[k] for k in ('title','body','url','tag')},ensure_ascii=False),
                            vapid_private_key=os.getenv('VAPID_PRIVATE_KEY',''),
                            vapid_claims={'sub':os.getenv('VAPID_SUBJECT','mailto:hectors@hscrefrigeracion.com')},
                            ttl=86400, timeout=15)
                except Exception as exc:
                    code = getattr(getattr(exc, 'response', None), 'status_code', None)
                    status = 'expired' if code in (404,410) else 'pending'
                    error = f'Canal push rechazó el intento ({code or "sin respuesta"}).'
            with notices.LOCK:
                state = notices._read()
                live = next((j for j in state.get('push_queue',[]) if j['tag']==job['tag']), None)
                actual = next((t for t in live['targets'] if t['endpoint']==target['endpoint']), None) if live else None
                if actual:
                    attempts = actual['attempts'] + 1
                    if status == 'pending' and attempts >= 8:
                        status = 'failed'
                    actual.update(status=status, attempts=attempts, error=error,
                                  next_at=time.time()+min(3600,30*2**attempts), updated_at=time.time())
                    if status == 'expired':
                        state['push_devices']=[d for d in state.get('push_devices',[]) if not (
                            d['endpoint']==target['endpoint'] and d.get('user_id')==target.get('user_id'))]
                    notices._write(state)
            processed += 1
            if processed >= 20:
                return


def start(app):
    global _STARTED
    if app.testing:
        return
    with _START_LOCK:
        if _STARTED:
            return
        _STARTED = True
    def run():
        while True:
            try:
                with app.app_context():
                    drain()
            except Exception:
                app.logger.exception('No se pudo procesar la cola de avisos')
            _WAKE.wait(30)
            _WAKE.clear()
    threading.Thread(target=run, daemon=True, name='hsc-push-delivery').start()


@bp.get('/config')
def config():
    return jsonify(ok=True, enabled=configured(), public_key=os.getenv('VAPID_PUBLIC_KEY',''))


@bp.post('/subscribe')
def subscribe():
    subscription = (request.get_json(silent=True) or {}).get('subscription') or {}
    endpoint = str(subscription.get('endpoint') or '')
    if not endpoint.startswith('https://') or not all((subscription.get('keys') or {}).get(k) for k in ('auth','p256dh')):
        return jsonify(ok=False,error='Suscripción inválida.'),400
    with notices.LOCK:
        state = notices._read()
        devices = [d for d in state.get('push_devices',[]) if d['endpoint'] != endpoint]
        devices.append(dict(endpoint=endpoint, subscription=subscription, **identity(), updated_at=time.time()))
        state['push_devices'] = devices
        notices._write(state)
    session['hsc_push_endpoints']=list(set(session.get('hsc_push_endpoints',[])+[endpoint]))[-6:]
    return jsonify(ok=True)


def revoke_session_devices():
    endpoints=session.get('hsc_push_endpoints',[])
    if not endpoints:
        return
    with notices.LOCK:
        state=notices._read()
        state['push_devices']=[d for d in state.get('push_devices',[]) if not (
            d['endpoint'] in endpoints and d.get('user_id')==session.get('hsc_user_id'))]
        notices._write(state)


@bp.get('')
def inbox():
    who = identity()
    result = notices.snapshot(**who)
    with notices.LOCK:
        state = notices._read()
        devices = [d for d in state.get('push_devices',[]) if all(d.get(k,'')==v for k,v in who.items())]
        deliveries = [dict(title=j['title'],status=t['status'],attempts=t['attempts'],error=t.get('error',''))
                      for j in reversed(state.get('push_queue',[])) for t in j['targets']
                      if all(t.get(k,'')==v for k,v in who.items())][:30]
    return jsonify(ok=True, **result, push=dict(configured=configured(), devices=len(devices), deliveries=deliveries))


@bp.post('/<item_id>/read')
def read(item_id):
    found, _ = notices.mark_read(item_id, **identity())
    return jsonify(ok=found), 200 if found else 404


@bp.post('/test')
def test():
    endpoint = (request.get_json(silent=True) or {}).get('endpoint')
    who = identity()
    with notices.LOCK:
        devices=notices._read().get('push_devices',[])
        if not endpoint or not any(d['endpoint']==endpoint and all(d.get(k,'')==v for k,v in who.items()) for d in devices):
            return jsonify(ok=False,error='Activa primero los avisos de este dispositivo.'),400
    home = '/hsc-partner/' if who['role']=='client' else '/hsc-tecnico/' if who['role']=='technician' else '/inicio-app'
    result = enqueue('Prueba desde el servidor HSC','Este aviso fue enviado por el servidor a tu dispositivo.',home,
                     'test:'+str(uuid4()),audience=who['role'],client_id=who['client_id'],user_id=who['user_id'],endpoint=endpoint)
    return jsonify(ok=True, **result)
