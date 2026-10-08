(() => {
  // Operational screens already handle their own history and unsaved edits.
  if (document.getElementById('backBtn')) return;
  const path = location.pathname;
  if (path === '/acceso') return;
  const safePrevious = (() => {
    try {
      const url = new URL(document.referrer);
      return url.origin === location.origin && url.href !== location.href &&
        !/\/(acceso|salir|logout)(\/|$)/.test(url.pathname) ? url.href : null;
    } catch (_) { return null; }
  })();
  const fallback = /factur|articul|servici|sat|complement|pago/i.test(path)
    ? '/facturacion' : /cliente|importar/i.test(path) ? '/clientes'
    : /cotizacion|editar|costos|generar_pdf/i.test(path) ? '/cotizaciones' : '/';
  const nav = document.createElement('nav');
  nav.className = 'hsc-page-navigation';
  nav.setAttribute('aria-label', 'Navegación de la pantalla');
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = '← Atrás';
  button.addEventListener('click', () => {
    // Result screens use their explicit edit destination: never revisit a POST.
    const edit = document.querySelector('a[href*="form-partida"]');
    if (edit) { location.assign(edit.href); return; }
    if (/generar_pdf/.test(path)) { location.assign('/cotizaciones'); return; }
    if (safePrevious && history.length > 1) history.back();
    else location.assign(safePrevious || (fallback === path ? '/' : fallback));
  });
  nav.appendChild(button);
  const style = document.createElement('style');
  style.textContent = '.hsc-page-navigation{padding:10px 20px;position:relative;z-index:1}.hsc-page-navigation button{min-height:42px;padding:8px 15px;border:1px solid #9aaec7;border-radius:11px;background:transparent;color:inherit;font:600 15px system-ui;cursor:pointer}html[data-theme="dark"] .hsc-page-navigation button{border-color:#40536e}@media print{.hsc-page-navigation{display:none!important}}';
  document.head.appendChild(style);
  document.body.prepend(nav);
})();
