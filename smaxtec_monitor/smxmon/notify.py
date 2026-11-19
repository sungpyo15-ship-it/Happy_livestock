"""(선택) 메일 발송: 일일 리포트 첨부 / 긴급 집단신호 알림."""
from __future__ import annotations

import logging
import mimetypes
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from .config import Settings

log = logging.getLogger("smxmon")


def send_email(s: Settings, subject: str, body: str, attachments: list[Path] | None = None) -> bool:
    if not s.email_enabled:
        log.info("메일 설정(SMTP_HOST, MAIL_TO) 없음 → 발송 생략")
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = s.mail_from or s.smtp_user
    msg["To"] = s.mail_to
    msg.set_content(body)
    for p in attachments or []:
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)

    ctx = ssl.create_default_context()
    try:
        if s.smtp_use_ssl:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=30) as smtp:
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ctx)
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        log.info("메일 발송 완료: %s", subject)
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.error("메일 발송 실패: %s", type(e).__name__)  # 서버 응답에 계정정보가 섞일 수 있어 유형만 기록
        return False
