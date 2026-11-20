"""수집 공통 타입·예외 (브라우저 라이브러리 없이도 사용 가능 — 서버용 API 모드)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ScrapeError(RuntimeError):
    pass


class LoginError(ScrapeError):
    pass


@dataclass
class FarmInfo:
    farm_id: str
    name: str


@dataclass
class RawAlert:
    """화면에서 읽은 그대로의 값 (정규화 전)."""
    farm_id: str
    animal_id: str
    alert_type_raw: str
    occurred_at_raw: str
    resolved_at_raw: str = ""
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
