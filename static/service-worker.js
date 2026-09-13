const CACHE_NAME = 'hsc-shell-v16';
const SAFE_ASSETS = [
  '/static/hsc_theme.css',
  '/static/hsc_theme.js',
  '/static/hsc_inputs.js',
  '/static/pwa.js',
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
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
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
    event.respondWith(fetch(request).then(response => {
      if(response.ok && !response.redirected && new URL(response.url).pathname === url.pathname){
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      }
      return response;
    }).catch(() => caches.match(request)));
    return;
  }
  if(!SAFE_PATHS.has(url.pathname)) return;
  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request).then(response => {
      if(response.ok){
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      }
      return response;
    }))
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || '/inicio-app';
  event.waitUntil(
    self.clients.matchAll({type:'window', includeUncontrolled:true}).then(clients => {
      const openClient = clients.find(client => new URL(client.url).origin === self.location.origin);
      if(openClient){
        openClient.navigate(targetUrl);
        return openClient.focus();
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
