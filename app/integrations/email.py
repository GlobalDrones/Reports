from __future__ import annotations

import base64
import logging
import smtplib
import time
from pathlib import Path
from email.message import EmailMessage

import requests

logger = logging.getLogger(__name__)

GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_SEND_MAIL_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


def _normalize_recipients(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _get_graph_access_token(settings, timeout: int) -> str:
    resp = requests.post(
        GRAPH_TOKEN_URL.format(tenant_id=settings.azure_tenant_id),
        data={
            "grant_type": "client_credentials",
            "client_id": settings.azure_client_id,
            "client_secret": settings.azure_client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _send_via_graph(
    settings,
    to_list: list[str],
    subject: str,
    text: str,
    html: str | None,
    attachments: list[str | Path] | None,
    timeout: int,
) -> None:
    access_token = _get_graph_access_token(settings, timeout)

    graph_attachments = []
    for attachment in attachments or []:
        path = Path(attachment)
        if not path.exists() or not path.is_file():
            continue
        graph_attachments.append(
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name,
                "contentType": "application/pdf",
                "contentBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )

    body = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML" if html else "Text",
                "content": html or text,
            },
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to_list],
            "attachments": graph_attachments,
        },
        "saveToSentItems": "false",
    }

    resp = requests.post(
        GRAPH_SEND_MAIL_URL.format(sender=settings.azure_sender_email),
        json=body,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    )
    resp.raise_for_status()


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

    use_graph = bool(
        settings.azure_tenant_id
        and settings.azure_client_id
        and settings.azure_client_secret
        and settings.azure_sender_email
    )
    if use_graph:
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                _send_via_graph(settings, to_list, subject, text, html, attachments, timeout)
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(f"email.graph.send_failed attempt={attempt} error={exc}")
                if attempt >= max_attempts:
                    break
                time.sleep(base_delay * (2 ** (attempt - 1)))
        raise RuntimeError("Email delivery via Microsoft Graph failed") from last_exc

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
