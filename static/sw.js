/**
 * EduFusion Service Worker
 * Handles: offline caching, push notifications, background sync
 */

const CACHE_NAME    = 'edufusion-v1';
const OFFLINE_URL   = '/offline';

// Assets to pre-cache on install (app shell)
const PRECACHE_URLS = [
  '/',
  '/dashboard/',
  '/offline',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

// ── Install: pre-cache app shell ─────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(PRECACHE_URLS).catch(err => {
        console.warn('[SW] Pre-cache partial failure (OK in dev):', err);
      });
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

// ── Activate: clear old caches ───────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for API/HTML, cache-first for static ───────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== 'GET') return;
  if (!url.origin.includes(self.location.origin)) return;

  // API calls — network-first (never serve stale API data)
  if (url.pathname.startsWith('/leave/api') ||
      url.pathname.startsWith('/attendance/api') ||
      url.pathname.startsWith('/dashboard/api') ||
      url.pathname.startsWith('/alert/') ||
      url.pathname.startsWith('/auth/api') ||
      url.pathname.startsWith('/notify/')) {
    event.respondWith(
      fetch(request).catch(() => new Response(
        JSON.stringify({ success: false, message: 'You are offline.' }),
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

  // HTML pages — network-first, fall back to cache, then offline
  event.respondWith(
    fetch(request)
      .then(response => {
        if (response.ok) {
          const ct = response.headers.get('content-type') || '';
          if (ct.includes('text/html')) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => c.put(request, clone));
          }
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then(cached => cached || caches.match(OFFLINE_URL))
      )
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
