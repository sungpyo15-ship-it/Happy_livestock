"""모바일 대시보드: HTML 1개 파일(외부 연결 없음, 오프라인 동작).

매일 아침 report 실행 시 생성 → OneDrive/Google Drive 동기화 폴더(SMX_PUBLISH_DIR)로 복사하면
휴대폰에서 앱처럼 열람. 홈 화면에 바로가기를 추가해 쓰면 됨.
화면: 방문 우선순위 목록(등급 필터·검색) → 목장 상세(방문 1장 요약과 같은 내용) / 그룹행동 탭.
"""
from __future__ import annotations

import json
from pathlib import Path

from .analysis import Analysis
from .normalize import AlertTypeMapper
from .visit import benchmarks, farm_payload


def build_dashboard(an: Analysis, mapper: AlertTypeMapper, out_path: Path) -> Path:
    bench = benchmarks(an)
    data = {
        "asOf": f"{an.as_of:%Y-%m-%d %H:%M}",
        "bench": {k: round(v, 1) for k, v in bench.items()} if bench else None,
        "farms": [farm_payload(s, an, mapper, bench) for s in an.farms],
        "clusters": len(an.clusters),
    }
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(TEMPLATE.replace("__DATA__", blob).replace("__ASOF__", data["asOf"]), encoding="utf-8")
    return out_path


TEMPLATE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1f4e78">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>smaXtec 목장 모니터</title>
<style>
:root{--bg:#f4f6f9;--card:#fff;--ink:#1b2430;--sub:#5d6b7a;--line:#e3e8ee;--navy:#1f4e78;
--urgent:#c62828;--high:#d9631e;--watch:#2f75b5;--ok:#4f8a2f;--chip:#eef2f7;--warn-bg:#fff4e5}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#18212b;--ink:#e6edf3;--sub:#93a1b0;
--line:#2a3542;--navy:#7fb2e5;--chip:#223040;--warn-bg:#3a2a12}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
word-break:keep-all;overflow-wrap:anywhere}
header{position:sticky;top:0;z-index:5;background:var(--navy);color:#fff;padding:12px 16px 10px;
padding-top:max(12px,env(safe-area-inset-top))}
@media (prefers-color-scheme:dark){header{background:#13283d}}
header h1{font-size:17px;margin:0}header .asof{font-size:12px;opacity:.85}
.tabs{display:flex;gap:6px;margin-top:8px}.tabs button{flex:1;border:0;border-radius:8px;padding:7px 0;
background:rgba(255,255,255,.15);color:#fff;font-size:13px}.tabs button.on{background:#fff;color:#1f4e78;font-weight:700}
main{padding:12px 16px 40px;max-width:760px;margin:0 auto}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
padding:5px 11px;font-size:13px}.filters button.on{background:var(--ink);color:var(--bg)}
input[type=search]{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--card);color:var(--ink);font-size:15px;margin-bottom:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:10px;
border-left:5px solid var(--g);cursor:pointer}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.name{font-weight:700;font-size:16px}.badge{font-size:12px;font-weight:700;color:#fff;background:var(--g);
border-radius:6px;padding:2px 7px;white-space:nowrap}
.meta{color:var(--sub);font-size:13px;margin-top:2px}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}.chip{background:var(--chip);border-radius:6px;
padding:2px 7px;font-size:12px}
.spark{display:block;margin-top:6px}
.g-긴급{--g:var(--urgent)}.g-우선{--g:var(--high)}.g-관찰{--g:var(--watch)}.g-정상{--g:var(--ok)}
h2{font-size:15px;margin:18px 0 8px;color:var(--navy)}
.back{border:0;background:none;color:var(--navy);font-size:15px;padding:4px 0;margin-bottom:6px}
.big{font-size:20px;font-weight:800}.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px;text-align:center}
.kpi b{display:block;font-size:18px}.kpi span{font-size:11px;color:var(--sub)}
.kpi small{font-size:11px;color:var(--sub)}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.warn{background:var(--warn-bg)}
ul.hl{margin:0;padding-left:18px}ul.hl li{margin:3px 0}
.tbl{width:100%;border-collapse:collapse;font-size:13px}.tbl th,.tbl td{padding:6px 4px;border-bottom:1px solid var(--line);
text-align:center}.tbl th{color:var(--sub);font-weight:600;font-size:12px}.tbl td:first-child,.tbl th:first-child{text-align:left}
.neg{color:var(--urgent);font-weight:700}.flag{color:var(--urgent);font-size:12px}
.scroll{overflow-x:auto}
label.ck{display:flex;gap:9px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line)}
label.ck input{width:20px;height:20px;margin-top:2px;flex:none}label.ck b{display:block;font-size:14px}
label.ck span{font-size:13px;color:var(--sub)}
textarea{width:100%;min-height:80px;border:1px solid var(--line);border-radius:10px;padding:8px;
background:var(--card);color:var(--ink);font-size:14px}
.foot{color:var(--sub);font-size:11px;margin-top:22px;text-align:center}
.empty{color:var(--sub);text-align:center;padding:30px 0}
</style></head>
<body>
<header><h1>smaXtec 목장 모니터</h1><div class="asof">기준 __ASOF__ · 최근 24시간</div>
<div class="tabs"><button data-t="list" class="on">방문 우선순위</button><button data-t="beh">그룹행동</button></div></header>
<main id="app"></main>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const st = { tab: "list", grade: "방문", q: "" };
const store = { get(k){ try { return JSON.parse(localStorage.getItem(k)); } catch(e){ return null; } },
                set(k,v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} } };
const day = D.asOf.slice(0,10);

function spark(tr, w=120, h=26){
  if(!tr.length) return "";
  const mx = Math.max(1, ...tr.map(x=>x.n)), bw = w/tr.length;
  return `<svg class="spark" width="${w}" height="${h}" role="img" aria-label="7일 추세">` + tr.map((x,i)=>{
    const bh = Math.max(1, x.n/mx*(h-2)); return `<rect x="${i*bw+1}" y="${h-bh}" width="${bw-3}" height="${bh}" rx="1.5"
    fill="${i==tr.length-1?'var(--g)':'var(--sub)'}" opacity="${i==tr.length-1?1:.45}"/>`; }).join("") + "</svg>";
}
function bars(tr){
  const w=320,h=90,mx=Math.max(1,...tr.map(x=>x.n)),bw=w/tr.length;
  return `<svg viewBox="0 0 ${w} ${h+16}" width="100%" role="img" aria-label="건강알림 7일 추세">` + tr.map((x,i)=>{
    const bh=x.n/mx*(h-14); return `<rect x="${i*bw+6}" y="${h-bh}" width="${bw-12}" height="${bh}" rx="3" fill="var(--navy)"
    opacity="${i==tr.length-1?1:.55}"/><text x="${i*bw+bw/2}" y="${h-bh-3}" font-size="11" text-anchor="middle"
    fill="currentColor">${x.n}</text><text x="${i*bw+bw/2}" y="${h+13}" font-size="10" text-anchor="middle"
    fill="currentColor" opacity=".7">${x.d}</text>`; }).join("") + "</svg>";
}
const sgn = v => v==null ? "-" : (v>0?"+":"")+v;

function list(){
  const counts = {}; D.farms.forEach(f => counts[f.grade] = (counts[f.grade]||0)+1);
  const groups = [["방문", `방문(긴급·우선) ${(counts["긴급"]||0)+(counts["우선"]||0)}`],
    ["긴급",`긴급 ${counts["긴급"]||0}`],["우선",`우선 ${counts["우선"]||0}`],["관찰",`관찰 ${counts["관찰"]||0}`],["전체",`전체 ${D.farms.length}`]];
  let fs = D.farms.filter(f => st.grade=="전체" || (st.grade=="방문" ? ["긴급","우선"].includes(f.grade) : f.grade==st.grade));
  if(st.q) fs = fs.filter(f => f.name.includes(st.q));
  return `<input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  <div class="filters">${groups.map(([k,l])=>`<button class="${st.grade==k?'on':''}" onclick="st.grade='${k}';render()">${l}</button>`).join("")}</div>
  ${fs.length ? fs.map(f => `<div class="card g-${f.grade}" onclick="location.hash='f='+encodeURIComponent('${f.id}')">
    <div class="row"><div class="name">${esc(f.name)}</div><span class="badge">${f.grade} ${f.score}</span></div>
    <div class="row"><div class="meta">건강알림 <b>${f.health_d0}</b>건/${f.health_animals}두 · 전일 ${f.health_d1}건${f.herd?` · ${f.herd}두`:""}${f.stale?" · ⚠지연":""}</div>${spark(f.trend)}</div>
    ${f.reasons.length?`<div class="chips">${f.reasons.map(r=>`<span class="chip">${esc(r)}</span>`).join("")}</div>`:""}
  </div>`).join("") : `<div class="empty">해당 목장 없음</div>`}`;
}

function beh(){
  const rows = []; D.farms.forEach(f => f.groups.forEach(g => rows.push({f, g})));
  const flagged = rows.filter(r => r.g.flags.length && !r.g.flags.includes("데이터 불완전(판정 제외)"));
  const b = D.bench;
  return `${b?`<div class="box">담당 목장 착유그룹 기준값 (${b.n}개 그룹 중앙값)<br>
    반추 <b>${Math.round(b.rum)}</b>분 · 횡와 <b>${b.lying_h}</b>h · 누워반추 <b>${Math.round(b.rum_lying_share)}</b>%</div>`:""}
  <h2>이상 그룹 ${flagged.length}개</h2>
  ${flagged.length ? `<div class="scroll"><table class="tbl"><tr><th>목장·그룹</th><th>반추</th><th>Δ</th><th>횡와h</th><th>판정</th></tr>
  ${flagged.map(({f,g})=>`<tr onclick="location.hash='f='+encodeURIComponent('${f.id}')"><td>${esc(f.name)}<br><small>${esc(g.name)} ${g.n??""}두</small></td>
  <td>${g.rum??"-"}</td><td class="${g.drum<0?'neg':''}">${sgn(g.drum)}</td><td>${g.lying_h??"-"}</td>
  <td class="flag">${g.flags.map(esc).join("<br>")}</td></tr>`).join("")}</table></div>` : `<div class="empty">이상 그룹 없음</div>`}`;
}

function detail(id){
  const f = D.farms.find(x => x.id == id); if(!f) return list();
  const ck = store.get(`ck:${f.id}:${day}`) || {}, memo = store.get(`memo:${f.id}`) || "";
  return `<button class="back" onclick="history.back()">‹ 목록</button>
  <div class="g-${f.grade}"><div class="row"><div class="big">${esc(f.name)}</div><span class="badge">${f.grade} ${f.score}점</span></div>
  <div class="meta">센서 ${f.herd??"-"}두 · 마지막 수집 ${f.last_ok??"없음"}${f.stale?" · ⚠데이터 지연":""}</div></div>
  <h2>핵심 요약</h2><div class="box"><ul class="hl">${f.headline.map(h=>`<li>${esc(h)}</li>`).join("")}</ul></div>
  <h2>알림 현황 (24h · 72h)</h2>
  <div class="kpis">${f.counts.map(c=>`<div class="kpi"><span>${esc(c.label)}</span><b>${c.n24}</b><small>72h ${c.n72}</small></div>`).join("")}</div>
  <h2>건강알림 7일 추세</h2><div class="box">${bars(f.trend)}<div class="meta">전일 ${f.health_d1}건 · 7일평균 ${f.health_7d??"-"}건${f.surge?" · <b class='neg'>급증</b>":""}</div></div>
  ${f.cluster?`<h2>집단신호 (의심 — 진단 아님)</h2><div class="box warn"><b>${f.cluster.n}두</b> · ${f.cluster.from} ~ ${f.cluster.to}<br>${esc(f.cluster.text)}
    <div class="meta">개체: ${f.cluster.animals.map(esc).join(", ")}</div></div>`:""}
  <h2>그룹 행동</h2>${f.groups.length?`<div class="scroll"><table class="tbl"><tr><th>그룹</th><th>반추</th><th>Δ7일</th><th>횡와h</th><th>Δ횡와</th><th>누워반추%</th></tr>
  ${f.groups.map(g=>`<tr><td>${esc(g.name)} <small>${g.n??""}</small>${g.flags.length?`<br><span class="flag">${g.flags.map(esc).join(", ")}</span>`:""}</td>
  <td>${g.rum??"-"}</td><td class="${g.drum<0?'neg':''}">${sgn(g.drum)}</td><td>${g.lying_h??"-"}</td><td>${sgn(g.dlying)}</td><td>${g.share??"-"}</td></tr>`).join("")}
  </table></div>${D.bench?`<div class="meta">기준(착유그룹 중앙값): 반추 ${Math.round(D.bench.rum)}분 · 횡와 ${D.bench.lying_h}h · 누워반추 ${Math.round(D.bench.rum_lying_share)}%</div>`:""}`
  :`<div class="empty">행동 데이터 없음</div>`}
  <h2>확인할 개체 (72시간)</h2>${f.animals.length?`<div class="scroll"><table class="tbl"><tr><th>개체</th><th>상태</th><th>알림</th><th>마지막</th></tr>
  ${f.animals.map(a=>`<tr><td><b>${esc(a.label)}</b></td><td>${esc(a.status||"-")}</td><td style="text-align:left">${esc(a.types)}</td><td>${a.last}</td></tr>`).join("")}</table></div>`
  :`<div class="empty">해당 개체 없음</div>`}
  <h2>현장 점검 체크리스트</h2><div class="box">${f.checklist.map((c,i)=>`<label class="ck"><input type="checkbox" ${ck[i]?"checked":""}
   onchange="tick('${f.id}',${i},this.checked)"><div><b>${esc(c.topic)}</b><span>${esc(c.text)}</span></div></label>`).join("")}</div>
  <h2>메모</h2><textarea placeholder="농장주 의견·조치·다음 방문 (이 휴대폰에만 저장)" oninput="store.set('memo:${f.id}',this.value)">${esc(memo)}</textarea>
  <div class="foot">센서 알림·행동지표 기반 선별 자료(진단 아님) · 목장 데이터 외부 공유 금지</div>`;
}
function tick(id,i,v){ const k=`ck:${id}:${day}`, o=store.get(k)||{}; o[i]=v; store.set(k,o); }

function render(keepFocus){
  const m = location.hash.match(/f=([^&]+)/);
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("on", !m && b.dataset.t==st.tab));
  const html = m ? detail(decodeURIComponent(m[1])) : (st.tab=="beh" ? beh() : list());
  const pos = keepFocus ? document.activeElement?.selectionStart : null;
  $("#app").innerHTML = html + (m?"":`<div class="foot">센서 알림·행동지표 기반 선별 자료(진단 아님) · 집단신호 ${D.clusters}건 · 외부 공유 금지</div>`);
  if(keepFocus){ const i=$("input[type=search]"); i.focus(); i.setSelectionRange(pos,pos); }
  if(m && !keepFocus) window.scrollTo(0,0);
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => { st.tab=b.dataset.t; if(location.hash) location.hash=""; render(); });
window.addEventListener("hashchange", () => render());
render();
</script></body></html>
"""
