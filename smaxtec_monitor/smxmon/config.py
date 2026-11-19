"""설정 로딩: .env(비밀정보) + config/*.toml(선택자·규칙·알림종류)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"


class ConfigError(RuntimeError):
    pass


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _int(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v else default


def _float(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    return float(v) if v else default


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, "").strip() or default)
    return p if p.is_absolute() else BASE_DIR / p


@dataclass(frozen=True)
class Settings:
    # repr=False → print(settings) 나 로그에 비밀번호가 찍히지 않음
    username: str = field(repr=False)
    password: str = field(repr=False)
    login_url: str
    headless: bool
    min_delay: float
    max_delay: float
    max_retries: int
    page_timeout_ms: int
    timezone: str
    db_path: Path
    report_dir: Path
    log_dir: Path
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = field(default="", repr=False)
    smtp_password: str = field(default="", repr=False)
    smtp_use_ssl: bool = False
    mail_from: str = ""
    mail_to: str = ""

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent

    @property
    def auth_state_path(self) -> Path:
        # 로그인 세션 쿠키 저장(재로그인 횟수 감소). 비밀번호와 같은 수준으로 취급.
        return self.data_dir / "auth_state.json"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.mail_to)

    def secrets(self) -> list[str]:
        """로그 마스킹 대상 문자열."""
        return [s for s in (self.username, self.password, self.smtp_user, self.smtp_password) if s]

    def require_credentials(self) -> None:
        missing = [n for n, v in (("SMX_USERNAME", self.username), ("SMX_PASSWORD", self.password)) if not v]
        if missing:
            raise ConfigError(f".env 에 {', '.join(missing)} 값이 없습니다. (.env.example 참고)")
        if not self.login_url or "TODO" in self.login_url:
            raise ConfigError(".env 의 SMX_LOGIN_URL 을 실제 로그인 주소로 채워주세요.")


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or BASE_DIR / ".env", override=False)
    return Settings(
        username=os.getenv("SMX_USERNAME", "").strip(),
        password=os.getenv("SMX_PASSWORD", ""),
        login_url=os.getenv("SMX_LOGIN_URL", "").strip(),
        headless=_bool(os.getenv("SMX_HEADLESS"), True),
        min_delay=_float("SMX_MIN_DELAY_SEC", 3.0),
        max_delay=_float("SMX_MAX_DELAY_SEC", 8.0),
        max_retries=_int("SMX_MAX_RETRIES", 3),
        page_timeout_ms=_int("SMX_PAGE_TIMEOUT_MS", 30000),
        timezone=os.getenv("SMX_TIMEZONE", "Asia/Seoul").strip() or "Asia/Seoul",
        db_path=_path("SMX_DB_PATH", "data/smaxtec.db"),
        report_dir=_path("SMX_REPORT_DIR", "reports"),
        log_dir=_path("SMX_LOG_DIR", "logs"),
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=_int("SMTP_PORT", 587),
        smtp_user=os.getenv("SMTP_USER", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_use_ssl=_bool(os.getenv("SMTP_USE_SSL"), False),
        mail_from=os.getenv("MAIL_FROM", "").strip(),
        mail_to=os.getenv("MAIL_TO", "").strip(),
    )


def load_toml(name: str, required: bool = True) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        if required:
            raise ConfigError(f"설정 파일이 없습니다: {path}")
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_farm_overrides() -> dict[str, dict[str, Any]]:
    """config/farms.toml (선택): 목장별 사육두수·표시명·제외 여부."""
    data = load_toml("farms.toml", required=False)
    return {str(k): v for k, v in data.get("farms", {}).items()}
