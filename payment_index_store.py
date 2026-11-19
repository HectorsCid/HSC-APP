"""Durable payment index, plus cross-process serialization of payment mutations."""
import json
import threading
from contextlib import contextmanager, ExitStack
from functools import wraps
from flask import current_app, has_app_context, jsonify

_local=threading.local()
_fallback=threading.RLock()


class PaymentBusy(TimeoutError):
    """The payment mutation has not started because its lock is busy."""


def store():
    value=current_app.extensions.get('operations_store') if has_app_context() else None
    return value if value and value.enabled else None


@contextmanager
def mutation():
    if getattr(_local,'active',False):
        yield
        return
    db=store()
    lock=db.resource_lock('billing-payment-index',timeout=2) if db else _fallback
    with ExitStack() as stack:
        try:
            stack.enter_context(lock)
        except TimeoutError as exc:
            raise PaymentBusy(str(exc)) from exc
        _local.active=True
        try:
            yield
        finally:
            _local.active=False


def serialized(fn):
    @wraps(fn)
    def guarded(*args,**kwargs):
        with mutation():
            return fn(*args,**kwargs)
    return guarded


def endpoint(fn):
    @wraps(fn)
    def guarded(*args,**kwargs):
        try:
            with mutation():
                return fn(*args,**kwargs)
        except PaymentBusy:
            return jsonify(ok=False,stage='busy',error='Otro pago se está procesando. No se inició esta operación; espera y actualiza antes de intentar.'),409
    return guarded


def read(loader):
    db=store()
    if not db:
        return loader()
    # Initialize outside the row transaction. A failed legacy read never commits an empty migration.
    with db.connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS billing_payment_index (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        row=conn.execute('SELECT payload FROM billing_payment_index WHERE id=1').fetchone()
    if row:
        return json.loads(row[0])
    with mutation():
        with db.connection() as conn:
            row=conn.execute('SELECT payload FROM billing_payment_index WHERE id=1').fetchone()
        if row:
            return json.loads(row[0])
        data=loader()
        if not isinstance(data,dict):
            raise ValueError('El índice anterior no tiene un formato válido; no se migró.')
        write(data)
        return data


def write(data):
    db=store()
    if not db:
        return False
    if not getattr(_local,'active',False):
        raise RuntimeError('El índice de pagos sólo se puede actualizar dentro del bloqueo de su operación.')
    with db.connection() as conn:
        p=db.placeholder
        conn.execute('CREATE TABLE IF NOT EXISTS billing_payment_index (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        conn.execute(f'INSERT INTO billing_payment_index(id,payload) VALUES (1,{p}) '
                     'ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(data,ensure_ascii=False),))
    return True
