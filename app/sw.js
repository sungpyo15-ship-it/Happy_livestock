// 오프라인 캐시: 우사 안 통신이 끊겨도 앱이 열리도록
const CACHE = "hl-cccs-v8";
const ASSETS = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg", "./manual.pdf"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).catch(() => {})); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  // 로그인(Cloudflare Access)·외부 요청은 캐시하지 않음
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/cdn-cgi/")) return;
  e.respondWith(fetch(e.request).then(r => { if (r.ok && r.type === "basic") { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {}); } return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match("./index.html"))));
});
