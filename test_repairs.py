import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, session
from PIL import Image
from operaciones_store import OperationsStore
import operation_repairs as repairs
from repair_bp import create_repair_blueprint


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OperationsStore(local_path=Path(self.tmp.name)/'test.db')
        self.store.import_matrix_snapshot(dict(clients=[dict(id='A',name='Cliente A'),dict(id='B',name='Cliente B')],
                                              equipment=[dict(id='A1',client_id='A',name='Cámara A1')],reports=[]))
        self.body = dict(id='REP_test',mutation_id='change1',expected_revision=0,client_id='A',equipment_id='A1',
                         service_date='2026-09-20',status='completed',outcome='working',symptom='No enfría',
                         work='Reparación de fuga',result='Trabajando bien a 33 psi',pressure_low='33',photos=[])

    def save(self, body=None, actor='tech1', admin=False):
        return repairs.save(self.store, body or self.body, actor, admin, 'Técnico 1')

    def test_retry_keeps_folio_and_visit(self):
        first=self.save()
        self.assertEqual(first['folio'],'REM-000001')
        self.assertEqual(first,self.save())
        self.assertEqual(len(repairs.listing(self.store,'tech1')[0]),1)
        self.assertEqual(self.store.snapshot()['reports'],[])

    def test_multiple_visits_and_edit_audit(self):
        first=self.save()
        other=self.save({**self.body,'id':'REP_second','mutation_id':'change2'})
        edited=self.save({**self.body,'mutation_id':'change3','expected_revision':1,'result':'Quedó a 34 psi'})
        self.assertEqual(first['folio'],edited['folio'])
        self.assertNotEqual(first['folio'],other['folio'])
        with self.store.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM operations_repair_changes').fetchone()[0],3)
            self.assertIn('33 psi',conn.execute("SELECT payload_json FROM operations_repair_changes WHERE id='change1'").fetchone()[0])

    def test_retire_completed_visit_preserves_audit_and_photo_refs(self):
        row=self.save({**self.body,'photos':[dict(id='p1',stage='after')]})
        repairs.attach(self.store,row['id'],'p1','change1',b'image','tech1',False,Mock(return_value='drive-ref'))
        repairs.finish(self.store,row['id'],'change1','tech1')
        with self.assertRaises(PermissionError):
            repairs.delete_visit(self.store,row['id'],'delete1',1,'tech2')
        with self.assertRaises(repairs.Conflict):
            repairs.delete_visit(self.store,row['id'],'delete1',0,'tech1')
        deleted=repairs.delete_visit(self.store,row['id'],'delete1',1,'tech1')
        self.assertEqual(deleted['status'],'deleted')
        self.assertEqual(repairs.delete_visit(self.store,row['id'],'delete1',1,'tech1'),deleted)
        self.assertEqual(repairs.listing(self.store,'tech1')[0],[])
        with self.assertRaises(LookupError):
            repairs.read(self.store,row['id'],'tech1')
        with self.store.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM operations_repair_changes WHERE repair_id=?',(row['id'],)).fetchone()[0],2)
            self.assertEqual(repairs.photo_refs(self.store,conn,row['id']),{'p1':'drive-ref'})

    def test_stale_edit_conflicts(self):
        self.save()
        with self.assertRaises(repairs.Conflict):
            self.save({**self.body,'mutation_id':'new'})

    def test_staff_read_but_only_owner_or_admin_edit(self):
        self.save()
        self.assertEqual(repairs.read(self.store,'REP_test','tech2')['pressure_low'],'33')
        with self.assertRaises(PermissionError):
            self.save({**self.body,'expected_revision':1,'mutation_id':'other'},actor='tech2')
        self.save({**self.body,'expected_revision':1,'mutation_id':'admin'},actor='owner',admin=True)

    def test_client_equipment_relationship_and_identity(self):
        with self.assertRaises(ValueError):
            self.save({**self.body,'client_id':'B'})
        self.save()
        with self.assertRaises(ValueError):
            self.save({**self.body,'expected_revision':1,'mutation_id':'move','equipment_id':'','equipment_key':'external:new','equipment_name':'Otra'})

    def test_external_clients_do_not_create_policy(self):
        row=self.save({**self.body,'client_id':'','equipment_id':'','client_key':'external:c1','equipment_key':'external:e1','client_name':'Tienda nueva','equipment_name':'Refrigerador'})
        self.assertEqual(row['equipment_key'],'external:e1')
        self.assertEqual(len(self.store.snapshot()['clients']),2)
        self.assertEqual(len(self.store.snapshot()['equipment']),1)

    def test_valve_eighth_turns_persist_and_reject_other_steps(self):
        valve = dict(id='v1', name='Circuito 1', movements=[dict(steps=1, at='2026-09-21T10:00:00Z'), dict(steps=-1, at='2026-09-21T10:01:00Z')])
        row = self.save({**self.body, 'valve_adjustments': [valve]})
        self.assertEqual(row['valve_adjustments'][0]['movements'][1]['steps'], -1)
        self.assertEqual(repairs.read(self.store, 'REP_test', 'tech2')['valve_adjustments'], [valve])
        with self.assertRaises(ValueError):
            self.save({**self.body, 'id': 'REP_bad', 'mutation_id': 'change_bad', 'valve_adjustments': [{**valve, 'movements': [dict(steps=2)]}]})

    def test_unfinished_hidden_and_not_finalized_until_all_photos(self):
        body={**self.body,'photos':[dict(id='p1',stage='before',caption='Antes'),dict(id='p2',stage='after',caption='Después')]}
        row=self.save(body)
        self.assertEqual(row['status'],'uploading')
        self.assertEqual(repairs.listing(self.store,'tech2')[0],[])
        with self.assertRaises(PermissionError):
            repairs.read(self.store,'REP_test','tech2')
        with self.assertRaises(repairs.Conflict):
            repairs.finish(self.store,'REP_test','change1','tech1')
        uploader=Mock(return_value='drive-id')
        for pid in ('p1','p2'):
            repairs.attach(self.store,'REP_test',pid,'change1',b'image','tech1',False,uploader)
            repairs.attach(self.store,'REP_test',pid,'change1',b'image','tech1',False,uploader)
        self.assertEqual(uploader.call_count,2)
        self.assertEqual(repairs.finish(self.store,'REP_test','change1','tech1')['status'],'completed')
        self.assertEqual(repairs.finish(self.store,'REP_test','change1','tech1')['revision'],1)
        self.assertEqual(len(repairs.listing(self.store,'tech2')[0]),1)

    def test_evidence_content_conflict_and_upload_failure(self):
        self.save({**self.body,'photos':[dict(id='p1',stage='after')]})
        with self.assertRaises(RuntimeError):
            repairs.attach(self.store,'REP_test','p1','change1',b'first','tech1',False,Mock(side_effect=RuntimeError('Drive down')))
        with self.store.connection() as conn:
            self.assertEqual(repairs.photo_refs(self.store,conn,'REP_test'),{})
        repairs.attach(self.store,'REP_test','p1','change1',b'first','tech1',False,Mock(return_value='drive-id'))
        with self.assertRaises(repairs.Conflict):
            repairs.attach(self.store,'REP_test','p1','change1',b'different','tech1',False,Mock())

    def test_no_six_photo_limit(self):
        row=self.save({**self.body,'photos':[dict(id=f'p{i}',stage='after') for i in range(35)]})
        self.assertEqual(len(row['photos']),35)

    def test_concurrent_retries_are_one_visit(self):
        self.store.initialize()
        with ThreadPoolExecutor(max_workers=2) as executor:
            rows=list(executor.map(lambda _:self.save(),range(2)))
        self.assertEqual(rows[0],rows[1])

    def test_search_pagination_and_literal_wildcards(self):
        for i in range(52):
            self.save({**self.body,'id':f'REP_{i:03}','mutation_id':f'm{i}'})
        rows,offset=repairs.listing(self.store,'tech1',query='33 psi')
        self.assertEqual((len(rows),offset),(50,50))
        rows,offset=repairs.listing(self.store,'tech1',offset=50,equipment_key='equipment:A1')
        self.assertEqual((len(rows),offset),(2,None))
        self.assertEqual(repairs.listing(self.store,'tech1',query='%')[0],[])

    def test_http_access_validation_and_remision(self):
        app=Flask(__name__);app.secret_key='test-only'
        def role():return session.get('role','')
        def forbidden(*allowed):
            if role() not in allowed:return jsonify(ok=False,error='Forbidden'),403
        def permission(_):
            if not session.get('write'):return jsonify(ok=False,error='Permission'),403
        uploader=Mock(return_value='drive-id')
        app.register_blueprint(create_repair_blueprint(self.store,role,forbidden,permission,uploader,lambda *args:('photo',200)))
        client=app.test_client()
        self.assertEqual(client.get('/api/operaciones/repairs').status_code,403)
        with client.session_transaction() as s:s.update(role='client',hsc_user_id='client')
        self.assertEqual(client.get('/api/operaciones/repairs').status_code,403)
        with client.session_transaction() as s:s.update(role='technician',hsc_user_id='tech1')
        self.assertEqual(client.post('/api/operaciones/repairs',json=self.body).status_code,403)
        with client.session_transaction() as s:s.update(write=True,hsc_user_name='Técnico')
        self.assertEqual(client.post('/api/operaciones/repairs',json=self.body).status_code,200)
        page=client.get('/api/operaciones/repairs/REP_test/remision')
        self.assertEqual(page.status_code,200)
        self.assertIn(b'33 psi',page.data)
        self.assertIn('no es factura',page.get_data(as_text=True))
        with client.session_transaction() as s:s.update(hsc_user_id='tech2')
        self.assertEqual(client.post('/api/operaciones/repairs/REP_test/finish',json={'mutation_id':'change1'}).status_code,403)
        self.assertEqual(client.post('/api/operaciones/repairs/REP_test/delete',json={'mutation_id':'delete1','expected_revision':1}).status_code,403)
        with client.session_transaction() as s:s.update(hsc_user_id='tech1')
        self.assertEqual(client.post('/api/operaciones/repairs/REP_test/delete',json={'mutation_id':'delete1','expected_revision':1}).status_code,200)
        self.assertEqual(client.get('/api/operaciones/repairs/REP_test/remision').status_code,404)

    def test_mutation_receipt_cannot_be_reused_by_another_visit(self):
        self.save()
        with self.assertRaises(PermissionError):self.save({**self.body,'id':'REP_other'})

    def test_photo_manifest_duplicate_and_completion_validation(self):
        with self.assertRaises(ValueError):self.save({**self.body,'work':''})
        with self.assertRaises(ValueError):self.save({**self.body,'photos':[dict(id='p1',stage='after')]*2})

    def test_photo_api_requires_image_owner_and_current_revision(self):
        app=Flask(__name__);app.secret_key='test-only'
        uploader=Mock(return_value='drive-ref')
        app.register_blueprint(create_repair_blueprint(self.store,lambda:'technician',lambda *roles:None,
                                                     lambda permission:None,uploader,lambda *args:('image',200)))
        body={**self.body,'photos':[dict(id='p1',stage='after')]}
        self.save(body)
        client=app.test_client()
        with client.session_transaction() as s:s['hsc_user_id']='tech1'
        output=io.BytesIO();Image.new('RGB',(30,30),'white').save(output,format='JPEG')
        def post(content):return client.post('/api/operaciones/repairs/REP_test/photos/p1',data={'mutation_id':'change1','file':(io.BytesIO(content),'foto.jpg')})
        self.assertEqual(post(b'not an image').status_code,400)
        self.assertEqual(post(output.getvalue()).status_code,200)
        self.assertEqual(post(output.getvalue()).status_code,200)
        self.assertEqual(uploader.call_count,1)
        self.save({**body,'expected_revision':1,'mutation_id':'new-revision'})
        self.assertEqual(post(output.getvalue()).status_code,409)
        with client.session_transaction() as s:s['hsc_user_id']='tech2'
        self.assertEqual(post(output.getvalue()).status_code,403)

    def test_remision_escapes_user_text_and_does_not_emit_unfinished(self):
        app=Flask(__name__);app.secret_key='test-only'
        app.register_blueprint(create_repair_blueprint(self.store,lambda:'admin',lambda *roles:None,
                                                     lambda permission:None,Mock(),Mock()))
        row=self.save({**self.body,'work':'<script>unsafe()</script>','photos':[dict(id='p1',stage='after')]})
        client=app.test_client()
        self.assertEqual(client.get('/api/operaciones/repairs/REP_test/remision').status_code,409)
        repairs.attach(self.store,row['id'],'p1','change1',b'image','tech1',False,Mock(return_value='id'))
        repairs.finish(self.store,row['id'],'change1','tech1')
        page=client.get('/api/operaciones/repairs/REP_test/remision').get_data(as_text=True)
        self.assertIn('&lt;script&gt;unsafe()',page)
        self.assertNotIn('<script>unsafe()',page)


if __name__=='__main__':unittest.main()
