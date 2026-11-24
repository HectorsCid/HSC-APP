"""Durable fence across the non-transactional PAC call and the local balance commit."""
import uuid
from flask import current_app
import requests


class StampReview(Exception):
    pass


def stamp(invoice_uuids, submit, *, confirmed_reps=None):
    store = current_app.extensions.get('operations_store')
    if not store or not store.enabled:
        raise StampReview('Base de pagos no disponible. No se inició el timbrado.')
    token = uuid.uuid4().hex
    p = store.placeholder
    with store.connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS billing_stamp_pending (invoice_uuid TEXT PRIMARY KEY, token TEXT NOT NULL, rep_id TEXT NOT NULL DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        for key in sorted(set(value.lower() for value in invoice_uuids)):
            old=conn.execute(f'SELECT token,rep_id FROM billing_stamp_pending WHERE invoice_uuid={p}',(key,)).fetchone()
            if old and old[1] and confirmed_reps:
                batch=conn.execute(f'SELECT invoice_uuid FROM billing_stamp_pending WHERE token={p}',(old[0],)).fetchall()
                if all(old[1] in confirmed_reps(row[0]) for row in batch):
                    conn.execute(f'DELETE FROM billing_stamp_pending WHERE token={p}',(old[0],))
            inserted = conn.execute(f'INSERT INTO billing_stamp_pending(invoice_uuid,token) VALUES ({p},{p}) ON CONFLICT(invoice_uuid) DO NOTHING RETURNING token',(key,token)).fetchone()
            if not inserted:
                raise StampReview('Hay un timbrado previo por verificar para esta factura. No se inició otro. Revisa Emitidas y Facturama.')

    def confirmed():
        with store.connection() as conn:
            conn.execute(f'DELETE FROM billing_stamp_pending WHERE token={p}',(token,))
    try:
        result = submit()
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (400,422):
            confirmed()
            raise
        raise StampReview('Facturama no confirmó el resultado. No vuelvas a timbrar; verifica la emisión.') from exc
    except requests.RequestException as exc:
        raise StampReview('Se perdió la respuesta del timbrado. Verifica la emisión antes de continuar.') from exc
    rep_id=result.get('Id') or result.get('id') if isinstance(result,dict) else None
    if rep_id:
        with store.connection() as conn:
            conn.execute(f'UPDATE billing_stamp_pending SET rep_id={p} WHERE token={p}',(str(rep_id),token))
    return result, confirmed
