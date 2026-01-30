from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)


def _mask_webhook(url: str) -> str:
    if not url:
        return ""
    if len(url) <= 16:
        return "***"
    return f"{url[:8]}...{url[-4:]}"


def _post_with_retry(
    url: str,
    payload: dict,
    *,
    timeout: int = 10,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> requests.Response:
    last_exc: Exception | None = None
    masked_url = _mask_webhook(url)

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "teams.webhook.failed",
                extra={"webhook": masked_url, "attempt": attempt, "max_attempts": max_attempts},
            )
            if attempt >= max_attempts:
                break
            retry_after = None
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                retry_after = exc.response.headers.get("Retry-After")
            delay = base_delay * (2 ** (attempt - 1))
            if retry_after and retry_after.isdigit():
                delay = max(delay, int(retry_after))
            time.sleep(delay)

    raise requests.RequestException("Teams webhook failed after retries") from last_exc


def send_teams_message(
    webhook_url: str, title: str, text: str, link_url: str, button_name: str = "Abrir"
) -> None:
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": title,
        "themeColor": "0078D7",
        "title": title,
        "text": text,
        "potentialAction": [
            {
                "@type": "OpenUri",
                "name": button_name,
                "targets": [{"os": "default", "uri": link_url}],
            }
        ],
    }
    _post_with_retry(webhook_url, payload, timeout=10)
