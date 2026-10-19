"""Bitácora fotográfica independiente de los reportes de mantenimiento y sus PDFs."""
import json
import re
from datetime import datetime, timezone


def reserve(store, fault_id, ident, actor_id, actor_name, status, notes, hashes):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',ident) or not actor_id:
        raise ValueError('Identificador de atención no válido.')
    if status not in {'Revisada','Reparada'} or not notes.strip() or len(notes)>5000 or len(hashes)>6:
        raise ValueError('Describe el trabajo y selecciona hasta seis fotografías.')
    store.initialize();p=store.placeholder
    payload=dict(id=ident,fault_id=fault_id,actor_id=actor_id,actor_name=actor_name,status=status,notes=notes.strip(),hashes=hashes,
                 created_at=datetime.now(timezone.utc).isoformat(),photos=[],ready=False)
    with store.connection() as conn:
        fault=conn.execute(f'SELECT id FROM operations_faults WHERE id={p} AND deleted_at=\'\'',(fault_id,)).fetchone()
        if not fault:raise LookupError('La falla ya no está disponible.')
        conn.execute(f'INSERT INTO operations_fault_attentions(id,fault_id,payload_json) VALUES ({p},{p},{p}) ON CONFLICT(id) DO NOTHING',
                     (ident,fault_id,json.dumps(payload)))
        current=json.loads(conn.execute(f'SELECT payload_json FROM operations_fault_attentions WHERE id={p}',(ident,)).fetchone()[0])
        for key in ('fault_id','actor_id','status','notes','hashes'):
            if current[key]!=payload[key]:raise ValueError('Este envío ya corresponde a otra captura. Reabre la falla antes de cambiarla.')
    return current


def complete(store, ident, photos):
    p=store.placeholder
    with store.connection() as conn:
        row=conn.execute(f'SELECT payload_json FROM operations_fault_attentions WHERE id={p}',(ident,)).fetchone()
        if not row:raise LookupError('La captura no está disponible.')
        payload=json.loads(row[0])
        if payload['ready']:return payload
        if [photo.get('position') for photo in photos]!=[photo['position'] for photo in payload['hashes']] or any(not photo.get('ref') for photo in photos):
            raise ValueError('Faltan fotografías por confirmar.')
        stamp=datetime.now(timezone.utc).isoformat()
        updated=conn.execute(f"UPDATE operations_faults SET status={p},resolution_notes={p},resolved_by={p},resolved_at={p},source='app',sync_status='pending',updated_at={p} WHERE id={p} AND deleted_at=''",
                             (payload['status'],payload['notes'],payload['actor_name'],stamp if payload['status']=='Reparada' else '',stamp,payload['fault_id']))
        if updated.rowcount!=1:raise LookupError('La falla ya no está disponible.')
        payload.update(ready=True,photos=photos)
        conn.execute(f'UPDATE operations_fault_attentions SET payload_json={p} WHERE id={p}',(json.dumps(payload),ident))
        store._queue_sync_in_transaction(conn,'fault',payload['fault_id'],'sheets','upsert',{
            'status':payload['status'],'resolution_notes':payload['notes'],'resolved_by':payload['actor_name'],
            'resolved_at':stamp if payload['status']=='Reparada' else ''})
    return payload


def history(store, fault_id):
    store.initialize()
    with store.connection() as conn:
        rows=conn.execute(f'SELECT payload_json FROM operations_fault_attentions WHERE fault_id={store.placeholder}',(fault_id,)).fetchall()
    return sorted((p for p in (json.loads(row[0]) for row in rows) if p['ready']),key=lambda p:p['created_at'],reverse=True)
