"""알림별 조치 가이드 (config/actions.toml): 확인 → 바로 할 조치(사양·환경) → 수의사 연락 기준.

약품명·용량은 넣지 않음(진료·처방은 수의사 업무). 내용은 config/actions.toml 을 직접 고쳐서 관리.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import load_toml

INCOMPLETE = "데이터 불완전(판정 제외)"


@lru_cache(maxsize=1)
def guides() -> tuple[dict[str, Any], ...]:
    out = []
    for g in load_toml("actions.toml", required=False).get("guide", []):
        if g.get("id") and g.get("title"):
            out.append({"id": str(g["id"]), "title": str(g["title"]), "when": list(g.get("when", [])),
                        "check": list(g.get("check", [])), "act": list(g.get("act", [])),
                        "vet": list(g.get("vet", []))})
    return tuple(out)


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
    """이 목장에 지금 해당하는 가이드 id (파일 순서)."""
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
