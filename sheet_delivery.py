"""Durable Sheets delivery gate and audited, attempt-specific recovery."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import uuid


def _now():
    return datetime.now(timezone.utc).isoformat()


class SheetDeliveryMixin:
    def _sheet_delivery(self, key, operation_id=None, state=None, *, details=None, connection=None):
        p = self.placeholder
        with (nullcontext(connection) if connection is not None else self.connection()) as conn:
            row = conn.execute(f'SELECT operation_id,state,updated_at,details_json FROM operations_sheet_deliveries WHERE resource_key={p}', (key,)).fetchone()
            previous = dict(json.loads(row[3] or '{}'), operation_id=row[0], state=row[1], updated_at=row[2]) if row else None
            if state is None:
                return previous
            data = details if details is not None else {k: v for k, v in (previous or {}).items() if k not in {'operation_id', 'state', 'updated_at'}}
            stamp = _now()
            conn.execute(f'INSERT INTO operations_sheet_deliveries(resource_key,operation_id,state,updated_at,details_json) VALUES ({p},{p},{p},{p},{p}) '
                         'ON CONFLICT(resource_key) DO UPDATE SET operation_id=excluded.operation_id,state=excluded.state,updated_at=excluded.updated_at,details_json=excluded.details_json',
                         (key, operation_id, state, stamp, json.dumps(data, ensure_ascii=False)))
            return dict(data, operation_id=operation_id, state=state, updated_at=stamp)

    def pending_sync_count(self):
        self.initialize()
        with self.connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM operations_sync_outbox WHERE status!='synced'").fetchone()[0]

    def _recover_sheet_delivery(self, key, delivery, *, actor_id, actor_name, mode):
        """Caller owns the resource lock. Gate and audit commit together; outbox untouched."""
        recovery = dict(operation_id=delivery['operation_id'], attempt_id=delivery['attempt_id'],
                        actor_id=actor_id, actor_name=actor_name, resumed_at=_now(), mode=mode)
        p = self.placeholder
        with self.connection() as conn:
            conn.execute(f'INSERT INTO operations_sheet_recoveries(id,resource_key,attempt_id,payload_json,resumed_at) VALUES ({p},{p},{p},{p},{p})',
                         (uuid.uuid4().hex, key, recovery['attempt_id'], json.dumps(recovery, ensure_ascii=False), recovery['resumed_at']))
            self._sheet_delivery(key, delivery['operation_id'], 'idle', connection=conn)
        return recovery

    def _classify_abandoned_delivery(self, key):
        """Only call while holding the lock: an active writer cannot be here."""
        delivery = self._sheet_delivery(key)
        if not delivery or delivery['state'] == 'idle':
            return delivery
        if not delivery.get('attempt_id'):
            # A v21 intent cannot prove whether HTTP was sent. Assign its stable
            # recovery reference once; never assume it is safe because it is old.
            delivery.update(attempt_id=uuid.uuid4().hex, phase='unknown', started_at=delivery['updated_at'])
            with self.connection() as conn:
                row = conn.execute(f'SELECT entity_type,entity_id,last_error FROM operations_sync_outbox WHERE id={self.placeholder}', (delivery['operation_id'],)).fetchone()
            if row:
                delivery.update(entity_type=row[0], entity_id=row[1], last_error=row[2])
        if delivery.get('phase') in {'prepared', 'acknowledged'}:
            self._recover_sheet_delivery(key, delivery, actor_id='system', actor_name='Recuperación automática', mode='automatic')
            return self._sheet_delivery(key)
        delivery['last_error'] = delivery.get('last_error') or 'El proceso terminó sin confirmar si Google recibió la escritura.'
        return self._sheet_delivery(key, delivery['operation_id'], 'uncertain', details=delivery)

    def sheet_delivery_status(self, spreadsheet_id):
        self.initialize()
        key = 'sheets:' + spreadsheet_id
        in_flight = False
        try:
            with self.resource_lock(key, timeout=0):
                delivery = self._classify_abandoned_delivery(key)
        except TimeoutError:
            in_flight = True
            delivery = self._sheet_delivery(key)
        result = dict(delivery or {'state': 'idle'}, in_flight=in_flight)
        result['requires_confirmation'] = result['state'] == 'uncertain' and not in_flight
        result['waiting_changes'] = self.pending_sync_count()
        with self.connection() as conn:
            row = conn.execute(f'SELECT payload_json FROM operations_sheet_recoveries WHERE resource_key={self.placeholder} ORDER BY resumed_at DESC LIMIT 1', (key,)).fetchone()
        result['last_recovery'] = json.loads(row[0]) if row else None
        return result

    @contextmanager
    def sheet_write_guard(self, operation, spreadsheet_id, expected_revision=None, *, prepare=None):
        from operaciones_store import ReportConflictError
        key = 'sheets:' + spreadsheet_id
        with self.resource_lock(key):
            previous = self._classify_abandoned_delivery(key)
            if previous and previous['state'] != 'idle':
                raise ReportConflictError('Google tiene una entrega sin confirmación. Revisa esa operación antes de enviar otra revisión.', code='external_write_uncertain')
            details = dict(attempt_id=uuid.uuid4().hex, entity_type=operation['entity_type'],
                           entity_id=operation['entity_id'], revision=expected_revision,
                           started_at=_now(), phase='prepared', last_error='')
            self._sheet_delivery(key, operation['id'], 'active', details=details)
            delivery = {'sent': False}
            try:
                # All external reads occur before the send phase. A crash here
                # is safe to retry automatically, even after a server restart.
                if prepare is not None:
                    delivery['plan'] = prepare()
                details['phase'] = 'sending'
                self._sheet_delivery(key, operation['id'], 'active', details=details)
                with self.connection() as conn:
                    p = self.placeholder
                    table = {'report': 'operations_reports', 'equipment': 'operations_equipment',
                             'client': 'operations_clients', 'fault': 'operations_faults'}.get(operation['entity_type'])
                    if self.dialect == 'sqlite':
                        conn.execute('BEGIN IMMEDIATE')
                    elif table:
                        conn.execute(f'SELECT id FROM {table} WHERE id={p} FOR UPDATE', (operation['entity_id'],)).fetchone()
                    row = conn.execute(f'SELECT status FROM operations_sync_outbox WHERE id={p}', (operation['id'],)).fetchone()
                    if not row or row[0] == 'synced':
                        raise ReportConflictError('La entrega fue reemplazada por una revisión más reciente.', code='stale_sync')
                    if operation['entity_type'] == 'report':
                        report = conn.execute(f'SELECT revision FROM operations_reports WHERE id={p}', (operation['entity_id'],)).fetchone()
                        if not report or (expected_revision is not None and int(report[0]) != int(expected_revision)):
                            raise ReportConflictError('La revisión del reporte cambió antes de enviarse.', code='stale_sync')
                    delivery['connection'] = conn
                    yield delivery
                    if not self.mark_sync_success(operation['id'], operation['entity_type'], operation['entity_id'],
                                                  connection=conn, expected_revision=expected_revision):
                        raise ReportConflictError('La entrega cambió durante su confirmación.', code='stale_sync')
                    details['phase'] = 'acknowledged'
                    self._sheet_delivery(key, operation['id'], 'active', details=details, connection=conn)
            except BaseException as exc:
                status = getattr(getattr(exc, 'resp', None), 'status', None)
                rejected = status in {400, 401, 403, 404, 409, 412, 422, 429}
                details['last_error'] = str(exc)[:1500] or type(exc).__name__
                details['phase'] = 'sending' if delivery['sent'] else 'prepared'
                self._sheet_delivery(key, operation['id'], 'uncertain' if delivery['sent'] and not rejected else 'idle', details=details)
                raise
            else:
                self._sheet_delivery(key, operation['id'], 'idle', details=details)

    def resolve_sheet_delivery(self, spreadsheet_id, operation_id, *, attempt_id='', external_quiescent=False,
                               actor_id='', actor_name=''):
        if not external_quiescent:
            raise ValueError('Confirma primero que ya no hay una solicitud anterior ejecutándose.')
        if not operation_id or not attempt_id or not actor_id:
            raise ValueError('Falta la referencia de esta revisión. Vuelve a abrir el diagnóstico.')
        key = 'sheets:' + spreadsheet_id
        try:
            with self.resource_lock(key, timeout=0):
                delivery = self._classify_abandoned_delivery(key)
                if not delivery or delivery['state'] != 'uncertain' or delivery['operation_id'] != operation_id or delivery.get('attempt_id') != attempt_id:
                    raise ValueError('El estado cambió; vuelve a revisar la operación. No se liberó otra entrega.')
                return self._recover_sheet_delivery(key, delivery, actor_id=actor_id, actor_name=actor_name, mode='manual')
        except TimeoutError as exc:
            raise ValueError('Hay una solicitud ejecutándose. Espera a que termine y vuelve a revisar.') from exc
