"""아이폰 '홈 화면 앱'(PWA) 파일 생성 + PC 내부 웹서버.

구조(데이터가 외부 서버에 저장되지 않음):
  [PC] reports/app/ ← 매일 06:00 갱신
  [PC] python -m smxmon serve  → http://127.0.0.1:8765 (PC 내부에서만 접속 가능)
  [PC] tailscale serve --bg 8765 → https://<PC이름>.<tailnet>.ts.net (내 Tailscale 기기만 접속, 종단간 암호화)
  [iPhone] Tailscale 앱 켜고 위 주소 열기 → 공유 → '홈 화면에 추가'
HTTPS 로 열리면 서비스워커가 마지막 화면을 저장 → PC 가 꺼져 있어도 마지막 데이터는 열림.
"""
from __future__ import annotations

import functools
import json
import logging
import struct
import zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("smxmon")
NAVY = (31, 78, 120)

MANIFEST = {
    "name": "smaXtec 목장 모니터", "short_name": "목장모니터", "lang": "ko",
    "start_url": "./", "scope": "./", "display": "standalone",
    "background_color": "#f4f6f9", "theme_color": "#1f4e78",
    "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
}

# 네트워크 우선(최신 데이터) → 실패하면 저장본(오프라인)
SERVICE_WORKER = """const C='smxmon-v4';
self.addEventListener('push',e=>{let d={};try{d=e.data.json();}catch(x){d={title:'목장모니터',body:e.data?e.data.text():''};}
  e.waitUntil(self.registration.showNotification(d.title||'목장모니터',{body:d.body||'',tag:d.tag||undefined,renotify:!!d.tag,
    data:{url:d.url||'/'},icon:'/icon-192.png',badge:'/icon-192.png'}));});
self.addEventListener('notificationclick',e=>{e.notification.close();const u=(e.notification.data&&e.notification.data.url)||'/';
  // 아이폰은 열린 앱을 다른 주소로 옮겨 주지 않음 → 주소를 저장해 두고 앱 화면이 스스로 이동(NAV_JS)
  e.waitUntil((async()=>{
    try{ const c=await caches.open('smx-nav'); await c.put('/__nav', new Response(JSON.stringify({u, t:Date.now()}))); }catch(x){}
    const ws=await clients.matchAll({type:'window',includeUncontrolled:true});
    for(const w of ws){ try{ w.postMessage({nav:u}); }catch(x){} }
    for(const w of ws){ if('focus' in w){ try{ return await w.focus(); }catch(x){} } }
    return clients.openWindow(u);})());});
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(['./','manifest.webmanifest','icon-192.png'])));});
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith((async()=>{
    try{
      const ctl=new AbortController();const t=setTimeout(()=>ctl.abort(),5000);
      const r=await fetch(e.request,{signal:ctl.signal,cache:'no-store'});clearTimeout(t);
      if(r.ok){const c=await caches.open(C);c.put(e.request,r.clone());}
      return r;
    }catch(err){
      const hit=await caches.match(e.request)||await caches.match('./');
      return hit||new Response('오프라인 — 저장된 화면 없음',{status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}});
    }
  })());
});
"""


def _png(size: int) -> bytes:
    """앱 아이콘: 남색 바탕 + 흰색 볼루스(원) + 심박선. 외부 라이브러리 없이 생성."""
    s = size
    pts = [(0.14, 0.58), (0.36, 0.58), (0.44, 0.40), (0.53, 0.76), (0.62, 0.30), (0.70, 0.58), (0.86, 0.58)]
    pts = [(x * s, y * s) for x, y in pts]
    th = s * 0.045
    cx, cy, rr = s * 0.5, s * 0.53, s * 0.36

    def seg_d(px, py, a, b):
        (x1, y1), (x2, y2) = a, b
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        return ((px - x1 - t * dx) ** 2 + (py - y1 - t * dy) ** 2) ** 0.5

    raw = bytearray()
    for y in range(s):
        raw.append(0)
        for x in range(s):
            px, py = x + 0.5, y + 0.5
            col = NAVY
            d_ring = abs(((px - cx) ** 2 + (py - cy) ** 2) ** 0.5 - rr)
            if d_ring < s * 0.022:
                col = (127, 178, 229)
            if min(seg_d(px, py, pts[i], pts[i + 1]) for i in range(len(pts) - 1)) < th:
                col = (255, 255, 255)
            raw += bytes(col)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def write_pwa(html: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "manifest.webmanifest").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "sw.js").write_text(SERVICE_WORKER, encoding="utf-8")
    for n in (180, 192, 512):   # 아이콘은 한 번만 생성
        p = out_dir / f"icon-{n}.png"
        if not p.exists():
            p.write_bytes(_png(n))
    return out_dir / "index.html"


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):  # 폴더 목록 노출 금지
        self.send_error(404)
        return None

    def log_message(self, fmt, *args):
        log.debug("app %s", fmt % args)


_Handler.extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json",
                           ".js": "text/javascript", ".html": "text/html; charset=utf-8"}


def serve(app_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    if not (app_dir / "index.html").exists():
        raise FileNotFoundError(f"{app_dir}/index.html 없음 → 먼저 리포트를 만드세요(2_RUN_REPORT.bat)")
    httpd = ThreadingHTTPServer((host, port), functools.partial(_Handler, directory=str(app_dir)))
    log.info("목장 모니터 앱 서버: http://%s:%d  (종료: Ctrl+C)", host, port)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


NAV_JS = r"""// 알림을 누르면 서비스워커가 '이동할 주소'를 저장 → 이 화면이 열리거나 다시 보일 때 그 주소로 이동(아이폰 대응)
function smxNav(){
  if(!("caches" in window)) return;
  caches.open("smx-nav").then(c => c.match("/__nav").then(r => r && r.json().then(d => {
    c.delete("/__nav");
    if(d && d.u && Date.now() - d.t < 5*60*1000 && (location.pathname + location.hash) !== d.u) location.href = d.u;
  }))).catch(() => {});
}
smxNav();
document.addEventListener("visibilitychange", () => { if(document.visibilityState === "visible") smxNav(); });
if("serviceWorker" in navigator) navigator.serviceWorker.addEventListener("message", e => {
  if(e.data && e.data.nav){ caches.open("smx-nav").then(c => c.delete("/__nav")).catch(()=>{});
    if((location.pathname + location.hash) !== e.data.nav) location.href = e.data.nav; } });
"""
