const CACHE_NAME = 'inspectit-v2';
const ASSETS = [
  '/web/inspectit-app.html',
  '/web/manifest.json',
  '/web/icon-192.png',
  '/web/icon-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  if (event.request.method !== 'GET') return;

  if (url.pathname.startsWith('/auth') ||
      url.pathname.startsWith('/companies') ||
      url.pathname.startsWith('/me') ||
      url.pathname.startsWith('/health')) {
    return;
  }

  // Network-first: always try to serve the current deployed version when
  // online, and only fall back to the cache when the network genuinely
  // fails (offline). The old cache-first strategy returned a cached
  // response immediately whenever one existed, so every deploy looked
  // broken to returning users until they manually cleared their cache —
  // the network fetch only ever updated the cache for a future load that
  // still wouldn't use it, since a cached response would already exist.
  event.respondWith(
    fetch(event.request).then(response => {
      if (response.ok) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
      }
      return response;
    }).catch(() => caches.match(event.request))
  );
});
