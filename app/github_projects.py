from __future__ import annotations

import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

GITHUB_COLORS = {
    "backlog": "#8B949E",
    "blocked": "#f97316",
    "progress": "#BF8700",
    "review": "#2188FF",
    "done": "#986EE2",
    "no_status": "#8B949E",
    "duplicate": "#64748b",
}

CHART_WIDTH = 640
CHART_HEIGHT = 220
CHART_PADDING = 50
BG_COLOR = "white"
GRID_COLOR = "#D0D7DE"
TEXT_COLOR = "#24292F"


@dataclass
class ProjectItem:
    created_at: datetime
    status: str
    status_updated_at: datetime | None
    iteration_title: str | None
    iteration_start: date | None
    iteration_end: date | None
    milestone: str | None
    difficulty: float
    estimate_hours: float
    labels: list[str]
    content_type: str = "Issue"
    repository: str | None = None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _run_graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers=headers,
        timeout=15,
    )
    if response.status_code != 200:
        raise requests.RequestException(
            f"GraphQL failed with {response.status_code}: {response.text}"
        )
    payload = response.json()
    if "errors" in payload:
        raise requests.RequestException(f"GraphQL errors: {payload['errors']}")
    return payload


def _bucket_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if not normalized:
        return "no_status"
    if any(key in normalized for key in ["cancel", "canceled", "cancelled"]):
        return "cancelled"
    if any(key in normalized for key in ["duplicate", "duplicado"]):
        return "duplicate"
    if any(key in normalized for key in ["done", "concl", "closed", "finalizado"]):
        return "done"
    if any(key in normalized for key in ["review", "revis", "qa"]):
        return "review"
    if any(key in normalized for key in ["progress", "andamento", "doing", "wip"]):
        return "progress"
    if any(key in normalized for key in ["blocked", "bloqueado", "imped"]):
        return "blocked"
    if any(
        key in normalized for key in ["backlog", "todo", "pendente", "to do", "ready", "planning"]
    ):
        return "backlog"
    return "backlog"


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


def _milestone_matches(value: str | None, target: str | None) -> bool:
    if not value or not target:
        return False
    return _normalize_text(target) in _normalize_text(value)


def _get_latest_milestone(items: list[ProjectItem]) -> str | None:
    milestone_dates: dict[str, date] = {}

    for item in items:
        if not item.milestone:
            continue
        milestone = item.milestone
        item_date = item.iteration_end or item.created_at.date()

        if milestone not in milestone_dates or item_date > milestone_dates[milestone]:
            milestone_dates[milestone] = item_date

    if not milestone_dates:
        return None

    latest_milestone = max(milestone_dates.items(), key=lambda x: x[1])
    return latest_milestone[0]


def _week_range(week_id: str | None) -> tuple[date, date] | None:
    if not week_id:
        return None
    try:
        year_str, week_str = week_id.split("-W")
        start = date.fromisocalendar(int(year_str), int(week_str), 1)
        end = start + timedelta(days=6)
        return start, end
    except Exception:
        return None


def _subtract_months(value: date, months: int) -> date:
    year = value.year
    month = value.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(value.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_field_name(name: str | None) -> str:
    return (name or "").strip().lower()


def _field_match(name: str | None, target: str | None) -> bool:
    if not target:
        return False
    return _normalize_field_name(name) == _normalize_field_name(target)


def _parse_numeric_from_text(value: str | None) -> float:
    if not value:
        return 0.0
    match = re.search(r"-?\d+(?:[\.,]\d+)?", value)
    if not match:
        return 0.0
    raw = match.group(0).replace(",", ".")
    return _safe_float(raw)


def _map_difficulty_label(label: str | None) -> float:
    if not label:
        return 0.0
    normalized = label.strip().upper()
    scale_map = {
        "XS": 1.0,
        "S": 2.0,
        "M": 3.0,
        "L": 4.0,
        "XL": 5.0,
        "P0": 5.0,
        "P1": 4.0,
        "P2": 3.0,
        "P3": 2.0,
        "P4": 1.0,
    }
    for key, value in scale_map.items():
        if normalized.startswith(key):
            return value
    return _parse_numeric_from_text(label)


def _is_duplicate_item(item: ProjectItem) -> bool:
    if _bucket_status(item.status) == "duplicate":
        return True
    labels = [label.strip().lower() for label in (item.labels or [])]
    return any(label in {"duplicate", "duplicado"} for label in labels)


def fetch_project_items(
    token: str,
    project_id: str,
    *,
    status_field: str = "Status",
    iteration_field: str = "Iteration",
    milestone_field: str = "Milestone",
    difficulty_field: str = "Dificuldade",
    estimate_field: str = "Estimate (Hours)",
) -> list[ProjectItem]:
    query = """
    query($projectId: ID!, $cursor: String) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              createdAt
              fieldValues(first: 100) {
                nodes {
                                    __typename
                                    ... on ProjectV2ItemFieldSingleSelectValue {
                                        name
                                        updatedAt
                                        field { ... on ProjectV2FieldCommon { name } }
                                    }
                                    ... on ProjectV2ItemFieldNumberValue {
                                        number
                                        field { ... on ProjectV2FieldCommon { name } }
                                    }
                                    ... on ProjectV2ItemFieldTextValue {
                                        text
                                        field { ... on ProjectV2FieldCommon { name } }
                                    }
                                    ... on ProjectV2ItemFieldDateValue {
                                        date
                                        field { ... on ProjectV2FieldCommon { name } }
                                    }
                                    ... on ProjectV2ItemFieldMilestoneValue {
                                        milestone { title }
                                        field { ... on ProjectV2FieldCommon { name } }
                                    }
                                    ... on ProjectV2ItemFieldIterationValue {
                                        iterationId
                                        title
                                        startDate
                                        duration
                                        field { ... on ProjectV2FieldCommon { name } }
                                    }
                }
              }
              content {
                __typename
                ... on Issue {
                  title
                  createdAt
                  labels(first: 50) { nodes { name } }
                  repository { name }
                }
                ... on PullRequest {
                  title
                  createdAt
                  labels(first: 50) { nodes { name } }
                  repository { name }
                }
                ... on DraftIssue {
                  title
                }
              }
            }
          }
        }
      }
    }
    """

    items: list[ProjectItem] = []
    cursor = None
    while True:
        payload = _run_graphql(query, {"projectId": project_id, "cursor": cursor}, token)
        node = payload.get("data", {}).get("node", {}).get("items", {})
        nodes = node.get("nodes", [])
        page_info = node.get("pageInfo", {})

        for item in nodes:
            created_at = _parse_datetime(item.get("createdAt")) or datetime.utcnow()
            status = ""
            status_updated_at = None
            iteration_title = None
            iteration_start = None
            iteration_end = None
            milestone_value = None
            difficulty_value = 0.0
            estimate_value = 0.0
            labels: list[str] = []

            content = item.get("content") or {}
            content_type = content.get("__typename", "Issue")
            repository_name = (content.get("repository") or {}).get("name")
            if content_type == "PullRequest":
                continue
            label_nodes = (content.get("labels") or {}).get("nodes") or []
            labels = [str(label.get("name", "")).strip() for label in label_nodes if label]

            for field in (item.get("fieldValues") or {}).get("nodes", []):
                field_name = (field.get("field") or {}).get("name")
                field_type = field.get("__typename") or ""

                if _field_match(field_name, status_field):
                    status = field.get("name") or status
                    status_updated_at = _parse_datetime(field.get("updatedAt"))

                if _field_match(field_name, iteration_field) and field.get("iterationId"):
                    iteration_title = field.get("title")
                    iteration_start = _parse_date(field.get("startDate"))
                    duration = field.get("duration") or 0
                    if iteration_start:
                        iteration_end = iteration_start + timedelta(days=int(duration))

                if _field_match(field_name, milestone_field):
                    if field_type == "ProjectV2ItemFieldSingleSelectValue":
                        milestone_value = field.get("name") or milestone_value
                    elif field_type == "ProjectV2ItemFieldTextValue":
                        milestone_value = field.get("text") or milestone_value
                    elif field_type == "ProjectV2ItemFieldMilestoneValue":
                        milestone = field.get("milestone") or {}
                        milestone_value = milestone.get("title") or milestone_value

                if _field_match(field_name, difficulty_field):
                    if field_type == "ProjectV2ItemFieldNumberValue":
                        difficulty_value = _safe_float(field.get("number"))
                    elif field_type == "ProjectV2ItemFieldSingleSelectValue":
                        difficulty_value = _map_difficulty_label(field.get("name"))
                    elif field_type == "ProjectV2ItemFieldTextValue":
                        difficulty_value = _map_difficulty_label(field.get("text"))

                if _field_match(field_name, estimate_field):
                    if field_type == "ProjectV2ItemFieldNumberValue":
                        estimate_value = _safe_float(field.get("number"))
                    elif field_type == "ProjectV2ItemFieldTextValue":
                        estimate_value = _parse_numeric_from_text(field.get("text"))
                    elif field_type == "ProjectV2ItemFieldSingleSelectValue":
                        estimate_value = _parse_numeric_from_text(field.get("name"))

            items.append(
                ProjectItem(
                    created_at=created_at,
                    status=status,
                    status_updated_at=status_updated_at,
                    iteration_title=iteration_title,
                    iteration_start=iteration_start,
                    iteration_end=iteration_end,
                    milestone=milestone_value,
                    difficulty=difficulty_value,
                    estimate_hours=estimate_value,
                    labels=labels,
                    content_type=content_type,
                    repository=repository_name,
                )
            )

        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return items


def _percent(value: float, total: float) -> int:
    if total <= 0:
        return 0
    return int(round((value / total) * 100))


def _line_chart_svg(
    dates: list[date],
    series: dict[str, list[float]],
    colors: dict[str, str],
    title: str,
    y_label: str = "",
    x_label: str = "",
) -> str:
    if not dates:
        return ""

    padding = CHART_PADDING

    legend_keys = list(colors.keys())
    legend_start_y = 20
    legend_height = len(legend_keys) * 14
    legend_bottom = legend_start_y + legend_height

    top_offset = max(padding, legend_bottom + 10)

    extra_height = max(0, top_offset - padding)
    height = CHART_HEIGHT + extra_height
    width = CHART_WIDTH

    max_value = max(max(values) for values in series.values()) if series else 1
    max_value = max(max_value, 1)

    num_points = len(dates)
    if num_points < 2:
        x_step = width - padding * 2
    else:
        x_step = (width - padding * 2) / (num_points - 1)

    def _x(idx: int) -> float:
        return padding + idx * x_step

    def _y(value: float) -> float:
        plot_height = height - padding - top_offset
        return height - padding - (value / max_value) * plot_height

    lines = []
    for key, values in series.items():
        points = [f"{_x(i):.2f},{_y(v):.2f}" for i, v in enumerate(values)]
        lines.append(
            f"<polyline fill='none' stroke='{colors.get(key, '#8B949E')}' stroke-width='3' stroke-linecap='round' stroke-linejoin='round' "
            f"points='{' '.join(points)}'/>"
        )

    axis = (
        f"<line x1='{padding}' y1='{height - padding}' x2='{width - padding}' y2='{height - padding}' stroke='{GRID_COLOR}' />"
        f"<line x1='{padding}' y1='{top_offset}' x2='{padding}' y2='{height - padding}' stroke='{GRID_COLOR}' />"
        f"<text x='{padding}' y='{top_offset - 8}' font-size='10' fill='{TEXT_COLOR}'>{max_value:.0f}</text>"
        f"<text x='{padding}' y='{height - padding + 16}' font-size='10' fill='{TEXT_COLOR}'>0</text>"
    )

    legend_svg = ""
    legend_x = width - 120
    curr_y = legend_start_y
    for key, color in colors.items():
        legend_svg += (
            f"<rect x='{legend_x}' y='{curr_y}' width='10' height='10' fill='{color}' rx='2'/>"
            f"<text x='{legend_x + 14}' y='{curr_y + 9}' font-size='10' fill='{TEXT_COLOR}'>{key}</text>"
        )
        curr_y += 14

    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<rect width='{width}' height='{height}' fill='{BG_COLOR}'/>"
        f"<text x='{padding}' y='20' font-size='13' fill='{TEXT_COLOR}' font-weight='700' font-family='sans-serif'>{title}</text>"
        f"{axis}{''.join(lines)}{legend_svg}"
        f"</svg>"
    )


def _stacked_bar_svg(
    labels: list[str],
    stacks: dict[str, list[float]],
    colors: dict[str, str],
    title: str,
    y_label: str = "",
    x_label: str = "",
) -> str:
    width = CHART_WIDTH
    padding = CHART_PADDING
    left_padding = 320

    bar_height = 20
    gap = 12

    used_keys = [k for k in colors.keys() if k in stacks and any(v > 0 for v in stacks[k])]

    legend_start_y = 16
    legend_height = len(used_keys) * 14
    legend_bottom = legend_start_y + legend_height

    top_offset = max(padding, legend_bottom + 10)

    height = top_offset + len(labels) * (bar_height + gap) + 30

    max_total = 1
    totals = []
    for i in range(len(labels)):
        total = sum(stacks[key][i] for key in stacks)
        totals.append(total)
        max_total = max(max_total, total)

    bars = []
    chart_draw_width = width - left_padding - padding

    for i, label in enumerate(labels):
        y = top_offset + i * (bar_height + gap) + 10
        y = top_offset + i * (bar_height + gap)

        x = left_padding

        bars.append(
            f"<text x='{left_padding - 10}' y='{y + 14}' text-anchor='end' font-size='11' fill='{TEXT_COLOR}'>{label}</text>"
        )

        for key, values in stacks.items():
            value = values[i]
            if value <= 0:
                continue

            if max_total > 0:
                width_value = (value / max_total) * chart_draw_width
            else:
                width_value = 0

            bars.append(
                f"<rect x='{x:.2f}' y='{y}' width='{width_value:.2f}' height='{bar_height}' fill='{colors.get(key, '#8B949E')}' rx='4' />"
            )
            if width_value > 24 and value > 0:
                text_x = x + width_value / 2
                bars.append(
                    f"<text x='{text_x:.2f}' y='{y + 14}' text-anchor='middle' font-size='10' fill='white' font-weight='600'>{int(value)}</text>"
                )
            x += width_value

        if totals[i] > 0:
            bars.append(
                f"<text x='{x + 6:.2f}' y='{y + 14}' text-anchor='start' font-size='10' fill='{TEXT_COLOR}'>{int(totals[i])}</text>"
            )

    legend_svg = ""
    legend_x = width - 120
    legend_y = legend_start_y
    for key in used_keys:
        color = colors.get(key, "#8B949E")
        legend_svg += (
            f"<rect x='{legend_x}' y='{legend_y}' width='10' height='10' fill='{color}' rx='2'/>"
            f"<text x='{legend_x + 14}' y='{legend_y + 9}' font-size='9' fill='{TEXT_COLOR}'>{key.capitalize()}</text>"
        )
        legend_y += 14

    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<rect width='{width}' height='{height}' fill='{BG_COLOR}'/>"
        f"<text x='{padding}' y='20' font-size='13' fill='{TEXT_COLOR}' font-weight='700' font-family='sans-serif'>{title}</text>"
        f"{''.join(bars)}{legend_svg}"
        f"</svg>"
    )


def _bar_chart_svg(
    categories: list[str],
    values: list[float],
    title: str,
    color: str,
    y_label: str = "",
    x_label: str = "",
) -> str:
    width = CHART_WIDTH
    height = CHART_HEIGHT
    padding = CHART_PADDING
    max_value = max(max(values), 1) if values else 1
    bar_width = (width - padding * 2) / max(1, len(categories))

    bars = []
    for i, value in enumerate(values):
        x = padding + i * bar_width + 6
        bar_height = (value / max_value) * (height - padding * 2)
        y = height - padding - bar_height
        bars.append(
            f"<rect x='{x:.2f}' y='{y:.2f}' width='{bar_width - 12:.2f}' height='{bar_height:.2f}' fill='{color}' rx='4' />"
        )
        if value > 0:
            label_y = y - 5 if bar_height > 15 else y + 12
            text_color = "white" if bar_height > 15 else TEXT_COLOR
            bars.append(
                f"<text x='{x + (bar_width - 12) / 2:.2f}' y='{label_y:.2f}' text-anchor='middle' font-size='10' fill='{text_color}' font-weight='600'>{int(value)}</text>"
            )
        cat_label = categories[i]
        if len(categories) > 8 and len(cat_label) > 10:
            cat_label = cat_label[:8] + ".."

        bars.append(
            f"<text x='{x + (bar_width - 12) / 2:.2f}' y='{height - padding + 14}' text-anchor='middle' font-size='10' fill='{TEXT_COLOR}'>{cat_label}</text>"
        )

    axis = (
        f"<line x1='{padding}' y1='{height - padding}' x2='{width - padding}' y2='{height - padding}' stroke='{GRID_COLOR}' />"
        f"<line x1='{padding}' y1='{padding}' x2='{padding}' y2='{height - padding}' stroke='{GRID_COLOR}' />"
        f"<text x='{padding}' y='{padding - 8}' font-size='10' fill='{TEXT_COLOR}'>{max_value:.0f}</text>"
    )

    params_svg = ""
    if y_label:
        params_svg += f"<text x='15' y='{height / 2}' transform='rotate(-90 15,{height / 2})' text-anchor='middle' font-size='11' fill='{TEXT_COLOR}' font-weight='bold'>{y_label}</text>"
    if x_label:
        params_svg += f"<text x='{width / 2}' y='{height - 10}' text-anchor='middle' font-size='11' fill='{TEXT_COLOR}' font-weight='bold'>{x_label}</text>"

    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<rect width='{width}' height='{height}' fill='{BG_COLOR}'/>"
        f"<text x='{padding}' y='20' font-size='13' fill='{TEXT_COLOR}' font-weight='700' font-family='sans-serif'>{title}</text>"
        f"{axis}{' '.join(bars)}{params_svg}"
        f"</svg>"
    )


def _multi_color_bar_chart_svg(
    categories: list[str],
    values: list[float],
    colors_list: list[str],
    title: str,
    y_label: str = "",
    x_label: str = "",
) -> str:
    padding = CHART_PADDING

    legend_start_y = 16
    shown_categories = categories[:6]
    legend_height = len(shown_categories) * 14
    legend_bottom = legend_start_y + legend_height

    top_offset = max(padding, legend_bottom + 10)
    extra_height = max(0, top_offset - padding)

    width = CHART_WIDTH
    height = CHART_HEIGHT + extra_height

    max_value = max(max(values), 1) if values else 1
    bar_width = (width - padding * 2) / max(1, len(categories))

    bars = []
    for i, value in enumerate(values):
        x = padding + i * bar_width + 6
        plot_height = height - padding - top_offset

        bar_height = (value / max_value) * plot_height
        y = height - padding - bar_height
        color = colors_list[i] if i < len(colors_list) else "#8B949E"

        bars.append(
            f"<rect x='{x:.2f}' y='{y:.2f}' width='{bar_width - 12:.2f}' height='{bar_height:.2f}' fill='{color}' rx='4' />"
        )
        if value > 0:
            label_y = y - 5 if bar_height > 15 else y + 12
            text_color = "white" if bar_height > 15 else TEXT_COLOR
            bars.append(
                f"<text x='{x + (bar_width - 12) / 2:.2f}' y='{label_y:.2f}' text-anchor='middle' font-size='10' fill='{text_color}' font-weight='600'>{int(value)}</text>"
            )
        bars.append(
            f"<text x='{x + (bar_width - 12) / 2:.2f}' y='{height - padding + 14}' text-anchor='middle' font-size='10' fill='{TEXT_COLOR}'>{categories[i]}</text>"
        )

    axis = (
        f"<line x1='{padding}' y1='{height - padding}' x2='{width - padding}' y2='{height - padding}' stroke='{GRID_COLOR}' />"
        f"<line x1='{padding}' y1='{top_offset}' x2='{padding}' y2='{height - padding}' stroke='{GRID_COLOR}' />"
        f"<text x='{padding}' y='{top_offset - 8}' font-size='10' fill='{TEXT_COLOR}'>{max_value:.0f}</text>"
    )

    params_svg = ""
    if y_label:
        params_svg += f"<text x='15' y='{height / 2}' transform='rotate(-90 15,{height / 2})' text-anchor='middle' font-size='11' fill='{TEXT_COLOR}' font-weight='bold'>{y_label}</text>"
    if x_label:
        params_svg += f"<text x='{width / 2}' y='{height - 10}' text-anchor='middle' font-size='11' fill='{TEXT_COLOR}' font-weight='bold'>{x_label}</text>"

    legend_svg = ""
    legend_x = width - 120
    legend_y = legend_start_y
    for i, category in enumerate(categories):
        if i >= 6:
            break
        color = colors_list[i] if i < len(colors_list) else "#8B949E"
        legend_svg += (
            f"<rect x='{legend_x}' y='{legend_y}' width='10' height='10' fill='{color}' rx='2'/>"
            f"<text x='{legend_x + 14}' y='{legend_y + 9}' font-size='9' fill='{TEXT_COLOR}'>{category}</text>"
        )
        legend_y += 14

    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>"
        f"<rect width='{width}' height='{height}' fill='{BG_COLOR}'/>"
        f"<text x='{padding}' y='20' font-size='13' fill='{TEXT_COLOR}' font-weight='700' font-family='sans-serif'>{title}</text>"
        f"{axis}{' '.join(bars)}{params_svg}{legend_svg}"
        f"</svg>"
    )


def load_project_charts(
    token: str | None,
    project_id: str | None,
    *,
    week_id: str | None = None,
    milestone_month: str | None = None,
    reference_date: date | None = None,
) -> dict[str, Any] | None:
    status_field = "Status"
    iteration_field = "Iteration"
    milestone_field = "Milestone"
    difficulty_field = "Dificuldade"
    estimate_field = "Estimate (Hours)"
    if not token or not project_id:
        return None

    try:
        items = fetch_project_items(
            token,
            project_id,
            status_field=status_field,
            iteration_field=iteration_field,
            milestone_field=milestone_field,
            difficulty_field=difficulty_field,
            estimate_field=estimate_field,
        )
    except requests.RequestException:
        logger.exception("github.project.fetch_failed")
        return None

    all_items = items

    if not milestone_month:
        milestone_month = _get_latest_milestone(items)
        logger.info(f"Auto-detected latest milestone: {milestone_month}")

    if milestone_month:
        items = [
            item
            for item in items
            if item.milestone and _milestone_matches(item.milestone, milestone_month)
        ]

    if reference_date:
        items = [item for item in items if item.created_at.date() <= reference_date]

    if not items:
        return None

    milestone_end_date = None
    if items:
        milestone_end_date = max(
            (item.iteration_end or item.created_at.date()) for item in items
        )
    burnup_end_date = reference_date or milestone_end_date
    burnup_start_date = None
    if burnup_end_date:
        burnup_start_date = _subtract_months(burnup_end_date, 5)

    date_counts = {}
    completed_counts = {}
    for item in items:
        status_key = _bucket_status(item.status)
        if status_key == "cancelled":
            continue
        created_day = item.created_at.date()
        if burnup_start_date and created_day < burnup_start_date:
            continue
        if burnup_end_date and created_day > burnup_end_date:
            continue
        date_counts[created_day] = date_counts.get(created_day, 0.0) + (item.difficulty or 0.0)
        if _is_duplicate_item(item) and item.status_updated_at:
            done_day = item.status_updated_at.date()
            if burnup_start_date and done_day < burnup_start_date:
                continue
            if burnup_end_date and done_day > burnup_end_date:
                continue
            completed_counts[done_day] = completed_counts.get(done_day, 0.0) + 0.0
        if status_key == "done" and item.status_updated_at:
            done_day = item.status_updated_at.date()
            if burnup_start_date and done_day < burnup_start_date:
                continue
            if burnup_end_date and done_day > burnup_end_date:
                continue
            completed_counts[done_day] = completed_counts.get(done_day, 0.0) + (
                item.difficulty or 0.0
            )

    duplicate_counts = {}
    for item in items:
        if not _is_duplicate_item(item):
            continue
        day = (item.status_updated_at or item.created_at).date()
        if reference_date and day > reference_date:
            continue
        if burnup_start_date and day < burnup_start_date:
            continue
        if burnup_end_date and day > burnup_end_date:
            continue
        duplicate_counts[day] = duplicate_counts.get(day, 0.0) + (item.difficulty or 0.0)

    all_dates = sorted(
        set(date_counts.keys()) | set(completed_counts.keys()) | set(duplicate_counts.keys())
    )
    if burnup_start_date:
        all_dates = [d for d in all_dates if d >= burnup_start_date]
    if burnup_end_date:
        all_dates = [d for d in all_dates if d <= burnup_end_date]
    cumulative_scope = []
    cumulative_done = []
    cumulative_duplicate = []
    scope_total = 0.0
    done_total = 0.0
    duplicate_total = 0.0
    for day in all_dates:
        scope_total += date_counts.get(day, 0.0)
        done_total += completed_counts.get(day, 0.0)
        duplicate_total += duplicate_counts.get(day, 0.0)
        cumulative_scope.append(scope_total)
        cumulative_done.append(done_total)
        cumulative_duplicate.append(duplicate_total)

    burnup_svg = _line_chart_svg(
        all_dates,
        {
            "Open": cumulative_scope,
            "Completed": cumulative_done,
            "Duplicate": cumulative_duplicate,
        },
        {"Open": "#4ade80", "Completed": "#a855f7", "Duplicate": "#64748b"},
        "BurnUp Milestone",
        y_label="Pontos",
        x_label="Tempo",
    )

    today = reference_date or date.today()
    iterations_source = all_items
    iterations = [item for item in iterations_source if item.iteration_start]
    current_iteration = None
    if iterations:
        upcoming = [
            item for item in iterations if item.iteration_end and item.iteration_end >= today
        ]
        if upcoming:
            current_iteration = min(upcoming, key=lambda x: x.iteration_end)
        else:
            current_iteration = max(iterations, key=lambda x: x.iteration_start)

    iteration_title = current_iteration.iteration_title if current_iteration else None

    filtered_items = items

    status_totals = {key: 0.0 for key in GITHUB_COLORS.keys()}
    for item in filtered_items:
        status_key = _bucket_status(item.status)
        if status_key == "cancelled" or status_key == "duplicate":
            continue
        effort = item.difficulty or 0.0
        status_totals[status_key] += effort

    progress_categories = ["Backlog", "Progress", "Review", "Done"]
    progress_values = [
        status_totals["backlog"],
        status_totals["progress"],
        status_totals["review"],
        status_totals["done"],
    ]

    progress_colors_list = [
        GITHUB_COLORS["backlog"],
        GITHUB_COLORS["progress"],
        GITHUB_COLORS["review"],
        GITHUB_COLORS["done"],
    ]

    progress_svg = _multi_color_bar_chart_svg(
        progress_categories,
        progress_values,
        progress_colors_list,
        "Progresso Atual vs Previsto",
        y_label="Pontos",
        x_label="Status",
    )

    milestone_map: dict[str, dict[str, dict[str, float]]] = {}
    for item in all_items:
        raw_ms = item.milestone or "No Milestone"
        milestone_label = " ".join(raw_ms.split()).strip()

        milestone_map.setdefault(milestone_label, {"hours": {}, "difficulty": {}, "count": {}})
        status_key = _bucket_status(item.status)
        if status_key in ["cancelled", "duplicate"]:
            continue
        milestone_map[milestone_label]["hours"][status_key] = (
            milestone_map[milestone_label]["hours"].get(status_key, 0.0) + item.estimate_hours
        )
        milestone_map[milestone_label]["difficulty"][status_key] = (
            milestone_map[milestone_label]["difficulty"].get(status_key, 0.0) + item.difficulty
        )
        milestone_map[milestone_label]["count"][status_key] = (
            milestone_map[milestone_label]["count"].get(status_key, 0.0) + 1
        )

    milestone_labels = list(milestone_map.keys())

    def _milestone_stack(kind: str) -> dict[str, list[float]]:
        return {
            key: [milestone_map[label][kind].get(key, 0.0) for label in milestone_labels]
            for key in GITHUB_COLORS.keys()
        }

    milestone_hours_svg = _stacked_bar_svg(
        milestone_labels,
        _milestone_stack("hours"),
        GITHUB_COLORS,
        "Milestones - Hours",
        y_label="Milestone",
        x_label="Horas",
    )
    milestone_difficulty_svg = _stacked_bar_svg(
        milestone_labels,
        _milestone_stack("difficulty"),
        GITHUB_COLORS,
        "Milestones - Dificuldade",
        y_label="Milestone",
        x_label="Pontos",
    )
    milestone_count_svg = _stacked_bar_svg(
        milestone_labels,
        _milestone_stack("count"),
        GITHUB_COLORS,
        "Milestones - Count",
        y_label="Milestone",
        x_label="Tasks",
    )

    filtered_items = [
        item for item in items if _bucket_status(item.status) not in ["cancelled", "duplicate"]
    ]
    total_count = len(filtered_items)
    total_review = sum(1 for item in filtered_items if _bucket_status(item.status) == "review")
    total_done = sum(1 for item in filtered_items if _bucket_status(item.status) == "done")
    difficulty_total = sum(item.difficulty for item in filtered_items)
    difficulty_review = sum(
        item.difficulty for item in filtered_items if _bucket_status(item.status) == "review"
    )
    difficulty_done = sum(
        item.difficulty for item in filtered_items if _bucket_status(item.status) == "done"
    )

    count_done = total_done

    total_table = {
        "count_total": total_count,
        "count_review": total_review,
        "count_done": count_done,
        "difficulty_total": difficulty_total,
        "difficulty_review": difficulty_review,
        "difficulty_done": difficulty_done,
        "done_count_percent": _percent(total_done, total_count),
        "done_difficulty_percent": _percent(difficulty_done, difficulty_total),
        "done_review_count_percent": _percent(total_done + total_review, total_count),
        "done_review_difficulty_percent": _percent(
            difficulty_done + difficulty_review, difficulty_total
        ),
    }

    week_stats = {"backlog": 0, "blocked": 0, "progress": 0, "review": 0, "done": 0}
    weekly_items = []
    week = _week_range(week_id)
    if week:
        start, end = week
        for item in items:
            updated_at = item.status_updated_at or item.created_at
            updated = updated_at.date() if updated_at else None
            if updated and updated <= end:
                status_key = _bucket_status(item.status)
                if status_key in ["cancelled", "duplicate"]:
                    continue
                weekly_items.append(item)
                if status_key not in week_stats:
                    status_key = "backlog"
                week_stats[status_key] += 1

    week_total = len(weekly_items)
    week_done = week_stats["done"]
    week_review = week_stats["review"]

    weekly_table = {
        "backlog": week_stats["backlog"],
        "blocked": week_stats["blocked"],
        "progress": week_stats["progress"],
        "review": week_stats["review"],
        "done": week_stats["done"],
        "done_percent": _percent(week_done, week_total),
        "done_review_percent": _percent(week_done + week_review, week_total),
    }

    weekly_progress_categories = ["Backlog", "Blocked", "In Progress", "In Review", "Done"]
    weekly_progress_values = [
        float(week_stats["backlog"]),
        float(week_stats["blocked"]),
        float(week_stats["progress"]),
        float(week_stats["review"]),
        float(week_stats["done"]),
    ]
    weekly_progress_colors = [
        GITHUB_COLORS["backlog"],
        GITHUB_COLORS["blocked"],
        GITHUB_COLORS["progress"],
        GITHUB_COLORS["review"],
        GITHUB_COLORS["done"],
    ]
    weekly_progress_svg = _multi_color_bar_chart_svg(
        weekly_progress_categories,
        weekly_progress_values,
        weekly_progress_colors,
        "Progresso da Semana",
        y_label="Tarefas",
        x_label="Status",
    )

    label_counts: dict[str, dict[str, float]] = {}
    for item in filtered_items:
        for label in item.labels:
            if label not in label_counts:
                label_counts[label] = {"backlog": 0.0, "progress": 0.0, "review": 0.0, "done": 0.0}
            status_key = _bucket_status(item.status)
            if status_key in ["cancelled", "duplicate"]:
                continue
            if status_key not in label_counts[label]:
                status_key = "backlog"
            label_counts[label][status_key] += 1

    sorted_labels = sorted(
        label_counts.items(), key=lambda x: sum(x[1].values()), reverse=True
    )
    top_labels = [label for label, _ in sorted_labels[:15]]

    milestone_labels_stacks = {
        "Backlog": [label_counts[label].get("backlog", 0.0) for label in top_labels],
        "In progress": [label_counts[label].get("progress", 0.0) for label in top_labels],
        "In review": [label_counts[label].get("review", 0.0) for label in top_labels],
        "Done": [label_counts[label].get("done", 0.0) for label in top_labels],
    }
    milestone_labels_colors = {
        "Backlog": GITHUB_COLORS["backlog"],
        "In progress": GITHUB_COLORS["progress"],
        "In review": GITHUB_COLORS["review"],
        "Done": GITHUB_COLORS["done"],
    }
    milestone_labels_svg = _stacked_bar_svg(
        top_labels,
        milestone_labels_stacks,
        milestone_labels_colors,
        "Labels da Milestone",
        y_label="Label",
        x_label="Tarefas",
    )

    return {
        "burnup_svg": burnup_svg,
        "weekly_progress_svg": weekly_progress_svg,
        "milestone_labels_svg": milestone_labels_svg,
        "tables": {
            "weekly": weekly_table,
            "total": total_table,
        },
        "current_iteration": iteration_title,
    }
