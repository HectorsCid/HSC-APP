"""Isolated, loopback-only UI fixture. No credentials, production writes or Google."""
from pathlib import Path
import sys
import tempfile
from flask import Flask, jsonify, render_template, request, session, send_file

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from operaciones_store import OperationsStore
fixture_folder = tempfile.TemporaryDirectory(prefix='hsc-worklists-test-')
store = OperationsStore(local_path=Path(fixture_folder.name) / 'fixture.db')
app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
app.secret_key = 'local-test-only'
state = {'fail': False}
data = dict(ok=True, source='database', clients=[dict(id='TEST', name='Cliente local', policy_active=True, selected_round='1')],
            equipment=[dict(id='TEST1', client_id='TEST', name='Refrigerador local', status='Activo')],
            reports=[], tasks=[], expenses=[], faults=[], observation_options=[], stats={})
data['equipment'].append(dict(id='TEST2', client_id='TEST', name='Cámara local', status='Activo'))
store.import_matrix_snapshot(data)
from repair_bp import create_repair_blueprint
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

@app.get('/hsc-tecnico/')
def shell():
    page = render_template('app_operativa_demo.html', operations_app_name='HSC Prueba local',
                          operations_role='admin', operations_is_owner=True, operations_user_id='offline-fixture',
                          operations_user_name='Cuenta de prueba local', operations_app_kind='technician',
                          operations_permissions={}, operations_manifest_url='/manifest.webmanifest')
    controls = '<aside style="position:fixed;bottom:90px;right:10px;z-index:99999;background:white;color:black;padding:12px">Prueba local: <a href="/test-mode?fail=1">Simular servidor caído</a> | <a href="/test-mode?fail=0">Restaurar servidor</a></aside>'
    return page.replace('</body>', controls + '</body>')

@app.get('/test-mode')
def test_mode():
    state['fail'] = request.args.get('fail') == '1'
    return '<p>Servidor simulado: ' + ('caído' if state['fail'] else 'disponible') + '</p><a href="/hsc-tecnico/">Volver a la app</a>'

@app.route('/api/operaciones/<path:path>', methods=['GET', 'POST'])
def api(path):
    if state['fail']:
        return jsonify(ok=False, error='Servidor no disponible (prueba local)'), 503
    if path == 'bootstrap':
        data['worklists'] = store.snapshot()['worklists']
        return jsonify(data)
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
