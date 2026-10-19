import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from flask import Flask, jsonify, session
from PIL import Image
from operaciones_store import OperationsStore
from test_operaciones_store import _payload
from fault_attention_bp import create_fault_attention_blueprint
import fault_attention


class FaultAttentionPhotoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=OperationsStore(local_path=Path(self.tmp.name)/'faults.db')
        self.store.import_matrix_snapshot(_payload());self.before=self.store.snapshot()['reports']
        self.app=Flask(__name__);self.app.secret_key='test';self.role='technician'
        def forbidden(*roles):return None if self.role in roles else (jsonify(ok=False,error='Sin permiso'),403)
        self.uploader=Mock(side_effect=lambda client,ident,pos,data,**kw:{'drive_ref':f'{ident}-{pos}','storage_ref':'separate'})
        self.notifier=Mock();self.served=Mock(return_value='image')
        self.app.register_blueprint(create_fault_attention_blueprint(self.store,lambda:self.role,forbidden,lambda permission:None,
            self.uploader,self.served,Mock(),Mock(),self.notifier))
        self.client=self.app.test_client()
        with self.client.session_transaction() as state:state.update(hsc_user_id='TECH',hsc_user_name='Técnico de prueba',hsc_client_id='UVMQ')
        image=io.BytesIO();Image.new('RGB',(12,12)).save(image,format='JPEG');self.image=image.getvalue()
    def send(self,ident='ATTN1',positions=(1,2,3,4,5,6),notes='Cambio de pieza',status='Reparada'):
        return self.client.post('/api/operaciones/faults/F-UVM-1/attentions',data={
            'id':ident,'notes':notes,'status':status,**{f'photo_{pos}':(io.BytesIO(self.image),f'{pos}.jpg') for pos in positions}},content_type='multipart/form-data')
    def test_six_photos_history_and_original_report_unchanged(self):
        response=self.send();self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(len(response.json['attention']['photos']),6)
        self.assertEqual(self.uploader.call_count,6)
        self.assertEqual(self.store.snapshot()['reports'],self.before)
        self.assertEqual(self.store.get_fault_evidence('F-UVM-1'),[])
        self.assertEqual(self.store.snapshot()['faults'][0]['status'],'Reparada')
        self.assertEqual(len(fault_attention.history(self.store,'F-UVM-1')),1)
        self.notifier.assert_called_once()
    def test_retry_does_not_duplicate_uploads_or_history(self):
        self.assertEqual(self.send().status_code,200)
        self.assertEqual(self.send().status_code,200)
        self.assertEqual(self.uploader.call_count,6)
        self.assertEqual(len(fault_attention.history(self.store,'F-UVM-1')),1)
        self.assertEqual(self.send(notes='Different content').status_code,400)
        self.assertEqual(self.uploader.call_count,6)
    def test_upload_failure_does_not_resolve_or_expose_incomplete_attention(self):
        self.uploader.side_effect=OSError('Drive unavailable')
        self.assertEqual(self.send().status_code,503)
        self.assertEqual(fault_attention.history(self.store,'F-UVM-1'),[])
        self.assertEqual(self.store.snapshot()['faults'][0]['status'],'Reportada')
        self.uploader.side_effect=lambda *args,**kw:{'drive_ref':'confirmed'}
        self.assertEqual(self.send().status_code,200)
    def test_positions_do_not_move_and_visits_are_kept_separate(self):
        response=self.send(positions=(1,5),status='Revisada')
        self.assertEqual([p['position'] for p in response.json['attention']['photos']],[1,5])
        self.assertEqual(self.send(ident='ATTN2',positions=(),notes='Segunda visita').status_code,200)
        self.assertEqual(len(fault_attention.history(self.store,'F-UVM-1')),2)
        self.assertEqual(self.send().status_code,400)  # Same ID cannot represent a different visit.
    def test_clients_read_only_and_company_scoped(self):
        self.send(positions=(3,))
        self.role='client'
        self.assertEqual(self.send().status_code,403)
        response=self.client.get('/api/operaciones/faults/F-UVM-1/attentions')
        self.assertEqual(response.status_code,200)
        photo=response.json['attentions'][0]['photos'][0]['url']
        self.assertEqual(self.client.get(photo).status_code,200)
        with self.client.session_transaction() as state:state['hsc_client_id']='OTHER'
        self.assertEqual(self.client.get('/api/operaciones/faults/F-UVM-1/attentions').status_code,404)
        self.assertEqual(self.client.get(photo).status_code,404)
    def test_seventh_photo_and_empty_notes_are_rejected(self):
        self.assertEqual(self.send(positions=(7,)).status_code,400)
        self.assertEqual(self.send(notes='').status_code,400)
        self.uploader.assert_not_called()


if __name__=='__main__':unittest.main()
