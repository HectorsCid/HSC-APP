(()=>{
  if(!('serviceWorker' in navigator)) return;

  let installPrompt = null;
  let registration = null;
  const isStandalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);

  function addStyles(){
    if(document.getElementById('hscPwaStyles')) return;
    const style = document.createElement('style');
    style.id = 'hscPwaStyles';
    style.textContent = `
      .hsc-pwa-install{position:fixed;left:16px;bottom:16px;z-index:9997;display:none;align-items:center;gap:8px;padding:10px 14px;border:1px solid rgba(96,165,250,.5);border-radius:999px;background:#0f2f5f;color:#fff;font:700 13px/1.2 "Segoe UI",system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.28);cursor:pointer}
      .hsc-pwa-install.show{display:inline-flex}.hsc-pwa-install:hover{background:#16447f}
      .hsc-pwa-backdrop{position:fixed;inset:0;z-index:10020;display:none;place-items:center;padding:18px;background:rgba(2,6,23,.72)}
      .hsc-pwa-backdrop.show{display:grid}.hsc-pwa-card{width:min(420px,100%);padding:22px;border:1px solid #334155;border-radius:20px;background:#111d30;color:#eef5ff;box-shadow:0 24px 70px rgba(0,0,0,.4);font-family:"Segoe UI",system-ui,sans-serif}
      .hsc-pwa-card h2{margin:0 0 8px;font-size:21px}.hsc-pwa-card p{margin:0 0 16px;color:#b5c3d8;line-height:1.5}.hsc-pwa-card ol{margin:0 0 18px;padding-left:22px;color:#dbeafe;line-height:1.6}
      .hsc-pwa-close{width:100%;padding:11px;border:0;border-radius:11px;background:#2563eb;color:#fff;font:800 14px "Segoe UI",system-ui,sans-serif;cursor:pointer}
      @media(max-width:600px){.hsc-pwa-install{left:12px;bottom:12px}}
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

  function updateStatus(message){
    document.querySelectorAll('[data-pwa-status]').forEach(element => {
      element.textContent = message;
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
    updateStatus('Notificación de prueba enviada.');
  }

  function bindPanelActions(){
    document.querySelectorAll('[data-pwa-install]').forEach(button => button.addEventListener('click', requestInstall));
    document.querySelectorAll('[data-pwa-notify]').forEach(button => button.addEventListener('click', testNotification));
    if(isStandalone()) updateStatus('HSC está instalada. Ya puedes probar los avisos.');
    else if('Notification' in window && Notification.permission === 'granted') updateStatus('Notificaciones autorizadas; falta instalar HSC.');
  }

  async function mount(){
    addStyles();
    const button = installButton();
    if(isIos && !isStandalone()) button.classList.add('show');

    window.addEventListener('beforeinstallprompt', event => {
      event.preventDefault();
      installPrompt = event;
      if(!isStandalone()) button.classList.add('show');
      updateStatus('HSC está lista para instalarse en este dispositivo.');
    });
    window.addEventListener('appinstalled', () => {
      installPrompt = null;
      button.classList.remove('show');
    });

    try{
      registration = await navigator.serviceWorker.register('/service-worker.js', {scope:'/'});
      bindPanelActions();
    }catch(error){
      console.warn('HSC: no se pudo registrar el modo aplicación.', error);
      updateStatus('No se pudo preparar el modo aplicación. Recarga la página e inténtalo otra vez.');
    }
  }

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
