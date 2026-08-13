/*
 * Service worker for the board UI.
 *
 * Caches the shell (page, manifest, icons) so the app opens instantly and
 * still opens with no connection. It deliberately does NOT cache /api/ —
 * analysis has to come from the live engine, and a stale cached best-move
 * would be worse than an error message.
 *
 * On the GitHub Pages build the engine itself runs in the page under Pyodide,
 * so the Python sources are cached too and the whole thing works offline once
 * it has been opened at least once. On the Flask deployment those files do not
 * exist and the fetches simply fail, which is why the install step tolerates
 * misses one by one instead of failing the whole cache.
 */
const VERSION = "janggi-shell-v1";
// Only what exists on BOTH builds. Everything else (the in-page engine on the
// Pages build, the Python sources) is cached by the fetch handler below the
// first time the page asks for it -- pre-fetching it here would 404 on the
// Flask deployment and fill the console with errors on every install.
const SHELL = [
  "./",
  "./manifest.webmanifest",
  "./icon-180.png",
  "./icon-192.png",
  "./icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) =>
      // Add one at a time: a single 404 must not abort the whole install.
      Promise.all(
        SHELL.map((url) => cache.add(url).catch(() => undefined))
      )
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.includes("/api/")) return;      // never serve a cached move
  if (url.origin !== self.location.origin) return; // Pyodide's CDN, etc.

  // Network first so a deployed update is picked up, cache as the fallback.
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((cache) => cache.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("./index.html")))
  );
});
