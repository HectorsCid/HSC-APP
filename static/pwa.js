(()=>{
  if(!('serviceWorker' in navigator)) return;

  let installPrompt = null;
  let registration = null;
  const isStandalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);

  function addStyles(){
    document.querySelectorAll('[id^="hscPwaStyles"]').forEach(previous => previous.remove());
    const style = document.createElement('style');
    style.id = 'hscPwaStylesV8';
    style.textContent = `
      .hsc-pwa-install{position:fixed;left:16px;bottom:16px;z-index:9997;display:none;align-items:center;gap:8px;padding:10px 14px;border:1px solid rgba(96,165,250,.5);border-radius:999px;background:#0f2f5f;color:#fff;font:700 13px/1.2 "Segoe UI",system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.28);cursor:pointer}
      .hsc-pwa-install.show{display:inline-flex}.hsc-pwa-install:hover{background:#16447f}
      .hsc-pwa-backdrop{position:fixed;inset:0;z-index:10020;display:none;place-items:center;padding:18px;background:rgba(2,6,23,.72)}
      .hsc-pwa-backdrop.show{display:grid}.hsc-pwa-card{width:min(420px,100%);padding:22px;border:1px solid #334155;border-radius:20px;background:#111d30;color:#eef5ff;box-shadow:0 24px 70px rgba(0,0,0,.4);font-family:"Segoe UI",system-ui,sans-serif}
      .hsc-pwa-card h2{margin:0 0 8px;font-size:21px}.hsc-pwa-card p{margin:0 0 16px;color:#b5c3d8;line-height:1.5}.hsc-pwa-card ol{margin:0 0 18px;padding-left:22px;color:#dbeafe;line-height:1.6}
      .hsc-pwa-close{width:100%;padding:11px;border:0;border-radius:11px;background:#2563eb;color:#fff;font:800 14px "Segoe UI",system-ui,sans-serif;cursor:pointer}
      h1.hsc-polished-title,h1.hsc-polished-title *{background:none!important;background-image:none!important;background-clip:border-box!important;-webkit-background-clip:border-box!important;-webkit-text-fill-color:currentColor!important}
      h1.hsc-polished-title{position:relative;display:block;width:max-content;max-width:100%;margin-top:10px!important;margin-bottom:20px!important;padding:9px 18px 10px!important;border:1px solid rgba(100,116,139,.3)!important;border-radius:10px!important;background:rgba(148,163,184,.08)!important;color:#14213d!important;font-family:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif!important;font-weight:650!important;font-variant-caps:normal!important;text-transform:none!important;letter-spacing:-.014em!important;line-height:1.12!important;text-wrap:balance;text-shadow:none!important;box-shadow:inset 0 2px 6px rgba(15,23,42,.13),inset 0 -1px 0 rgba(255,255,255,.5),0 1px 0 rgba(15,23,42,.035)!important}
      h1.hsc-polished-title span{color:inherit!important}
      h1.hsc-polished-title::after{display:none!important}
      html[data-theme="dark"] h1.hsc-polished-title{color:#f1f5f9!important;background:rgba(5,13,29,.2)!important;border-color:rgba(93,124,166,.34)!important;text-shadow:none!important;box-shadow:inset 0 2px 6px rgba(0,0,0,.34),inset 0 -1px 0 rgba(255,255,255,.04),0 1px 0 rgba(255,255,255,.025)!important}
      html[data-theme="dark"] h1.hsc-polished-title span{color:inherit!important}
      html[data-theme="dark"] h1.hsc-polished-title::after{background:#60a5fa}
      h1.hsc-polished-title.hsc-title-centered{margin-left:auto!important;margin-right:auto!important}
      .hsc-desktop-history{display:none;position:fixed;left:14px;top:10px;z-index:10010;overflow:hidden;border:1px solid rgba(148,163,184,.3);border-radius:12px;background:rgba(15,23,42,.9);box-shadow:0 10px 28px rgba(0,0,0,.26);backdrop-filter:blur(12px)}
      .hsc-desktop-history button{width:46px;height:40px;border:0;background:transparent;color:#e2e8f0;font:600 25px/1 system-ui;cursor:pointer}.hsc-desktop-history button+button{border-left:1px solid rgba(148,163,184,.22)}.hsc-desktop-history button:hover{background:rgba(255,255,255,.1)}
      @media(min-width:769px) and (pointer:fine){html.hsc-standalone .hsc-desktop-history{display:flex}html.hsc-standalone body .side{padding-top:62px!important}html.hsc-standalone .logout{top:62px!important}}
      @media(max-width:600px){.hsc-pwa-install{left:12px;bottom:12px}h1.hsc-polished-title{padding-bottom:8px!important}}
    `;
    document.head.appendChild(style);
  }

  function iosHelp(){
    let modal = document.getElementById('hscPwaHelp');
    if(!modal){
      modal = document.createElement('div');
      modal.id = 'hscPwaHelp';
      modal.className = 'hsc-pwa-backdrop';
      modal.innerHTML = '<div class="hsc-pwa-card" role="dialog" aria-modal="true" aria-labelledby="hscPwaTitle"><h2 id="hscPwaTitle">Instalar HSC</h2><p>En iPhone la instalación se termina desde Safari:</p><ol><li>Toca el botón <strong>Compartir</strong>.</li><li>Elige <strong>Agregar a pantalla de inicio</strong>.</li><li>Confirma con <strong>Agregar</strong>.</li></ol><button class="hsc-pwa-close" type="button">Entendido</button></div>';
      document.body.appendChild(modal);
      modal.querySelector('button').addEventListener('click', () => modal.classList.remove('show'));
      modal.addEventListener('click', event => { if(event.target === modal) modal.classList.remove('show'); });
    }
    modal.classList.add('show');
  }

  function installButton(){
    let button = document.getElementById('hscInstallApp');
    if(button) return button;
    button = document.createElement('button');
    button.id = 'hscInstallApp';
    button.type = 'button';
    button.className = 'hsc-pwa-install';
    button.innerHTML = '<span aria-hidden="true">⬇</span> Instalar HSC';
    button.addEventListener('click', requestInstall);
    document.body.appendChild(button);
    return button;
  }

  function polishTitles(){
    document.querySelectorAll('h1').forEach(title => {
      title.classList.add('hsc-polished-title');
      if(getComputedStyle(title).textAlign === 'center' || title.closest('header')?.matches('[data-title-centered]')){
        title.classList.add('hsc-title-centered');
      }
    });
  }

  function mountDesktopHistory(){
    if(!isStandalone()) return;
    document.documentElement.classList.add('hsc-standalone');
    if(document.getElementById('hscDesktopHistory')) return;
    const nav = document.createElement('nav');
    nav.id = 'hscDesktopHistory';
    nav.className = 'hsc-desktop-history';
    nav.setAttribute('aria-label', 'Navegación');
    nav.innerHTML = '<button type="button" aria-label="Volver" title="Atrás">←</button><button type="button" aria-label="Avanzar" title="Adelante">→</button>';
    const [back, forward] = nav.querySelectorAll('button');
    back.addEventListener('click', () => history.back());
    forward.addEventListener('click', () => history.forward());
    document.body.appendChild(nav);
  }

  function updateStatus(message){
    document.querySelectorAll('[data-pwa-status]').forEach(element => {
      element.textContent = message;
    });
  }

  function decodePushKey(value){
    const padding = '='.repeat((4 - value.length % 4) % 4);
    const raw = atob((value + padding).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map(character => character.charCodeAt(0)));
  }

  async function subscribeForServerNotifications(){
    if(!registration || !('PushManager' in window) || Notification.permission !== 'granted') return false;
    const configResponse = await fetch('/api/push/config', {headers:{'Accept':'application/json'}});
    const config = await configResponse.json();
    if(!configResponse.ok || !config.enabled || !config.public_key) return false;
    let subscription = await registration.pushManager.getSubscription();
    if(!subscription){
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly:true,
        applicationServerKey:decodePushKey(config.public_key)
      });
    }
    const response = await fetch('/api/push/subscribe', {
      method:'POST', headers:{'Content-Type':'application/json','Accept':'application/json'},
      body:JSON.stringify({subscription:subscription.toJSON()})
    });
    return response.ok;
  }

  function syncInstallActions(installed){
    document.querySelectorAll('[data-pwa-install]').forEach(button => {
      button.disabled = installed;
      button.textContent = installed ? '✓ Instalada' : '⬇ Instalar';
      button.title = installed ? 'HSC ya está instalada' : 'Instalar HSC en este dispositivo';
    });
  }

  async function requestInstall(){
    if(isStandalone()){
      updateStatus('HSC ya está instalada en este dispositivo.');
      return;
    }
    if(installPrompt){
      installPrompt.prompt();
      const choice = await installPrompt.userChoice;
      if(choice.outcome === 'accepted') updateStatus('Instalación aceptada. HSC aparecerá entre tus aplicaciones.');
      installPrompt = null;
      document.querySelectorAll('#hscInstallApp,[data-pwa-install]').forEach(button => button.classList.remove('show'));
      return;
    }
    if(isIos){
      iosHelp();
      updateStatus('En iPhone usa Compartir y después Agregar a pantalla de inicio.');
      return;
    }
    updateStatus('Abre el menú de Chrome y elige Instalar HSC o Agregar a pantalla de inicio.');
  }

  async function testNotification(){
    if(!('Notification' in window)){
      updateStatus('Este navegador no admite notificaciones.');
      return;
    }
    if(isIos && !isStandalone()){
      iosHelp();
      updateStatus('En iPhone primero instala HSC y después activa las notificaciones desde la app.');
      return;
    }
    const permission = await Notification.requestPermission();
    if(permission !== 'granted'){
      updateStatus('Las notificaciones no fueron autorizadas. Puedes habilitarlas en los permisos del navegador.');
      return;
    }
    registration = registration || await navigator.serviceWorker.ready;
    await registration.showNotification('HSC está listo', {
      body:'Las notificaciones funcionan correctamente en este dispositivo.',
      icon:'/static/img/hsc-app-192.png',
      badge:'/static/img/hsc-app-192.png',
      tag:'hsc-prueba',
      data:{url:'/inicio-app'}
    });
    try{
      const subscribed = await subscribeForServerNotifications();
      updateStatus(subscribed ? 'Notificaciones y recordatorios activados.' : 'Notificación de prueba enviada. Falta configurar los avisos del servidor.');
    }catch(error){
      console.warn('HSC: no se pudo activar el canal de avisos.', error);
      updateStatus('Notificación de prueba enviada; el canal de recordatorios todavía no está disponible.');
    }
  }

  function bindPanelActions(){
    document.querySelectorAll('[data-pwa-install]').forEach(button => button.addEventListener('click', requestInstall));
    document.querySelectorAll('[data-pwa-notify]').forEach(button => button.addEventListener('click', testNotification));
    syncInstallActions(isStandalone());
    if(isStandalone()) updateStatus('HSC está instalada. Ya puedes probar los avisos.');
    else if('Notification' in window && Notification.permission === 'granted') updateStatus('Notificaciones autorizadas; falta instalar HSC.');
  }

  async function mount(){
    addStyles();
    polishTitles();
    mountDesktopHistory();
    const hasInlineInstall = Boolean(document.querySelector('[data-pwa-install]'));
    const button = hasInlineInstall ? null : installButton();
    if(button && isIos && !isStandalone()) button.classList.add('show');

    window.addEventListener('beforeinstallprompt', event => {
      event.preventDefault();
      installPrompt = event;
      if(button && !isStandalone()) button.classList.add('show');
      updateStatus('HSC está lista para instalarse en este dispositivo.');
    });
    window.addEventListener('appinstalled', () => {
      installPrompt = null;
      button?.classList.remove('show');
      syncInstallActions(true);
    });

    try{
      registration = await navigator.serviceWorker.register('/service-worker.js', {scope:'/'});
      bindPanelActions();
      if('Notification' in window && Notification.permission === 'granted'){
        subscribeForServerNotifications().catch(error => console.warn('HSC: no se pudo renovar el canal de avisos.', error));
      }
    }catch(error){
      console.warn('HSC: no se pudo registrar el modo aplicación.', error);
      updateStatus('No se pudo preparar el modo aplicación. Recarga la página e inténtalo otra vez.');
    }
  }

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
