// Autobuses Asturias · guarda la app y los horarios para poder consultarlos sin conexión.
const VERSION = 'autobuses-1.1';
const BASICOS = ['./', 'index.html', 'manifest.webmanifest', 'icono-180.png', 'icono-192.png', 'icono-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(BASICOS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function primeroRed(req){
  const cache = await caches.open(VERSION);
  try {
    const r = await fetch(req, {cache: 'no-cache'});
    if (r.ok) cache.put(req, r.clone());
    return r;
  } catch {
    return (await cache.match(req, {ignoreSearch: true})) ||
           (req.mode === 'navigate' ? cache.match('index.html') : Response.error());
  }
}

async function primeroCache(req){
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req);
  if (hit) return hit;
  const r = await fetch(req);
  if (r.ok || r.type === 'opaque') cache.put(req, r.clone());
  return r;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  // La app y los horarios: primero la red y, sin conexión, lo guardado
  if (url.origin === location.origin) {
    e.respondWith(url.pathname.match(/\.(png|webmanifest)$/) ? primeroCache(req) : primeroRed(req));
    return;
  }
  // Tipografías: se guardan la primera vez
  if (/fonts\.(googleapis|gstatic)\.com/.test(url.hostname)) e.respondWith(primeroCache(req));
});
