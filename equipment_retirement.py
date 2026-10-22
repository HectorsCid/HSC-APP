"""Administrator-only equipment removal, with durable anti-resurrection receipts."""
import hashlib
import json
from datetime import datetime, timezone


def lock_equipment(store, conn):
    if store.dialect == 'postgres':
        # Readers remain available; relation writers taking row locks wait until
        # the deletion/history decision commits instead of racing that decision.
        conn.execute('LOCK TABLE operations_equipment, operations_equipment_retirements IN EXCLUSIVE MODE')
    else:
        conn.execute('BEGIN IMMEDIATE')


def assert_editable(store, conn, ids):
    p = store.placeholder
    for ident in ids:
        if conn.execute(f'SELECT id FROM operations_equipment_retirements WHERE id={p}', (ident,)).fetchone():
            raise ValueError('Este equipo fue eliminado o dado de baja por el administrador. No se reactivó ni se reutilizó su ID.')


def require_reference(store, conn, equipment_id):
    """Serialize related writes with retirement; edits to archived history remain valid."""
    if not equipment_id:
        return
    if store.dialect == 'sqlite' and not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    suffix = ' FOR KEY SHARE' if store.dialect == 'postgres' else ''
    if not conn.execute(f'SELECT id FROM operations_equipment WHERE id={store.placeholder}' + suffix,
                        (equipment_id,)).fetchone():
        raise ValueError('El equipo fue eliminado o ya no está disponible. No se vinculó el nuevo registro.')


def removal(store, equipment_id, *, fingerprint=None, actor_id=''):
    store.initialize()
    p = store.placeholder
    with store.connection() as conn:
        lock_equipment(store, conn)
        if store.dialect == 'postgres':
            conn.execute('LOCK TABLE operations_reports, operations_faults, operations_tasks, operations_expenses, '
                         'operations_sync_outbox, operations_media_cache, operations_worklists, operations_repairs '
                         'IN SHARE ROW EXCLUSIVE MODE')
        old = conn.execute(f'SELECT action FROM operations_equipment_retirements WHERE id={p}', (equipment_id,)).fetchone()
        if old:
            return {'ok': True, 'already_done': True, 'action': old[0], 'id': equipment_id}
        cursor = conn.execute(f'SELECT * FROM operations_equipment WHERE id={p}', (equipment_id,))
        row = cursor.fetchone()
        if row is None:
            raise ValueError('El equipo ya no existe. Actualiza la lista.')
        equipment = dict(zip([c[0] for c in cursor.description], row))
        refs = {}
        for table, label in (('operations_reports', 'Reportes o borradores'), ('operations_faults', 'Fallas'),
                             ('operations_tasks', 'Tareas'), ('operations_expenses', 'Gastos')):
            refs[label] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE equipment_id={p}', (equipment_id,)).fetchone()[0]
        refs['Reparaciones'] = conn.execute(f'SELECT COUNT(*) FROM operations_repairs WHERE equipment_key={p}',
                                           ('equipment:' + equipment_id,)).fetchone()[0]
        refs['Listas de trabajo'] = sum(any(item.get('equipment_id') == equipment_id for item in json.loads(r[0]).get('items', []))
                                      for r in conn.execute('SELECT payload_json FROM operations_worklists').fetchall())
        refs['Cambios pendientes de sincronizar'] = conn.execute(
            f"SELECT COUNT(*) FROM operations_sync_outbox WHERE entity_type='equipment' AND entity_id={p} AND status!='synced'",
            (equipment_id,)).fetchone()[0]
        refs['Fotografías'] = int(bool(equipment['has_photo'] or equipment['photo_ref'])) + conn.execute(
            f"SELECT COUNT(*) FROM operations_media_cache WHERE kind='equipment' AND record_id={p}", (equipment_id,)).fetchone()[0]
        refs = {key: value for key, value in refs.items() if value}
        action = 'archive' if refs else 'delete'
        digest = hashlib.sha256(json.dumps([equipment, refs, action], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result = {'ok': True, 'id': equipment_id, 'name': equipment['name'], 'action': action,
                  'references': refs, 'fingerprint': digest}
        if fingerprint is None:
            return result
        if fingerprint != digest:
            raise ValueError('El equipo o su historial cambiaron. Revisa de nuevo antes de confirmar; no se eliminó nada.')
        if not actor_id:
            raise ValueError('Falta identificar al administrador.')
        stamp = datetime.now(timezone.utc).isoformat()
        backup = json.dumps({'equipment': equipment, 'references': refs}, ensure_ascii=False)
        conn.execute(f'INSERT INTO operations_equipment_retirements(id,client_id,action,actor_id,created_at,backup_json) '
                     f'VALUES ({p},{p},{p},{p},{p},{p})',
                     (equipment_id, equipment['client_id'], action, actor_id, stamp, backup))
        if action == 'archive':
            conn.execute(f'UPDATE operations_equipment SET status={p},updated_at={p} WHERE id={p}',
                         ('Inactivo', stamp, equipment_id))
        else:
            conn.execute(f'DELETE FROM operations_equipment WHERE id={p}', (equipment_id,))
        result['applied'] = True
        return result


def create_retirement_blueprint(store, forbidden, invalidate):
    from flask import Blueprint, request, jsonify, session, current_app
    bp = Blueprint('equipment_retirement', __name__)

    @bp.post('/api/operaciones/equipment/<path:equipment_id>/removal-preview')
    @bp.post('/api/operaciones/equipment/<path:equipment_id>/remove')
    def retire(equipment_id):
        denied = forbidden('admin')
        if denied is not None:
            return denied
        if not request.is_json:
            return jsonify(ok=False, error='Solicitud JSON requerida.'), 415
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error='Solicitud no válida.'), 400
        apply = request.path.endswith('/remove')
        if apply and (body.get('confirm') is not True or not isinstance(body.get('fingerprint'), str) or not body['fingerprint']):
            return jsonify(ok=False, error='Primero revisa el equipo y confirma la acción.'), 400
        try:
            result = removal(store, equipment_id, fingerprint=body['fingerprint'] if apply else None,
                             actor_id=str(session.get('hsc_user_id') or 'owner'))
            if apply:
                invalidate()
            return jsonify(result)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 409
        except Exception:
            current_app.logger.exception('No se pudo retirar el equipo %s', equipment_id)
            return jsonify(ok=False, error='No se pudo confirmar la operación. Puedes cerrar y volver a consultar el equipo.'), 503
    return bp
