from __future__ import annotations

import smtplib
import time
from pathlib import Path
from email.message import EmailMessage


def _normalize_recipients(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def send_email_message(
    settings,
    recipients: str | list[str],
    subject: str,
    text: str,
    html: str | None = None,
    attachments: list[str | Path] | None = None,
    *,
    timeout: int = 15,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> None:
    to_list = _normalize_recipients(recipients)
    if not to_list:
        raise ValueError("No email recipients configured")

    smtp_host = settings.smtp_host
    smtp_port = settings.smtp_port
    if not smtp_host:
        raise ValueError("SMTP host not configured")

    from_addr = settings.smtp_from or settings.smtp_user
    if not from_addr:
        raise ValueError("SMTP sender not configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(to_list)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    for attachment in attachments or []:
        path = Path(attachment)
        if not path.exists() or not path.is_file():
            continue
        data = path.read_bytes()
        message.add_attachment(
            data,
            maintype="application",
            subtype="pdf",
            filename=path.name,
        )

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if settings.smtp_use_ssl:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout) as server:
                    _send_with_login(server, settings, message)
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as server:
                    if settings.smtp_use_tls:
                        server.starttls()
                    _send_with_login(server, settings, message)
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= max_attempts:
                break
            time.sleep(base_delay * (2 ** (attempt - 1)))

    raise RuntimeError("Email delivery failed") from last_exc


def _send_with_login(server: smtplib.SMTP, settings, message: EmailMessage) -> None:
    if settings.smtp_user and settings.smtp_password:
        username = str(settings.smtp_user).strip()
        password = str(settings.smtp_password).strip().replace(" ", "")
        server.login(username, password)
    server.send_message(message)
