"""Isolated, loopback-only UI fixture. No credentials, production writes or Google."""
from pathlib import Path
import sys
import tempfile
from flask import Flask, jsonify, render_template, request, session, send_file, make_response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from operaciones_store import OperationsStore
fixture_folder = tempfile.TemporaryDirectory(prefix='hsc-worklists-test-')
store = OperationsStore(local_path=Path(fixture_folder.name) / 'fixture.db')
app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
app.secret_key = 'local-test-only'
app.config['TEMPLATES_AUTO_RELOAD'] = True
state = {'fail': False}
data = dict(ok=True, source='database', clients=[dict(id='TEST', name='Cliente local', policy_active=True, selected_round='1')],
            equipment=[dict(id='TEST1', client_id='TEST', name='Refrigerador local', status='Activo')],
            reports=[], tasks=[], expenses=[], faults=[], observation_options=[], stats={})
data['equipment'].append(dict(id='TEST2', client_id='TEST', name='Cámara local', status='Activo'))
store.import_matrix_snapshot(data)
from repair_bp import create_repair_blueprint
from payment_bp import create_payment_blueprint
import operation_payments as pay
from datetime import timedelta
day = pay.today()
last_week = pay.week_start(day) - timedelta(days=7)
with store.connection() as conn:
    for uid, name in [('PAY1','Técnico de prueba Uno'),('PAY2','Técnico de prueba Dos')]:
        conn.execute("INSERT INTO operations_users(id,name,role,created_at,updated_at) VALUES (?,?,'technician',?,?)", (uid,name,day.isoformat(),day.isoformat()))
pay.mutate(store, 'PAY1', dict(action='salary', mutation_id='fixture-salary', expected_revision=0,
                              weekly_amount='2000', effective_week=last_week.isoformat()), 'fixture', last_week)
for ident, uid, value, days, status in [('e1','PAY1',350,9,'Pendiente'),('e2','PAY1',120,2,'Aprobado'),('e3','PAY2',80,5,'Liquidado')]:
    store.save_expense(dict(id=ident,user_id=uid,technician_name='Técnico de prueba',expense_date=(day-timedelta(days=days)).isoformat(),amount=value,concept='Gasolina / compra de prueba',status=status,reimbursable=True))
photo_bytes = {}

def store_fixture_photo(repair_id, photo_id, content):
    key = repair_id + ':' + photo_id
    photo_bytes[key] = content
    return key

def fixture_photo(kind, key, ref):
    import io
    return send_file(io.BytesIO(photo_bytes[ref]), mimetype='image/jpeg')

@app.before_request
def fixture_identity():
    session.update(hsc_user_id='offline-fixture', hsc_user_name='Cuenta de prueba local')
    if state['fail'] and request.path.startswith('/api/'):
        return jsonify(ok=False, error='Servidor no disponible (prueba local)'), 503

app.register_blueprint(create_repair_blueprint(store, lambda:'admin', lambda *roles:None,
                                             lambda permission:None, store_fixture_photo, fixture_photo))
app.register_blueprint(create_payment_blueprint(store, lambda *roles:None))

@app.get('/hsc-tecnico/')
def shell():
    page = render_template('app_operativa_demo.html', operations_app_name='HSC Prueba local',
                          operations_role='admin', operations_is_owner=True, operations_user_id='offline-fixture',
                          operations_user_name='Cuenta de prueba local', operations_app_kind='technician',
                          operations_permissions={}, operations_manifest_url='/manifest.webmanifest')
    controls = '<aside style="position:relative;margin:100px 12px;background:white;color:black;padding:12px">Prueba local: <a href="/test-mode?fail=1">Simular servidor caído</a> | <a href="/test-mode?fail=0">Restaurar servidor</a> | <a href="/test-mode?ack=1">Perder siguiente confirmación de pago</a></aside>'
    return page.replace('</body>', controls + '</body>')

@app.get('/test-mode')
def test_mode():
    if 'fail' in request.args:
        state['fail'] = request.args.get('fail') == '1'
    state['ack'] = request.args.get('ack') == '1'
    return '<p>Servidor simulado: ' + ('caído' if state['fail'] else 'disponible') + '</p><a href="/hsc-tecnico/">Volver a la app</a>'

@app.after_request
def lose_payment_ack(response):
    if state.get('ack') and request.path.startswith('/api/operaciones/payments/') and request.method == 'POST' and response.status_code == 200:
        state['ack'] = False
        return make_response(jsonify(ok=False,error='Confirmación perdida después de guardar (prueba local)'),503)
    return response

@app.route('/api/operaciones/<path:path>', methods=['GET', 'POST'])
def api(path):
    if state['fail']:
        return jsonify(ok=False, error='Servidor no disponible (prueba local)'), 503
    if path == 'bootstrap':
        data['worklists'] = store.snapshot()['worklists']
        data['expenses'] = store.snapshot()['expenses']
        return jsonify(data)
    if path == 'users':
        return jsonify(ok=True, users=store.list_users())
    if path.startswith('expenses/') and path.endswith('/status'):
        return jsonify(ok=True,expense=store.update_expense_status(path.split('/')[1],request.json['status'],admin_notes=request.json.get('admin_notes','')))
    if path == 'worklists' and request.method == 'POST':
        try:
            result = store.save_worklist(request.json, actor_id='offline-fixture', is_admin=True)
            return jsonify(ok=True, worklist=result)
        except (ValueError, PermissionError) as exc:
            return jsonify(ok=False, error=str(exc)), 409
    if path == 'equipment' and request.method == 'POST':
        for row in request.json['items']:
            old = next((item for item in data['equipment'] if item['id'] == row['id']), None)
            if old is None:
                data['equipment'].append(row)
            else:
                old.update(row)
        return jsonify(ok=True)
    if path == 'profile':
        return jsonify(ok=True, profile=dict(name='Cuenta de prueba local'))
    return jsonify(ok=True, users=[], notifications=[], unread_count=0, draft=None)

@app.get('/manifest.webmanifest', endpoint='pwa_manifest')
def manifest():
    return jsonify(name='HSC Prueba', start_url='/hsc-tecnico/', display='standalone')

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8766, use_reloader=False)
