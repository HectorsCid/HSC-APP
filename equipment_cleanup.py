"""Limpieza excepcional y reversible; no modifica la sincronización ordinaria."""
import hashlib
import json
from datetime import datetime, timezone

CLIENT_NAME = 'Visscher Caravelle'
MARKER = 'one_time_visscher_empty_equipment_20261022'


def reconcile(store, matrix, *, expected=None):
    store.initialize()
    clients = [c for c in matrix.get('clients', [])
               if c.get('name', '').strip().casefold() == CLIENT_NAME.casefold()]
    if len(clients) != 1 or any((matrix.get('stats') or {}).get(k) for k in
                              ('duplicate_clients', 'duplicate_equipment', 'duplicate_reports')):
        raise ValueError('No se puede identificar una matriz completa y sin duplicados.')
    matrix_id = clients[0]['id']
    present = {e['id'] for e in matrix.get('equipment', []) if e.get('client_id') == matrix_id}
    if len(present) != 30:
        raise ValueError(f'La matriz contiene {len(present)} equipos del cliente; se esperaban 30. No se cambió nada.')
    p = store.placeholder
    with store.connection() as conn:
        # Serializa con altas, reportes e importaciones durante la comprobación final.
        if store.dialect == 'postgres':
            conn.execute('LOCK TABLE operations_clients, operations_equipment, operations_reports, '
                         'operations_faults, operations_tasks, operations_expenses, '
                         'operations_sync_outbox, operations_media_cache, operations_worklists, operations_meta IN SHARE ROW EXCLUSIVE MODE')
        else:
            conn.execute('BEGIN IMMEDIATE')
        done = conn.execute(f'SELECT value FROM operations_meta WHERE key={p}', (MARKER,)).fetchone()
        if done:
            return {'done': True, 'result': json.loads(done[0])}
        local = conn.execute(f'SELECT id FROM operations_clients WHERE id={p} OR matrix_id={p}',
                             (matrix_id, matrix_id)).fetchall()
        if len(local) != 1:
            raise ValueError('El cliente no tiene una correspondencia única en HSC.')
        cursor = conn.execute(f'SELECT * FROM operations_equipment WHERE client_id={p} ORDER BY id', (local[0][0],))
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if not present.issubset({row['id'] for row in rows}):
            raise ValueError('Faltan equipos de la matriz en HSC. Primero realiza la importación habitual.')
        protected = {r[0] for table in ('operations_reports', 'operations_faults', 'operations_tasks', 'operations_expenses')
                     for r in conn.execute(f'SELECT equipment_id FROM {table}').fetchall()}
        protected.update(r[0] for r in conn.execute("SELECT entity_id FROM operations_sync_outbox WHERE status!='synced'").fetchall())
        protected.update(r[0] for r in conn.execute('SELECT record_id FROM operations_media_cache').fetchall())
        for saved in conn.execute('SELECT payload_json FROM operations_worklists').fetchall():
            protected.update(item.get('equipment_id') for item in json.loads(saved[0]).get('items', []))
        protected.update(r.get('equipment_id') for key in ('reports', 'faults') for r in matrix.get(key, []))
        candidates, skipped = [], []
        for row in rows:
            if row['id'] in present or row['status'].strip().casefold() == 'inactivo':
                continue
            if row['id'] in protected or row['source'] != 'sheets' or row['has_photo'] or row['photo_ref']:
                skipped.append({'id': row['id'], 'name': row['name']})
            else:
                candidates.append(row)
        token = hashlib.sha256(json.dumps(candidates, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result = {'done': False, 'matrix_count': len(present), 'candidates': candidates,
                  'protected': skipped, 'fingerprint': token}
        if expected is None:
            return result
        if expected != token:
            raise ValueError('Los datos cambiaron desde la revisión. Vuelve a revisar antes de continuar.')
        if not candidates:
            raise ValueError('No hay espacios vacíos que retirar.')
        stamp = datetime.now(timezone.utc).isoformat()
        # El respaldo incluye los registros completos. Nunca borramos filas ni fotos.
        store._upsert_meta(conn, MARKER, json.dumps({'at': stamp, 'equipment': candidates}, ensure_ascii=False))
        for row in candidates:
            conn.execute(f'UPDATE operations_equipment SET status={p},updated_at={p} WHERE id={p}',
                         ('Inactivo', stamp, row['id']))
        result['applied'] = len(candidates)
        return result


def create_cleanup_blueprint(store, forbidden, read_matrix, invalidate):
    import secrets
    from flask import Blueprint, request, session, render_template
    bp = Blueprint('equipment_cleanup', __name__)

    @bp.route('/admin/limpieza-equipos-visscher', methods=['GET', 'POST'])
    def cleanup():
        denied = forbidden('admin')
        if denied is not None:
            return denied
        session.setdefault('equipment_cleanup_csrf', secrets.token_urlsafe(32))
        error, result = '', None
        try:
            expected = None
            if request.method == 'POST':
                if not secrets.compare_digest(request.form.get('csrf', ''), session['equipment_cleanup_csrf']):
                    return 'Solicitud no válida. Recarga la página.', 403
                expected = request.form.get('fingerprint', '')
            result = reconcile(store, read_matrix(), expected=expected)
            if result.get('applied'):
                invalidate()
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else 'No se pudo completar la consulta. No apliques cambios; vuelve a intentarlo.'
        return render_template('equipment_cleanup.html', result=result, error=error,
                               csrf=session['equipment_cleanup_csrf'])
    return bp
