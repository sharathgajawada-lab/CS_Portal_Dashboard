// cs-portal-v3 — bumped to force cache clear on all clients
const CACHE_NAME = 'cs-portal-v3';
const BATCH_URL  = '/api/metrics/batch';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  // Delete ALL old caches on activation
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Cache batch API for 5 minutes (stale-while-revalidate)
  if (url.pathname === BATCH_URL) {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        if (cached) {
          const age = (Date.now() - new Date(cached.headers.get('sw-cached-at') || 0)) / 1000;
          if (age < 300) {
            fetch(e.request).then(r => {
              const h = new Headers(r.clone().headers);
              h.set('sw-cached-at', new Date().toISOString());
              cache.put(e.request, new Response(r.clone().body, { headers: h }));
            });
            return cached;
          }
        }
        const r = await fetch(e.request);
        const h = new Headers(r.clone().headers);
        h.set('sw-cached-at', new Date().toISOString());
        cache.put(e.request, new Response(r.clone().body, { headers: h }));
        return r;
      })
    );
    return;
  }

  // For HTML documents — ALWAYS fetch fresh, never serve from cache
  if (e.request.destination === 'document') {
    e.respondWith(fetch(e.request));
    return;
  }

  // Other static assets — cache with network fallback
  if (e.request.destination === 'script' || e.request.destination === 'style') {
    e.respondWith(
      caches.open(CACHE_NAME).then(async cache => {
        const cached = await cache.match(e.request);
        const fresh  = fetch(e.request).then(r => { cache.put(e.request, r.clone()); return r; });
        return cached || fresh;
      })
    );
  }
});
