"""검정성적(유우군 능력검정·DHI) 엑셀 올리기 → 알림과 연결 → 유량 손실·경제성 추정.

엑셀 형식은 출처마다 달라서 머리글 이름으로 열을 찾음(아래 COLS). 한 파일 = 한 목장 · 한 검정일(또는 '검정일' 열).
개체 연결: 이표/이력번호(숫자 끝 9자리) 또는 관리번호 → smaXtec 개체(animals 표).

분석(목장·검정일별)
  - 평균 유량·유지방·유단백·유지방/유단백 비(F/P)·체세포·MUN, 체세포 20만 이상 비율
  - 분만 60일 이내 F/P > 1.5 비율(케토시스 위험 지표), 전체 F/P < 1.0 비율(산독증 위험 지표)
  - 알림 연결: 검정일 전 30일 안에 건강알림이 있었던 소 vs 없던 소의 유량 차이(비유일수 구간별로 맞춰 비교)
               유방염 의심 알림 소의 체세포 vs 나머지
  - 경제성(추정): 알림 소 유량 차이 × 두수 × 30일 × 원유 단가 → 월 손실 추정
  ⚠ 연관 관계(알림 소가 원래 유량이 낮았을 수도)이지 인과가 아님. 단가는 rules.toml [economics] milk_price.
"""
from __future__ import annotations

import re
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS dhi (
    farm_id   TEXT NOT NULL,
    test_date TEXT NOT NULL,
    cow_key   TEXT NOT NULL,         -- 파일의 이표 또는 관리번호
    animal_id TEXT,                  -- 연결된 smaXtec 개체(없으면 NULL)
    label     TEXT, parity INTEGER, dim INTEGER,
    milk REAL, fat REAL, protein REAL, scc REAL, mun REAL,   -- scc: 천/ml
    uploaded_at TEXT,
    PRIMARY KEY (farm_id, test_date, cow_key)
);
"""
# 머리글 → 항목 (앞쪽이 우선, 공백·괄호 무시하고 '포함'으로 찾음)
COLS: dict[str, list[str]] = {
    "official": ["이력번호", "이표번호", "이표", "개체식별번호", "개체번호", "바코드", "귀표"],
    "label": ["관리번호", "명호", "소번호", "목장번호", "번호"],
    "test_date": ["검정일자", "검정일", "검사일"],
    "parity": ["산차"],
    "dim": ["비유일수", "착유일수", "유일수", "dim"],
    "milk": ["검정일유량", "일유량", "유량", "산유량", "유량kg"],
    "fat": ["유지방률", "유지방", "유지율", "지방"],
    "protein": ["유단백률", "유단백", "단백질", "단백"],
    "scc": ["체세포수", "체세포", "scc"],
    "mun": ["mun", "요소태질소", "유요소태질소"],
}
NEED = ("milk",)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _norm(s: Any) -> str:
    return re.sub(r"[\s()（）\[\]/·._%-]", "", str(s or "")).lower()


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def _date(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    m = re.search(r"(20\d{2})[.\-/년\s]*(\d{1,2})[.\-/월\s]*(\d{1,2})", str(v or ""))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def _digits9(v: Any) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    return d[-9:] if len(d) >= 6 else ""


def parse(path: Path, test_date: str | None = None) -> dict[str, Any]:
    """엑셀 → {"rows": [...], "found": {항목: 머리글}, "missing": [...], "test_date": ..., "warnings": [...]}"""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    best = None
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True, max_row=3000))
        for hi, row in enumerate(rows[:20]):
            names = [_norm(c) for c in row]
            found: dict[str, int] = {}
            for key, alts in COLS.items():
                for exact in (True, False):          # 같은 이름 먼저, 없으면 포함
                    j = next((j for alt in alts for j, n in enumerate(names)
                              if n and j not in found.values() and (n == _norm(alt) if exact else _norm(alt) in n)
                              and not (key == "milk" and re.search(r"305|누적|보정|전산차", n))), None)
                    if j is not None:
                        found[key] = j
                        break
            if "milk" in found and len(found) >= 3 and (best is None or len(found) > len(best[2])):
                best = (ws, hi, found, rows)
    if best is None:
        return {"rows": [], "found": {}, "missing": list(NEED) + ["이표/관리번호"], "test_date": test_date,
                "warnings": ["머리글(유량·산차·관리번호 등)을 찾지 못함 — 첫 20줄 안에 머리글 줄이 있어야 함"]}
    ws, hi, found, rows = best
    header = rows[hi]
    warnings: list[str] = []
    out = []
    for row in rows[hi + 1:]:
        if not row or all(v in (None, "") for v in row):
            continue
        g = lambda k: row[found[k]] if k in found and found[k] < len(row) else None  # noqa: E731
        milk = _num(g("milk"))
        key = str(g("official") or g("label") or "").strip()
        if milk is None or not key or milk <= 0 or milk > 90:
            continue
        out.append({"official": str(g("official") or "").strip(), "label": str(g("label") or "").strip(),
                    "test_date": _date(g("test_date")) or test_date, "parity": _num(g("parity")), "dim": _num(g("dim")),
                    "milk": milk, "fat": _num(g("fat")), "protein": _num(g("protein")), "scc": _num(g("scc")),
                    "mun": _num(g("mun"))})
    sccs = [r["scc"] for r in out if r["scc"]]
    if sccs and statistics.median(sccs) > 3000:          # 개/ml → 천/ml
        for r in out:
            if r["scc"]:
                r["scc"] = r["scc"] / 1000
        warnings.append("체세포를 개/ml 로 보고 천/ml 로 바꿈")
    for k in ("fat", "protein"):
        v = [r[k] for r in out if r[k]]
        if v and statistics.median(v) < 1:                # 0.038 → 3.8%
            for r in out:
                if r[k]:
                    r[k] *= 100
    if not any(r["test_date"] for r in out):
        warnings.append("검정일을 찾지 못함 — 올릴 때 검정일을 입력하세요")
    return {"rows": out, "found": {k: str(header[j]) for k, j in found.items()},
            "missing": [k for k in ("official", "label", "parity", "dim", "fat", "protein", "scc", "test_date") if k not in found],
            "test_date": next((r["test_date"] for r in out if r["test_date"]), test_date), "warnings": warnings}


def store_rows(conn: sqlite3.Connection, farm_id: str, parsed: dict[str, Any], test_date: str | None = None) -> dict[str, int]:
    ensure_schema(conn)
    by_off, by_lab = {}, {}
    for aid, lab, off in conn.execute("SELECT animal_id, label, official_id FROM animals WHERE farm_id=?", (farm_id,)):
        if off and _digits9(off):
            by_off[_digits9(off)] = aid
        for x in (lab, off):
            if x:
                by_lab[str(x).strip().lstrip("0") or "0"] = aid
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = matched = 0
    for r in parsed["rows"]:
        td = r["test_date"] or test_date
        if not td:
            continue
        aid = by_off.get(_digits9(r["official"])) if r["official"] else None
        if aid is None and r["label"]:
            aid = by_lab.get(r["label"].lstrip("0") or "0")
        key = r["official"] or r["label"]
        conn.execute("INSERT OR REPLACE INTO dhi VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (farm_id, td, key, aid, r["label"], int(r["parity"]) if r["parity"] else None,
                      int(r["dim"]) if r["dim"] is not None else None, r["milk"], r["fat"], r["protein"], r["scc"], r["mun"], now))
        n += 1
        matched += aid is not None
    conn.commit()
    return {"rows": n, "matched": matched}


def _band(dim: int | None) -> str:
    if dim is None:
        return "?"
    return "0~60" if dim <= 60 else "61~150" if dim <= 150 else "151~305" if dim <= 305 else "305+"


def farm_dhi(conn: sqlite3.Connection, farm_id: str, health: set[str], milk_price: float = 1100.0,
             max_tests: int = 6) -> dict[str, Any] | None:
    have = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dhi'").fetchone()
    if not have:
        return None
    dates = [r[0] for r in conn.execute("SELECT DISTINCT test_date FROM dhi WHERE farm_id=? ORDER BY test_date DESC",
                                        (farm_id,)).fetchall()][:max_tests]
    if not dates:
        return None
    tests = []
    for td in sorted(dates):
        rows = conn.execute("SELECT animal_id, dim, milk, fat, protein, scc, mun FROM dhi WHERE farm_id=? AND test_date=?",
                            (farm_id, td)).fetchall()
        t0 = datetime.strptime(td, "%Y-%m-%d")
        al: dict[str, set] = defaultdict(set)
        if any(r[0] for r in rows):
            q = f"""SELECT animal_id, alert_type FROM alerts WHERE farm_id=? AND occurred_at > ? AND occurred_at <= ?
                    AND alert_type IN ({','.join('?' * len(health))})"""
            for aid, typ in conn.execute(q, (farm_id, f"{t0 - timedelta(days=30):%Y-%m-%d %H:%M:%S}",
                                             f"{t0 + timedelta(days=1):%Y-%m-%d %H:%M:%S}", *health)):
                al[aid].add(typ)
        mean = lambda xs: round(statistics.mean(xs), 2) if xs else None  # noqa: E731
        fp = [(r[3] / r[4], r[1]) for r in rows if r[3] and r[4]]
        sccs = [r[5] for r in rows if r[5] is not None]
        t: dict[str, Any] = {
            "date": td, "n": len(rows), "matched": sum(1 for r in rows if r[0]),
            "milk": mean([r[2] for r in rows]), "fat": mean([r[3] for r in rows if r[3]]),
            "protein": mean([r[4] for r in rows if r[4]]), "fp": mean([x for x, _ in fp]),
            "scc": mean(sccs), "scc200": round(sum(1 for x in sccs if x >= 200) / len(sccs) * 100, 1) if sccs else None,
            "mun": mean([r[6] for r in rows if r[6]]),
            "fp_high_early": round(sum(1 for x, d in fp if d is not None and d <= 60 and x > 1.5) /
                                   max(1, sum(1 for _, d in fp if d is not None and d <= 60)) * 100, 1)
                             if any(d is not None and d <= 60 for _, d in fp) else None,
            "fp_low": round(sum(1 for x, _ in fp if x < 1.0) / len(fp) * 100, 1) if fp else None}
        early = [(x, d) for x, d in fp if d is not None and d <= 60]
        early_rows = [r for r in rows if r[1] is not None and r[1] <= 60 and r[3] and r[4]]
        t["fp_early_n"] = [sum(1 for x, _ in early if x > 1.5), len(early)]      # [해당, 분만 60일 내 두수]
        t["fp_low_n"] = [sum(1 for x, _ in fp if x < 1.0), len(fp)]
        t["scc200_n"] = [sum(1 for x in sccs if x >= 200), len(sccs)]
        t["early_fat"] = mean([r[3] for r in early_rows])
        t["early_protein"] = mean([r[4] for r in early_rows])
        t["no_dim"] = sum(1 for r in rows if r[1] is None)
        # 읽기 점검: 평균값이 상식 범위를 벗어나면 열을 잘못 읽었을 가능성
        w = []
        if t["fat"] is not None and not 2.5 <= t["fat"] <= 6.0:
            w.append(f"평균 유지방 {t['fat']}% — 유지방 열을 잘못 읽었을 수 있음")
        if t["protein"] is not None and not 2.6 <= t["protein"] <= 4.5:
            w.append(f"평균 유단백 {t['protein']}% — 유단백 열을 잘못 읽었을 수 있음")
        if t["no_dim"] > len(rows) / 2:
            w.append(f"비유일수 없는 소 {t['no_dim']}두 — 분만 60일 내 지표는 일부 소만으로 계산")
        t["warn"] = w
        # 알림 소 vs 없는 소 — 비유일수 구간별 유량 차이의 가중 평균
        matched = [r for r in rows if r[0]]
        a_rows = [r for r in matched if al.get(r[0])]
        n_rows = [r for r in matched if not al.get(r[0])]
        diffs, wts = [], []
        for b in ("0~60", "61~150", "151~305", "305+"):
            aa = [r[2] for r in a_rows if _band(r[1]) == b]
            nn = [r[2] for r in n_rows if _band(r[1]) == b]
            if aa and len(nn) >= 3:
                diffs.append(statistics.mean(aa) - statistics.mean(nn))
                wts.append(len(aa))
        t["alert_cows"] = len(a_rows)
        t["milk_diff"] = round(sum(d * w for d, w in zip(diffs, wts)) / sum(wts), 1) if wts else None
        t["loss_month"] = (round(-t["milk_diff"] * sum(wts) * 30 * milk_price / 10000) if t["milk_diff"] is not None
                           and t["milk_diff"] < 0 else 0 if t["milk_diff"] is not None else None)   # 만원
        mast = [r[5] for r in matched if "MASTITIS" in al.get(r[0], ()) and r[5] is not None]
        rest = [r[5] for r in matched if "MASTITIS" not in al.get(r[0], ()) and r[5] is not None]
        t["scc_mastitis"] = (round(statistics.median(mast)), round(statistics.median(rest))) if mast and rest else None
        tests.append(t)
    return {"tests": tests, "milk_price": milk_price}


def template(path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "검정성적"
    ws.append(["검정일", "관리번호", "이표번호", "산차", "비유일수", "유량(kg)", "유지방(%)", "유단백(%)", "체세포(천/ml)", "MUN(mg/dl)"])
    ws.append(["2026-11-05", "118", "002123456789", 2, 85, 38.5, 3.9, 3.2, 120, 14])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append([])
    ws.append(["※ 검정 기관·목장 프로그램에서 받은 엑셀을 그대로 올려도 됩니다(머리글 이름으로 열을 찾음). 이 양식은 직접 입력할 때만."])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
