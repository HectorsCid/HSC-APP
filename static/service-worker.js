const CACHE_NAME = 'hsc-shell-v37';
const SAFE_ASSETS = [
  '/static/hsc_theme.css',
  '/static/hsc_theme.js',
  '/static/hsc_inputs.js',
  '/static/pwa.js',
  '/static/operations_offline.js',
  '/static/operations_worklists.js',
  '/static/operations_repairs.js',
  '/static/operations_repairs.css',
  '/static/operations_payments.js',
  '/static/operations_payments.css',
  '/static/operations_profile.css',
  '/static/img/hsc-app-192.png',
  '/static/img/hsc-app-512.png',
  '/manifest.webmanifest',
  '/manifest-hsc-tecnico.webmanifest',
  '/manifest-hsc-partner.webmanifest'
];
const SAFE_PATHS = new Set(SAFE_ASSETS);

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SAFE_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith('hsc-shell-') && key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if(request.method !== 'GET') return;
  const url = new URL(request.url);
  if(url.origin !== self.location.origin) return;
  const operationsShell = request.mode === 'navigate' && (
    url.pathname === '/hsc-tecnico/' || url.pathname === '/hsc-partner/'
  );
  if(operationsShell){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    // El HTML contiene la identidad y empresa de la sesión: nunca se comparte ni
    // se restaura desde caché. Los pendientes sin conexión viven por cuenta aparte.
    event.respondWith(fetch(request,{signal:controller.signal}).catch(() => new Response(
      '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HSC sin conexión</title><body style="font-family:system-ui;background:#0f1b2d;color:#fff;padding:32px"><h1>HSC no pudo validar tu sesión</h1><p>Recupera la conexión y vuelve a abrir la app. Tus reportes pendientes siguen guardados en este dispositivo.</p><button onclick="location.reload()" style="padding:12px 18px">Reintentar</button></body></html>',
      {status:503,headers:{'Content-Type':'text/html;charset=utf-8','Cache-Control':'no-store'}}
    )).finally(()=>clearTimeout(timer)));
    return;
  }
  if(!SAFE_PATHS.has(url.pathname)) return;
  event.respondWith(
    caches.match(url.pathname).then(cached => cached || fetch(request).then(response => {
      if(response.ok){
        const copy = response.clone();
        event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(url.pathname, copy)));
      }
      return response;
    }))
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/inicio-app',self.location.origin);
  if(target.origin!==self.location.origin)return;
  const targetUrl=target.href;
  event.waitUntil(
    self.clients.matchAll({type:'window', includeUncontrolled:true}).then(clients => {
      const openClient = clients.find(client => {const url=new URL(client.url);return url.origin===target.origin&&url.pathname===target.pathname;});
      if(openClient){
        return openClient.navigate(targetUrl).then(()=>openClient.focus());
      }
      return self.clients.openWindow(targetUrl);
    })
  );
});

self.addEventListener('push', event => {
  let payload = {};
  try{ payload = event.data?.json() || {}; }catch(_){ payload = {body:event.data?.text() || ''}; }
  event.waitUntil(self.registration.showNotification(payload.title || 'HSC', {
    body: payload.body || 'Tienes un aviso pendiente.',
    icon: '/static/img/hsc-app-192.png',
    badge: '/static/img/hsc-app-192.png',
    tag: payload.tag || 'hsc-aviso',
    renotify: true,
    data: {url: payload.url || '/inicio-app'},
    actions: [{action:'open', title:'Abrir HSC'}]
  }));
});
