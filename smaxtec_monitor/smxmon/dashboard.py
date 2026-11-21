"""smaXtec 목장 모니터 '앱' — HTML 1개 파일(외부 연결 없음, 오프라인 동작).

일일 리포트(엑셀)·방문요약(엑셀)의 내용을 모든 목장에 대해 한 화면에서 봄.
- 탭: 요약표 · 목장별 · 7일추세 · 그룹행동 · 집단신호
- PC(넓은 화면): 왼쪽 목장 목록 + 오른쪽 목장 요약 / 휴대폰: 목록 → 상세
- reports/smaxtec_app.html 이 항상 최신본 (5_OPEN_APP.bat 로 열기)
"""
from __future__ import annotations

import json
from pathlib import Path

from .analysis import Analysis
from .normalize import AlertTypeMapper
from .pwa import write_pwa
from .trials_report import trial_payload
from .transition import INDICATORS
from .visit import KPI_TYPES, benchmarks, farm_payload


def build_dashboard(an: Analysis, mapper: AlertTypeMapper, out_path: Path) -> Path:
    bench = benchmarks(an)
    f0 = next(iter(an.farms), None)
    data = {
        "asOf": f"{an.as_of:%Y-%m-%d %H:%M}",
        "bench": {k: round(v, 1) for k, v in bench.items()} if bench else None,
        "kpiLabels": {c: mapper.label(c) for c in KPI_TYPES},
        "dates": [f"{d:%m/%d}" for d in sorted(f0.trend)] if f0 else [],
        "farms": [farm_payload(s, an, mapper, bench) for s in an.farms],
        "clusters": len(an.clusters),
        "tbench": {k: round(v, 1) for k, v in an.transition_bench.items()},
        "tInd": [[c, lbl] for c, lbl in INDICATORS],
        "months": f0.history.months if f0 and f0.history else [],
        "hbench": an.history_bench,
        "trials": [trial_payload(r) for r in getattr(an, "trials", [])],
    }
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = TEMPLATE.replace("__DATA__", blob).replace("__ASOF__", data["asOf"])
    out_path.write_text(html, encoding="utf-8")
    (out_path.parent / "smaxtec_app.html").write_text(html, encoding="utf-8")  # 항상 최신본
    write_pwa(html, out_path.parent / "app")   # 아이폰 홈 화면 앱(서버용)
    return out_path


TEMPLATE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1f4e78">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="목장모니터">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icon-180.png">
<title>smaXtec 목장 모니터</title>
<style>
:root{--bg:#f4f6f9;--card:#fff;--ink:#1b2430;--sub:#5d6b7a;--line:#e3e8ee;--navy:#1f4e78;--head:#1f4e78;
--urgent:#c62828;--high:#d9631e;--watch:#2f75b5;--ok:#4f8a2f;--chip:#eef2f7;--warn-bg:#fff4e5;--sel:#e8f0fa;
--heat:248,105,107}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#18212b;--ink:#e6edf3;--sub:#93a1b0;--line:#2a3542;
--navy:#7fb2e5;--head:#13283d;--chip:#223040;--warn-bg:#3a2a12;--sel:#1d2e42;--heat:235,87,87}}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
word-break:keep-all;overflow-wrap:anywhere}
header{position:sticky;top:0;z-index:5;background:var(--head);color:#fff;padding:10px 16px 0;
padding-top:max(10px,env(safe-area-inset-top))}
header .top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:2px 10px;max-width:1280px;margin:0 auto}
header h1{white-space:nowrap}
header h1{font-size:17px;margin:0}header .asof{font-size:12px;opacity:.85}
.tabs{display:flex;gap:4px;overflow-x:auto;margin:8px auto 0;max-width:1280px;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tabs button{flex:none;border:0;border-radius:8px 8px 0 0;padding:8px 14px;background:transparent;color:#fff;
opacity:.75;font-size:14px;cursor:pointer}.tabs button.on{background:var(--bg);color:var(--ink);opacity:1;font-weight:700}
main{padding:12px 16px 40px;max-width:1280px;margin:0 auto}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;
padding:5px 11px;font-size:13px;cursor:pointer}.filters button.on{background:var(--ink);color:var(--bg)}
input[type=search]{width:100%;padding:9px 12px;border:1px solid var(--line);border-radius:10px;
background:var(--card);color:var(--ink);font-size:15px;margin-bottom:10px}
.split{display:grid;grid-template-columns:340px minmax(0,1fr);gap:18px;align-items:start}
.split aside{position:sticky;top:96px;max-height:calc(100vh - 110px);overflow:auto;padding-right:4px}
@media (max-width:900px){.split{display:block}.split aside{position:static;max-height:none;overflow:visible}
.split.has-detail aside{display:none}.split:not(.has-detail) section{display:none}}
@media (min-width:901px){.back{display:none}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:11px 13px;margin-bottom:8px;
border-left:5px solid var(--g);cursor:pointer}.card.sel{background:var(--sel)}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
.name{font-weight:700;font-size:15px}.badge{font-size:12px;font-weight:700;color:#fff;background:var(--g);
border-radius:6px;padding:2px 7px;white-space:nowrap}
.meta{color:var(--sub);font-size:13px;margin-top:2px}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}.chip{background:var(--chip);border-radius:6px;
padding:2px 7px;font-size:12px}
.g-긴급{--g:var(--urgent)}.g-우선{--g:var(--high)}.g-관찰{--g:var(--watch)}.g-정상{--g:var(--ok)}
h2{font-size:15px;margin:20px 0 8px;color:var(--navy)}
.back{border:0;background:none;color:var(--navy);font-size:15px;padding:4px 0;margin-bottom:6px;cursor:pointer}
.big{font-size:21px;font-weight:800}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
@media (min-width:700px){.kpis{grid-template-columns:repeat(8,1fr)}}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:7px 4px;text-align:center}
.kpi b{display:block;font-size:18px}.kpi span,.kpi small{font-size:11px;color:var(--sub)}
.box{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.warn{background:var(--warn-bg)}
ul.hl{margin:0;padding-left:18px}ul.hl li{margin:3px 0}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th,.tbl td{padding:6px 6px;border-bottom:1px solid var(--line);text-align:center;white-space:nowrap}
.tbl th{color:var(--sub);font-weight:600;font-size:12px;background:var(--card);position:sticky;top:0}
.tbl td.l,.tbl th.l{text-align:left}.tbl td.wrap{white-space:normal;text-align:left;min-width:180px}
.tbl tr.click{cursor:pointer}.tbl tr.click:hover td{background:var(--sel)}
.tbl th.sort{cursor:pointer}.tbl th.sort:after{content:" ↕";opacity:.4}
.tbl .stick{position:sticky;left:0;background:var(--card);z-index:1}
.tbl tr.tot td{font-weight:700;background:var(--chip)}
.neg{color:var(--urgent);font-weight:700}.flag{color:var(--urgent);font-size:12px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--g);margin-right:5px}
label.ck{display:flex;gap:9px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line)}
label.ck input{width:20px;height:20px;margin-top:2px;flex:none}label.ck b{display:block;font-size:14px}
label.ck span{font-size:13px;color:var(--sub)}
textarea{width:100%;min-height:80px;border:1px solid var(--line);border-radius:10px;padding:8px;
background:var(--card);color:var(--ink);font-size:14px}
.foot{color:var(--sub);font-size:11px;margin-top:22px;text-align:center}
.empty{color:var(--sub);text-align:center;padding:30px 0}
.placeholder{color:var(--sub);text-align:center;padding:80px 0}
.note{color:var(--sub);font-size:12px;margin:6px 2px}
</style></head>
<body>
<header><div class="top"><h1>smaXtec 목장 모니터</h1><div class="asof">기준 __ASOF__ · 최근 24시간<!--CLOUD_NAV--></div></div>
<nav class="tabs" id="tabs"></nav></header>
<main id="app"></main>
<script>
const D = __DATA__;
const TABS = [["sum","요약표"],["farm","목장별"],["trend","7일추세"],["hist","누적·월별"],["tr","전환기"],["trial","투입비교"],["beh","그룹행동"],["clu","집단신호"]];
const KPI = Object.keys(D.kpiLabels);
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const sgn = v => v==null ? "-" : (v>0?"+":"")+v;
const day = D.asOf.slice(0,10);
// 클라우드 앱이면 서버가 window.SMX_CLOUD 를 넣어 줌 → 메모·체크리스트를 모든 기기에 동기화
const CLOUD = window.SMX_CLOUD || null;
const NOTE_KEY = /^(memo:[\w.-]+|ck:[\w.-]+:\d{4}-\d{2}-\d{2})$/;
const syncT = {};
function pushNote(k, v){
  if(!CLOUD || !NOTE_KEY.test(k)) return;
  clearTimeout(syncT[k]);
  syncT[k] = setTimeout(() => {
    fetch("/api/notes", {method:"POST", credentials:"same-origin",
      headers:{"Content-Type":"application/json", "X-CSRF":CLOUD.csrf}, body: JSON.stringify({key:k, value:v})})
      .then(r => { const e=document.getElementById("syncst"); if(e) e.textContent = r.ok ? "모든 기기에 저장됨" : "저장 실패 — 다시 로그인해 주세요"; })
      .catch(() => { const e=document.getElementById("syncst"); if(e) e.textContent = "오프라인 — 연결되면 다시 입력해 주세요"; });
  }, 700);
}
const store = { get(k){ try { return JSON.parse(localStorage.getItem(k)); } catch(e){ return null; } },
                set(k,v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} pushNote(k, v); } };
const st = Object.assign({ tab:"sum", grade:"전체", q:"", cat:"건강 전체", sortK:null, sortD:1, behAll:false },
                         store.get("ui") || {});
st.q = "";
const save = () => store.set("ui", {tab:st.tab, grade:st.grade, cat:st.cat, behAll:st.behAll, hcat:st.hcat, hunit:st.hunit});
const heat = (v, mx) => v ? `background:rgba(var(--heat),${Math.min(.85, .12 + .73*v/Math.max(1,mx))})` : "";
const go = id => { location.hash = "f=" + encodeURIComponent(id); };

function bars(tr){
  if(!tr.length) return "";
  const w=320,h=90,mx=Math.max(1,...tr.map(x=>x.n)),bw=w/tr.length;
  return `<svg viewBox="0 0 ${w} ${h+16}" width="100%" style="max-width:440px;display:block" role="img" aria-label="건강알림 7일 추세">`+
  tr.map((x,i)=>{ const bh=x.n/mx*(h-14); return `<rect x="${i*bw+6}" y="${h-bh}" width="${bw-12}" height="${bh}" rx="3"
  fill="var(--navy)" opacity="${i==tr.length-1?1:.55}"/><text x="${i*bw+bw/2}" y="${h-bh-3}" font-size="11"
  text-anchor="middle" fill="currentColor">${x.n}</text><text x="${i*bw+bw/2}" y="${h+13}" font-size="10"
  text-anchor="middle" fill="currentColor" opacity=".7">${x.d}</text>`; }).join("")+"</svg>";
}

/* ───── 요약표 ───── */
function sumTab(){
  const cols = [["rank","순위"],["grade","등급"],["name","목장"],["herd","센서두수"],
    ...KPI.map(k=>[k, D.kpiLabels[k]]),["h","건강알림 두수"],["ha","참고:건수"],["per100","100두당"],["d1","전일"],["d7","7일평균"],
    ["surge","급증"],["clu","집단"],["score","점수"]];
  let rows = D.farms.map((f,i)=>({f, rank:i+1, grade:f.grade, name:f.name, herd:f.herd??0, ...f.row.c, h:f.health_d0,
    ha:f.health_events, per100:f.row.per100??0, d1:f.health_d1, d7:f.health_7d??0, surge:f.surge?1:0,
    clu:f.row.cluster_n, score:f.score}));
  if(st.q) rows = rows.filter(r=>r.name.includes(st.q));
  if(st.grade!=="전체") rows = rows.filter(r=>st.grade==="방문" ? ["긴급","우선"].includes(r.grade) : r.grade===st.grade);
  if(st.sortK){ const k=st.sortK; rows.sort((a,b)=>(typeof a[k]=="string"?a[k].localeCompare(b[k]):a[k]-b[k])*st.sortD); }
  const mx = {}; KPI.forEach(k=>mx[k]=Math.max(1,...D.farms.map(f=>f.row.c[k])));
  return `${gradeFilter()}<input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  <div class="scroll"><table class="tbl"><tr>${cols.map(([k,l])=>`<th class="sort ${k=='name'?'l stick':''}" onclick="sortBy('${k}')">${l}</th>`).join("")}</tr>
  ${rows.map(r=>`<tr class="click g-${r.grade}" onclick="go('${r.f.id}')"><td>${r.rank}</td><td><span class="badge">${r.grade}</span></td>
   <td class="l stick"><b>${esc(r.name)}</b></td><td>${r.f.herd??"-"}</td>
   ${KPI.map(k=>`<td style="${heat(r[k],mx[k])}">${r[k]}</td>`).join("")}
   <td><b>${r.h}</b></td><td>${r.ha}</td><td>${r.f.row.per100??"-"}</td><td>${r.d1}</td><td>${r.f.health_7d??"-"}</td>
   <td>${r.surge?'<span class="neg">▲</span>':""}</td><td>${r.clu?`<span class="neg">${r.clu}두</span>`:""}</td><td><b>${r.score}</b></td></tr>`).join("")}
  </table></div><div class="note">행을 누르면 목장 요약으로 이동 · 제목을 누르면 정렬</div>`;
}
function sortBy(k){ st.sortD = st.sortK===k ? -st.sortD : (["name","grade"].includes(k)?1:-1); st.sortK=k; render(); }
function gradeFilter(){
  const c={}; D.farms.forEach(f=>c[f.grade]=(c[f.grade]||0)+1);
  const g=[["전체",`전체 ${D.farms.length}`],["방문",`방문(긴급·우선) ${(c["긴급"]||0)+(c["우선"]||0)}`],
    ["긴급",`긴급 ${c["긴급"]||0}`],["우선",`우선 ${c["우선"]||0}`],["관찰",`관찰 ${c["관찰"]||0}`],["정상",`정상 ${c["정상"]||0}`]];
  return `<div class="filters">${g.map(([k,l])=>`<button class="${st.grade==k?'on':''}" onclick="st.grade='${k}';save();render()">${l}</button>`).join("")}</div>`;
}

/* ───── 목장별 ───── */
function farmList(selId){
  let fs = D.farms.filter(f => st.grade=="전체" || (st.grade=="방문" ? ["긴급","우선"].includes(f.grade) : f.grade==st.grade));
  if(st.q) fs = fs.filter(f => f.name.includes(st.q));
  return `<input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">${gradeFilter()}
  ${fs.length ? fs.map(f => `<div class="card g-${f.grade} ${f.id==selId?'sel':''}" onclick="go('${f.id}')">
    <div class="row"><div class="name">${esc(f.name)}</div><span class="badge">${f.grade} ${f.score}</span></div>
    <div class="meta">건강알림 <b>${f.health_d0}</b>두 · 전일 ${f.health_d1}두${f.herd?` · ${f.herd}두`:""}${f.stale?" · ⚠지연":""}</div>
    ${f.reasons.length?`<div class="chips">${f.reasons.slice(0,3).map(r=>`<span class="chip">${esc(r)}</span>`).join("")}</div>`:""}
  </div>`).join("") : `<div class="empty">해당 목장 없음</div>`}`;
}
function farmTab(id){
  return `<div class="split ${id?'has-detail':''}"><aside>${farmList(id)}</aside>
  <section>${id ? detail(id) : `<div class="placeholder">왼쪽에서 목장을 선택하세요</div>`}</section></div>`;
}
function detail(id){
  const f = D.farms.find(x => x.id == id); if(!f) return `<div class="empty">목장을 찾을 수 없음</div>`;
  const ck = store.get(`ck:${f.id}:${day}`) || {}, memo = store.get(`memo:${f.id}`) || "";
  const cats = Object.keys(f.trend_types), mxT = Math.max(1,...cats.filter(c=>c!="건강 전체").flatMap(c=>f.trend_types[c]));
  return `<button class="back" onclick="location.hash='';">‹ 목록</button>
  <div class="g-${f.grade}"><div class="row"><div class="big">${esc(f.name)}</div><span class="badge">${f.grade} ${f.score}점</span></div>
  <div class="meta">센서 ${f.herd??"-"}두 · 마지막 수집 ${f.last_ok??"없음"}${f.stale?" · ⚠데이터 지연":""}</div></div>
  <h2>1. 핵심 요약</h2><div class="box"><ul class="hl">${f.headline.map(h=>`<li>${esc(h)}</li>`).join("")}</ul></div>
  <h2>2. 알림 현황 — 두수 (24시간 · 72시간)</h2>
  <div class="kpis">${f.counts.map(c=>`<div class="kpi"><span>${esc(c.label)}</span><b>${c.n24}</b><small>72h ${c.n72}</small></div>`).join("")}</div>
  <div class="note">건강알림 24h <b>${f.health_d0}두</b> (${f.health_events}건) · 전일 ${f.health_d1}두 · 7일평균 ${f.health_7d??"-"}두${f.surge?" · <b class='neg'>급증</b>":""}</div>
  <h2>3. 7일 추세 — 종류별 두수</h2>
  <div class="scroll"><table class="tbl"><tr><th class="l">구분</th>${D.dates.map(d=>`<th>${d}</th>`).join("")}<th>7일 두수</th></tr>
  ${cats.map(c=>`<tr class="${c=='건강 전체'?'tot':''}"><td class="l">${esc(c)}</td>${f.trend_types[c].map(v=>`<td style="${c=='건강 전체'?'':heat(v,mxT)}">${v||""}</td>`).join("")}
   <td><b>${f.trend_total[c]}</b></td></tr>`).join("")}</table></div>
  <div class="box" style="margin-top:8px">${bars(f.trend)}<div class="note">막대 = 건강알림 두수(일별, 같은 개체 반복은 1두)</div></div>
  ${f.cluster?`<h2>집단신호 (의심 — 진단 아님)</h2><div class="box warn"><b>${f.cluster.n}두</b> · ${f.cluster.from} ~ ${f.cluster.to}<br>${esc(f.cluster.text)}
    <div class="meta">개체: ${f.cluster.animals.map(esc).join(", ")}</div></div>`:""}
  <h2>4. 그룹 행동</h2>${f.groups.length?`<div class="scroll"><table class="tbl"><tr><th class="l">그룹</th><th>두수</th><th>반추(분)</th><th>7일평균</th><th>Δ반추</th><th>누운시간(h)</th><th>Δ누운시간</th><th>누워반추%</th><th class="l">판정</th></tr>
  ${f.groups.map(g=>`<tr><td class="l">${esc(g.name)}</td><td>${g.n??""}</td><td>${g.rum??"-"}</td><td>${g.rum7??"-"}</td>
  <td class="${g.drum<0?'neg':''}">${sgn(g.drum)}</td><td>${g.lying_h??"-"}</td><td>${sgn(g.dlying)}</td><td>${g.share??"-"}</td>
  <td class="wrap flag">${g.flags.map(esc).join(", ")}</td></tr>`).join("")}
  </table></div>${D.bench?`<div class="note">비교 기준(담당목장 착유그룹 ${D.bench.n}개 중앙값): 반추 ${Math.round(D.bench.rum)}분 · 누운 시간 ${D.bench.lying_h}h · 누워반추 ${Math.round(D.bench.rum_lying_share)}%</div>`:""}`
  :`<div class="empty">행동 데이터 없음</div>`}
  ${histSection(f)}
  ${trSection(f)}
  ${D.trials.filter(t=>t.farm_id==f.id).length?`<h2>투입 전후 비교</h2>${D.trials.filter(t=>t.farm_id==f.id).map(trialCard).join("")}`:""}
  <h2>5. 확인할 개체 (최근 72시간)</h2>${f.animals.length?`<div class="scroll"><table class="tbl"><tr><th class="l">개체</th><th>상태</th><th>알림수</th><th class="l">알림 종류</th><th>마지막</th></tr>
  ${f.animals.map(a=>`<tr><td class="l"><b>${esc(a.label)}</b></td><td>${esc(a.status||"-")}</td><td>${a.n}</td><td class="wrap">${esc(a.types)}</td><td>${a.last}</td></tr>`).join("")}</table></div>`
  :`<div class="empty">해당 개체 없음</div>`}
  <h2>6. 최근 24시간 알림 (${f.alerts24.length}건)</h2>${f.alerts24.length?`<div class="scroll" style="max-height:320px;overflow:auto"><table class="tbl"><tr><th>발생</th><th class="l">개체</th><th class="l">알림</th><th>상태</th><th>코드</th></tr>
  ${f.alerts24.map(a=>`<tr><td>${a.t}</td><td class="l"><b>${esc(a.animal)}</b></td><td class="l">${esc(a.type)}</td><td>${esc(a.status)}</td><td style="color:var(--sub)">${esc(a.raw)}</td></tr>`).join("")}</table></div>`
  :`<div class="empty">없음</div>`}
  <h2>7. 현장 점검 체크리스트</h2><div class="box">${f.checklist.map((c,i)=>`<label class="ck"><input type="checkbox" ${ck[i]?"checked":""}
   onchange="tick('${f.id}',${i},this.checked)"><div><b>${esc(c.topic)}</b><span>${esc(c.text)}</span></div></label>`).join("")}</div>
  <h2>메모</h2><textarea placeholder="농장주 의견·조치·다음 방문 (${CLOUD?"휴대폰·PC 모두에 자동 저장":"이 기기에만 저장"})" oninput="store.set('memo:${f.id}',this.value)">${esc(memo)}</textarea>
  <div class="note" id="syncst">${CLOUD?"휴대폰·PC 모두에 자동 저장됩니다":"이 기기에만 저장됩니다"}</div>
  <div class="foot">센서 알림·행동지표 기반 선별 자료(진단 아님) · 목장 데이터 외부 공유 금지</div>`;
}
function tick(id,i,v){ const k=`ck:${id}:${day}`, o=store.get(k)||{}; o[i]=v; store.set(k,o); }

/* ───── 7일추세 ───── */
function trendTab(){
  const cats = D.farms.length ? Object.keys(D.farms[0].trend_types) : [];
  if(!cats.includes(st.cat)) st.cat = "건강 전체";
  let fs = [...D.farms]; if(st.q) fs = fs.filter(f=>f.name.includes(st.q));
  if(st.sortK==="t7") fs.sort((a,b)=>(a.trend_total[st.cat]-b.trend_total[st.cat])*st.sortD);
  const mx = Math.max(1,...D.farms.flatMap(f=>f.trend_types[st.cat]));
  return `<div class="filters">${cats.map(c=>`<button class="${st.cat==c?'on':''}" onclick="st.cat='${c}';save();render()">${esc(c)}</button>`).join("")}</div>
  <input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  <div class="scroll"><table class="tbl"><tr><th class="l stick">목장</th><th>센서두수</th>${D.dates.map(d=>`<th>${d}</th>`).join("")}
  <th class="sort" onclick="sortBy('t7')">7일 두수</th><th>100두당</th></tr>
  ${fs.map(f=>{ const t=f.trend_total[st.cat]; return `<tr class="click" onclick="go('${f.id}')"><td class="l stick g-${f.grade}"><span class="dot"></span>${esc(f.name)}</td>
   <td>${f.herd??"-"}</td>${f.trend_types[st.cat].map(v=>`<td style="${heat(v,mx)}">${v||""}</td>`).join("")}
   <td><b>${t}</b></td><td>${f.herd?(t/f.herd*100).toFixed(1):"-"}</td></tr>`; }).join("")}</table></div>
  <div class="note">두수 = 그날 해당 알림이 1번 이상 나온 개체 수(중복 제외) · 7일 두수 = 7일 동안 1번 이상 해당된 개체 수</div>`;
}

/* ───── 그룹행동 ───── */
function behTab(){
  const rows=[]; D.farms.forEach(f=>f.groups.forEach(g=>rows.push({f,g})));
  const ok = r => r.g.flags.length && !r.g.flags.includes("데이터 불완전(판정 제외)");
  let show = st.behAll ? rows : rows.filter(ok);
  if(st.q) show = show.filter(r=>r.f.name.includes(st.q));
  const b = D.bench;
  return `${b?`<div class="box">담당 목장 착유그룹 기준값 (${b.n}개 그룹 중앙값): 반추 <b>${Math.round(b.rum)}</b>분 · 누운 시간 <b>${b.lying_h}</b>h · 누워반추 <b>${Math.round(b.rum_lying_share)}</b>%</div>`:""}
  <div class="note">용어 · <b>누운 시간</b>: 하루 중 누워 쉰 시간 · <b>누워반추</b>: 되새김 중 누워서 한 비율(높을수록 편안) · <b>함께 눕기</b>: 같은 시간에 그룹 소들이 함께 누워 있는 비율(떨어지면 누울 자리 부족·과밀·더위 의심) · <b>Δ</b>: 7일 평균 대비 변화</div>
  <div class="filters" style="margin-top:10px"><button class="${!st.behAll?'on':''}" onclick="st.behAll=false;save();render()">이상 그룹 ${rows.filter(ok).length}</button>
  <button class="${st.behAll?'on':''}" onclick="st.behAll=true;save();render()">전체 그룹 ${rows.length}</button></div>
  <input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  ${show.length?`<div class="scroll"><table class="tbl"><tr><th class="l stick">목장</th><th class="l">그룹</th><th>두수</th><th>반추</th><th>7일</th><th>Δ반추</th><th>누운시간h</th><th>Δ누운시간</th><th>누워반추%</th><th>함께눕기%</th><th class="l">판정</th></tr>
  ${show.map(({f,g})=>`<tr class="click" onclick="go('${f.id}')"><td class="l stick">${esc(f.name)}</td><td class="l">${esc(g.name)}</td><td>${g.n??""}</td>
  <td>${g.rum??"-"}</td><td>${g.rum7??"-"}</td><td class="${g.drum<0?'neg':''}">${sgn(g.drum)}</td><td>${g.lying_h??"-"}</td><td>${sgn(g.dlying)}</td>
  <td>${g.share??"-"}</td><td>${g.sync??"-"}</td><td class="wrap flag">${g.flags.map(esc).join(", ")}</td></tr>`).join("")}</table></div>`
  : `<div class="empty">해당 그룹 없음</div>`}`;
}

/* ───── 누적·월별 ───── */
const MLBL = () => D.months.map((m,i)=>`${m.slice(2,4)}.${m.slice(5)}${i==D.months.length-1?"*":""}`);
// 목장별 종류 내역 (30·90일 누적표 / 월별 표) — 누적·월별 탭 펼침과 목장 상세에서 함께 사용
function cumTable(f){
  const h=f.hist, cats=Object.keys(h.cum["90"]), b=D.hbench["90"]||{};
  return `<table class="tbl"><tr><th class="l">구분</th><th>30일 두수</th><th>100두당</th><th>90일 두수</th><th>100두당</th><th>중앙값(90일)</th></tr>
  ${cats.map(c=>`<tr class="${c=='건강 전체'?'tot':''}"><td class="l">${esc(c)}</td><td>${h.cum["30"][c]}</td><td>${h.per100["30"][c]??"-"}</td>
   <td>${h.cum["90"][c]}</td><td style="${b[c]!=null&&h.per100["90"][c]!=null&&h.per100["90"][c]>b[c]*1.3?'color:var(--urgent);font-weight:700':''}">${h.per100["90"][c]??"-"}</td><td>${b[c]??"-"}</td></tr>`).join("")}</table>`;
}
function monthTable(f, unit, hi){
  const h=f.hist, cats=Object.keys(h.cum["90"]);
  const val = v => unit=="p100" && f.herd ? +(v/f.herd*100).toFixed(1) : v;
  const mx = Math.max(1,...cats.filter(c=>c!="건강 전체").flatMap(c=>h.monthly[c].slice(0,-1).map(val)));
  const hs = i => i===hi ? "outline:2px solid var(--navy);outline-offset:-2px;font-weight:700;" : "";
  return `<table class="tbl"><tr><th class="l">구분 (${unit=="p100"&&f.herd?"100두당":"두수"})</th>${MLBL().map((m,i)=>`<th style="${hs(i)}">${m}</th>`).join("")}</tr>
  ${cats.map(c=>`<tr class="${c=='건강 전체'?'tot':''}"><td class="l">${esc(c)}</td>${h.monthly[c].map((v,i)=>`<td style="${hs(i)}${c!='건강 전체'&&i<D.months.length-1?heat(val(v),mx):''}">${val(v)||""}</td>`).join("")}</tr>`).join("")}</table>`;
}
const HX = {};   // 펼친 목장: HX["c:"+id] / HX["m:"+id] = 강조할 달 번호
function hx(kind, id, month){
  const k = kind+":"+id;
  if (month === undefined) { if (k in HX) delete HX[k]; else HX[k] = null; }
  else HX[k] = (HX[k] === month) ? (delete HX[k], undefined) : month;
  const y = window.scrollY; render(); window.scrollTo(0, y);   // 화면 위치 유지, 검색창 포커스 안 함
}
const farmLink = f => `<span class="dot"></span>${esc(f.name)} <a href="#f=${encodeURIComponent(f.id)}" onclick="event.stopPropagation()" style="color:var(--navy);text-decoration:none;font-weight:700" title="목장 상세">›</a>`;

function histTab(){
  const fs0 = D.farms.filter(f=>f.hist);
  if(!fs0.length) return `<div class="empty">기록 없음</div>`;
  const cats = Object.keys(fs0[0].hist.cum["90"]);
  if(!cats.includes(st.hcat)) st.hcat = "건강 전체";
  const c = st.hcat, unit = st.hunit || "p100";
  let fs = fs0.filter(f=>!st.q || f.name.includes(st.q));
  const p = (f,w) => f.hist.per100[w][c];
  const k = st.sortK || "p90";
  const val = f => k=="p30"?p(f,"30") : k=="n30"?f.hist.cum["30"][c] : k=="n90"?f.hist.cum["90"][c] : p(f,"90");
  fs.sort((a,b)=>((val(b)??-1)-(val(a)??-1))*(st.sortK?-st.sortD:1));
  const rk = w => { const ok=fs0.filter(f=>p(f,w)!=null).sort((a,b)=>p(a,w)-p(b,w)); const m={}; ok.forEach((f,i)=>m[f.id]=i+1); return [m, ok.length]; };
  const [r30,n30] = rk("30"), [r90,n90] = rk("90");
  const b = (D.hbench["90"]||{})[c];
  const mv = f => f.hist.monthly[c].map(v => unit=="p100" && f.herd ? +(v/f.herd*100).toFixed(1) : v);
  const mx = Math.max(1,...fs.flatMap(f=>mv(f).slice(0,-1)));
  const sub = (n, inner) => `<tr class="sub"><td colspan="${n}" style="background:var(--chip);padding:8px;white-space:normal;text-align:left">
     <div class="scroll" style="background:var(--card)">${inner}</div>
     <div class="note">종류별 두수 · 한 개체가 여러 종류에 해당할 수 있어 종류별 합이 '건강 전체'보다 클 수 있음</div></td></tr>`;
  return `<div class="filters">${cats.map(x=>`<button class="${c==x?'on':''}" onclick="st.hcat='${x}';save();render()">${esc(x)}</button>`).join("")}</div>
  ${b!=null?`<div class="box">담당 목장 중앙값 (90일, 100두당): <b>${b}</b>두 · ${esc(c)}</div>`:""}
  <input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)" style="margin-top:10px">
  <h2>30·90일 누적 — ${esc(c)}</h2>
  <div class="scroll"><table class="tbl"><tr><th class="l stick">목장</th><th>센서두수</th>
   <th class="sort" onclick="sortBy('n30')">30일 두수</th><th class="sort" onclick="sortBy('p30')">100두당</th><th>순위</th>
   <th class="sort" onclick="sortBy('n90')">90일 두수</th><th class="sort" onclick="sortBy('p90')">100두당</th><th>순위</th></tr>
  ${fs.map(f=>`<tr class="click" onclick="hx('c','${f.id}')"><td class="l stick g-${f.grade}">${("c:"+f.id) in HX?"▾":"▸"} ${farmLink(f)}</td><td>${f.herd??"-"}</td>
   <td>${f.hist.cum["30"][c]}</td><td>${p(f,"30")??"-"}</td><td>${r30[f.id]?r30[f.id]+"/"+n30:"-"}</td>
   <td>${f.hist.cum["90"][c]}</td><td style="${b!=null&&p(f,"90")!=null&&p(f,"90")>b*1.3?'color:var(--urgent);font-weight:700':''}">${p(f,"90")??"-"}</td><td>${r90[f.id]?r90[f.id]+"/"+n90:"-"}</td></tr>
   ${("c:"+f.id) in HX ? sub(8, cumTable(f)) : ""}`).join("")}
  </table></div>
  <div class="note">목장 줄을 누르면 종류별 내역 · › 는 목장 상세 · 순위: 100두당이 적을수록 1위 · 빨간 숫자 = 담당목장 중앙값보다 30% 이상 많음</div>
  <h2>월별 추이 (12개월) — ${esc(c)}</h2>
  <div class="filters"><button class="${unit=='p100'?'on':''}" onclick="st.hunit='p100';save();render()">100두당</button>
   <button class="${unit=='n'?'on':''}" onclick="st.hunit='n';save();render()">두수</button></div>
  <div class="scroll"><table class="tbl"><tr><th class="l stick">목장</th>${MLBL().map(m=>`<th>${m}</th>`).join("")}</tr>
  ${fs.map(f=>{ const k="m:"+f.id, open = k in HX, hi = HX[k];
    return `<tr class="click" onclick="hx('m','${f.id}')"><td class="l stick">${open?"▾":"▸"} ${farmLink(f)}</td>${mv(f).map((v,i)=>
      `<td onclick="event.stopPropagation();hx('m','${f.id}',${i})" style="${i<D.months.length-1?heat(v,mx):''}${open&&hi===i?'outline:2px solid var(--navy);outline-offset:-2px;font-weight:700':''}">${v||""}</td>`).join("")}</tr>
    ${open ? sub(D.months.length+1, monthTable(f, unit, hi)) : ""}`; }).join("")}
  </table></div><div class="note">목장 줄을 누르면 종류별 월별 내역 · 숫자를 누르면 그 달을 강조해서 펼침 · * 이번 달은 진행 중 · 100두당은 현재 센서 두수 기준</div>`;
}
function histSection(f){
  const h=f.hist; if(!h) return "";
  return `<h2>누적·월별 알림 <small style="color:var(--sub);font-weight:400">${h.rank["90"]?`90일 건강알림 ${h.rank["90"]}/${h.of}위 (적을수록 1위)`:""}</small></h2>
  <div class="scroll">${cumTable(f)}</div>
  <div class="scroll" style="margin-top:6px">${monthTable(f, "n")}</div>
  <div class="note">* 이번 달은 진행 중 · 빨간 숫자 = 담당목장 중앙값보다 30% 이상 많음 · 한 개체가 여러 종류에 해당할 수 있음</div>`;
}

/* ───── 전환기 ───── */
const pc = v => v==null ? "-" : Math.round(v)+"%";
function trTab(){
  const b = D.tbench;
  let fs = D.farms.filter(f=>f.transition);
  if(st.q) fs = fs.filter(f=>f.name.includes(st.q));
  const k = st.sortK || "healthy";
  const val = (f,k) => { const t=f.transition; return k=="healthy"?t.healthy : k=="n"?t.n : k=="pre"?t.pre_rum : k=="yr"?t.healthy_year : k=="fresh"?t.fresh.length : k=="up"?t.upcoming.length : t.rates[k]; };
  fs.sort((a,c)=> (a.transition.small-c.transition.small) || ((val(a,k)??999)-(val(c,k)??999))*(st.sortK?st.sortD:1));
  const mx = {}; D.tInd.forEach(([c])=>mx[c]=Math.max(1,...fs.map(f=>f.transition.rates[c]||0)));
  return `${b&&b.n?`<div class="box">담당 목장 중앙값(${b.n}곳, 분만 후 21일 · 최근 90일 완료 분만${b.healthy_year!=null?` · 12개월 무사통과 ${pc(b.healthy_year)}`:""}): 무사통과 <b>${pc(b.healthy)}</b> · ${D.tInd.map(([c,l])=>`${l} <b>${pc(b[c])}</b>`).join(" · ")} · 분만전 반추↓ <b>${pc(b.pre_rum)}</b></div>`:""}
  <input type="search" placeholder="목장 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)" style="margin-top:10px">
  ${fs.length?`<div class="scroll"><table class="tbl"><tr><th>순위</th><th class="l stick">목장</th><th class="sort" onclick="sortBy('n')">분만</th>
  <th class="sort" onclick="sortBy('healthy')">무사통과</th>${D.tInd.map(([c,l])=>`<th class="sort" onclick="sortBy('${c}')">${l}</th>`).join("")}
  <th class="sort" onclick="sortBy('pre')">분만전반추↓</th><th class="sort" onclick="sortBy('yr')">12개월<br>무사통과</th><th>입력률</th><th class="sort" onclick="sortBy('fresh')">신선우</th><th class="sort" onclick="sortBy('up')">분만예정</th><th class="l">판정</th></tr>
  ${fs.map(f=>{const t=f.transition; return `<tr class="click" style="${t.small?'opacity:.55':''}" onclick="go('${f.id}')"><td>${t.rank??(t.small?"참고":"-")}</td>
   <td class="l stick">${esc(f.name)}</td><td>${t.n}${t.n_first?` <small>(초산${t.n_first})</small>`:""}</td>
   <td style="${t.healthy!=null?`background:rgba(var(--heat),${Math.max(0,Math.min(.8,(70-t.healthy)/70))})`:""}"><b>${pc(t.healthy)}</b></td>
   ${D.tInd.map(([c])=>`<td style="${heat(t.rates[c],mx[c])}">${pc(t.rates[c])}</td>`).join("")}
   <td>${pc(t.pre_rum)}</td><td>${pc(t.healthy_year)}${t.n_year?` <small>(${t.n_year})</small>`:""}</td><td>${pc(t.confirm)}</td><td>${t.fresh.length}</td><td>${t.upcoming.length}</td>
   <td class="wrap flag">${t.flags.map(esc).join("<br>")}${t.small&&t.n?'<span style="color:var(--sub)">분만 적음(참고)</span>':""}</td></tr>`;}).join("")}
  </table></div>` : `<div class="empty">분만 기록 없음 (과거 1년 이력 수집 후 계산)</div>`}
  <div class="note">발생률 = 분만 후 21일 안에 해당 알림이 1번 이상 나온 두수 ÷ 분만 두수 · 센서 '의심' 알림 기준(진단 아님) · 담당 목장 간 비교용</div>`;
}
function trSection(f){
  const t=f.transition, b=D.tbench; if(!t) return "";
  const cell = (v,bv,hi) => { if(v==null) return "-"; const bad = bv!=null && (hi ? v<=bv-15 : v>=bv+10); return `<span class="${bad?'neg':''}">${Math.round(v)}%</span>`; };
  return `<h2>4-2. 전환기 성적 <small style="color:var(--sub);font-weight:400">분만 후 21일 · 최근 90일 완료 분만 ${t.n}두${t.rank?` · 무사통과 ${t.rank}/${t.of}위`:t.small?" · 참고용":""}</small></h2>
  <div class="scroll"><table class="tbl"><tr><th class="l">구분</th><th>무사통과</th>${D.tInd.map(([,l])=>`<th>${l}</th>`).join("")}<th>분만전반추↓</th></tr>
  <tr><td class="l"><b>이 목장 (90일)</b></td><td><b>${cell(t.healthy,b.healthy,true)}</b></td>${D.tInd.map(([c])=>`<td>${cell(t.rates[c],b[c],false)}</td>`).join("")}<td>${cell(t.pre_rum,b.pre_rum,false)}</td></tr>
  ${t.n_first?`<tr><td class="l">초산우</td><td>-</td>${D.tInd.map(([c])=>`<td>${pc(t.rates_first[c])}</td>`).join("")}<td>-</td></tr>
  <tr><td class="l">경산우</td><td>-</td>${D.tInd.map(([c])=>`<td>${pc(t.rates_multi[c])}</td>`).join("")}<td>-</td></tr>`:""}
  ${t.n_year?`<tr><td class="l">최근 12개월 <small>(${t.n_year}두)</small></td><td><b>${pc(t.healthy_year)}</b></td>${D.tInd.map(([c])=>`<td>${pc(t.rates_year[c])}</td>`).join("")}<td>-</td></tr>`:""}
  ${b&&b.n?`<tr class="tot"><td class="l">담당목장 중앙값</td><td>${pc(b.healthy)}</td>${D.tInd.map(([c])=>`<td>${pc(b[c])}</td>`).join("")}<td>${pc(b.pre_rum)}</td></tr>`:""}
  </table></div>
  <div class="note">첫 알림까지 평균 ${t.first_dim??"-"}일 · 최근 30일 분만 ${t.n_recent}두 무사통과 ${pc(t.healthy_recent)} · 분만 입력률 ${pc(t.confirm)}${t.flags.length?` · <b class="neg">${t.flags.map(esc).join(" / ")}</b>`:""}</div>
  ${t.monthly.length?`<div class="scroll" style="margin-top:6px"><table class="tbl"><tr><th class="l">분만월</th>${t.monthly.map(m=>`<th>${m.month.slice(2)}</th>`).join("")}</tr>
   <tr><td class="l">분만 두수</td>${t.monthly.map(m=>`<td>${m.n}</td>`).join("")}</tr>
   <tr><td class="l">무사통과</td>${t.monthly.map(m=>`<td><b>${pc(m.healthy)}</b></td>`).join("")}</tr>
   <tr><td class="l">케토시스</td>${t.monthly.map(m=>`<td>${pc(m.ketosis)}</td>`).join("")}</tr>
   <tr><td class="l">반추↓</td>${t.monthly.map(m=>`<td>${pc(m.rum)}</td>`).join("")}</tr></table></div>`:""}
  <h2>현재 신선우 (분만 후 21일 이내 ${t.fresh.length}두)</h2>${t.fresh.length?`<div class="scroll"><table class="tbl"><tr><th class="l">개체</th><th>산차</th><th>분만</th><th>경과일</th><th class="l">알림</th></tr>
   ${t.fresh.map(x=>`<tr><td class="l"><b>${esc(x.label)}</b></td><td>${x.parity??"-"}</td><td>${x.calved}</td><td>${x.dim}</td><td class="wrap ${x.n?'flag':''}">${esc(x.alerts)||"없음"}</td></tr>`).join("")}</table></div>`
   :`<div class="empty">없음</div>`}
  <h2>분만 예정 (14일 이내 ${t.upcoming.length}두)</h2>${t.upcoming.length?`<div class="chips">${t.upcoming.map(u=>`<span class="chip">${u.imminent?'<b class="neg">임박</b> ':""}${esc(u.label)} ${u.due!="-"?u.due+` (${u.days<0?"예정일 지남":u.days+"일 후"})`:""}</span>`).join("")}</div>`
   :`<div class="empty">없음</div>`}`;
}

/* ───── 투입비교 ───── */
const VC = {"개선(유의)":"#1e7b34","개선 경향":"#4f8a2f","악화(유의)":"var(--urgent)","악화 경향":"#d9631e"};
const f1 = (v,k=1) => v==null ? "-" : v.toFixed(k);
const s1 = (v,k=1) => v==null ? "-" : (v>0?"+":"")+v.toFixed(k);
const pv = v => v==null ? "-" : v<0.001 ? "<0.001" : v.toFixed(3);
function rumChart(t){
  const pts = t.rum.filter(x=>x[1]!=null); if(pts.length<3) return "";
  const W=320,H=110,vals=pts.map(x=>x[1]),mn=Math.min(...vals)-5,mx=Math.max(...vals)+5,n=t.rum.length;
  const X=i=>8+i*(W-16)/Math.max(1,n-1), Y=v=>H-8-(v-mn)/(mx-mn)*(H-24);
  const idx = d => t.rum.findIndex(x=>x[0]==d);
  const bi=t.before?idx(t.before[1]):-1, ai=t.after?idx(t.after[0]):-1;
  const seg=(from,to)=>t.rum.map((x,i)=>[i,x[1]]).filter(([i,v])=>i>=from&&i<=to&&v!=null).map(([i,v])=>`${X(i)},${Y(v)}`).join(" ");
  return `<svg viewBox="0 0 ${W} ${H+14}" width="100%" style="max-width:520px;display:block" role="img" aria-label="일별 반추 시간">
   ${ai>0?`<rect x="${X(bi)}" y="4" width="${X(ai)-X(bi)}" height="${H-12}" fill="currentColor" opacity=".06"/>`:""}
   <polyline points="${seg(0,bi)}" fill="none" stroke="var(--sub)" stroke-width="2"/>
   <polyline points="${seg(ai,n-1)}" fill="none" stroke="var(--navy)" stroke-width="2.5"/>
   <text x="8" y="12" font-size="10" fill="currentColor" opacity=".7">${Math.round(mx)}분</text>
   <text x="8" y="${H-10}" font-size="10" fill="currentColor" opacity=".7">${Math.round(mn)}분</text>
   <text x="8" y="${H+12}" font-size="10" fill="currentColor" opacity=".7">${t.rum[0][0]}</text>
   ${ai>0?`<text x="${X(ai)}" y="${H+12}" font-size="10" fill="currentColor" opacity=".7" text-anchor="middle">투입후 ${t.rum[ai][0]}</text>`:""}
   <text x="${W-8}" y="${H+12}" font-size="10" fill="currentColor" opacity=".7" text-anchor="end">${t.rum[n-1][0]}</text></svg>
   <div class="note">회색 = 투입 전 · 남색 = 투입 후 · 음영 = 적응기간(비교 제외)</div>`;
}
function metricTable(ms, cnt){
  const k = cnt?2:1;
  return `<div class="scroll"><table class="tbl" style="table-layout:fixed"><colgroup><col style="width:30%"><col style="width:30%"><col></colgroup>
  <tr><th class="l">지표</th><th>투입 전 → 후</th><th class="l">순효과 · 판정</th></tr>
  ${ms.map(m=>`<tr><td class="l" style="white-space:normal">${esc(m.name)}<br><small style="color:var(--sub)">${esc(m.unit)}</small></td>
   <td style="white-space:normal">${f1(m.before,k)} → <b>${f1(m.after,k)}</b><br><small style="color:var(--sub)">${s1(m.delta,k)}${cnt?` · ${m.nb}→${m.na}두일`:""}</small></td>
   <td class="l" style="white-space:normal"><b>${s1(m.net ?? m.delta,k)}</b> <span style="color:${VC[m.verdict]||'var(--sub)'};font-weight:${VC[m.verdict]?700:400}">${esc(m.verdict)}</span>
   <br><small style="color:var(--sub)">${m.ctrl==null?"대조 없음":`대조 ${s1(m.ctrl,k)}(${m.ctrl_n}곳)`} · p ${pv(m.p)}</small></td></tr>`).join("")}</table></div>`;
}
function trialCard(t){
  const done = t.beh.length || t.alerts.length;
  return `<div class="box" style="margin-bottom:14px"><div class="row"><div><div class="name">${esc(t.farm)}</div>
   <div style="font-weight:700;color:var(--navy)">${esc(t.product)}${t.kind?` <small style="color:var(--sub)">[${esc(t.kind)}]</small>`:""}</div></div>
   ${t.farm_id?`<button class="back" style="display:block" onclick="go('${t.farm_id}')">목장 ›</button>`:""}</div>
   <div class="meta">시작 ${t.start}${t.before?` · 비교: 전 ${t.before[0]}~${t.before[1]} vs 후 ${t.after[0]}${t.after[1]?"~"+t.after[1]:"부터"} (적응 ${t.adapt}일 제외)`:""}</div>
   <div class="meta">대상: ${esc(t.groups.join(", ")||"-")} · 상태: <b>${esc(t.status)}</b>${t.memo?` · ${esc(t.memo)}`:""}</div>
   ${done?`<ul class="hl" style="margin-top:8px">${t.summary.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>
   ${rumChart(t)}
   ${t.beh.length?`<h2>그룹 행동 (일 평균)</h2>${metricTable(t.beh,false)}`:""}
   ${t.alerts.length?`<h2>건강알림 (100두·일당 두수)</h2>${metricTable(t.alerts,true)}`:""}`:""}
  </div>`;
}
function trialTab(){
  const ts = D.trials.filter(t=>!st.q || t.farm.includes(st.q) || t.product.includes(st.q));
  return `<input type="search" placeholder="목장·제품 검색" value="${esc(st.q)}" oninput="st.q=this.value;render(true)">
  ${ts.length ? ts.map(trialCard).join("") : `<div class="empty">등록된 투입 기록이 없습니다.<br>PC 의 9_TRIALS.bat 으로 투입기록.xlsx 를 열어 입력하세요.</div>`}
  <div class="note">순효과 = 이 목장 변화 − 같은 기간 대조 목장 변화 · p(대조보정) 0.05 미만이면 우연일 가능성 낮음 · 관찰 비교이므로 인과 단정 금지(날씨·사료로트·우군 변동 영향)</div>`;
}

/* ───── 집단신호 ───── */
function cluTab(){
  const fs = D.farms.filter(f=>f.cluster);
  return fs.length ? fs.map(f=>`<div class="card g-${f.grade}" onclick="go('${f.id}')"><div class="row"><div class="name">${esc(f.name)}</div>
   <span class="badge">${f.cluster.n}두</span></div><div class="meta">${f.cluster.from} ~ ${f.cluster.to}</div>
   <div style="margin-top:6px;font-size:14px">${esc(f.cluster.text)}</div><div class="meta">개체: ${f.cluster.animals.map(esc).join(", ")}</div></div>`).join("")
   + `<div class="note">단시간에 여러 개체가 체온·반추 알림 → 사료·급여·환경·전염성 질병 등 공통 원인 '의심' 신호(진단 아님)</div>`
   : `<div class="empty">집단신호 없음</div>`;
}

function render(keepFocus){
  const m = location.hash.match(/f=([^&]+)/);
  if(m) st.tab = "farm";
  $("#tabs").innerHTML = TABS.map(([k,l])=>`<button class="${st.tab==k?'on':''}" onclick="setTab('${k}')">${l}${k=='clu'&&D.clusters?` ${D.clusters}`:""}</button>`).join("");
  const pos = keepFocus ? document.activeElement?.selectionStart : null;
  const view = {sum:sumTab, trend:trendTab, hist:histTab, tr:trTab, trial:trialTab, beh:behTab, clu:cluTab}[st.tab];
  $("#app").innerHTML = st.tab=="farm" ? farmTab(m ? decodeURIComponent(m[1]) : null)
    : view() + `<div class="foot">센서 알림·행동지표 기반 선별 자료(진단 아님) · 외부 공유 금지</div>`;
  if(keepFocus){ const i=$("input[type=search]"); if(i){ i.focus(); i.setSelectionRange(pos,pos); } }
  else if(m && window.innerWidth<=900) window.scrollTo(0,0);
}
function setTab(k){ st.tab=k; st.sortK=null; save(); if(location.hash) history.replaceState(null,"",location.pathname); render(); }
window.addEventListener("hashchange", () => render());
render();
// 클라우드: 다른 기기에서 쓴 메모·체크를 받아와 화면 갱신 (입력 중인 메모는 덮어쓰지 않음)
if (CLOUD) {
  fetch("/api/notes", {credentials:"same-origin"}).then(r => r.ok ? r.json() : {}).then(d => {
    let changed = false;
    for (const [k, v] of Object.entries(d || {})) {
      if (!NOTE_KEY.test(k)) continue;
      try { if (localStorage.getItem(k) !== JSON.stringify(v)) { localStorage.setItem(k, JSON.stringify(v)); changed = true; } } catch(e) {}
    }
    // 이 기기에만 있던 예전 메모·체크는 서버로 올림
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (NOTE_KEY.test(k) && !(k in (d || {}))) pushNote(k, JSON.parse(localStorage.getItem(k)));
      }
    } catch(e) {}
    if (changed && document.activeElement?.tagName !== "TEXTAREA") render();
  }).catch(() => {});
}
// 아이폰 홈 화면 앱: HTTPS 로 열렸을 때 마지막 화면을 저장(오프라인에서도 열림)
if ("serviceWorker" in navigator && window.isSecureContext && location.protocol !== "file:") {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
</script></body></html>
"""
