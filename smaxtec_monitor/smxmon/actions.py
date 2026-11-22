"""알림별 조치 가이드: 확인 → 바로 할 조치(사양·환경) → 수의사 연락 기준 (+ 근거·출처).

기본 내용: config/actions.toml
서버(팀 공용): 관리자가 엑셀로 받아 고쳐 올리면 <SMX_CLOUD_DATA>/actions_override.json 이 우선 사용됨.
약품명·용량은 넣지 않음(진료·처방은 수의사 업무).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import load_toml

INCOMPLETE = "데이터 불완전(판정 제외)"
SPECIAL = {"LYING": "누운 시간 감소·부족(그룹행동)", "SYNC": "함께 눕기 감소(그룹행동)", "GROUP_RUM": "그룹 반추 감소(그룹행동)"}
FIELDS = ("check", "act", "vet", "ref")
_cache: dict[str, Any] = {}


def override_path() -> Path | None:
    p = os.getenv("SMX_ACTIONS_OVERRIDE", "").strip()
    if p:
        return Path(p)
    d = os.getenv("SMX_CLOUD_DATA", "").strip()
    return Path(d) / "actions_override.json" if d else None


def _clean(g: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(g["id"]), "title": str(g["title"]), "when": [str(x) for x in g.get("when", [])],
            **{k: [str(x) for x in g.get(k, []) if str(x).strip()] for k in FIELDS}}


def default_guides() -> list[dict[str, Any]]:
    return [_clean(g) for g in load_toml("actions.toml", required=False).get("guide", []) if g.get("id") and g.get("title")]


def guides() -> tuple[dict[str, Any], ...]:
    """현재 쓰는 가이드(관리자가 올린 것이 있으면 그것)."""
    p = override_path()
    key = f"{p}:{p.stat().st_mtime_ns}" if p and p.exists() else "default"
    if _cache.get("key") != key:
        data = None
        if key != "default":
            try:
                data = [_clean(g) for g in json.loads(p.read_text(encoding="utf-8"))]
            except (ValueError, KeyError, OSError):
                data = None
        _cache.update(key=key, data=tuple(data or default_guides()))
    return _cache["data"]


def is_custom() -> bool:
    p = override_path()
    return bool(p and p.exists())


def signals(s: Any) -> set[str]:
    """목장의 현재 신호: 72시간 알림 종류 + 그룹행동 판정(LYING/SYNC/GROUP_RUM)."""
    sig = {k for k, v in (getattr(s, "counts_72h", None) or {}).items() if v}
    sig |= {k for k, v in (getattr(s, "counts_d0", None) or {}).items() if v}
    cl = getattr(s, "cluster", None)
    if cl is not None:
        sig |= {k for k, v in (cl.animals_by_type or {}).items() if v}
    for g in getattr(s, "behaviour", None) or []:
        if INCOMPLETE in g.flags:
            continue
        if any(f.startswith("누운 시간") for f in g.flags):
            sig.add("LYING")
        if any(f.startswith("함께 눕기") for f in g.flags):
            sig.add("SYNC")
        if g.rum_flag:
            sig.add("GROUP_RUM")
    return sig


def farm_guides(s: Any) -> list[str]:
    """이 목장에 지금 해당하는 가이드 id (순서대로)."""
    sig = signals(s)
    return [g["id"] for g in guides() if sig & set(g["when"])]


def flag_guide(flag: str) -> str | None:
    """그룹행동 판정 문구 → 가이드 id."""
    if flag.startswith("누운 시간"):
        return "lying"
    if flag.startswith("함께 눕기"):
        return "sync"
    if flag.startswith("반추"):
        return "group_rum"
    return None


# ───────────────────────── 엑셀로 받기·올리기 (관리자) ─────────────────────────
XL_HEADERS = ["id (바꾸지 마세요)", "제목", "보여줄 신호 (쉼표로 구분)", "① 먼저 확인 (한 줄에 한 항목)",
              "② 바로 할 조치 (한 줄에 한 항목)", "③ 수의사 연락 기준 (한 줄에 한 항목)", "근거·출처 (한 줄에 하나)"]


def export_xlsx(path: Path, codes: dict[str, str]) -> Path:
    """codes: 알림 코드 → 이름 (안내 시트용)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "조치가이드"
    ws.append(XL_HEADERS)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True), PatternFill("solid", fgColor="DDEBF7")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for g in guides():
        ws.append([g["id"], g["title"], ", ".join(g["when"])] + ["\n".join(g[k]) for k in FIELDS])
    for col, w in zip("ABCDEFG", (14, 18, 22, 60, 55, 42, 50)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "C2"
    info = wb.create_sheet("쓰는 법")
    lines = ["조치 가이드 편집 방법",
             "1. '조치가이드' 시트의 칸을 고칩니다. 한 칸 안에서 Alt+Enter(맥: Option+Enter)로 줄을 바꾸면 항목이 나뉩니다.",
             "2. 새 가이드를 추가하려면 아래에 줄을 추가하고 id 를 영문으로 새로 정합니다(예: calf_diarrhea). 줄을 지우면 그 가이드가 빠집니다.",
             "3. '보여줄 신호'에는 아래 코드만 씁니다. 해당 신호가 목장에 있을 때 그 가이드가 앱에 나옵니다.",
             "4. 수치는 [문헌](논문·공식 지침) / [관행](현장 경험 기준)으로 구분해 적으면 팀원이 무게를 판단하기 쉽습니다.",
             "5. 약품명·용량은 넣지 않습니다(진료·처방은 수의사 업무).",
             "6. 저장 후 앱 계정 화면(관리자) → '조치 가이드 편집' → 올리기. 팀원 전체 화면에 다음 갱신부터 반영됩니다.", "",
             "신호 코드", ]
    for i, t in enumerate(lines, 1):
        info.cell(row=i, column=1, value=t).font = Font(bold=(i in (1, len(lines))))
    r = len(lines) + 1
    for code, name in list(codes.items()) + list(SPECIAL.items()):
        info.cell(row=r, column=1, value=code)
        info.cell(row=r, column=2, value=name)
        r += 1
    info.column_dimensions["A"].width = 24
    info.column_dimensions["B"].width = 40
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def import_xlsx(path: Path, valid_codes: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """엑셀 → (가이드 목록, 오류 목록). 오류가 있으면 저장하지 말 것."""
    import re
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    errs: list[str] = []
    if not rows or not str(rows[0][0] or "").startswith("id"):
        return [], ["'조치 가이드 엑셀 받기'로 받은 파일이 아닙니다(첫 줄 머리글이 다름)"]
    out, seen = [], set()
    ok_codes = valid_codes | set(SPECIAL)
    split = lambda v: [x.strip(" -•·\t") for x in str(v or "").replace("\r", "").split("\n") if x.strip(" -•·\t")]  # noqa: E731
    for i, row in enumerate(rows[1:], 2):
        row = list(row) + [None] * (7 - len(row))
        if all(v in (None, "") for v in row[:7]):
            continue
        gid, title = str(row[0] or "").strip(), str(row[1] or "").strip()
        when = [w.strip().upper() for w in re.split(r"[,\s]+", str(row[2] or "")) if w.strip()]
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", gid):
            errs.append(f"{i}행: id '{gid}' — 영문 소문자·숫자·밑줄로(예: calf_diarrhea)")
        elif gid in seen:
            errs.append(f"{i}행: id '{gid}' 가 중복")
        seen.add(gid)
        if not title:
            errs.append(f"{i}행: 제목이 비어 있음")
        bad = [w for w in when if w not in ok_codes]
        if not when:
            errs.append(f"{i}행({title}): 보여줄 신호가 비어 있음")
        elif bad:
            errs.append(f"{i}행({title}): 모르는 신호 코드 {', '.join(bad)} ('쓰는 법' 시트의 코드표 참고)")
        g = {"id": gid, "title": title, "when": when, "check": split(row[3]), "act": split(row[4]),
             "vet": split(row[5]), "ref": split(row[6])}
        if not g["check"] and not g["act"]:
            errs.append(f"{i}행({title}): '먼저 확인'과 '바로 할 조치'가 모두 비어 있음")
        if sum(len(x) for k in FIELDS for x in g[k]) > 6000:
            errs.append(f"{i}행({title}): 내용이 너무 김")
        out.append(g)
    if not out:
        errs.append("가이드가 하나도 없습니다")
    return out, errs


def save_override(items: list[dict[str, Any]]) -> Path:
    p = override_path()
    if p is None:
        raise RuntimeError("서버 데이터 폴더(SMX_CLOUD_DATA)가 없습니다")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    _cache.clear()
    return p


def reset_override() -> None:
    p = override_path()
    if p and p.exists():
        p.unlink()
    _cache.clear()
