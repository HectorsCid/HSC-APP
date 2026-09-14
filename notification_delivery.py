"""Cola persistente de push y suscripciones vinculadas a la sesión autenticada."""
import json
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, session
import notification_center as notices

bp = Blueprint('notification_delivery', __name__, url_prefix='/api/operaciones/avisos')
_START_LOCK = threading.Lock()
_DRAIN_LOCK = threading.Lock()
_STARTED = False
_WAKE = threading.Event()
_DOCUMENT_SCAN_AT = 0
_TASK_REMINDER_SCAN_AT = 0


def identity():
    role = str(session.get('hsc_role') or '').strip().lower()
    if role not in {'admin','client','technician'}:
        role = 'admin' if session.get('hsc_authenticated') is True else ''
    return dict(role=role, client_id=session.get('hsc_client_id', ''),
                user_id=session.get('hsc_user_id') or ('owner' if role=='admin' else ''))


@bp.before_request
def require_identity():
    if not session.get('hsc_authenticated') or identity()['role'] not in {'admin', 'client', 'technician'}:
        return jsonify(ok=False, error='Inicia sesión para consultar tus avisos.'), 401


def configured():
    return bool(os.getenv('VAPID_PUBLIC_KEY') and os.getenv('VAPID_PRIVATE_KEY'))


NOTICE_OPTIONS = {
    'admin': {'agenda':'Recordatorios de pendientes','fallas':'Fallas nuevas y resueltas','gastos':'Gastos nuevos de técnicos','facturas':'Facturas programadas','respaldos':'Respaldos','reportes':'Reportes y generación de PDF'},
    'technician': {'agenda':'Recordatorios de mis pendientes','fallas':'Fallas por atender','pagos':'Estado de mis gastos y reembolsos'},
    'client': {'fallas':'Fallas resueltas','agenda':'Actividades y visitas programadas','cotizaciones':'Cotizaciones disponibles','facturas':'Facturas y cambios de estado','complementos':'Complementos de pago','reportes':'Reportes terminados'},
}


def notification_preferences(user_id):
    with notices.LOCK:
        return dict(notices._read().get('notification_preferences', {}).get(user_id, {}))


def accepts_notice(user_id, category):
    if category == 'correo':
        return False  # Módulo retirado; tampoco entregar avisos IMAP que quedaron en cola.
    return notification_preferences(user_id).get(category, True) is not False


@bp.route('/preferences/<user_id>', methods=['GET','POST'])
def manage_preferences(user_id):
    if identity()['role'] != 'admin':
        return jsonify(ok=False,error='Sólo administración puede cambiar estas opciones.'),403
    from app import OPERACIONES_STORE
    user = {'role':'admin'} if user_id=='owner' else OPERACIONES_STORE.get_user_by_id(user_id)
    if not user:
        return jsonify(ok=False,error='Cuenta inexistente.'),404
    options=NOTICE_OPTIONS.get(user['role'], {})
    if request.method=='POST':
        values=(request.get_json(silent=True) or {}).get('notifications', {})
        if not isinstance(values,dict) or any(type(v) is not bool for v in values.values()):
            return jsonify(ok=False,error='Opciones inválidas.'),400
        with notices.LOCK:
            state=notices._read()
            state.setdefault('notification_preferences', {})[user_id]={k:values.get(k,True) for k in options}
            notices._write(state)
    return jsonify(ok=True,options=options,notifications=notification_preferences(user_id))


def enqueue(title, body, url, tag, category='sistema', audience='admin', client_id='', user_id='', endpoint=None, exclude_user_id=''):
    if audience == 'client' and not client_id:
        raise ValueError('Los avisos Partner requieren una empresa destinataria.')
    if audience == 'technician' and category == 'pagos' and not user_id:
        raise ValueError('Los avisos de reembolsos requieren el técnico destinatario.')
    notices.publish(title, body, url=url, key=tag, category=category,
                    audience=audience, client_id=client_id, user_id=user_id, exclude_user_id=exclude_user_id)
    with notices.LOCK:
        state = notices._read()
        queue = state.setdefault('push_queue', [])
        if any(row['tag'] == tag for row in queue):
            return {'queued': False, 'configured': configured(), 'sent': 0}
        targets = [device for device in state.get('push_devices', [])
                   if device['role'] == audience
                   and (not exclude_user_id or device['user_id'] != exclude_user_id)
                   and (not client_id or device.get('client_id') == client_id)
                   and (not user_id or device.get('user_id') == user_id)
                   and (not endpoint or device['endpoint'] == endpoint)
                   and accepts_notice(device['user_id'], category)]
        queue.append(dict(tag=tag, category=category, title=str(title)[:160], body=str(body)[:800], url=url,
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
            if device and not accepts_notice(device['user_id'], job.get('category','sistema')):
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
                    scan_task_reminders(app)
                    drain()
                scan_partner_documents(app)
            except Exception:
                app.logger.exception('No se pudo procesar la cola de avisos')
            _WAKE.wait(30)
            _WAKE.clear()
    threading.Thread(target=run, daemon=True, name='hsc-push-delivery').start()


def send_task_reminders(store, now):
    """Resumen diario por destinatario, con registro persistente entre reinicios."""
    if now.hour < 8:
        return
    day = now.date().isoformat()
    users = {u['id']:u for u in store.list_users() if u.get('status')=='active'}
    partner_clients = {u.get('client_id') for u in users.values() if u.get('role')=='client' and u.get('client_id')}
    groups = {}
    for task in store.tasks_for_reminders(day):
        client_id = task.get('client_id')
        if client_id in partner_clients:
            groups.setdefault(('client',client_id), {})[task['id']] = task
        recipients = set(task.get('assigned_user_ids') or [])
        if task.get('created_by'):
            recipients.add(task['created_by'])
        for user_id in recipients:
            role = 'admin' if user_id=='owner' else users.get(user_id,{}).get('role')
            if role in {'admin','technician'}:
                groups.setdefault((role,user_id), {})[task['id']] = task
    from urllib.parse import urlencode
    for (role,recipient), indexed in groups.items():
        tag=f'task-reminder:{day}:{role}:{recipient}'
        with notices.LOCK:
            if tag in notices._read().get('task_reminder_days',{}):
                continue
        tasks=list(indexed.values())
        title=('Hoy tienes visita' if len(tasks)==1 else f'Hoy tienes {len(tasks)} visitas programadas') if role=='client' else ('Hoy tienes un pendiente' if len(tasks)==1 else f'Hoy tienes {len(tasks)} pendientes')
        body=' · '.join(f"{t.get('scheduled_time') or 'Sin hora'}: {t['title']}" for t in tasks[:5])
        if len(tasks)>5:
            body+=f' · y {len(tasks)-5} más. Consulta tu calendario.'
        params={'open':'calendar','date':day}
        if role=='client':params['client']=recipient
        enqueue(title,body,('/hsc-partner/?' if role=='client' else '/hsc-tecnico/?')+urlencode(params),tag,
                category='agenda',audience=role,client_id=recipient if role=='client' else '',user_id='' if role=='client' else recipient)
        with notices.LOCK:
            state=notices._read()
            ledger=state.setdefault('task_reminder_days',{})
            ledger[tag]=day
            cutoff=(now.date()-timedelta(days=14)).isoformat()
            state['task_reminder_days']={key:date for key,date in ledger.items() if date>=cutoff}
            notices._write(state)


def scan_task_reminders(app):
    global _TASK_REMINDER_SCAN_AT
    if time.time() < _TASK_REMINDER_SCAN_AT:
        return
    _TASK_REMINDER_SCAN_AT=time.time()+60
    try:
        from app import OPERACIONES_STORE
        if OPERACIONES_STORE.enabled:
            send_task_reminders(OPERACIONES_STORE,datetime.now(ZoneInfo('America/Mexico_City')))
    except Exception:
        app.logger.exception('No se pudieron preparar los recordatorios de agenda')


def observe_partner_documents(client_id, payload):
    """Primera lectura establece la base; las siguientes notifican novedades."""
    import hashlib
    events=[]
    for group,category,label in [('quotes','cotizaciones','Cotización'),('invoices','facturas','Factura'),('complements','complementos','Complemento de pago')]:
        for row in payload.get(group,[]):
            if not row.get('id'):
                continue
            key=f"{category}:{row['id']}"
            signature=json.dumps({k:row.get(k) for k in ('status','status_label','active','cancellation_status','paid','total','amount','description')},sort_keys=True,ensure_ascii=False)
            events.append((key,hashlib.sha256(signature.encode()).hexdigest(),category,label,row))
    with notices.LOCK:
        state=notices._read()
        baseline=state.setdefault('partner_document_baselines',{})
        previous=baseline.get(client_id)
    if previous is not None:
        from urllib.parse import urlencode
        for key,signature,category,label,row in events:
            if previous.get(key)==signature:
                continue
            enqueue(f'{label} disponible' if key not in previous else f'{label} actualizada',
                    f"{row.get('folio') or row.get('reference') or row['id']} · {row.get('description') or row.get('status_label') or row.get('status') or 'Consulta el documento en tu cuenta'}",
                    '/hsc-partner/?'+urlencode({'client':client_id,'open':'documents'}),
                    f'document:{client_id}:{key}:{signature}',category=category,audience='client',client_id=client_id)
    with notices.LOCK:
        state=notices._read()
        state.setdefault('partner_document_baselines',{})[client_id]={key:signature for key,signature,*_ in events}
        notices._write(state)


def scan_partner_documents(app):
    global _DOCUMENT_SCAN_AT
    if time.time() < _DOCUMENT_SCAN_AT:
        return
    _DOCUMENT_SCAN_AT=time.time()+300
    from app import OPERACIONES_STORE, _partner_documents_payload
    if not OPERACIONES_STORE.enabled:
        return
    client_ids={u['client_id'] for u in OPERACIONES_STORE.list_users() if u['role']=='client' and u['status']=='active' and u.get('client_id')}
    if not client_ids:
        return
    for client in OPERACIONES_STORE.snapshot().get('clients',[]):
        if client['id'] not in client_ids:
            continue
        try:
            with app.test_request_context('/'):
                payload=_partner_documents_payload(client)
            observe_partner_documents(client['id'],payload)
        except Exception:
            app.logger.exception('No se pudieron revisar los documentos Partner de %s',client['id'])


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
    preferences=notification_preferences(who['user_id'])
    result['items']=[row for row in result['items'] if row.get('category') != 'correo' and preferences.get(row.get('category'),True) is not False]
    result['unread']=sum(not row.get('read') for row in result['items'])
    with notices.LOCK:
        state = notices._read()
        devices = [d for d in state.get('push_devices',[]) if all(d.get(k,'')==v for k,v in who.items())]
        deliveries = [dict(title=j['title'],status=t['status'],attempts=t['attempts'],error=t.get('error',''))
                      for j in reversed(state.get('push_queue',[])) for t in j['targets']
                      if all(t.get(k,'')==v for k,v in who.items())][:30]
    return jsonify(ok=True, role=who['role'], categories=NOTICE_OPTIONS.get(who['role'], {}), **result, push=dict(configured=configured(), devices=len(devices), deliveries=deliveries))


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


@bp.delete('/<item_id>')
def delete_notice(item_id):
    notices.delete_for_user([item_id], **identity())
    return jsonify(ok=True)


@bp.post('/delete-many')
def delete_many_notices():
    ids=(request.get_json(silent=True) or {}).get('ids', [])
    if not isinstance(ids,list) or len(ids)>2000 or any(not isinstance(value,str) for value in ids):
        return jsonify(ok=False,error='Selección inválida.'),400
    count=notices.delete_for_user(ids, **identity())
    return jsonify(ok=True,deleted=count)
