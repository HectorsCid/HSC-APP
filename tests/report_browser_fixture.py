"""Loopback-only manual/browser fixture for real report routes and templates.

All records and photos are synthetic, SQLite is temporary, and outgoing network
connections are blocked by measure_incident_memory.load_app.
"""
import io
import atexit
import os
import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'venv311/Lib/site-packages')]
from measure_incident_memory import load_app
from flask import request, session, send_file
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument('--state-dir', help='Optional directory for synthetic test state across restarts')
parser.add_argument('--port', type=int, default=8767)
args = parser.parse_args() if __name__ == '__main__' else parser.parse_args([])
scratch = tempfile.TemporaryDirectory(prefix='hsc-report-browser-')
atexit.register(lambda: os.chdir(ROOT))
state_dir = Path(args.state_dir).resolve() if args.state_dir else Path(scratch.name)
state_dir.mkdir(parents=True, exist_ok=True)
module, store = load_app(str(state_dir))
store.import_matrix_snapshot(dict(clients=[dict(id='TEST', name='Cliente de prueba', policy_active=True, selected_round='1')],
    equipment=[dict(id='TEST1', client_id='TEST', name='Equipo de prueba', status='Activo')], reports=[], faults=[]))
app = module.app
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
photo_dir = state_dir / 'photos'
photo_dir.mkdir(exist_ok=True)
photos = {path.stem: path.read_bytes() for path in photo_dir.glob('*.jpg')}


def save_photo(client, report, position, content, *, upload_id=''):
    import hashlib
    key = hashlib.sha256(upload_id.encode() + content).hexdigest()
    photos[key] = content
    (photo_dir / (key + '.jpg')).write_bytes(content)
    return dict(drive_ref=key, storage_ref=key)


module.store_operations_evidence = save_photo
module.serve_drive_image_ref_fast = lambda ref: send_file(io.BytesIO(photos[ref]), mimetype='image/jpeg')
module._schedule_operations_sync = lambda **kwargs: None
module._notify_operations_fault = lambda fault: None
import notification_delivery
notification_delivery.enqueue = lambda *args, **kwargs: None


@app.before_request
def synthetic_identity():
    identity = 'TECH-B' if request.host.startswith('localhost') else 'TECH-A'
    session.update(hsc_authenticated=True, hsc_user_id=identity, hsc_user_name=identity,
                   hsc_role='admin', hsc_permissions={'createReports': True})


@app.get('/test/photo.jpg')
def sample_photo():
    out = io.BytesIO()
    width, height = int(request.args.get('width', 1600)), int(request.args.get('height', 1200))
    if min(width, height) <= 0 or width * height > 24_000_000:
        return 'Synthetic image dimensions out of range', 400
    with Image.new('RGB', (width, height), '#487fad') as image:
        image.save(out, 'JPEG')
    out.seek(0)
    return send_file(out, mimetype='image/jpeg')


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=args.port, use_reloader=False, threaded=True)
