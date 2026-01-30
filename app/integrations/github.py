from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)
_auth_failed = False


def parse_github_url(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Invalid GitHub issue/PR URL")

    path = parsed.path.strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 4:
        raise ValueError("Invalid GitHub issue/PR URL")

    owner, repo, kind, number_raw = parts[0], parts[1], parts[2], parts[3]
    repo = repo.removesuffix(".git")
    if kind not in {"issues", "pull", "pulls"}:
        raise ValueError("Invalid GitHub issue/PR URL")
    if not number_raw.isdigit():
        raise ValueError("Invalid GitHub issue/PR URL")

    return owner, repo, int(number_raw)


def github_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request_with_retry(
    url: str,
    token: str,
    *,
    timeout: int = 10,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> requests.Response:
    last_exc: Exception | None = None
    headers = github_headers(token)

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 401:
                logger.error(
                    "github.auth_failed",
                    extra={"url": url, "status": response.status_code},
                )
                global _auth_failed
                _auth_failed = True
                raise requests.RequestException("GitHub authentication failed (401)")

            if response.status_code in {403, 429}:
                remaining = response.headers.get("X-RateLimit-Remaining")
                if remaining == "0":
                    reset = response.headers.get("X-RateLimit-Reset")
                    logger.warning(
                        "github.rate_limit",
                        extra={"url": url, "reset": reset},
                    )
                    raise requests.RequestException("GitHub rate limit exceeded")
            if response.status_code >= 400:
                response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            logger.debug(
                "github.request.failed",
                extra={"url": url, "attempt": attempt, "max_attempts": max_attempts},
            )
            if _auth_failed:
                break
            if attempt >= max_attempts:
                break

            retry_after = None
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                retry_after = exc.response.headers.get("Retry-After")
            delay = base_delay * (2 ** (attempt - 1))
            if retry_after and retry_after.isdigit():
                delay = max(delay, int(retry_after))
            time.sleep(delay)

    raise requests.RequestException("Request failed after retries") from last_exc


def get_issue(owner: str, repo: str, number: int, token: str) -> dict:
    response = _request_with_retry(
        f"https://api.github.com/repos/{owner}/{repo}/issues/{number}",
        token,
    )
    return response.json()


def get_issue_title(token: str, url: str, *, raise_on_error: bool = False) -> Optional[str]:
    try:
        owner, repo, number = parse_github_url(url)
    except ValueError as exc:
        if raise_on_error:
            raise
        logger.warning(
            "github.url.invalid",
            extra={"url": url, "error": str(exc)},
        )
        return None

    try:
        issue = get_issue(owner, repo, number, token)
        title = issue.get("title")
        return title if title else None
    except requests.RequestException as exc:
        if raise_on_error:
            raise
        logger.debug(
            "github.issue.fetch_failed",
            extra={"url": url, "error": str(exc)},
        )
        return None
