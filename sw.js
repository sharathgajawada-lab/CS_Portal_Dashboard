const CACHE_NAME = 'cs-portal-v1';
const BATCH_URL = '/api/metrics/batch';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  
  // Cache batch API response for 5 minutes
  if (url.pathname === BATCH_URL) {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        if (cached) {
          const date = new Date(cached.headers.get('sw-cached-at') || 0);
          const age = (Date.now() - date.getTime()) / 1000;
          if (age < 300) {
            // Refresh in background
            fetch(e.request).then(r => {
              const copy = r.clone();
              const headers = new Headers(copy.headers);
              headers.set('sw-cached-at', new Date().toISOString());
              cache.put(e.request, new Response(copy.body, { headers }));
            });
            return cached;
          }
        }
        const response = await fetch(e.request);
        const copy = response.clone();
        const headers = new Headers(copy.headers);
        headers.set('sw-cached-at', new Date().toISOString());
        cache.put(e.request, new Response(copy.body, { headers }));
        return response;
      })
    );
    return;
  }

  // Cache static assets (HTML, JS, CSS)
  if (e.request.destination === 'document' || 
      e.request.destination === 'script' ||
      e.request.destination === 'style') {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        const fresh = fetch(e.request).then(r => {
          cache.put(e.request, r.clone());
          return r;
        });
        return cached || fresh;
      })
    );
  }
});
