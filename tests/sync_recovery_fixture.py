"""Local-only browser fixture: synthetic owner accounts and a fake Google writer."""
import argparse
import atexit
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'venv311/Lib/site-packages')]
from measure_incident_memory import load_app
from flask import request,session,jsonify
from operaciones_sync import sync_operations_outbox
from test_operaciones_sync import FakeSheets

parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8789)
args=parser.parse_args()
scratch=tempfile.TemporaryDirectory(prefix='hsc-sync-browser-')
atexit.register(lambda:os.chdir(ROOT))
module,store=load_app(scratch.name);app=module.app
module.SHEET_ID='SYNTHETIC-SHEET';module.OPERACIONES_SHEETS_SYNC_ENABLED=True
module.OPERACIONES_MATRIX_AUTO_SYNC=False
fake=FakeSheets()
module._schedule_operations_sync=lambda **kw: False

@app.before_request
def identity():
    session.update(hsc_authenticated=True,hsc_role='admin',hsc_user_id='owner',hsc_user_name='Propietario de prueba')

def seed():
    store.import_matrix_snapshot(dict(clients=[dict(id='TEST',name='Cliente de prueba')],equipment=[dict(id='TEST1',client_id='TEST',name='Equipo de prueba')],reports=[],faults=[]))
    draft=store.save_report_draft(dict(client_id='TEST',equipment_id='TEST1',round='1',payload={'p1':'120','inicio':'2026-09-28','fin':'2026-09-28'}))
    report=store.finalize_report(draft['id'])
    operation=store.pending_sync()[0]
    try:
        with store.sheet_write_guard(operation,module.SHEET_ID,store.get_report_detail(report['id'])['revision']) as delivery:
            delivery['sent']=True
            raise TimeoutError('Se perdió la respuesta de Google durante el envío de prueba.')
    except TimeoutError:pass
    return operation

operation=seed()

@app.post('/test/new-attempt')
def replace_attempt():
    state=store.sheet_delivery_status(module.SHEET_ID)
    store.resolve_sheet_delivery(module.SHEET_ID,state['operation_id'],attempt_id=state['attempt_id'],external_quiescent=True,actor_id='other-owner',actor_name='Segundo propietario')
    try:
        with store.sheet_write_guard(operation,module.SHEET_ID) as delivery:
            delivery['sent']=True;raise TimeoutError('Otro intento sin respuesta')
    except TimeoutError:pass
    return jsonify(ok=True)

@app.post('/test/enable-worker')
def enable_worker():
    def schedule(**kw):
        def write():
            sync_operations_outbox(store,fake,module.SHEET_ID,limit=200)
        threading.Thread(target=write,daemon=True).start()
        return True
    module._schedule_operations_sync=schedule
    return jsonify(ok=True)

app.run(host='127.0.0.1',port=args.port,use_reloader=False,threaded=True)
