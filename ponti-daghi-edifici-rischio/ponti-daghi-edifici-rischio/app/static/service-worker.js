// A.6 — funzionamento offline (priorità alta, non rimandabile).
// L'app-shell (HTML/manifest/icone) viene precaricata in cache all'install,
// così l'app si apre anche a rete assente al primo avvio successivo
// all'installazione — non solo le scritture (gestite lato client via
// IndexedDB, vedi index.html), ma anche il caricamento della pagina stessa.
//
// Strategia:
// - App shell: cache-first, con aggiornamento in background quando la rete
//   c'è (stale-while-revalidate) — un ispettore che riapre l'app senza
//   rete vede comunque l'interfaccia.
// - Chiamate API (/api/...): network-first — i dati devono essere il più
//   possibile freschi quando la rete c'è; se non risponde, si passa la
//   mano alla coda offline lato client (index.html), che sa già come
//   gestire l'assenza di rete per le scritture. Per le letture (GET) in
//   assenza di rete, non c'è cache dei dati qui: l'interfaccia mostra la
//   coda locale (strutture non sincronizzate) più quanto già in memoria.

const CACHE_NAME = 'ponti-daghi-shell-v1';
const APP_SHELL = [
  '/',
  '/static/index.html',
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {
      // Se il precaricamento fallisce (es. install fatto offline la primissima
      // volta), non blocchiamo l'installazione: l'app funziona comunque come
      // sito normale finché la cache non si popola al primo giro online.
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Chiamate API: network-first, nessun fallback su dati in cache (dati
  // di readiness/ispezioni non vanno mai mostrati "vecchi" spacciandoli
  // per attuali durante un'emergenza reale — meglio un errore esplicito,
  // gestito da index.html, che un dato scaduto silenzioso).
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // App shell: cache-first con aggiornamento in background.
  if (event.request.method === 'GET') {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        const network = fetch(event.request)
          .then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
            }
            return response;
          })
          .catch(() => cached); // rete assente: quello che c'è in cache (se c'è)
        return cached || network;
      })
    );
  }
});
