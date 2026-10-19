import hashlib
import io
from flask import Blueprint, jsonify, request, session, current_app, url_for
from werkzeug.exceptions import HTTPException
import fault_attention as attention


def create_fault_attention_blueprint(store,role,forbidden,permission,uploader,photo_response,invalidate,schedule,notifier=None):
    bp=Blueprint('fault_attentions',__name__)
    def access(fault_id,write=False):
        denied=forbidden('admin','technician') if write else forbidden('admin','technician','client')
        if denied:return None,denied
        if write:
            denied=permission('createReports')
            if denied:return None,denied
        if not store.enabled:return None,(jsonify(ok=False,error='La base operativa no está disponible.'),503)
        fault=next((f for f in store.snapshot()['faults'] if f['id']==fault_id),None)
        if not fault or (role()=='client' and fault['client_id']!=str(session.get('hsc_client_id') or '')):
            return None,(jsonify(ok=False,error='Falla no disponible.'),404)
        return fault,None
    @bp.errorhandler(Exception)
    def error(exc):
        if isinstance(exc,HTTPException):return exc
        code=400 if isinstance(exc,ValueError) else 404 if isinstance(exc,LookupError) else 503
        if code==503:current_app.logger.exception('No se confirmó la atención fotográfica')
        return jsonify(ok=False,error=str(exc) if code!=503 else 'No se confirmó el guardado. Conserva la captura y reintenta; no se duplicará.'),code
    @bp.after_request
    def private(response):
        response.headers['Cache-Control']='private, no-store';return response
    def public(row):
        return {k:row[k] for k in ('id','status','notes','created_at','actor_name')} | {'photos':[
            {'position':photo['position'],'url':url_for('fault_attentions.photo',fault_id=row['fault_id'],ident=row['id'],position=photo['position'])}
            for photo in row['photos']]}
    @bp.get('/api/operaciones/faults/<path:fault_id>/attentions')
    def listing(fault_id):
        fault,denied=access(fault_id)
        if denied:return denied
        return jsonify(ok=True,attentions=[public(row) for row in attention.history(store,fault_id)])
    @bp.post('/api/operaciones/faults/<path:fault_id>/attentions')
    def save(fault_id):
        fault,denied=access(fault_id,True)
        if denied:return denied
        if (request.content_length or 0)>13*1024*1024:raise ValueError('Las fotos deben enviarse comprimidas.')
        actor=str(session.get('hsc_user_id') or ('owner' if role()=='admin' else ''))
        name=str(session.get('hsc_user_name') or ('Administrador HSC' if role()=='admin' else 'Técnico HSC'))
        files=[];seen=set()
        from PIL import Image
        for field,upload in request.files.items(multi=True):
            if not field.startswith('photo_') or field[6:] not in {'1','2','3','4','5','6'} or field in seen:raise ValueError('Revisa los seis espacios de fotografías.')
            seen.add(field);content=upload.stream.read(2*1024*1024+1)
            if not content or len(content)>2*1024*1024:raise ValueError('Cada foto debe pesar menos de 2 MB después de comprimir.')
            try:
                with Image.open(io.BytesIO(content)) as img:
                    if img.format not in {'JPEG','PNG','WEBP'} or img.width*img.height>20000000:raise ValueError()
                    img.verify()
            except Exception:raise ValueError('Una fotografía no es válida o es demasiado grande.')
            files.append((int(field[6:]),content))
        files.sort();ident=str(request.form.get('id') or '')
        hashes=[{'position':pos,'sha256':hashlib.sha256(content).hexdigest()} for pos,content in files]
        with store.resource_lock('fault-attention:'+ident):
            row=attention.reserve(store,fault_id,ident,actor,name,str(request.form.get('status') or ''),str(request.form.get('notes') or ''),hashes)
            was_ready=row['ready']
            if not row['ready']:
                photos=[]
                for pos,content in files:
                    stored=uploader(fault.get('client_id') or 'Fallas','Atencion_'+ident,pos,content,upload_id=ident+':'+str(pos))
                    if not stored.get('drive_ref'):raise OSError('Drive no confirmó la fotografía.')
                    photos.append(dict(position=pos,ref=stored['drive_ref']))
                row=attention.complete(store,ident,photos)
        if notifier and not was_ready:
            try:notifier(fault,row)
            except Exception:current_app.logger.exception('No se pudo crear el aviso de atención')
        invalidate();schedule(force=True)
        return jsonify(ok=True,attention=public(row))
    @bp.get('/api/operaciones/faults/<path:fault_id>/attentions/<ident>/photos/<int:position>')
    def photo(fault_id,ident,position):
        fault,denied=access(fault_id)
        if denied:return denied
        row=next((r for r in attention.history(store,fault_id) if r['id']==ident),None)
        ref=next((p['ref'] for p in row['photos'] if p['position']==position),None) if row else None
        if not ref:return jsonify(ok=False,error='Fotografía no disponible.'),404
        return photo_response('fault-attention',ident+':'+str(position),ref)
    return bp
