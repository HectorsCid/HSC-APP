(() => {
  const pending = new Map();
  let submitting = new WeakSet();
  let sequence = 0;
  let failure = '';
  let dismissed = false;
  const notice = document.createElement('div');
  notice.id = 'hscWaitingNotice';
  notice.hidden = true;
  notice.setAttribute('role', 'status');
  notice.setAttribute('aria-live', 'polite');
  const spinner = document.createElement('span');
  spinner.className = 'hsc-wait-spinner';
  spinner.setAttribute('aria-hidden', 'true');
  const label = document.createElement('span');
  const close = document.createElement('button');
  close.type = 'button';
  close.textContent = 'Cerrar';
  close.addEventListener('click', () => { failure = ''; dismissed = true; render(); });
  notice.append(spinner, label, close);
  document.body.appendChild(notice);
  const style = document.createElement('style');
  style.textContent = '#hscWaitingNotice{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:20050;display:flex;align-items:center;gap:12px;width:max-content;max-width:calc(100% - 24px);padding:14px 18px;border:1px solid #9fc1ef;border-radius:14px;background:#edf5ff;color:#1456a8;box-shadow:0 5px 22px #0002;font:600 16px/1.4 system-ui;pointer-events:none}#hscWaitingNotice[hidden]{display:none}.hsc-wait-spinner{flex-shrink:0;width:20px;height:20px;border:3px solid #bdd6f6;border-top-color:#2563eb;border-radius:50%;animation:hsc-wait-turn .8s linear infinite}@keyframes hsc-wait-turn{to{transform:rotate(360deg)}}html[data-theme="dark"] #hscWaitingNotice{background:#172c47;color:#b9d8ff;border-color:#3f618b}@media(prefers-reduced-motion:reduce){.hsc-wait-spinner{animation:none}}@media print{#hscWaitingNotice{display:none!important}}';
  document.head.appendChild(style);
  style.textContent += '#hscWaitingNotice[data-error="true"]{background:#fff0f0;color:#9b2424;border-color:#db9393;pointer-events:auto}#hscWaitingNotice button{border:1px solid currentColor;border-radius:8px;padding:6px 10px;background:transparent;color:inherit;cursor:pointer;font:inherit}html[data-theme="dark"] #hscWaitingNotice[data-error="true"]{background:#412329;color:#ffd0d0;border-color:#97515a}';
  style.textContent += '#hscWaitingNotice button{pointer-events:auto}';
  function render() {
    notice.hidden = dismissed || (!pending.size && !failure);
    notice.setAttribute('data-error', failure ? 'true' : 'false');
    spinner.hidden = Boolean(failure);
    close.hidden = false;
    label.textContent = failure || [...pending.values()].pop() || '';
  }
  function begin(message) {
    failure = '';
    dismissed = false;
    const id = ++sequence;
    pending.set(id, message);
    render();
    return () => { pending.delete(id); render(); };
  }
  function fail(message) {
    dismissed = false;
    failure = typeof message === 'string' && message.trim()
      ? message.trim().slice(0, 350) : 'No se confirmó la operación. Revisa el detalle antes de reintentar.';
    render();
  }
  // Background polling, typing suggestions and health checks stay silent.
  function messageFor(path, method) {
    if (/\/avisos\/(subscribe|config)/.test(path)) return null;
    if (/\/email(?:\/|$)|retry-email/.test(path) && method !== 'GET') return 'Enviando por correo…';
    if (/\/api\/facturar/.test(path)) return 'Enviando la factura para timbrado…';
    if (/\/api\/pagos\/crear/.test(path)) return 'Timbrando el complemento de pago…';
    if (/\/api\/pagos\/(info|resolve)/.test(path)) return 'Consultando factura y saldo…';
    if (/\/cancel$/.test(path) && method !== 'GET') return 'Procesando la cancelación…';
    if (/\/pdf(?:_json)?(?:\/|$)/.test(path)) return 'Preparando el PDF…';
    if (/\/documentos(?:\/|$)/.test(path)) return method === 'GET' ? 'Cargando documentos…' : method === 'DELETE' ? 'Quitando el documento…' : 'Subiendo el documento…';
    if (/\/api\/operaciones\//.test(path) && method !== 'GET') {
      if (/photo|evidence|receipt/.test(path)) return method === 'DELETE' ? 'Quitando la fotografía…' : 'Guardando la imagen…';
      if (/finaliz|complete/.test(path)) return 'Finalizando y confirmando el trabajo…';
      if (/sync|import|refresh/.test(path)) return 'Sincronizando con el servidor…';
      if (/payments/.test(path)) return 'Registrando el pago…';
      return 'Guardando los cambios en el servidor…';
    }
    if (/\/api\/(clientes|articulos|invoice-templates|invoice-schedules)/.test(path) && method !== 'GET') return method === 'DELETE' ? 'Eliminando el registro…' : 'Guardando los cambios…';
    if (/\/reportes\/auto\/run/.test(path)) return 'Iniciando la generación de reportes…';
    return null;
  }
  const originalFetch = window.fetch;
  window.fetch = async function(input, options) {
    let message;
    try {
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
      if (url.origin === location.origin) message = messageFor(url.pathname, (options?.method || input?.method || 'GET').toUpperCase());
    } catch (_) {}
    const finish = message ? begin(message) : () => {};
    try {
      const response = await originalFetch.apply(this, arguments);
      if (message) {
        if (!response.ok) fail(response.status === 401 || response.status === 403
          ? 'No se pudo completar: revisa tu sesión y permisos.'
          : `No se confirmó la operación (error ${response.status}). Revisa el estado antes de reintentar.`);
        // Inspect the JSON already consumed by the caller, without cloning photos
        // or PDFs, or changing HTTP errors into successful responses.
        if (typeof response.json === 'function') {
          const json = response.json.bind(response);
          response.json = async () => {
            try {
              const data = await json();
              if (!response.ok || data?.ok === false || data?.success === false) {
                const detail = data?.error;
                fail(typeof detail === 'string' ? detail : detail?.message || data?.message);
              }
              return data;
            } catch (error) {
              fail('El servidor no devolvió una confirmación válida. Revisa el estado antes de reintentar.');
              throw error;
            }
          };
        }
      }
      return response;
    } catch (error) {
      if (message) fail(error.name === 'AbortError'
        ? 'La operación tardó demasiado. No se confirmó el resultado; revisa el estado antes de reintentar.'
        : 'Se perdió la conexión. No se confirmó el resultado; revisa el estado antes de reintentar.');
      throw error;
    }
    finally { finish(); }
  };
  document.addEventListener('submit', event => {
    const form = event.target;
    // Existing async handlers retain ownership of validation and locking.
    if (event.defaultPrevented) return;
    if (submitting.has(form)) { event.preventDefault(); return; }
    const action = event.submitter?.formAction || form.action || location.href;
    const path = new URL(action, location.href).pathname;
    const text = event.submitter?.textContent || '';
    const message = /vista_previa|prev/.test(path) ? 'Preparando la vista previa…'
      : /generar_pdf|pdf/.test(path) ? 'Generando el PDF y guardándolo en Drive…'
      : /import/.test(path) ? 'Procesando la importación…'
      : /elimin|borrar/.test(path) ? 'Eliminando el registro…'
      : /finaliz/i.test(text) ? 'Finalizando el reporte…' : 'Guardando los cambios…';
    submitting.add(form);
    // Do not disable submitters: their name/value and formaction must be sent.
    begin(message);
  });
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    submitting = new WeakSet();
    pending.clear(); failure = ''; render();
  });
  window.HSCWaiting = {begin, fail, messageFor};
})();
