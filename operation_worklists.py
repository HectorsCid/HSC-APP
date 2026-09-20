"""Flexible field worklists. No Sheets export, payment or bonus side effects."""
import json
import re
import uuid
from datetime import date, datetime, timezone


def save_worklist(store, body, *, actor_id, is_admin=False):
    store.initialize()
    p = store.placeholder
    ident = str(body.get('id') or '')
    mutation = str(body.get('mutation_id') or '')
    if not actor_id or not re.fullmatch(r'[\w-]{1,100}', ident) or not re.fullmatch(r'[\w-]{1,100}', mutation):
        raise ValueError('La lista y el cambio requieren identificadores válidos.')
    with store.connection() as conn:
        row = conn.execute(f'SELECT payload_json FROM operations_worklists WHERE id={p}', (ident,)).fetchone()
        old = json.loads(row[0]) if row else None
        if old and not is_admin and old['assigned_user_id'] != actor_id:
            raise PermissionError('Esta lista pertenece a otro técnico.')
        previous = conn.execute(f'SELECT list_id,actor_id FROM operations_worklist_changes WHERE id={p}', (mutation,)).fetchone()
        if previous:
            if previous[0] != ident or previous[1] != actor_id:
                raise PermissionError('El identificador del cambio ya está en uso.')
            return old  # Retry after a lost acknowledgement: never apply twice.
        expected = body.get('expected_revision', 0)
        if not isinstance(expected, int) or expected != (old['revision'] if old else 0):
            raise ValueError('La lista cambió en otro dispositivo. Conservamos tu propuesta; revisa la versión actual antes de reenviar.')
        assignee = str(body.get('assigned_user_id') or actor_id)
        if not is_admin and assignee != actor_id:
            raise PermissionError('Sólo el administrador puede asignar listas a otros técnicos.')
        if assignee != actor_id and (not old or old['assigned_user_id'] != assignee):
            person = conn.execute(f'SELECT role,status FROM operations_users WHERE id={p}', (assignee,)).fetchone()
            if not person or person[0] != 'technician' or person[1] != 'active':
                raise ValueError('Elige un técnico activo.')
        title = str(body.get('title') or '').strip()
        target_date = str(body.get('target_date') or '')
        try:
            if date.fromisoformat(target_date).isoformat() != target_date:
                raise ValueError()
        except ValueError:
            raise ValueError('Elige una fecha válida para la jornada.')
        if not title or len(title) > 120:
            raise ValueError('Escribe un nombre para la lista de hasta 120 caracteres.')
        status = body.get('status', 'active')
        if status not in ('active', 'completed', 'deleted'):
            raise ValueError('Estado de lista no válido.')
        if old and old['status'] == 'deleted':
            raise ValueError('Esta lista ya fue eliminada. Crea otra lista para continuar.')
        client = str(body.get('client_id') or '')
        if not conn.execute(f'SELECT id FROM operations_clients WHERE id={p}', (client,)).fetchone():
            raise ValueError('El cliente ya no está disponible.')
        equipment = {r[0] for r in conn.execute(f'SELECT id FROM operations_equipment WHERE client_id={p}', (client,)).fetchall()}
        items = body.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 200:
            raise ValueError('Selecciona entre 1 y 200 equipos.')
        stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        old_items = {item['id']: item for item in (old or {}).get('items', [])}
        normalized, seen, item_ids = [], set(), set()
        for entry in items:
            if not isinstance(entry, dict):
                raise ValueError('Equipo de lista no válido.')
            eid, rnd = str(entry.get('equipment_id') or ''), str(entry.get('round') or '')
            item_id = str(entry.get('id') or uuid.uuid4().hex)
            # Duplicate equipment is not a second unit of work, even with another round.
            if eid not in equipment or rnd not in ('1', '2', '3', '4') or eid in seen:
                raise ValueError('Revisa cliente, equipos duplicados y rondas de la lista.')
            if not re.fullmatch(r'[\w-]{1,100}', item_id) or item_id in item_ids:
                raise ValueError('Identificador de elemento no válido.')
            prior = old_items.get(item_id)
            if prior and (prior['equipment_id'], prior['round']) != (eid, rnd):
                raise ValueError('Al cambiar equipo o ronda, crea un nuevo elemento de lista.')
            seen.add(eid)
            item_ids.add(item_id)
            normalized.append(dict(id=item_id, equipment_id=eid, round=rnd,
                                   added_at=prior['added_at'] if prior else stamp))
        result = dict(id=ident, title=title, client_id=client, assigned_user_id=assignee,
                      target_date=target_date, status=status, items=normalized,
                      revision=expected + 1, created_by=old['created_by'] if old else actor_id,
                      created_at=old['created_at'] if old else stamp, updated_by=actor_id, updated_at=stamp)
        if old:
            updated = conn.execute(f'UPDATE operations_worklists SET payload_json={p},revision={p} WHERE id={p} AND revision={p}',
                                   (json.dumps(result, ensure_ascii=False), result['revision'], ident, expected))
        else:
            updated = conn.execute(f'INSERT INTO operations_worklists(id,revision,payload_json) VALUES ({p},{p},{p}) ON CONFLICT(id) DO NOTHING',
                                   (ident, result['revision'], json.dumps(result, ensure_ascii=False)))
        if updated.rowcount != 1:
            raise ValueError('La lista cambió mientras guardabas. Revisa la versión actual.')
        # Auditable revision history for future planning/bonus rules; no earnings generated.
        conn.execute(f'INSERT INTO operations_worklist_changes(id,list_id,actor_id,saved_at,payload_json) VALUES ({p},{p},{p},{p},{p})',
                     (mutation, ident, actor_id, stamp, json.dumps(result, ensure_ascii=False)))
        return result
