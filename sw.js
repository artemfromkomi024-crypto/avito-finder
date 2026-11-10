const CACHE = 'avito-finder-v3';
const ASSETS = ['./index.html', './data.json', './manifest.webmanifest', './icon.svg'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// сеть в приоритете: так обновления приложения доезжают сразу
function networkFirst(request) {
  return fetch(request)
    .then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(request, copy));
      return res;
    })
    .catch(() => caches.match(request));
}

self.addEventListener('fetch', e => {
  const req = e.request;
  const url = new URL(req.url);

  if (req.mode === 'navigate' || url.pathname.endsWith('index.html') || url.pathname.endsWith('data.json')) {
    e.respondWith(networkFirst(req));
    return;
  }

  e.respondWith(caches.match(req).then(r => r || fetch(req)));
});
