"""Repair visits are independent of preventive rounds, Sheets and billing.

Stable identities, compare-and-set edits and mutation receipts protect offline
retries. Photo bytes never enter the bootstrap payload or the relational DB.
"""
import hashlib
import json
import re
from datetime import date, datetime, timezone


class Conflict(ValueError):
    pass


def token(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Identificador de reparación o foto no válido.')
    return value


def clean(value, limit=4000):
    if not isinstance(value, (str, int, float)):
        value = ''
    value = str(value).strip()
    if len(value) > limit:
        raise ValueError(f'Un campo supera los {limit} caracteres.')
    return value


def read(store, ident, actor, admin=False, *, edit=False, conn=None, lock=False):
    if conn is None:
        store.initialize()
        with store.connection() as connection:
            return read(store, ident, actor, admin, edit=edit, conn=connection)
    suffix = ' FOR UPDATE' if lock and store.dialect == 'postgres' else ''
    row = conn.execute(f'SELECT payload_json FROM operations_repairs WHERE id={store.placeholder}{suffix}', (ident,)).fetchone()
    if not row:
        raise LookupError('La reparación no está disponible.')
    result = json.loads(row[0])
    if result.get('status') == 'deleted':
        raise LookupError('Esta visita fue retirada del historial.')
    if not admin and result['created_by'] != actor and (edit or result['status'] != 'completed'):
        raise PermissionError('Esta reparación no está disponible para tu cuenta.')
    return result


def locked(store, conn):
    if store.dialect == 'sqlite':
        conn.execute('BEGIN IMMEDIATE')


def photo_refs(store, conn, ident):
    return dict(conn.execute(f'SELECT id,drive_ref FROM operations_repair_photos WHERE repair_id={store.placeholder}', (ident,)).fetchall())


def save(store, body, actor, admin=False, author=''):
    store.initialize()
    ident, mutation = token(body.get('id')), token(body.get('mutation_id'))
    p = store.placeholder
    with store.connection() as conn:
        locked(store, conn)
        try:
            old = read(store, ident, actor, admin, edit=True, conn=conn, lock=True)
        except LookupError:
            old = None
        receipt = conn.execute(f'SELECT repair_id,actor_id FROM operations_repair_changes WHERE id={p}', (mutation,)).fetchone()
        if receipt:
            if tuple(receipt) != (ident, actor):
                raise PermissionError('Este cambio ya pertenece a otra operación.')
            return old
        expected = body.get('expected_revision', 0)
        if type(expected) is not int or expected != (old['revision'] if old else 0):
            raise Conflict('La reparación cambió en otro dispositivo. Tu propuesta local se conserva; compara ambas versiones antes de guardar.')
        result = {k: clean(body.get(k, ''), n) for k, n in {
            'client_id': 100, 'equipment_id': 100, 'client_name': 200, 'equipment_name': 200,
            'address': 400, 'model': 160, 'serial': 160, 'location': 200,
            'symptom': 6000, 'diagnosis': 6000, 'work': 10000, 'parts': 4000,
            'result': 6000, 'recommendations': 6000, 'refrigerant': 50,
            'pressure_low': 40, 'pressure_high': 40, 'temperature': 40, 'amperage': 40,
            'measurement_notes': 2000, 'received_by': 160,
        }.items()}
        result['client_key'] = 'client:' + result['client_id'] if result['client_id'] else 'external:' + token(clean(body.get('client_key'), 120).removeprefix('external:'))
        result['equipment_key'] = 'equipment:' + result['equipment_id'] if result['equipment_id'] else 'external:' + token(clean(body.get('equipment_key'), 120).removeprefix('external:'))
        if result['client_id']:
            client = conn.execute(f'SELECT name FROM operations_clients WHERE id={p}', (result['client_id'],)).fetchone()
            if not client:
                raise ValueError('El cliente seleccionado ya no existe.')
            result['client_name'] = client[0]
        if result['equipment_id']:
            equipment = conn.execute(f'SELECT name,client_id FROM operations_equipment WHERE id={p}', (result['equipment_id'],)).fetchone()
            if not equipment or equipment[1] != result['client_id']:
                raise ValueError('El equipo no pertenece al cliente seleccionado.')
            result['equipment_name'] = equipment[0]
        if old and any(old[k] != result[k] for k in ('client_key', 'equipment_key')):
            raise ValueError('No se puede mover una visita guardada a otro cliente o equipo.')
        if not result['client_name'] or not result['equipment_name']:
            raise ValueError('Indica el cliente y el equipo.')
        service_date = clean(body.get('service_date'), 10)
        if date.fromisoformat(service_date).isoformat() != service_date:
            raise ValueError('Fecha de visita no válida.')
        status = body.get('status')
        if status not in ('draft', 'completed'):
            raise ValueError('Estado no válido.')
        if old and old['status'] == 'completed' and status == 'draft':
            raise ValueError('Una visita finalizada no puede convertirse en borrador.')
        if status == 'completed' and (not result['symptom'] or not result['work'] or not result['result']):
            raise ValueError('Para finalizar indica qué fallaba, qué hiciste y cómo quedó.')
        if body.get('outcome') not in ('working', 'follow_up', 'stopped'):
            raise ValueError('Selecciona cómo quedó el equipo.')
        photos, seen = [], set()
        valves, valve_ids = [], set()
        raw_valves = body.get('valve_adjustments', [])
        if not isinstance(raw_valves, list) or len(raw_valves) > 20:
            raise ValueError('Registro de válvulas no válido.')
        for valve in raw_valves:
            if not isinstance(valve, dict):
                raise ValueError('Válvula no válida.')
            valve_id = token(valve.get('id'))
            movements = valve.get('movements', [])
            if valve_id in valve_ids or not isinstance(movements, list) or len(movements) > 1000:
                raise ValueError('Movimientos de válvula no válidos.')
            valve_ids.add(valve_id)
            checked = []
            for move in movements:
                if not isinstance(move, dict) or type(move.get('steps')) is not int or move['steps'] not in (-1, 1):
                    raise ValueError('Cada movimiento debe ser de 1/8 de vuelta.')
                checked.append(dict(steps=move['steps'], at=clean(move.get('at', ''), 40)))
            valves.append(dict(id=valve_id, name=clean(valve.get('name', ''), 100), movements=checked))
        raw_photos = body.get('photos', [])
        if not isinstance(raw_photos, list):
            raise ValueError('Lista de fotografías no válida.')
        for item in raw_photos:
            if not isinstance(item, dict):
                raise ValueError('Fotografía no válida.')
            pid = token(item.get('id'))
            if pid in seen or item.get('stage') not in ('before', 'during', 'after'):
                raise ValueError('Fotografía repetida o etapa no válida.')
            seen.add(pid)
            photos.append(dict(id=pid, stage=item['stage'], caption=clean(item.get('caption', ''), 200)))
        refs = photo_refs(store, conn, ident)
        stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        folio = old['folio'] if old else 'REM-' + str(conn.execute(
            'UPDATE operations_repair_sequence SET next_value=next_value+1 WHERE id=1 RETURNING next_value'
        ).fetchone()[0]).zfill(6)
        result.update(id=ident, mutation_id=mutation, revision=expected + 1,
                      folio=folio,
                      client_key=result['client_key'], equipment_key=result['equipment_key'],
                      service_date=service_date, outcome=body['outcome'], requested_status=status,
                      status='uploading' if status == 'completed' and any(photo['id'] not in refs for photo in photos) else status,
                      photos=photos, valve_adjustments=valves, created_by=old['created_by'] if old else actor,
                      author=old['author'] if old else clean(author or actor, 200),
                      created_at=old['created_at'] if old else stamp, updated_at=stamp, updated_by=actor)
        blob = json.dumps(result, ensure_ascii=False)
        if old:
            updated = conn.execute(f'UPDATE operations_repairs SET revision={p},state={p},service_date={p},payload_json={p} WHERE id={p} AND revision={p}',
                                   (result['revision'], result['status'], service_date, blob, ident, expected))
        else:
            updated = conn.execute(f'INSERT INTO operations_repairs(id,revision,owner_id,client_key,equipment_key,service_date,state,payload_json) VALUES ({",".join([p]*8)}) ON CONFLICT(id) DO NOTHING',
                                   (ident, 1, actor, result['client_key'], result['equipment_key'], service_date, result['status'], blob))
        if updated.rowcount != 1:
            raise Conflict('Otra persona guardó esta reparación. Se conserva tu propuesta local.')
        conn.execute(f'INSERT INTO operations_repair_changes(id,repair_id,actor_id,saved_at,payload_json) VALUES ({",".join([p]*5)})',
                     (mutation, ident, actor, stamp, blob))
        return result


def attach(store, ident, pid, mutation, content, actor, admin, uploader):
    store.initialize()
    token(pid)
    digest = hashlib.sha256(content).hexdigest()
    p = store.placeholder
    with store.connection() as conn:
        locked(store, conn)
        repair = read(store, ident, actor, admin, edit=True, conn=conn, lock=True)
        if repair['mutation_id'] != mutation:
            raise Conflict('La reparación cambió; la foto sigue conservada en tu dispositivo.')
        if pid not in {photo['id'] for photo in repair['photos']}:
            raise ValueError('Esta foto no pertenece a la reparación.')
        existing = conn.execute(f'SELECT digest FROM operations_repair_photos WHERE repair_id={p} AND id={p}', (ident, pid)).fetchone()
        if existing:
            if existing[0] != digest:
                raise Conflict('La fotografía ya tiene otro contenido.')
            return
        # Row lock serializes retries; Drive filename is stable even after a DB rollback.
        ref = uploader(ident, pid, content)
        conn.execute(f'INSERT INTO operations_repair_photos(repair_id,id,digest,drive_ref) VALUES ({p},{p},{p},{p})', (ident, pid, digest, ref))


def finish(store, ident, mutation, actor, admin=False):
    store.initialize()
    p = store.placeholder
    with store.connection() as conn:
        locked(store, conn)
        repair = read(store, ident, actor, admin, edit=True, conn=conn, lock=True)
        if repair['mutation_id'] != mutation:
            raise Conflict('La reparación cambió durante el envío. Revisa ambas versiones.')
        refs = photo_refs(store, conn, ident)
        if any(photo['id'] not in refs for photo in repair['photos']):
            raise Conflict('Aún faltan fotografías por confirmar. La visita no se ha finalizado.')
        repair['status'] = repair['requested_status']
        blob = json.dumps(repair, ensure_ascii=False)
        conn.execute(f'UPDATE operations_repairs SET state={p},payload_json={p} WHERE id={p}', (repair['status'], blob, ident))
        conn.execute(f'UPDATE operations_repair_changes SET payload_json={p} WHERE id={p}', (blob, mutation))
        return repair


def delete_visit(store, ident, mutation, expected_revision, actor, admin=False):
    """Retire a completed visit without erasing its folio, audit or photo refs."""
    store.initialize()
    ident, mutation = token(ident), token(mutation)
    if type(expected_revision) is not int:
        raise ValueError('Revisión de visita no válida.')
    p = store.placeholder
    with store.connection() as conn:
        locked(store, conn)
        suffix = ' FOR UPDATE' if store.dialect == 'postgres' else ''
        row = conn.execute(f'SELECT revision,payload_json FROM operations_repairs WHERE id={p}{suffix}', (ident,)).fetchone()
        if not row:
            raise LookupError('No se encontró la visita.')
        repair = json.loads(row[1])
        if not admin and repair['created_by'] != actor:
            raise PermissionError('Sólo quien registró la visita o el administrador puede retirarla.')
        if repair.get('status') == 'deleted':
            if repair.get('deletion_mutation_id') == mutation and repair.get('deleted_by') == actor:
                return repair
            raise Conflict('La visita ya fue retirada por otra operación.')
        if repair.get('status') != 'completed':
            raise ValueError('Sólo se puede retirar una visita finalizada y confirmada.')
        if row[0] != expected_revision:
            raise Conflict('La visita cambió. Actualiza antes de retirarla.')
        if conn.execute(f'SELECT 1 FROM operations_repair_changes WHERE id={p}', (mutation,)).fetchone():
            raise Conflict('Este identificador ya pertenece a otro cambio.')
        stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        repair.update(status='deleted', revision=row[0] + 1, deleted_at=stamp,
                      deleted_by=actor, deletion_mutation_id=mutation)
        blob = json.dumps(repair, ensure_ascii=False)
        updated = conn.execute(f'UPDATE operations_repairs SET revision={p},state={p},payload_json={p} WHERE id={p} AND revision={p}',
                               (repair['revision'], 'deleted', blob, ident, expected_revision))
        if updated.rowcount != 1:
            raise Conflict('La visita cambió. Actualiza antes de retirarla.')
        conn.execute(f'INSERT INTO operations_repair_changes(id,repair_id,actor_id,saved_at,payload_json) VALUES ({",".join([p]*5)})',
                     (mutation, ident, actor, stamp, blob))
        return repair


def listing(store, actor, admin=False, query='', equipment_key='', offset=0):
    store.initialize()
    p = store.placeholder
    where, params = ["state<>'deleted'"], []
    if not admin:
        where.append(f'(state=\'completed\' OR owner_id={p})')
        params.append(actor)
    if equipment_key:
        where.append(f'equipment_key={p}')
        params.append(equipment_key)
    if query:
        # Parameterized LIKE, with literal wildcard escaping.
        where.append(f"LOWER(payload_json) LIKE {p} ESCAPE '!' ")
        params.append('%' + query.lower().replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%')
    with store.connection() as conn:
        rows = conn.execute(f'SELECT payload_json FROM operations_repairs WHERE {" AND ".join(where)} ORDER BY service_date DESC,id DESC LIMIT 51 OFFSET {p}', (*params, offset)).fetchall()
    # Metadata/text only: never load photo bytes into history or bootstrap.
    return [json.loads(row[0]) for row in rows[:50]], offset + 50 if len(rows) > 50 else None
