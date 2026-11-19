"""화면 문자열 → 표준값 변환 (알림종류 코드, 일시)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

DB_TIME_FMT = "%Y-%m-%d %H:%M:%S"


def to_db_time(dt: datetime) -> str:
    """DB 저장 형식: 현지시간(기본 KST) 기준, 시간대 표기 없음, 초 단위."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime(DB_TIME_FMT)


def from_db_time(s: str | None) -> datetime | None:
    return datetime.strptime(s, DB_TIME_FMT) if s else None


# ───────────────────────── 알림 종류 ─────────────────────────
@dataclass(frozen=True)
class AlertType:
    code: str
    label: str
    category: str  # health / reproduction / other
    keywords: tuple[str, ...]


class AlertTypeMapper:
    """config/alert_types.toml 의 키워드로 화면 표기를 표준 코드로 변환.

    여러 키워드가 걸리면 가장 긴 키워드를 우선(예: '체온 하강' vs '체온').
    """

    def __init__(self, cfg: dict[str, Any]):
        self.types: dict[str, AlertType] = {}
        for code, t in cfg.get("types", {}).items():
            self.types[code] = AlertType(
                code=code,
                label=t.get("label", code),
                category=t.get("category", "other"),
                keywords=tuple(k.lower() for k in t.get("keywords", [])),
            )
        if "OTHER" not in self.types:
            self.types["OTHER"] = AlertType("OTHER", "기타", "other", ())

    def map(self, raw: str) -> str:
        text = " ".join((raw or "").lower().split())
        best_code, best_len = "OTHER", 0
        for t in self.types.values():
            for kw in t.keywords:
                if kw and kw in text and len(kw) > best_len:
                    best_code, best_len = t.code, len(kw)
        return best_code

    def label(self, code: str) -> str:
        return self.types.get(code, self.types["OTHER"]).label

    def category(self, code: str) -> str:
        return self.types.get(code, self.types["OTHER"]).category

    def codes(self, category: str | None = None) -> list[str]:
        return [c for c, t in self.types.items() if category is None or t.category == category]


# ───────────────────────── 일시 파싱 ─────────────────────────
_REL_PATTERNS = [
    (re.compile(r"(\d+)\s*(분|min|mins|minute|minutes)\s*(전|ago)?", re.I), "minutes"),
    (re.compile(r"(\d+)\s*(시간|h|hr|hrs|hour|hours)\s*(전|ago)?", re.I), "hours"),
    (re.compile(r"(\d+)\s*(일|d|day|days)\s*(전|ago)?", re.I), "days"),
]


class DateParseError(ValueError):
    pass


def parse_datetime(text: str, formats: list[str], now: datetime) -> tuple[datetime, bool]:
    """화면 일시 문자열 파싱.

    반환: (datetime, is_relative)
      is_relative=True 이면 '3시간 전' 같은 상대표기 → 수집 시점마다 값이 흔들리므로
      DB 쪽에서 허용오차(tolerance) 매칭으로 중복을 막는다.
    """
    s = " ".join((text or "").split())
    if not s:
        raise DateParseError("빈 일시 문자열")

    for fmt in formats:
        try:
            if "%Y" in fmt or "%y" in fmt:
                dt = datetime.strptime(s, fmt)
            else:  # 연도 없는 표기(예: 11-19 07:30) → 올해로 보고, 미래면 작년
                dt = datetime.strptime(f"{now.year} {s}", f"%Y {fmt}")
                if dt > now + timedelta(days=1):
                    dt = dt.replace(year=now.year - 1)
            return dt.replace(second=0, microsecond=0), False
        except ValueError:
            continue

    low = s.lower()
    if low in ("방금", "방금 전", "just now", "now"):
        return now.replace(second=0, microsecond=0), True
    for pat, unit in _REL_PATTERNS:
        m = pat.fullmatch(low) or pat.search(low)
        if m and (m.group(3) or low.endswith(("전", "ago"))):
            dt = now - timedelta(**{unit: int(m.group(1))})
            return dt.replace(second=0, microsecond=0), True

    raise DateParseError(f"일시 형식을 해석할 수 없음: {s!r} → config/selectors.toml [datetime].formats 에 형식 추가 필요")
