(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HscSyncRecovery = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';
  function describe(delivery = {}) {
    const paused = delivery.requires_confirmation === true;
    const date = delivery.started_at ? new Date(delivery.started_at) : null;
    const type = {report:'Reporte',equipment:'Equipo',client:'Cliente',fault:'Falla'}[delivery.entity_type] || 'Entidad';
    return {
      title: paused ? 'Sincronización pausada' : delivery.in_flight ? 'Sincronización en curso' : 'Sincronización disponible',
      canResume: paused && !delivery.in_flight,
      fields: [[type, delivery.entity_id || 'Sin referencia disponible'],
        ['Operación',delivery.operation_id || '—'],
        ['Fecha y hora',date && !Number.isNaN(date.valueOf()) ? date.toLocaleString('es-MX') : 'Sin registro anterior'],
        ['Revisión enviada',delivery.revision ?? (delivery.entity_type === 'report' ? 'No registrada en la versión anterior' : 'No aplica a esta entidad')],
        ['Último fallo',delivery.last_error || 'Sin fallo registrado'],
        ['Cambios en espera',String(delivery.waiting_changes ?? 0)]]
    };
  }
  function confirmation(target, checked) {
    if (!checked) throw Error('Confirma que ya no hay una solicitud anterior ejecutándose.');
    if (!target?.operation_id || !target?.attempt_id) throw Error('Vuelve a revisar la operación antes de confirmar.');
    return {operation_id:target.operation_id,attempt_id:target.attempt_id,confirm_no_request_in_flight:true};
  }
  function mount({panel, modal, fetchJson, onUpdate}) {
    let current = {}, target = null, busy = false, timer = null, generation = 0, returnFocus = null;
    const q = selector => modal.querySelector(selector);
    const status = q('[data-recovery-result]'), confirm = q('[data-recovery-confirm]');
    const checkbox = q('[data-recovery-check]'), close = q('[data-recovery-close]');
    function fields(container, delivery) {
      container.replaceChildren();
      for (const [label,value] of describe(delivery).fields) {
        const dt = document.createElement('dt'), dd = document.createElement('dd');
        dt.textContent = label; dd.textContent = value; container.append(dt,dd);
      }
    }
    function update(payload) {
      current = {...payload.sheet_delivery};
      const view = describe(current);
      panel.hidden = !view.canResume && !current.in_flight;
      panel.querySelector('[data-recovery-title]').textContent = view.title;
      panel.querySelector('[data-recovery-explanation]').textContent = view.canResume
        ? 'Los cambios están guardados en HSC. Esperan a que se revise una entrega que Google no confirmó.'
        : 'Google está procesando una entrega. Los demás cambios esperan su turno; no es necesario reanudarla.';
      fields(panel.querySelector('dl'),current);
      panel.querySelector('button').hidden = !view.canResume;
      const badge = document.querySelector('[data-sync-paused-badge]');
      if (badge) badge.hidden = !view.canResume;
    }
    function dismiss() {
      if (busy) return;
      generation++; clearTimeout(timer); modal.close(); returnFocus?.focus();
    }
    panel.querySelector('button').onclick = () => {
      // Freeze the reference shown to the owner. Polls never retarget consent.
      target = {...current}; generation++; clearTimeout(timer);
      fields(q('dl'),target); checkbox.checked = false; checkbox.disabled = false;
      confirm.disabled = true; confirm.hidden = false; status.textContent = ''; q('[data-recovery-audit]').textContent = '';
      returnFocus = document.activeElement; modal.showModal(); checkbox.focus();
    };
    checkbox.onchange = () => {confirm.disabled = busy || !checkbox.checked;};
    close.onclick = dismiss;
    modal.addEventListener('cancel', event => {event.preventDefault(); dismiss();});
    async function poll(version, rounds=0) {
      if (!modal.open || version !== generation) return;
      try {
        const payload = await fetchJson('/api/operaciones/sync-status');
        if (!modal.open || version !== generation) return;
        update(payload); onUpdate?.(payload);
        const d = payload.sheet_delivery || {}, remaining = d.waiting_changes ?? payload.pending;
        if (d.requires_confirmation) {
          status.textContent = 'Se encontró otra entrega sin confirmación. Tus cambios siguen guardados. Cierra esta ventana y revisa su nueva referencia.';
          return;
        }
        if (remaining === 0) {status.textContent = 'Sincronización completada. Google confirmó todos los cambios pendientes.'; return;}
        status.textContent = `Reanudación registrada. ${remaining} cambio(s) siguen guardados y esperan confirmación de Google.`;
        if (rounds < 29) timer = setTimeout(() => poll(version, rounds+1), 2000);
        else status.textContent += ' Puedes cerrar esta ventana; los reintentos continúan automáticamente.';
      } catch (_) {status.textContent = 'La reanudación quedó registrada. No se pudo consultar el avance; vuelve a revisar el diagnóstico. Los pendientes se conservan.';}
    }
    confirm.onclick = async () => {
      if (busy) return;
      let body;
      try {body = confirmation(target, checkbox.checked);} catch (error) {status.textContent = error.message; return;}
      busy = true; confirm.disabled = true; checkbox.disabled = true; close.disabled = true;
      status.textContent = 'Comprobando la referencia y reanudando…';
      try {
        const payload = await fetchJson('/api/operaciones/sync/resolve-uncertain', {method:'POST', body:JSON.stringify(body)});
        update(payload); confirm.hidden = true;
        const recovery = payload.recovery || {};
        q('[data-recovery-audit]').textContent = `Reanudó ${recovery.actor_name || 'el propietario'} · ${new Date(recovery.resumed_at).toLocaleString('es-MX')}`;
        status.textContent = payload.queue_state === 'disabled'
          ? 'Reanudación registrada. Los cambios se conservan, pero el envío automático está desactivado en la configuración del servidor.'
          : 'Reanudación registrada. La cola está activa; esperando confirmación de Google.';
        if (payload.queue_state !== 'disabled') timer = setTimeout(() => poll(generation), 500);
      } catch (error) {
        // No blind repeat after a lost HTTP reply: this attempt may have resolved.
        status.textContent = `${error.message} Cierra esta ventana y revisa el estado actualizado antes de volver a confirmar. Tus cambios se conservan.`;
        confirm.hidden = true;
      } finally {busy = false; close.disabled = false;}
    };
    return {update};
  }
  return {describe, confirmation, mount};
});
