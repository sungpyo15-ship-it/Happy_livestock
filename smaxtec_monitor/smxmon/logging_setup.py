"""로깅: 파일 로테이션 + 비밀정보 자동 마스킹."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class SecretMaskFilter(logging.Filter):
    """로그 메시지에 아이디/비밀번호 문자열이 섞여 들어가면 *** 로 치환."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        # 짧은 문자열(예: 'a')까지 치환하면 로그가 망가지므로 3자 이상만
        self.secrets = sorted({s for s in secrets if len(s) >= 3}, key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        msg = record.getMessage()
        masked = msg
        for s in self.secrets:
            masked = masked.replace(s, "***")
        if masked != msg:
            record.msg, record.args = masked, ()
        return True


def setup_logging(log_dir: Path, secrets: list[str], verbose: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("smxmon")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    mask = SecretMaskFilter(secrets)

    fh = RotatingFileHandler(log_dir / "smxmon.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh.addFilter(mask)

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(fmt)
    ch.addFilter(mask)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
