"""Administrative balances. Expenses retain their existing settlement workflow.

Salary is opt-in, denominated in integer MXN cents and due on Saturdays. Rate
changes are effective-dated; recording a payment is NOT a money transfer.
Reads neither generate expenses nor write ledger entries.
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo


class Conflict(ValueError):
    pass


def today():
    return datetime.now(ZoneInfo('America/Mexico_City')).date()


def week_start(day):
    return day - timedelta(days=(day.weekday() + 1) % 7)


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Fecha no válida.')
    return date.fromisoformat(value)


def cents(value, *, legacy=False):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or number > 10000000:
            raise ValueError()
        rounded = number.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if not legacy and number != rounded:
            raise ValueError()
        return int(rounded * 100)
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError('El importe debe ser positivo o cero, con hasta dos decimales.')


def _text(value, limit=500):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Texto no válido o demasiado largo.')
    return value.strip()


def _read(store, conn):
    people = {r[0]: dict(id=r[0], name=r[1], role=r[2], status=r[3]) for r in conn.execute(
        'SELECT id,name,role,status FROM operations_users').fetchall()}
    accounts = {r[0]: dict(id=r[0], revision=r[1], name=r[2], plans=json.loads(r[3])) for r in conn.execute(
        'SELECT user_id,revision,name,plans_json FROM operations_pay_accounts').fetchall()}
    expenses = []
    for r in conn.execute('SELECT id,user_id,technician_name,expense_date,amount,concept,status,reimbursable FROM operations_expenses').fetchall():
        expenses.append(dict(id=r[0], user_id=r[1], name=r[2], date=r[3], amount_cents=cents(r[4], legacy=True),
                             concept=r[5], status=r[6], reimbursable=bool(r[7])))
    payments = [json.loads(r[0]) for r in conn.execute('SELECT payload_json FROM operations_salary_payments').fetchall()]
    return people, accounts, expenses, payments


def _calculate(user_id, data, day):
    people, accounts, expenses, payments = data
    person = people.get(user_id, {})
    account = accounts.get(user_id, {})
    own_expenses = [e for e in expenses if e['user_id'] == user_id]
    if not person and not account and not own_expenses:
        raise LookupError('No se encontró el técnico.')
    if person.get('role') == 'client' and not account and not own_expenses:
        raise ValueError('Una cuenta de cliente no es una cuenta de pagos de técnico.')
    start = week_start(day)
    weeks = {}

    def bucket(key):
        if key not in weeks:
            end = iso_date(key) + timedelta(days=6)
            weeks[key] = dict(start=key, end=end.isoformat(), expenses=[], payments=[], expense_due_cents=0,
                              expense_paid_cents=0, salary_cents=0, salary_paid_cents=0, salary_due_cents=0,
                              projected_salary_cents=0)
        return weeks[key]

    bucket(start.isoformat())
    for expense in own_expenses:
        b = bucket(week_start(iso_date(expense['date'])).isoformat())
        b['expenses'].append(expense)
        if expense['reimbursable']:
            if expense['status'] in ('Reembolsado', 'Liquidado'):
                b['expense_paid_cents'] += expense['amount_cents']
            elif expense['status'] != 'Rechazado':
                b['expense_due_cents'] += expense['amount_cents']
    plans = sorted(account.get('plans', []), key=lambda p: p['effective_week'])
    if plans:
        cursor = iso_date(plans[0]['effective_week'])
        # Include the current week and future scheduled rate changes, not invented payroll.
        finish = max(start, iso_date(plans[-1]['effective_week']))
        pos = 0
        while cursor <= finish:
            while pos + 1 < len(plans) and plans[pos + 1]['effective_week'] <= cursor.isoformat():
                pos += 1
            amount = plans[pos]['amount_cents']
            if amount or cursor == start:
                b = bucket(cursor.isoformat())
                b['salary_cents' if cursor + timedelta(days=6) <= day else 'projected_salary_cents'] = amount
            cursor += timedelta(days=7)
    for payment in payments:
        if payment['user_id'] != user_id:
            continue
        b = bucket(payment['week_start'])
        b['payments'].append(payment)
        if not payment.get('voided_at'):
            b['salary_paid_cents'] += payment['amount_cents']
    for b in weeks.values():
        b['salary_due_cents'] = max(0, b['salary_cents'] - b['salary_paid_cents'])
        b['due_cents'] = b['expense_due_cents'] + b['salary_due_cents']
        b['expenses'].sort(key=lambda e: (e['date'], e['id']), reverse=True)
    expense_due = sum(b['expense_due_cents'] for b in weeks.values())
    salary_due = sum(b['salary_due_cents'] for b in weeks.values())
    return dict(id=user_id, name=person.get('name') or account.get('name') or (own_expenses[0]['name'] if own_expenses else '') or 'Cuenta histórica',
                status=person.get('status', 'historical'), role=person.get('role', 'historical'),
                revision=account.get('revision', 0), plans=plans, expense_due_cents=expense_due,
                salary_due_cents=salary_due, due_cents=expense_due + salary_due,
                projected_salary_cents=weeks[start.isoformat()]['projected_salary_cents'],
                current_week=start.isoformat(), payment_day=(start + timedelta(days=6)).isoformat(),
                as_of=day.isoformat(), weeks=sorted(weeks.values(), key=lambda w: w['start'], reverse=True))


def listing(store, day=None):
    store.initialize()
    with store.connection() as conn:
        data = _read(store, conn)
    people, accounts, expenses, _ = data
    ids = {p['id'] for p in people.values() if p['role'] == 'technician'} | set(accounts) | {e['user_id'] for e in expenses}
    rows = [_calculate(uid, data, day or today()) for uid in ids]
    for row in rows:
        row.pop('weeks')
        row.pop('plans')
    return sorted(rows, key=lambda r: (-r['due_cents'], r['name'].casefold(), r['id']))


def detail(store, user_id, day=None, offset=0):
    store.initialize()
    with store.connection() as conn:
        result = _calculate(user_id, _read(store, conn), day or today())
    offset = max(0, int(offset))
    total = len(result['weeks'])
    result['weeks'] = result['weeks'][offset:offset+12]
    result['next_offset'] = offset + 12 if offset + 12 < total else None
    return result


def mutate(store, user_id, body, actor, day=None):
    """Serialize an account; stable mutation IDs make a lost acknowledgement safe."""
    if not isinstance(body, dict):
        raise ValueError('Solicitud no válida.')
    mutation = body.get('mutation_id', '')
    if not isinstance(mutation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', mutation):
        raise ValueError('Identificador de operación no válido.')
    day = day or today()
    fingerprint = hashlib.sha256(json.dumps([user_id, actor, body], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    store.initialize()
    p = store.placeholder
    with store.connection() as conn:
        if store.dialect == 'sqlite':
            conn.execute('BEGIN IMMEDIATE')
        data = _read(store, conn)
        before = _calculate(user_id, data, day)
        conn.execute(f'INSERT INTO operations_pay_accounts(user_id,revision,name,plans_json) VALUES ({p},0,{p},\'[]\') ON CONFLICT(user_id) DO NOTHING', (user_id, before['name']))
        suffix = ' FOR UPDATE' if store.dialect == 'postgres' else ''
        row = conn.execute(f'SELECT revision,plans_json FROM operations_pay_accounts WHERE user_id={p}{suffix}', (user_id,)).fetchone()
        receipt = conn.execute(f'SELECT fingerprint FROM operations_pay_changes WHERE id={p}', (mutation,)).fetchone()
        if receipt:
            if receipt[0] != fingerprint:
                raise Conflict('Este identificador ya fue utilizado para otro cambio.')
            return dict(ok=True, duplicate=True)
        if type(body.get('expected_revision')) is not int or body['expected_revision'] != row[0]:
            raise Conflict('El saldo o sueldo cambió en otra pantalla. Actualiza antes de registrar el movimiento.')
        plans = json.loads(row[1])
        current = week_start(day)
        action = body.get('action')
        audit = dict(body=body, actor=actor, at=datetime.now(timezone.utc).isoformat())
        if action == 'salary':
            if before['role'] not in ('technician', 'admin'):
                raise ValueError('Configura sueldo sólo para una cuenta de personal existente.')
            effective = iso_date(body.get('effective_week'))
            if effective != week_start(effective) or effective < current or effective > current + timedelta(days=35):
                raise ValueError('Elige una semana actual o de las próximas cinco semanas, desde el domingo.')
            if plans and effective == current and day == current + timedelta(days=6):
                raise ValueError('El sueldo de este sábado ya corresponde al historial. Cambia el sueldo desde la próxima semana.')
            amount = cents(body.get('weekly_amount'))
            # Disabling salary also cancels already-scheduled future rates. Keep
            # their previous values in the mutation audit, never revive them later.
            if amount == 0:
                audit['cancelled_plans'] = [plan for plan in plans if plan['effective_week'] >= effective.isoformat()]
                plans = [plan for plan in plans if plan['effective_week'] < effective.isoformat()]
            else:
                plans = [plan for plan in plans if plan['effective_week'] != effective.isoformat()]
            plans.append(dict(effective_week=effective.isoformat(), amount_cents=amount, actor=actor, at=audit['at']))
            plans.sort(key=lambda plan: plan['effective_week'])
        elif action == 'payment':
            before = _calculate(user_id, _read(store, conn), day)
            key = iso_date(body.get('week_start')).isoformat()
            week = next((w for w in before['weeks'] if w['start'] == key), None)
            amount = cents(body.get('amount'))
            paid_on = iso_date(body.get('paid_on'))
            if not week or not amount or amount > week['salary_due_cents']:
                raise ValueError('El pago debe ser mayor a cero y no superar el sueldo pendiente de esa semana.')
            if not (iso_date(week['end']) <= paid_on <= day):
                raise ValueError('La fecha de pago debe estar entre ese sábado y hoy.')
            payment = dict(id=mutation, user_id=user_id, week_start=key, amount_cents=amount,
                           paid_on=paid_on.isoformat(), note=_text(body.get('note', '')), actor=actor, created_at=audit['at'])
            conn.execute(f'INSERT INTO operations_salary_payments(id,user_id,week_start,amount_cents,payload_json) VALUES ({p},{p},{p},{p},{p})',
                         (mutation, user_id, key, amount, json.dumps(payment)))
        elif action == 'void':
            payment_row = conn.execute(f'SELECT payload_json FROM operations_salary_payments WHERE id={p} AND user_id={p}', (body.get('payment_id'), user_id)).fetchone()
            if not payment_row:
                raise LookupError('No se encontró el pago.')
            payment = json.loads(payment_row[0])
            reason = _text(body.get('reason', ''))
            if payment.get('voided_at') or not reason:
                raise ValueError('El pago ya está anulado o falta el motivo de corrección.')
            payment.update(voided_at=audit['at'], voided_by=actor, void_reason=reason)
            conn.execute(f'UPDATE operations_salary_payments SET payload_json={p} WHERE id={p}', (json.dumps(payment), payment['id']))
        else:
            raise ValueError('Acción de pagos no válida.')
        conn.execute(f'UPDATE operations_pay_accounts SET revision=revision+1,name={p},plans_json={p} WHERE user_id={p}',
                     (before['name'], json.dumps(plans), user_id))
        conn.execute(f'INSERT INTO operations_pay_changes(id,user_id,fingerprint,payload_json) VALUES ({p},{p},{p},{p})',
                     (mutation, user_id, fingerprint, json.dumps(audit)))
    return dict(ok=True, duplicate=False)
