const CACHE_NAME = 'hsc-shell-v8';
const SAFE_ASSETS = [
  '/static/hsc_theme.css',
  '/static/hsc_theme.js',
  '/static/hsc_inputs.js',
  '/static/pwa.js',
  '/static/img/hsc-app-192.png',
  '/static/img/hsc-app-512.png'
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
  if(url.origin !== self.location.origin || !SAFE_PATHS.has(url.pathname)) return;
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
  event.waitUntil(
    self.clients.matchAll({type:'window', includeUncontrolled:true}).then(clients => {
      const openClient = clients.find(client => new URL(client.url).origin === self.location.origin);
      if(openClient){
        openClient.navigate('/inicio-app');
        return openClient.focus();
      }
      return self.clients.openWindow('/inicio-app');
    })
  );
});
