"""Smoke-test the real Flask app in a temporary DB with networking disabled."""
import re
from unittest.mock import patch

import report_browser_fixture as fixture

app, module, store = fixture.app, fixture.module, fixture.store
client = app.test_client()
for kind in ('technician', 'partner'):
    with patch.object(store, 'snapshot', side_effect=AssertionError('Public shell must not load account data')):
        response = client.get('/offline/' + kind)
    assert response.status_code == 200, response.status_code
    page = response.get_data(as_text=True)
    assert 'data-user-id=""' in page
    assert 'data-user-name=""' in page
    assert 'data-operations-owner="false"' in page
    assert 'data-offline-shell="true"' in page

source = (fixture.ROOT / 'static/service-worker.js').read_text(encoding='utf-8')
assets = re.findall(r"'([^']+)'", source.split('const SAFE_ASSETS = [', 1)[1].split('];', 1)[0])
for asset in assets:
    response = client.get(asset)
    assert response.status_code == 200, (asset, response.status_code)

with patch.object(module, '_app_access_password', return_value='test-password'):
    wrong = client.get('/api/operaciones/bootstrap', headers={'X-HSC-Account': 'OTHER-ACCOUNT'})
    assert wrong.status_code == 401, wrong.status_code
    assert wrong.get_json()['code'] == 'account_changed'
    correct = client.get('/api/operaciones/bootstrap', headers={'X-HSC-Account': 'TECH-B'})
    assert correct.status_code == 200

# Saturated image worker must never send the uncompressed original as a thumbnail.
with app.test_request_context('/'), \
        patch.object(module, 'serve_drive_image_ref_fast', return_value=module.make_response(b'fake-original')), \
        patch.object(module, 'prepare_photo', side_effect=RuntimeError('worker busy')):
    response = module._serve_operations_thumbnail('equipment', 'TEST1', 'test-photo')
    assert response.status_code == 503
    assert len(response.get_data()) < 1024
    assert response.headers['Retry-After'] == '2'
print(f'PASS: 2 neutral offline shells, {len(assets)} cached assets, account binding and small thumbnail failure.')
