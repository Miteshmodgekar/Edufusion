/**
 * EduFusion Service Worker
 * Handles: offline caching, push notifications, background sync
 */

const CACHE_NAME    = 'edufusion-v7';
const OFFLINE_URL   = '/offline';

// Assets to pre-cache on install (app shell)
const PRECACHE_URLS = [
  '/',
  '/offline',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ── Install: pre-cache core app shell ────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      for (const url of PRECACHE_URLS) {
        try {
          await cache.add(url);
        } catch (err) {
          console.warn('[SW] Pre-cache partial failure:', url, err);
        }
      }
    })
  );
  self.skipWaiting();
});

// ── Message: handle SKIP_WAITING from page ───────────────────────────────────
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// ── Activate: migrate existing cached user data before purging old caches ─────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(async keys => {
      const oldKeys = keys.filter(k => k !== CACHE_NAME);
      const newCache = await caches.open(CACHE_NAME);
      for (const oldKey of oldKeys) {
        try {
          const oldCache = await caches.open(oldKey);
          const oldReqs = await oldCache.keys();
          for (const req of oldReqs) {
            const res = await oldCache.match(req);
            if (res) await newCache.put(req, res);
          }
        } catch (e) {}
        await caches.delete(oldKey);
      }
    }).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for API/HTML, cache-first for static & CDN ─────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // External CDN assets (Bootstrap, Bootstrap Icons, Google Fonts) — cache-first
  if (url.hostname.includes('cdn.jsdelivr.net') ||
      url.hostname.includes('fonts.googleapis.com') ||
      url.hostname.includes('fonts.gstatic.com')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => c.put(request, clone));
          }
          return response;
        }).catch(() => cached);
      })
    );
    return;
  }

  // Only handle same-origin requests below
  if (!url.origin.includes(self.location.origin)) return;

  // Non-GET requests (POST / PUT / DELETE)
  if (request.method !== 'GET') {
    event.respondWith(
      fetch(request).catch(() => new Response(
        JSON.stringify({ success: false, offline: true, message: 'You are offline. Reconnect to perform this action.' }),
        { headers: { 'Content-Type': 'application/json' } }
      ))
    );
    return;
  }

  // Read-only GET API calls — network-first with cache fallback
  if (url.pathname.startsWith('/dashboard/api') ||
      url.pathname.startsWith('/attendance/api') ||
      url.pathname.startsWith('/alert/my') ||
      url.pathname.startsWith('/marks/api')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => {
              c.put(request, clone);
              c.put(url.pathname, response.clone());
            });
          }
          return response;
        })
        .catch(async () => {
          let cached = await caches.match(request);
          if (!cached) cached = await caches.match(url.pathname);
          if (cached) return cached;
          return new Response(
            JSON.stringify({ success: false, offline: true, message: 'You are offline.' }),
            { headers: { 'Content-Type': 'application/json' } }
          );
        })
    );
    return;
  }

  // Other API endpoints — network-only with friendly offline response
  if (url.pathname.startsWith('/leave/api') ||
      url.pathname.startsWith('/alert/') ||
      url.pathname.startsWith('/auth/api') ||
      url.pathname.startsWith('/notify/')) {
    event.respondWith(
      fetch(request).catch(() => new Response(
        JSON.stringify({ success: false, offline: true, message: 'You are offline.' }),
        { headers: { 'Content-Type': 'application/json' } }
      ))
    );
    return;
  }

  // Static assets — cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(request, clone));
          return response;
        });
      })
    );
    return;
  }

  // HTML pages & Navigations — network-first with resilient multi-key cache fallback
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const ct = response.headers.get('content-type') || '';
          if (ct.includes('text/html')) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => {
              c.put(request, clone);
              c.put(url.pathname, response.clone());
              const normalized = url.pathname.endsWith('/') ? url.pathname.slice(0, -1) : url.pathname + '/';
              c.put(normalized, response.clone());
            });
          }
        }
        return response;
      })
      .catch(async () => {
        // 1. Try exact Request match
        let cached = await caches.match(request);
        if (cached) return cached;

        // 2. Try URL pathname
        cached = await caches.match(url.pathname);
        if (cached) return cached;

        // 3. Try with / without trailing slash
        const alt = url.pathname.endsWith('/') ? url.pathname.slice(0, -1) : url.pathname + '/';
        cached = await caches.match(alt);
        if (cached) return cached;

        // 4. If navigating to dashboard or root, match any stored dashboard
        if (url.pathname === '/' || url.pathname.startsWith('/dashboard')) {
          cached = await caches.match('/dashboard/');
          if (cached) return cached;
          cached = await caches.match('/dashboard');
          if (cached) return cached;
        }

        // 5. Fallback to offline page
        return (await caches.match(OFFLINE_URL)) || new Response('Offline', {
          status: 503,
          headers: { 'Content-Type': 'text/plain' }
        });
      })
  );
});

// ── Push Notifications ───────────────────────────────────────────────────────
self.addEventListener('push', event => {
  let data = { title: 'EduFusion', body: 'You have a new notification.' };
  try { data = event.data.json(); } catch (e) {}

  const options = {
    body:    data.body  || '',
    icon:    '/static/icons/icon-192.png',
    badge:   '/static/icons/icon-72.png',
    tag:     data.tag   || 'edufusion-notif',
    data:    { url: data.url || '/dashboard/' },
    vibrate: [200, 100, 200],
    requireInteraction: false,
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'EduFusion', options)
  );
});

// ── Notification Click ────────────────────────────────────────────────────────
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || '/dashboard/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(targetUrl);
    })
  );
});
