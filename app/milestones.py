from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MilestoneEntry:
    name: str
    closed_week: int
    closed_previous: int
    total_closed: int
    total_issues: int

    @property
    def percent(self) -> int:
        if self.total_issues <= 0:
            return 0
        return int(round((self.total_closed / self.total_issues) * 100))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _donut_svg(label: str, percent: int) -> str:
    width = 170
    height = 150
    center_x = 85
    center_y = 88
    radius = 44
    stroke = 12
    circumference = 2 * math.pi * radius
    progress = max(0, min(100, percent)) / 100
    dash = circumference * progress

    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        f"viewBox='0 0 {width} {height}'>"
        f"<text x='{center_x}' y='26' text-anchor='middle' "
        f"font-family='Inter, Arial' font-size='11' fill='#4b5f5a' font-weight='600'>{label}</text>"
        f"<circle cx='{center_x}' cy='{center_y}' r='{radius}' fill='none' "
        f"stroke='#9ca3af' stroke-width='{stroke}' opacity='0.85'/>"
        f"<circle cx='{center_x}' cy='{center_y}' r='{radius}' fill='none' "
        f"stroke='#4aa879' stroke-width='{stroke}' stroke-linecap='round' "
        f"stroke-dasharray='{dash:.2f} {circumference:.2f}' transform='rotate(-90 {center_x} {center_y})'/>"
        f"<text x='{center_x}' y='{center_y + 5}' text-anchor='middle' "
        f"font-family='Inter, Arial' font-size='13' fill='#1b1f1e' font-weight='700'>{percent}%</text>"
        f"</svg>"
    )


def _normalize_entry(payload: dict[str, Any]) -> MilestoneEntry | None:
    name = str(payload.get("name") or payload.get("title") or "").strip()
    if not name:
        return None

    return MilestoneEntry(
        name=name,
        closed_week=_safe_int(payload.get("closed_week")),
        closed_previous=_safe_int(payload.get("closed_previous") or payload.get("closed_prev")),
        total_closed=_safe_int(payload.get("total_closed")),
        total_issues=_safe_int(payload.get("total_issues")),
    )


def _parse_week_range(week_id: str | None) -> tuple[date, date] | None:
    if not week_id:
        return None
    try:
        year_str, week_str = week_id.split("-W")
        year = int(year_str)
        week = int(week_str)
        start = date.fromisocalendar(year, week, 1)
        end = start + timedelta(days=6)
        return start, end
    except Exception:
        return None


def _parse_milestone_url(url: str) -> tuple[str, str, int] | None:
    trimmed = url.strip().rstrip("/")
    if not trimmed:
        return None
    parts = trimmed.split("/")
    try:
        owner = parts[-4]
        repo = parts[-3]
        number = int(parts[-1])
        if parts[-2] != "milestone":
            return None
        return owner, repo, number
    except Exception:
        return None


def _get_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 15,
    max_attempts: int = 3,
    base_delay: float = 0.5,
) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "milestones.request.failed",
                extra={
                    "url": url,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
            )
            if attempt >= max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            time.sleep(delay)

    raise requests.RequestException("Request failed after retries") from last_exc


def _fetch_closed_issues_count(
    owner: str,
    repo: str,
    milestone_number: int,
    start: date,
    end: date,
    token: str | None,
) -> int:
    if start > end:
        return 0
    count = 0
    page = 1
    max_pages = 10
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())

    while page <= max_pages:
        response = _request_with_retry(
            "GET",
            f"https://api.github.com/repos/{owner}/{repo}/issues",
            headers=_get_headers(token),
            params={
                "milestone": milestone_number,
                "state": "closed",
                "per_page": 100,
                "page": page,
            },
            timeout=15,
        )
        issues = response.json()
        if not issues:
            break
        for issue in issues:
            closed_at = issue.get("closed_at")
            if not closed_at:
                continue
            try:
                closed_dt = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                if closed_dt.tzinfo is not None:
                    closed_dt = closed_dt.astimezone(tz=None).replace(tzinfo=None)
            except ValueError:
                continue
            if start_dt <= closed_dt <= end_dt:
                count += 1
        page += 1

    return count


def _extract_labels(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels") or []
    names = []
    for label in labels:
        name = label.get("name") if isinstance(label, dict) else str(label)
        if name:
            names.append(str(name).strip().lower())
    return names


def _label_matches(labels: list[str], keywords: list[str]) -> bool:
    for label in labels:
        for key in keywords:
            if key in label:
                return True
    return False


def _classify_status(labels: list[str], state: str) -> str:
    if state == "closed":
        return "done"
    if _label_matches(labels, ["review", "code review", "in review", "pr review"]):
        return "review"
    if _label_matches(labels, ["progress", "in progress", "doing", "wip"]):
        return "progress"
    if _label_matches(labels, ["blocked", "blocker", "imped", "impedimento"]):
        return "blocked"
    if _label_matches(labels, ["backlog", "to do", "todo", "pendente"]):
        return "backlog"
    return "backlog"


def _has_difficulty(labels: list[str]) -> bool:
    return _label_matches(labels, ["dificuldade", "difficulty", "hard"])


def _load_issue_status_totals(
    urls: list[str],
    token: str | None,
) -> dict[str, int]:
    totals = {
        "backlog": 0,
        "blocked": 0,
        "progress": 0,
        "review": 0,
        "done": 0,
        "total_count": 0,
        "review_count": 0,
        "done_count": 0,
        "difficulty_total": 0,
        "difficulty_review": 0,
        "difficulty_done": 0,
    }

    for url in urls:
        parsed = _parse_milestone_url(url)
        if not parsed:
            continue
        owner, repo, number = parsed
        page = 1
        max_pages = 10
        while page <= max_pages:
            response = _request_with_retry(
                "GET",
                f"https://api.github.com/repos/{owner}/{repo}/issues",
                headers=_get_headers(token),
                params={
                    "milestone": number,
                    "state": "all",
                    "per_page": 100,
                    "page": page,
                },
                timeout=15,
            )
            issues = response.json()
            if not issues:
                break
            for issue in issues:
                labels = _extract_labels(issue)
                state = str(issue.get("state") or "").lower()
                status = _classify_status(labels, state)
                totals[status] += 1
                totals["total_count"] += 1
                if status == "review":
                    totals["review_count"] += 1
                if status == "done":
                    totals["done_count"] += 1
                if _has_difficulty(labels):
                    totals["difficulty_total"] += 1
                    if status == "review":
                        totals["difficulty_review"] += 1
                    if status == "done":
                        totals["difficulty_done"] += 1
            page += 1

    return totals


def _load_from_urls(
    urls: list[str],
    token: str | None,
    week_id: str | None,
) -> list[MilestoneEntry] | None:
    if not urls:
        return None
    if not urls:
        return None

    week_range = _parse_week_range(week_id)
    prev_week_start = prev_week_end = None
    if week_range:
        prev_week_end = week_range[0] - timedelta(days=1)
        prev_week_start = prev_week_end - timedelta(days=6)

    entries: list[MilestoneEntry] = []
    for url in urls:
        parsed = _parse_milestone_url(url)
        if not parsed:
            continue
        owner, repo, number = parsed
        try:
            response = _request_with_retry(
                "GET",
                f"https://api.github.com/repos/{owner}/{repo}/milestones/{number}",
                headers=_get_headers(token),
                timeout=15,
            )
            data = response.json()
            name = str(data.get("title") or "").strip() or f"{owner}/{repo}"
            closed_issues = _safe_int(data.get("closed_issues"))
            open_issues = _safe_int(data.get("open_issues"))
            total_issues = closed_issues + open_issues

            closed_week = 0
            closed_previous = 0
            if week_range:
                closed_week = _fetch_closed_issues_count(
                    owner,
                    repo,
                    number,
                    week_range[0],
                    week_range[1],
                    token,
                )
            if prev_week_start and prev_week_end:
                closed_previous = _fetch_closed_issues_count(
                    owner,
                    repo,
                    number,
                    prev_week_start,
                    prev_week_end,
                    token,
                )

            entries.append(
                MilestoneEntry(
                    name=name,
                    closed_week=closed_week,
                    closed_previous=closed_previous,
                    total_closed=closed_issues,
                    total_issues=total_issues,
                )
            )
        except requests.RequestException:
            continue

    return entries or None


def _resolve_urls(
    project_urls: dict[str, Any] | None,
    project_slug: str | None,
    milestone_month: str | None = None,
) -> tuple[list[str], str | None, list[str]]:
    if not project_urls or not project_slug:
        return [], None, []

    raw = project_urls.get(project_slug)
    if isinstance(raw, list):
        return raw, None, []
    if not isinstance(raw, dict):
        return [], None, []

    month_keys = [str(key) for key in raw.keys()]
    if not month_keys:
        return [], None, []

    selected_month = milestone_month if milestone_month in raw else month_keys[-1]
    urls = raw.get(selected_month, []) if isinstance(raw.get(selected_month), list) else []
    return urls, selected_month, month_keys


def list_milestone_months(
    project_urls: dict[str, Any] | None,
    project_slug: str,
) -> list[str]:
    if not project_urls or not project_slug:
        return []
    raw = project_urls.get(project_slug)
    if isinstance(raw, dict):
        return [str(key) for key in raw.keys()]
    return []


def load_milestone_section(
    token: str | None = None,
    week_id: str | None = None,
    project_urls: dict[str, Any] | None = None,
    project_slug: str | None = None,
    milestone_month: str | None = None,
) -> dict[str, Any] | None:
    urls, selected_month, _ = _resolve_urls(project_urls, project_slug, milestone_month)
    entries = _load_from_urls(urls, token, week_id) if urls else None
    if not entries:
        return None

    count = len(entries)
    columns = 2 if count == 4 else min(3, count)

    aggregated_milestones = {}
    for entry in entries:
        key = entry.name.strip().lower()
        if key not in aggregated_milestones:
            aggregated_milestones[key] = {
                "name": entry.name,
                "total_closed": 0,
                "total_issues": 0
            }
        aggregated_milestones[key]["total_closed"] += entry.total_closed
        aggregated_milestones[key]["total_issues"] += entry.total_issues

    milestone_cards = []
    for key, data in aggregated_milestones.items():
        total_issues = data["total_issues"]
        total_closed = data["total_closed"]
        percent = int(round((total_closed / total_issues) * 100)) if total_issues > 0 else 0
        name = data["name"]
        
        milestone_cards.append({
            "name": name,
            "title": f"{name} - % concluída",
            "percent": percent,
            "svg": _donut_svg(name, percent),
        })

    table_rows = [
        {
            "name": entry.name,
            "closed_week": entry.closed_week,
            "closed_previous": entry.closed_previous,
            "total_closed": entry.total_closed,
            "total_issues": entry.total_issues,
        }
        for entry in entries
    ]

    status_totals = _load_issue_status_totals(urls, token)
    total_issues = status_totals["total_count"]
    review_total = status_totals["review_count"]
    done_total = status_totals["done_count"]
    difficulty_total = status_totals["difficulty_total"]

    def _percent(value: int, total: int) -> int:
        if total <= 0:
            return 0
        return int(round((value / total) * 100))

    done_percent = _percent(done_total, total_issues)
    done_review_percent = _percent(done_total + review_total, total_issues)

    status_table = {
        "backlog": status_totals["backlog"],
        "blocked": status_totals["blocked"],
        "progress": status_totals["progress"],
        "review": status_totals["review"],
        "done": status_totals["done"],
        "done_percent": done_percent,
        "done_review_percent": done_review_percent,
    }

    difficulty_table = {
        "total_count": total_issues,
        "review_count": review_total,
        "difficulty_total": difficulty_total,
        "difficulty_review": status_totals["difficulty_review"],
        "difficulty_done": status_totals["difficulty_done"],
        "done_count_percent": _percent(done_total, total_issues),
        "done_difficulty_percent": _percent(
            status_totals["difficulty_done"],
            difficulty_total,
        ),
        "done_review_count_percent": _percent(done_total + review_total, total_issues),
        "done_review_difficulty_percent": _percent(
            status_totals["difficulty_done"] + status_totals["difficulty_review"],
            difficulty_total,
        ),
    }

    return {
        "milestones": milestone_cards,
        "rows": table_rows,
        "count": count,
        "columns": columns,
        "month": selected_month,
        "status_table": status_table,
        "difficulty_table": difficulty_table,
    }
