"""Presentación de facturas en Partner. Nunca modifica el registro fiscal."""
import json
from datetime import datetime, timezone

STATUSES = {'automatic', 'pending', 'partial', 'paid'}


def read_display(store, client_id):
    store.initialize()
    with store.connection() as conn:
        rows = conn.execute(f'SELECT invoice_id,payload_json,revision FROM operations_partner_invoice_display WHERE client_id={store.placeholder}', (client_id,)).fetchall()
    return {row[0]: {**json.loads(row[1]), 'revision': row[2]} for row in rows}


def save_display(store, client_id, invoice_id, body, actor_id):
    status, hidden = body.get('status'), body.get('hidden')
    revision = body.get('expected_revision')
    if status not in STATUSES or not isinstance(hidden, bool) or type(revision) is not int or revision < 0:
        raise ValueError('Estado o visibilidad no válidos.')
    if not actor_id or not client_id or not invoice_id:
        raise ValueError('Falta identificar la empresa y la factura.')
    store.initialize()
    result = dict(status=status, hidden=hidden, updated_by=actor_id,
                  updated_at=datetime.now(timezone.utc).isoformat(), revision=revision+1)
    p = store.placeholder
    with store.connection() as conn:
        if revision:
            updated = conn.execute(f'UPDATE operations_partner_invoice_display SET payload_json={p},revision={p} WHERE client_id={p} AND invoice_id={p} AND revision={p}',
                                   (json.dumps(result), revision+1, client_id, invoice_id, revision))
        else:
            updated = conn.execute(f'INSERT INTO operations_partner_invoice_display(client_id,invoice_id,payload_json,revision) VALUES ({p},{p},{p},{p}) ON CONFLICT(client_id,invoice_id) DO NOTHING',
                                   (client_id, invoice_id, json.dumps(result), 1))
        if updated.rowcount != 1:
            raise ValueError('Otro administrador cambió esta factura. Actualiza antes de guardar.')
    return result


def apply_display(invoices, overrides, *, is_admin=False):
    result = []
    for invoice in invoices:
        display = overrides.get(str(invoice.get('id')), {})
        if display.get('hidden') and not is_admin:
            continue
        result.append({**invoice, 'portal_payment_status': display.get('status', 'automatic'),
                       'portal_hidden': bool(display.get('hidden')), 'portal_revision': display.get('revision', 0)})
    return result
