"""Recipient planning for durable calendar events; no external side effects."""
from urllib.parse import urlencode


def plan_task_notices(event_id, event):
    if event.get('kind', '').startswith('expense_'):
        expense = event['after']
        actor = event.get('actor_id', '')
        created = event['kind'] == 'expense_created'
        user = '' if created else expense['user_id']
        if user == actor and not created:
            return []
        return [dict(title='Nuevo gasto por revisar' if created else 'Actualización de tu gasto',
            body=f"{expense.get('technician_name') or ''} · {expense['concept']} · ${expense['amount']:,.2f} · {expense['status']}",
            url='/hsc-tecnico/?open=expenses', tag=f'expense-event:{event_id}',
            category='gastos' if created else 'pagos', audience='admin' if created or user=='owner' else 'technician',
            user_id=user, exclude_user_id=actor)]
    before, task = event.get('before') or {}, event['after']
    actor = event.get('actor_id', '')
    status = task['status']
    title = ('Solicitud de visita' if status == 'Solicitada' else
             'Actividad terminada' if status == 'Terminada' else
             'Actividad cancelada' if status == 'Cancelada' else
             'Visita confirmada' if before.get('status') == 'Solicitada' else
             'Actividad actualizada' if before else 'Nueva actividad programada')
    body = f"{task['title']} · {task['scheduled_date']} · {task.get('scheduled_time') or 'Sin hora definida'}"
    if event.get('actor_name'):
        body = event['actor_name'] + ' · ' + body
    result = []
    def add(audience, user='', client='', label=title):
        if user and user == actor:
            return
        params = {'open':'calendar', 'date':task['scheduled_date']}
        if client:
            params['client'] = client
        result.append(dict(title=label, body=body, category='agenda', audience=audience,
            user_id=user, client_id=client, exclude_user_id=actor,
            url=('/hsc-partner/?' if audience=='client' else '/hsc-tecnico/?')+urlencode(params),
            tag=f'task-event:{event_id}:{audience}:{user}:{client}'))
    add('admin')
    if status != 'Solicitada':
        current = set(task.get('assigned_user_ids') or [])
        previous = set(before.get('assigned_user_ids') or [])
        for user in sorted(current | previous):
            if user == 'owner':
                continue  # The admin broadcast already includes the owner.
            add('technician', user=user, label='Ya no tienes asignada esta actividad' if user not in current else title)
    client = task.get('client_id')
    if client and task.get('notify_client') and status != 'Solicitada':
        add('client', client=client)
    old_client = before.get('client_id')
    if old_client and before.get('notify_client') and old_client != client:
        add('client', client=old_client, label='Visita retirada de tu calendario')
        result[-1]['body'] = f"{before.get('title') or 'Actividad'} · {before.get('scheduled_date') or ''} · Esta visita fue retirada."
        result[-1]['url'] = '/hsc-partner/?'+urlencode({'open':'calendar','client':old_client,'date':before.get('scheduled_date','')})
    return result
