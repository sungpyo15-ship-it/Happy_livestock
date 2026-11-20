"""개체 기록부 앱: HTML 1개 파일(외부 연결 없음). 목장 → 개체 목록(번식 현황) → 개체 상세(번식 타임라인·질병 기록)."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .breeding import CowRecord


def _cow(r: CowRecord, health: list[dict]) -> dict:
    d = asdict(r)
    for k in ("calving", "prev_calving", "pd_date", "conception", "expected_calving", "expected_dry", "dry_off", "abort"):
        d[k] = f"{d[k]:%Y-%m-%d}" if d[k] else None
    d["heats"] = [f"{x:%Y-%m-%d}" for x in r.heats]
    d["ais"] = [f"{x:%Y-%m-%d}" for x in r.ais]
    d["status_ko"], d["n_ai"], d["health"] = r.status_ko, r.n_ai, health
    d["n_memo"] = sum(1 for x in health if x["source"] != "센서 알림")
    return d


def build_app(farms: list[dict], today: date, out: Path) -> Path:
    """farms: [{id, name, beef, kpi, recs: [CowRecord], health: {aid: [...]}, memo_n}]"""
    data = {"today": f"{today:%Y-%m-%d}", "farms": [
        {"id": f["id"], "name": f["name"], "beef": f["beef"], "kpi": f["kpi"], "memo_n": f["memo_n"],
         "cows": [_cow(r, f["health"].get(r.animal_id, [])) for r in f["recs"]]} for f in farms]}
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("__DATA__", blob).replace("__TODAY__", data["today"]), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#2e5d3a">
<title>개체 기록부</title>
<style>
:root{--bg:#f5f6f2;--card:#fff;--ink:#1d261f;--sub:#5f6b62;--line:#e1e5dd;--head:#2e5d3a;--acc:#2e5d3a;--chip:#eef1ea;
--sel:#e7efe6;--bad:#b3261e;--preg:#e2efda;--dry:#ddebf7;--bred:#fff2cc;--open:#fce4d6;--neg:#f8cbad;--fresh:#ededed}
@media (prefers-color-scheme:dark){:root{--bg:#121612;--card:#1a201b;--ink:#e3eae3;--sub:#9aa79c;--line:#2b342c;--head:#1d3b25;
--acc:#8fc79c;--chip:#232b24;--sel:#22302a;--bad:#ef7b72;--preg:#25402a;--dry:#1f3448;--bred:#4a4120;--open:#4a3020;--neg:#55271c;--fresh:#2b2f2b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,
"Apple SD Gothic Neo","Malgun Gothic",sans-serif;word-break:keep-all;overflow-wrap:anywhere}
header{position:sticky;top:0;z-index:5;background:var(--head);color:#fff;padding:12px 16px;padding-top:max(12px,env(safe-area-inset-top))}
header .top{max-width:1100px;margin:0 auto;display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 12px;align-items:baseline}
header h1{font-size:17px;margin:0;white-space:nowrap}header .sub{font-size:12px;opacity:.85}
main{max-width:1100px;margin:0 auto;padding:12px 16px 40px}
.back{border:0;background:none;color:var(--acc);font-size:15px;padding:4px 0;cursor:pointer}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:5px 11px;font-size:13px;cursor:pointer}
.filters button.on{background:var(--ink);color:var(--bg)}
input[type=search]{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font-size:15px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:8px;cursor:pointer}
.card:hover{background:var(--sel)}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.name{font-weight:700}.meta{color:var(--sub);font-size:13px}
.badge{font-size:12px;border-radius:6px;padding:2px 7px;background:var(--chip);white-space:nowrap}
.badge.memo{background:var(--acc);color:var(--bg);font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:10px 0}
@media (min-width:700px){.kpis{grid-template-columns:repeat(4,1fr)}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 10px}
.kpi span{font-size:12px;color:var(--sub);display:block}.kpi b{font-size:19px}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:center;white-space:nowrap}
th{color:var(--sub);font-size:12px;font-weight:600;position:sticky;top:0;background:var(--card)}
td.l,th.l{text-align:left}td.wrap{white-space:normal;text-align:left;min-width:160px}
tr.click{cursor:pointer}tr.click:hover td{background:var(--sel)}
.st{border-radius:6px;padding:2px 6px;font-size:12px}
.s-pregnant{background:var(--preg)}.s-dry{background:var(--dry)}.s-bred{background:var(--bred)}.s-open{background:var(--open)}
.s-open_neg{background:var(--neg)}.s-fresh{background:var(--fresh)}.s-heifer{background:var(--chip)}
.flag{color:var(--bad);font-size:12px;font-weight:600}
h2{font-size:15px;margin:18px 0 8px;color:var(--acc)}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 14px;font-size:14px}
@media (min-width:700px){.grid2{grid-template-columns:repeat(4,minmax(0,1fr))}}
.grid2 span{color:var(--sub);font-size:12px;display:block}
.tl{list-style:none;margin:0;padding:0}.tl li{display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--line);font-size:14px}
.tl .d{color:var(--sub);min-width:88px;font-variant-numeric:tabular-nums}
.empty{color:var(--sub);text-align:center;padding:24px 0}
.note{color:var(--sub);font-size:12px;margin:6px 2px}
</style></head><body>
<header><div class="top"><h1>개체 기록부 <small style="font-weight:400;opacity:.8">데모</small></h1><div class="sub">__TODAY__ 기준 · smaXtec 기록</div></div></header>
<main id="app"></main>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const st = {memoOnly: D.farms.some(f=>f.memo_n>0), q:"", status:"전체", sort:null};
const STATUS = ["전체","주의","fresh","open","open_neg","bred","pregnant","dry","heifer"];
const SKO = {fresh:"신선",open:"공태",open_neg:"재수정",bred:"감정 전",pregnant:"임신",dry:"건유",heifer:"미경산"};
const v = x => x ?? "-";

function farmList(){
  let fs = D.farms.filter(f => (!st.memoOnly || f.memo_n>0) && (!st.q || f.name.includes(st.q)));
  return `<input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  <div class="filters"><button class="${st.memoOnly?'on':''}" onclick="st.memoOnly=true;render()">메모 쓰는 목장 ${D.farms.filter(f=>f.memo_n>0).length}</button>
   <button class="${!st.memoOnly?'on':''}" onclick="st.memoOnly=false;render()">전체 ${D.farms.length}</button></div>
  ${fs.map(f=>`<div class="card" onclick="location.hash='f-'+encodeURIComponent('${f.id}')">
    <div class="row"><div class="name">${esc(f.name)}</div>${f.memo_n?`<span class="badge memo">메모·기록 ${f.memo_n}</span>`:`<span class="badge">메모 없음</span>`}</div>
    <div class="meta">${f.kpi.n}두 · 임신우 ${v(f.kpi.pregnant_pct)}% · 공태 ${v(f.kpi.days_open)}일 · 수태당 수정 ${v(f.kpi.ai_per_conception)}회${f.kpi.problem?` · <span class="flag">주의 ${f.kpi.problem}두</span>`:""}</div>
  </div>`).join("") || `<div class="empty">해당 목장 없음</div>`}`;
}

function farmPage(id){
  const f = D.farms.find(x=>x.id==id); if(!f) return farmList();
  const k = f.kpi;
  let cows = f.cows.filter(c => (!st.q || (c.label+c.official_id).includes(st.q)) &&
     (st.status=="전체" || (st.status=="주의" ? c.flags.length : c.status==st.status)));
  return `<button class="back" onclick="location.hash=''">‹ 목장 목록</button>
  <div class="row"><h2 style="margin:4px 0;font-size:19px;color:var(--ink)">${esc(f.name)}</h2>${f.memo_n?`<span class="badge memo">메모·기록 ${f.memo_n}</span>`:""}</div>
  <div class="kpis">
   <div class="kpi"><span>임신우 비율</span><b>${v(k.pregnant_pct)}%</b></div>
   <div class="kpi"><span>평균 공태일수</span><b>${v(k.days_open)}일</b></div>
   <div class="kpi"><span>수태당 수정 횟수</span><b>${v(k.ai_per_conception)}회</b></div>
   <div class="kpi"><span>평균 첫 수정</span><b>${v(k.first_ai_dim)}일</b></div>
   <div class="kpi"><span>평균 분만간격</span><b>${v(k.calving_interval)}일</b></div>
   <div class="kpi"><span>주의 개체</span><b class="${k.problem?'flag':''}" style="font-size:19px">${k.problem}두</b></div>
   <div class="kpi"><span>30일 내 분만예정</span><b>${k.calving_30d}두</b></div>
   <div class="kpi"><span>전체 / 분만기록</span><b>${k.n} / ${k.cows}</b></div>
  </div>
  <input type="search" placeholder="관리번호·이표 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  <div class="filters">${STATUS.map(s=>{ const n = s=="전체"?f.cows.length : s=="주의"?f.cows.filter(c=>c.flags.length).length : f.cows.filter(c=>c.status==s).length;
     return n||s=="전체" ? `<button class="${st.status==s?'on':''}" onclick="st.status='${s}';render()">${SKO[s]||s} ${n}</button>` : ""; }).join("")}</div>
  <div class="scroll"><table><tr><th class="l">관리번호</th><th>상태</th><th>산차</th><th>분만일</th><th>착유일수</th><th>수정</th><th>마지막 수정</th><th>감정</th><th>분만예정</th><th>공태일수</th><th>질병기록</th><th class="l">주의</th></tr>
  ${cows.map(c=>`<tr class="click" onclick="location.hash='c-'+encodeURIComponent('${f.id}')+'-'+encodeURIComponent('${c.animal_id}')">
    <td class="l"><b>${esc(c.label)}</b></td><td><span class="st s-${c.status}">${SKO[c.status]}</span></td><td>${v(c.parity)}</td>
    <td>${v(c.calving)}</td><td>${v(c.dim)}</td><td>${c.n_ai||""}</td><td>${c.ais.length?c.ais[c.ais.length-1]:""}</td>
    <td>${c.pd_result===true?"임신":c.pd_result===false?"비임신":""}</td><td>${v(c.expected_calving)}</td><td>${v(c.days_open)}</td>
    <td>${c.n_memo||""}${c.health.length>c.n_memo?` <small style="color:var(--sub)">(+센서 ${c.health.length-c.n_memo})</small>`:""}</td>
    <td class="wrap flag">${c.flags.map(esc).join(", ")}</td></tr>`).join("")}</table></div>
  <div class="note">행을 누르면 개체 상세 · 공태일수 = 분만 → 수태일(임신 전이면 오늘) · 수집 이력 이전 분만은 분만일 '-'</div>`;
}

function cowPage(fid, aid){
  const f = D.farms.find(x=>x.id==fid), c = f && f.cows.find(x=>x.animal_id==aid); if(!c) return farmList();
  return `<button class="back" onclick="location.hash='f-'+encodeURIComponent('${f.id}')">‹ ${esc(f.name)}</button>
  <div class="row"><h2 style="margin:4px 0;font-size:20px;color:var(--ink)">${esc(c.label)}</h2><span class="st s-${c.status}">${esc(c.status_ko)}</span></div>
  <div class="meta">이표 ${esc(c.official_id||"-")} · ${esc(c.group||"")}</div>
  ${c.flags.length?`<div class="box" style="margin-top:8px"><span class="flag">${c.flags.map(esc).join(" · ")}</span></div>`:""}
  <h2>이번 산차 번식</h2><div class="box grid2">
   <div><span>산차</span>${v(c.parity)}</div><div><span>분만일</span>${v(c.calving)}</div><div><span>착유일수</span>${v(c.dim)}</div><div><span>공태일수</span>${v(c.days_open)}</div>
   <div><span>첫 발정</span>${c.first_heat_dim!=null?c.first_heat_dim+"일":"-"} (${c.heats.length}회)</div><div><span>첫 수정</span>${c.first_ai_dim!=null?c.first_ai_dim+"일":"-"}</div>
   <div><span>수정 (${c.n_ai}회)</span>${c.ais.join("<br>")||"-"}</div><div><span>임신감정</span>${v(c.pd_date)} ${c.pd_result===true?"임신":c.pd_result===false?"비임신":""}</div>
   <div><span>수태일(추정)</span>${v(c.conception)}</div><div><span>분만예정</span>${v(c.expected_calving)}</div>
   ${f.beef?"":`<div><span>건유예정</span>${v(c.expected_dry)}</div>`}<div><span>이전 분만 · 분만간격</span>${v(c.prev_calving)} ${c.calving_interval?"· "+c.calving_interval+"일":""}</div>
  </div>
  <h2>질병·치료 기록 (${c.health.length})</h2>
  ${c.health.length?`<div class="scroll"><table><tr><th>날짜</th><th class="l">출처</th><th class="l">분류</th><th class="l">내용</th><th class="l">조치</th><th class="l">메모</th></tr>
   ${c.health.map(h=>`<tr><td>${esc(h.date)}</td><td class="l">${esc(h.source)}</td><td class="l">${esc(h.category)}</td><td class="l"><b>${esc(h.what)}</b></td>
   <td class="l">${esc(h.action)}</td><td class="wrap">${esc(h.memo)}</td></tr>`).join("")}</table></div>`:`<div class="empty">기록 없음</div>`}
  <h2>번식 이벤트 이력 (최근)</h2>
  ${c.timeline.length?`<ul class="tl box">${c.timeline.map(t=>`<li><span class="d">${t.date}</span><b>${esc(t.kind)}</b> <span class="meta">${esc(t.extra)}</span></li>`).join("")}</ul>`:`<div class="empty">기록 없음</div>`}
  <div class="note">smaXtec 에 입력·감지된 기록 기준. 정액·약품명 등 smaXtec 에 없는 정보는 표시되지 않음</div>`;
}

function render(keep){
  const h = decodeURIComponent(location.hash.slice(1));
  const pos = keep ? document.activeElement?.selectionStart : null;
  let html;
  if(h.startsWith("c-")){ const [, fid, aid] = h.match(/^c-(.+?)-(.+)$/) || []; html = cowPage(fid, aid); }
  else if(h.startsWith("f-")) html = farmPage(h.slice(2));
  else html = farmList();
  $("#app").innerHTML = html;
  if(keep){ const i=$("input[type=search]"); if(i){ i.focus(); i.setSelectionRange(pos,pos); } } else window.scrollTo(0,0);
}
window.addEventListener("hashchange", () => { st.q=""; if(!location.hash.startsWith("#c-")) st.status = st.status; render(); });
render();
</script></body></html>
"""
