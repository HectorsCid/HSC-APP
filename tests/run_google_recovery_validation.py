"""Explicit, real Google integration test. Creates its own folder and spreadsheet.

Only accepts a temporary drive.file access token; never loads app credentials or
production IDs. Existing files are never listed outside the newly created folder.
The new test artifacts are retained for inspection; no file is deleted.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'venv311/Lib/site-packages')]
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from operaciones_store import OperationsStore,ReportConflictError
from operaciones_sync import sync_operations_outbox,_get_sheet_titles,_plan_operation,_write_plan
from drive_registry import ensure_object,resource_key,FOLDER
from test_operaciones_sync import FakeSheets


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--token-file',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    token=json.loads(Path(args.token_file).read_text(encoding='utf-8'))['token']
    # Request errors are summarized without HTTP headers or credentials.
    def service(name,version):return build(name,version,credentials=Credentials(token=token),cache_discovery=False)
    drive=service('drive','v3');sheets=service('sheets','v4')
    run='HSC-PRUEBAS-SYNC-'+uuid.uuid4().hex[:12]
    folder=drive.files().create(body={'name':run,'mimeType':FOLDER,'description':'Datos ficticios. Pruebas de recuperación HSC; no contiene información de producción.'},fields='id').execute()['id']
    sheet=drive.files().create(body={'name':run+'-Matriz','mimeType':'application/vnd.google-apps.spreadsheet','parents':[folder]},fields='id').execute()['id']
    results={'folder_url':'https://drive.google.com/drive/folders/'+folder,'sheet_url':'https://docs.google.com/spreadsheets/d/'+sheet,'tests':[]}
    def save():Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    def passed(name):results['tests'].append(name);save();print('PASS Google:',name,flush=True)
    save()
    try:
        model=FakeSheets().sheets
        sheets.spreadsheets().batchUpdate(spreadsheetId=sheet,body={'requests':[{'addSheet':{'properties':{'title':title}}} for title in model]}).execute()
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=sheet,body={'valueInputOption':'RAW','data':[{'range':"'%s'!A1"%title,'values':[data['headers']]} for title,data in model.items()]}).execute()
        with tempfile.TemporaryDirectory(prefix='hsc-google-synthetic-') as directory:
            store=OperationsStore(local_path=Path(directory)/'test.sqlite3')
            store.import_matrix_snapshot(dict(clients=[dict(id='TEST',name='Cliente ficticio')],equipment=[dict(id='TEST1',client_id='TEST',name='Equipo ficticio')],reports=[],faults=[]))
            draft=store.save_report_draft(dict(client_id='TEST',equipment_id='TEST1',round='1',payload={'inicio':'2026-09-28','fin':'2026-09-28','p1':'100'}))
            report=store.finalize_report(draft['id']);report_id=report['id']
            operation=store.pending_sync()[0]
            plan=_plan_operation(store,operation,sheets,sheet,_get_sheet_titles(sheets,sheet),{})
            import operaciones_sync
            def lost_ack(request,delivery):
                delivery['sent']=True
                result=request.execute() # A real Google write, then simulated loss of its reply.
                raise TimeoutError('Prueba controlada: Google escribió; respuesta perdida')
            with patch('operaciones_sync._execute_sheet_write',side_effect=lost_ack):
                try:_write_plan(sheets,sheet,plan)
                except TimeoutError:pass
            state=store.sheet_delivery_status(sheet);assert state['requires_confirmation']
            remote=sheets.spreadsheets().values().get(spreadsheetId=sheet,range="'Reportes'!A1:AL").execute()['values']
            assert sum(row[0]==report_id for row in remote[1:])==1
            before=store.pending_sync()
            edit=store.save_report_draft(dict(client_id='TEST',equipment_id='TEST1',round='1',edit_report_id=report_id,payload={'p1':'200'},changed_fields={'p1':'200'},base_values={'p1':'100'}))
            store.finalize_report(edit['id'])
            after_edit=store.pending_sync()
            assert after_edit[0]['id']!=operation['id']
            assert sync_operations_outbox(store,sheets,sheet)['synced']==0
            # Writer failures update attempt/error metadata, never change payloads.
            assert store.pending_sync()[0]['payload']==after_edit[0]['payload']
            store.resolve_sheet_delivery(sheet,state['operation_id'],attempt_id=state['attempt_id'],actor_id='owner-test',actor_name='Pruebas Google',external_quiescent=True)
            assert sync_operations_outbox(store,sheets,sheet)['synced']==1
            remote=sheets.spreadsheets().values().get(spreadsheetId=sheet,range="'Reportes'!A1:AL").execute()['values']
            rows=[row for row in remote[1:] if row[0]==report_id];assert len(rows)==1
            assert rows[0][remote[0].index('PresionCto1')]=='200'
            assert store.pending_sync()==[]
            assert sync_operations_outbox(store,sheets,sheet)['synced']==0
            passed('Respuesta perdida después de escritura real; pausa, revisión nueva conservada, reanudación sin duplicar filas y valor final 200')

            barrier=threading.Barrier(3)
            def create(_):
                own=service('drive','v3');barrier.wait()
                return ensure_object(store,own,folder,'Fotografías de prueba')
            with ThreadPoolExecutor(3) as pool:ids=list(pool.map(create,range(3)))
            assert len(set(ids))==1;canonical=ids[0]
            passed('Tres creaciones simultáneas reutilizan una carpeta canónica real')
            from PIL import Image
            photo=io.BytesIO();Image.new('RGB',(640,480),'#227799').save(photo,'JPEG');content=photo.getvalue()
            file_id=ensure_object(store,drive,canonical,'foto.prueba.jpg',content=content,mime='image/jpeg',immutable=True)
            assert ensure_object(store,drive,canonical,'foto.prueba.jpg',content=content,mime='image/jpeg',immutable=True)==file_id
            try:ensure_object(store,drive,canonical,'foto.prueba.jpg',content=content+b'changed',mime='image/jpeg',immutable=True)
            except ReportConflictError:pass
            else:raise AssertionError('Photo replacement was not rejected')
            passed('Reintento real conserva el mismo archivo y rechaza sustituir sus bytes')
            alias=drive.files().create(body={'name':'Fotografías de prueba','mimeType':FOLDER,'parents':[folder]},fields='id').execute()['id']
            note=drive.files().create(body={'name':'nota-ficticia.txt','parents':[alias]},media_body=MediaIoBaseUpload(io.BytesIO(b'Synthetic test only'),mimetype='text/plain'),fields='id').execute()['id']
            assert ensure_object(store,drive,folder,'Fotografías de prueba')==canonical
            registry=store.drive_registry(resource_key(folder,'Fotografías de prueba',True))
            assert alias in registry['duplicates']
            assert drive.files().get(fileId=note,fields='id,parents,trashed').execute().get('trashed') is False
            assert drive.files().get(fileId=alias,fields='id,trashed').execute().get('trashed') is False
            passed('Carpeta duplicada detectada y conciliada sin borrar la carpeta ni su archivo')
    except Exception as exc:
        results['failure_type']=type(exc).__name__;save();raise
    finally:
        print('Test artifacts:',json.dumps({k:v for k,v in results.items() if k.endswith('_url')}),flush=True)

if __name__=='__main__':main()
