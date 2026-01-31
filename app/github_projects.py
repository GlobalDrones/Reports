from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
from typing import Any, Dict, List, Optional, Tuple

import requests
import json

GITHUB_COLORS = {
    "backlog": "#8B949E",
    "blocked": "#f97316",
    "progress": "#BF8700",
    "review": "#2188FF",
    "done": "#986EE2",
    "no_status": "#30363d",
    "duplicate": "#64748b",
    "open_scope": "#238636",
}

CHART_WIDTH = 800
CHART_HEIGHT = 280
CHART_PADDING = 50
BG_COLOR = "#e6edf3"
TEXT_COLOR = "#0d1117"
GRID_COLOR = "#30363d"

logger = logging.getLogger(__name__)


@dataclass
class ProjectItem:
    id: str
    created_at: datetime
    status: str
    status_updated_at: datetime | None
    iteration_title: str | None
    iteration_start: date | None
    iteration_end: date | None
    milestone: str | None
    difficulty: float
    estimate_hours: float
    labels: List[str]
    content_type: str = "Issue"
    repository: str | None = None
    is_archived: bool = False
    content_state_reason: str | None = None
    content_state: str | None = None
    milestone_due: date | None = None


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


def _bucket_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if not normalized:
        return "no_status"

    if any(k in normalized for k in ["cancel", "suspend", "abort", "abandon"]):
        return "cancelled"

    if "duplic" in normalized:
        return "duplicate"

    if normalized in ["done", "concluído", "concluido", "finalizado", "closed", "entregue"]:
        return "done"
    if (
        normalized.startswith("done")
        or normalized.startswith("concl")
        or normalized.startswith("closed")
    ):
        return "done"

    if (
        "revis" in normalized
        or "review" in normalized
        or "qa" in normalized
        or "valid" in normalized
    ):
        return "review"

    if (
        "progres" in normalized
        or "andamento" in normalized
        or "doing" in normalized
        or "wip" in normalized
    ):
        return "progress"

    if "block" in normalized or "bloq" in normalized or "imped" in normalized:
        return "blocked"

    return "backlog"


def _is_duplicate_item(item: ProjectItem) -> bool:
    if _bucket_status(item.status) == "duplicate":
        return True
    labels = [l.lower() for l in item.labels]
    if "duplicate" in labels or "duplicado" in labels:
        return True
    if item.content_state_reason and str(item.content_state_reason).upper() == "DUPLICATE":
        return True
    return False


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _map_difficulty_label(label: str | None) -> float:
    if not label:
        return 0.0
    normalized = label.strip().upper()
    match = re.search(r"(\d+([\.,]\d+)?)", normalized)
    if match:
        return _safe_float(match.group(1).replace(",", "."))

    scale_map = {
        "XS": 1.0,
        "S": 2.0,
        "M": 3.0,
        "L": 5.0,
        "XL": 8.0,
        "P0": 8.0,
        "P1": 5.0,
        "P2": 3.0,
        "P3": 2.0,
        "P4": 1.0,
    }
    for key, val in scale_map.items():
        if normalized.startswith(key):
            return val
    return 0.0


def fetch_project_items(token: str, project_id: str) -> List[ProjectItem]:
    query = """
    query($projectId: ID!, $cursor: String) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              createdAt
              isArchived
              fieldValues(first: 20) {
                nodes {
                  ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
                  ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
                  ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
                  ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
                  ... on ProjectV2ItemFieldMilestoneValue { milestone { title dueOn } field { ... on ProjectV2FieldCommon { name } } }
                  ... on ProjectV2ItemFieldIterationValue { 
                    title startDate duration 
                    field { ... on ProjectV2FieldCommon { name } } 
                  }
                }
              }
                            content {
                                __typename
                                ... on Issue { title state stateReason labels(first: 10) { nodes { name } } repository { name } closedAt updatedAt }
                                ... on PullRequest { title labels(first: 10) { nodes { name } } repository { name } closedAt mergedAt updatedAt }
                                ... on DraftIssue { title }
                            }
            }
          }
        }
      }
    }
    """

    items = []
    cursor = None

    FIELD_STATUS = "Status"
    FIELD_DIFFICULTY = "Dificuldade"
    FIELD_ITERATION = "Iteration"
    FIELD_MILESTONE = "Milestone"
    FIELD_ESTIMATE = "Estimate"

    while True:
        try:
            resp = requests.post(
                "https://api.github.com/graphql",
                json={"query": query, "variables": {"projectId": project_id, "cursor": cursor}},
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch GitHub items: {e}")
            break

        nodes = data.get("data", {}).get("node", {}).get("items", {}).get("nodes", [])
        page_info = data.get("data", {}).get("node", {}).get("items", {}).get("pageInfo", {})

        for node in nodes:
            content = node.get("content") or {}
            field_values = node.get("fieldValues", {}).get("nodes", [])

            status = ""
            status_updated_at = None
            iteration_title = None
            iteration_start = None
            iteration_end = None
            milestone = None
            milestone_due = None
            difficulty = 0.0
            estimate = 0.0

            for fv in field_values:
                f_name = fv.get("field", {}).get("name", "")

                if _normalize_text(f_name) == _normalize_text(FIELD_STATUS):
                    status = fv.get("name") or ""

                elif _normalize_text(f_name) == _normalize_text(FIELD_DIFFICULTY):
                    val = fv.get("number")
                    if val is None:
                        val = fv.get("name")
                    if val is None:
                        val = fv.get("text")
                    difficulty = _map_difficulty_label(str(val)) if val else 0.0

                elif _normalize_text(f_name) == _normalize_text(FIELD_MILESTONE):
                    if fv.get("milestone"):
                        milestone = fv.get("milestone", {}).get("title")
                        due_raw = fv.get("milestone", {}).get("dueOn")
                        milestone_due = _parse_date(due_raw) if due_raw else None
                    else:
                        milestone = fv.get("title")
                        milestone_due = None

                elif _normalize_text(f_name) == _normalize_text(FIELD_ITERATION):
                    iteration_title = fv.get("title")
                    if fv.get("startDate"):
                        iteration_start = _parse_date(fv.get("startDate"))
                        duration = fv.get("duration", 0)
                        iteration_end = iteration_start + timedelta(days=duration)

            labels = []
            lbl_nodes = (content.get("labels") or {}).get("nodes") or []
            labels = [l.get("name") for l in lbl_nodes if l.get("name")]

            created_at = _parse_datetime(node.get("createdAt")) or datetime.now()

            content_closed_at = None
            content_updated_at = None
            if content.get("__typename") == "Issue":
                content_closed_at = _parse_datetime(content.get("closedAt"))
                content_updated_at = _parse_datetime(content.get("updatedAt"))
            elif content.get("__typename") == "PullRequest":
                content_closed_at = _parse_datetime(
                    content.get("closedAt") or content.get("mergedAt")
                )
                content_updated_at = _parse_datetime(content.get("updatedAt"))

            item = ProjectItem(
                id=node.get("id"),
                created_at=created_at,
                status=status,
                status_updated_at=(content_closed_at or content_updated_at),
                iteration_title=iteration_title,
                iteration_start=iteration_start,
                iteration_end=iteration_end,
                milestone=milestone,
                milestone_due=milestone_due,
                difficulty=difficulty,
                estimate_hours=estimate,
                labels=labels,
                content_type=content.get("__typename", "Issue"),
                repository=(content.get("repository") or {}).get("name"),
                is_archived=node.get("isArchived", False),
                content_state_reason=content.get("stateReason"),
                content_state=content.get("state"),
            )
            items.append(item)

        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return items


def _svg_header(title: str) -> str:
    return f"""<text x="{CHART_PADDING}" y="25" font-size="16" fill="{TEXT_COLOR}" font-weight="600" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif">{title}</text>"""


def _burnup_chart_svg(
    dates: List[date],
    scope_series: List[float],
    completed_series: List[float],
    duplicate_series: List[float] | None,
    title: str,
    final_open: int | None = None,
    final_done: int | None = None,
    final_dup: int | None = None,
) -> str:
    if not dates:
        return ""

    width, height = CHART_WIDTH, CHART_HEIGHT
    pad = CHART_PADDING

    raw_max = max(max(scope_series), 1)
    max_y = math.ceil(raw_max * 1.05 / 50) * 50

    def get_y(val):
        return height - pad - (val / max_y * (height - 2 * pad))

    def get_x(idx):
        return pad + (idx / (len(dates) - 1) * (width - 2 * pad))

    scope_points = []
    completed_points = []
    duplicate_points = []

    for i, d in enumerate(dates):
        x = get_x(i)
        y_scope = get_y(scope_series[i])
        y_done = get_y(completed_series[i])
        y_dup = get_y(duplicate_series[i]) if duplicate_series else get_y(0)
        scope_points.append(f"{x:.1f},{y_scope:.1f}")
        completed_points.append(f"{x:.1f},{y_done:.1f}")
        duplicate_points.append(f"{x:.1f},{y_dup:.1f}")

    done_area_path = (
        f"M {pad},{height - pad} "
        + " ".join([f"L {p}" for p in completed_points])
        + f" L {get_x(len(dates) - 1)},{height - pad} Z"
    )

    scope_area_path = (
        f"M {pad},{height - pad} "
        + " ".join([f"L {p}" for p in scope_points])
        + f" L {get_x(len(dates) - 1)},{height - pad} Z"
    )

    dup_line = f"M " + " ".join([f"{p}" for p in duplicate_points])

    scope_line = f"M " + " ".join([f"{p}" for p in scope_points])
    done_line = f"M " + " ".join([f"{p}" for p in completed_points])
    dup_line = dup_line

    x_labels_svg = ""
    step = max(1, len(dates) // 8)
    for i in range(0, len(dates), step):
        x_labels_svg += f'<text x="{get_x(i):.1f}" y="{height - pad + 20}" font-size="10" fill="{TEXT_COLOR}" text-anchor="middle">{dates[i].strftime("%d/%b")}</text>'

    last_idx = len(dates) - 1
    last_x = get_x(last_idx)
    last_scope_val = scope_series[-1] if scope_series else 0
    last_done_val = completed_series[-1] if completed_series else 0
    last_dup_val = duplicate_series[-1] if (duplicate_series and len(duplicate_series) > 0) else 0
    last_scope_y = get_y(last_scope_val)
    last_done_y = get_y(last_done_val)
    display_scope = int(final_open) if final_open is not None else int(last_scope_val)
    display_done = int(final_done) if final_done is not None else int(last_done_val)
    last_values_svg = (
        f'<text x="{last_x + 8:.1f}" y="{last_scope_y - 6:.1f}" font-size="11" fill="{GITHUB_COLORS["open_scope"]}" font-weight="600">{display_scope}</text>'
        + f'<text x="{last_x + 8:.1f}" y="{last_done_y + 4:.1f}" font-size="11" fill="{GITHUB_COLORS["done"]}" font-weight="600">{display_done}</text>'
        + f'<text x="{last_x + 8:.1f}" y="{get_y(last_dup_val) + 14:.1f}" font-size="11" fill="#9ca3af" font-weight="600">{int(final_dup) if final_dup is not None else int(last_dup_val)}</text>'
    )

    legend_items = [("open_scope", "Open Scope"), ("done", "Completed"), ("duplicate", "Duplicate")]
    legend_svg = ""
    for idx, (key, label) in enumerate(legend_items):
        cx = width - 360 + idx * 90
        tx = width - 350 + idx * 90
        color = GITHUB_COLORS.get(key, "#9ca3af")
        legend_svg += f'<circle cx="{cx:.0f}" cy="30" r="4" fill="{color}"/>'
        legend_svg += f'<text x="{tx:.0f}" y="34" font-size="12" fill="{TEXT_COLOR}">{label}</text>'

    return f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
        {_svg_header(title)}
        
        <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="{GRID_COLOR}" stroke-width="1"/>
        <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="{GRID_COLOR}" stroke-width="1"/>
        
        <text x="{pad - 10}" y="{pad}" font-size="10" fill="{TEXT_COLOR}" text-anchor="end">{int(max_y)}</text>
        <text x="{pad - 10}" y="{height - pad}" font-size="10" fill="{TEXT_COLOR}" text-anchor="end">0</text>
        
        <path d="{scope_area_path}" fill="{GITHUB_COLORS["open_scope"]}" opacity="0.2"/>
        <path d="{done_area_path}" fill="{GITHUB_COLORS["done"]}" opacity="0.3"/>

        <path d="{scope_line}" fill="none" stroke="{GITHUB_COLORS["open_scope"]}" stroke-width="2"/>
        <path d="{done_line}" fill="none" stroke="{GITHUB_COLORS["done"]}" stroke-width="2"/>
        <path d="{dup_line}" fill="none" stroke="#9ca3af" stroke-width="2" stroke-dasharray="4 4" opacity="0.9"/>
        
        {x_labels_svg}
        
           <!-- Legend: compute positions with extra spacing to avoid overlap -->
           {legend_svg}
           {last_values_svg}
    </svg>
    """


def _horizontal_stacked_bar_svg(
    labels: List[str],
    data: Dict[str, List[float]],
    title: str,
) -> str:
    bar_height = 20
    gap = 10
    top_margin = 50
    left_margin = 250

    chart_h = top_margin + len(labels) * (bar_height + gap) + 30
    width = CHART_WIDTH

    max_total = 0
    for l in labels:
        row = data.get(l, {}) or {}
        try:
            row_sum = sum(float(v) for v in (row.values() if isinstance(row, dict) else row))
        except Exception:
            row_sum = 0
        max_total = max(max_total, row_sum)

    max_total = max(max_total, 1)
    scale_x = (width - left_margin - CHART_PADDING) / max_total

    bars_svg = ""
    status_order = [
        "done",
        "review",
        "progress",
        "backlog",
    ]
    plot_order = ["backlog", "progress", "review", "done"]

    for i, label in enumerate(labels):
        y = top_margin + i * (bar_height + gap)
        vals = data[label]

        bars_svg += f'<text x="{left_margin - 10}" y="{y + 14}" font-size="11" fill="{TEXT_COLOR}" text-anchor="end">{label[:40]}</text>'

        current_x = left_margin

        row_data = data.get(label, {}) or {}

        for st in plot_order:
            try:
                val = float(row_data.get(st, 0) or 0)
            except Exception:
                val = 0.0
            if val > 0:
                seg_width = val * scale_x
                color = GITHUB_COLORS.get(st, "#333")
                bars_svg += f'<rect x="{current_x}" y="{y}" width="{seg_width}" height="{bar_height}" fill="{color}" rx="2"/>'
                if seg_width > 15:
                    bars_svg += f'<text x="{current_x + seg_width / 2}" y="{y + 14}" font-size="9" fill="white" text-anchor="middle">{int(val)}</text>'
                current_x += seg_width

    legend_svg = ""
    lx = width - 250
    for idx, st in enumerate(plot_order):
        c = GITHUB_COLORS.get(st)
        legend_svg += f'<rect x="{lx + idx * 60}" y="20" width="10" height="10" fill="{c}" rx="2"/>'
        legend_svg += f'<text x="{lx + idx * 60 + 14}" y="29" font-size="10" fill="{TEXT_COLOR}">{st.capitalize()}</text>'

    return f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{chart_h}" viewBox="0 0 {width} {chart_h}">
        {_svg_header(title)}
        {legend_svg}
        {bars_svg}
    </svg>
    """


def _simple_bar_chart_svg(
    categories: List[str], values: List[float], colors: List[str], title: str, y_label: str
) -> str:
    width, height = CHART_WIDTH, CHART_HEIGHT
    pad = CHART_PADDING

    max_val = max(max(values), 1)
    bar_w = (width - 2 * pad) / len(categories) * 0.6
    gap = (width - 2 * pad) / len(categories) * 0.4

    bars = ""
    for i, (cat, val) in enumerate(zip(categories, values)):
        h = (val / max_val) * (height - 2 * pad)
        x = pad + i * (bar_w + gap) + gap / 2
        y = height - pad - h

        bars += f'<rect x="{x}" y="{y}" width="{bar_w}" height="{h}" fill="{colors[i]}" rx="4"/>'
        bars += f'<text x="{x + bar_w / 2}" y="{y - 5}" font-size="11" fill="{TEXT_COLOR}" text-anchor="middle" font-weight="bold">{int(val) if val.is_integer() else f"{val:.1f}"}</text>'
        bars += f'<text x="{x + bar_w / 2}" y="{height - pad + 15}" font-size="11" fill="{TEXT_COLOR}" text-anchor="middle">{cat}</text>'

    return f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
        {_svg_header(title)}
        <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="{GRID_COLOR}"/>
        <text x="15" y="{height / 2}" transform="rotate(-90 15,{height / 2})" font-size="12" fill="{TEXT_COLOR}" text-anchor="middle">{y_label}</text>
        {bars}
    </svg>
    """


def load_project_charts(
    token: str,
    project_id: str,
    milestone_month: str = "Março",
    reference_date: date | None = None,
    milestone_label: str | None = None,
) -> dict[str, Any]:
    if not reference_date:
        reference_date = date.today()

    raw_items = fetch_project_items(token, project_id)

    active_items: List[ProjectItem] = []
    for item in raw_items:
        if item.is_archived:
            continue
        if item.content_type == "DraftIssue":
            continue
        if item.content_type == "PullRequest":
            continue
        if _bucket_status(item.status) == "cancelled":
            continue
        if item.content_state_reason and str(item.content_state_reason).upper() == "NOT_PLANNED":
            continue
        if milestone_month and not _milestone_matches(item.milestone, milestone_month):
            continue
        active_items.append(item)

    if not active_items:
        return {}

    milestone_dues = [it.milestone_due for it in active_items if getattr(it, "milestone_due", None)]
    end_date = min(milestone_dues) if milestone_dues else reference_date
    created_dates = [it.created_at.date() for it in active_items if getattr(it, "created_at", None)]
    start_date = min(created_dates) if created_dates else (end_date - timedelta(days=30))
    if start_date > end_date:
        start_date = end_date - timedelta(days=30)

    events_pts: List[Tuple[date, str, float]] = []
    for item in active_items:
        try:
            created_day = item.created_at.date()
        except Exception:
            continue
        if created_day > end_date:
            continue
        events_pts.append((created_day, "scope", item.difficulty))

        is_dup_item = _is_duplicate_item(item)

        is_done_column = _bucket_status(item.status) == "done"
        if is_done_column and item.status_updated_at and not is_dup_item:
            done_day = item.status_updated_at.date()
            if done_day <= end_date:
                events_pts.append((done_day, "done", item.difficulty))

        if is_dup_item:
            dup_day = (item.status_updated_at or item.created_at).date()
            if dup_day <= end_date:
                events_pts.append((dup_day, "dup", item.difficulty))

    events_pts.sort(key=lambda x: x[0])

    burnup_dates: List[date] = []
    burnup_scope_pts: List[float] = []
    burnup_done_pts: List[float] = []
    burnup_dup_pts: List[float] = []

    scope_acc = done_acc = dup_acc = 0.0
    curr_date = start_date
    idx = 0
    while curr_date <= end_date:
        while idx < len(events_pts) and events_pts[idx][0] <= curr_date:
            _, tipo, val = events_pts[idx]
            if tipo == "scope":
                scope_acc += val
            elif tipo == "done":
                done_acc += val
            elif tipo == "dup":
                dup_acc += val
            idx += 1

        burnup_dates.append(curr_date)
        burnup_scope_pts.append(scope_acc)
        burnup_done_pts.append(done_acc)
        burnup_dup_pts.append(dup_acc)
        curr_date += timedelta(days=1)

    def _is_strictly_done(it: ProjectItem) -> bool:
        if _bucket_status(it.status) == "done":
            return True
        return False

    total_scope_pts = sum(it.difficulty for it in active_items if it.created_at.date() <= end_date)
    total_dup_pts = sum(it.difficulty for it in active_items if _is_duplicate_item(it))
    total_done_pts = sum(
        it.difficulty for it in active_items if _is_strictly_done(it) and not _is_duplicate_item(it)
    )

    if burnup_scope_pts:
        burnup_scope_pts[-1] = total_scope_pts
        burnup_done_pts[-1] = total_done_pts
        burnup_dup_pts[-1] = total_dup_pts

    scope_series = burnup_scope_pts

    final_open_display = int(total_scope_pts - total_done_pts - total_dup_pts)
    final_done_display = int(total_done_pts)
    final_dup_display = int(total_dup_pts)

    burnup_svg = _burnup_chart_svg(
        burnup_dates,
        scope_series,
        burnup_done_pts,
        burnup_dup_pts,
        f"BurnUp: {milestone_label or milestone_month}",
        final_open=final_open_display,
        final_done=final_done_display,
        final_dup=final_dup_display,
    )

    cutoff = reference_date or date.today()

    baseline_items = [
        it
        for it in active_items
        if not (
            _bucket_status(it.status) == "backlog"
            and getattr(it, "content_state", None) == "CLOSED"
        )
    ]

    iter_items = [
        it
        for it in baseline_items
        if getattr(it, "iteration_start", None) and getattr(it, "iteration_end", None)
    ]
    items_cut = [it for it in iter_items if it.iteration_end <= cutoff]

    count_totals: Dict[str, int] = {
        k: 0 for k in ["backlog", "blocked", "progress", "review", "done", "duplicate"]
    }
    difficulty_totals: Dict[str, float] = {
        k: 0.0 for k in ["backlog", "blocked", "progress", "review", "done", "duplicate"]
    }

    for it in items_cut:
        sk = _bucket_status(it.status)
        if sk == "cancelled":
            continue
        is_dup_flag = _is_duplicate_item(it)

        if (
            is_dup_flag
            and getattr(it, "status_updated_at", None)
            and it.status_updated_at.date() <= cutoff
            and _bucket_status(it.status) == "done"
        ):
            count_totals["done"] += 1
            difficulty_totals["done"] += float(it.difficulty or 0.0)
            continue

        if is_dup_flag:
            day = (it.status_updated_at or it.created_at).date()
            if day <= cutoff:
                count_totals["duplicate"] += 1
                difficulty_totals["duplicate"] += float(it.difficulty or 0.0)
            else:
                count_totals["backlog"] += 1
                difficulty_totals["backlog"] += float(it.difficulty or 0.0)
            continue

        if getattr(it, "status_updated_at", None) and it.status_updated_at.date() > cutoff:
            count_totals["backlog"] += 1
            difficulty_totals["backlog"] += float(it.difficulty or 0.0)
        else:
            key = sk if sk in ["backlog", "progress", "review", "done"] else "backlog"
            count_totals[key] += 1
            difficulty_totals[key] += float(it.difficulty or 0.0)

    prog_cats = ["Backlog", "Progress", "Review", "Done"]
    prog_vals_points = [
        difficulty_totals["backlog"],
        difficulty_totals["progress"],
        difficulty_totals["review"],
        difficulty_totals["done"],
    ]
    prog_cols = [
        GITHUB_COLORS["backlog"],
        GITHUB_COLORS["progress"],
        GITHUB_COLORS["review"],
        GITHUB_COLORS["done"],
    ]

    progress_svg = _simple_bar_chart_svg(
        prog_cats, prog_vals_points, prog_cols, "Progresso Atual (Previsto)", "Pontos"
    )

    categories = ["backlog", "blocked", "progress", "review", "done"]
    total_count = sum(count_totals.get(c, 0) for c in categories)
    weekly_table = {
        "backlog": count_totals.get("backlog", 0),
        "blocked": count_totals.get("blocked", 0),
        "progress": count_totals.get("progress", 0),
        "review": count_totals.get("review", 0),
        "done": count_totals.get("done", 0),
        "done_percent": int(round((count_totals.get("done", 0) / max(total_count, 1)) * 100)),
        "done_review_percent": int(
            round(
                (
                    (count_totals.get("done", 0) + count_totals.get("review", 0))
                    / max(total_count, 1)
                )
                * 100
            )
        ),
        "difficulty_points": {k: difficulty_totals.get(k, 0.0) for k in difficulty_totals.keys()},
        "selected_count": len(items_cut),
    }

    label_map: Dict[str, Dict[str, int]] = {}
    ref_date = reference_date or date.today()

    filtered_items = [
        it
        for it in active_items
        if getattr(it, "iteration_start", None)
        and it.created_at.date() <= ref_date
        and _bucket_status(it.status) not in ["cancelled", "duplicate"]
    ]

    for item in filtered_items:
        st = _bucket_status(item.status)
        if st not in ["backlog", "progress", "review", "done"]:
            st = "backlog"
        for lbl in item.labels:
            if lbl not in label_map:
                label_map[lbl] = {"backlog": 0, "progress": 0, "review": 0, "done": 0}
            label_map[lbl][st] = label_map[lbl].get(st, 0) + 1

    sorted_labels = sorted(label_map.items(), key=lambda x: sum(x[1].values()), reverse=True)
    exclude_norm = _normalize_text("Geração de Relatório")
    filtered_sorted = [(k, v) for (k, v) in sorted_labels if _normalize_text(k) != exclude_norm]
    top_labels = [k for k, v in filtered_sorted]
    features_data = {lbl: label_map[lbl] for lbl in top_labels}
    features_svg = _horizontal_stacked_bar_svg(
        top_labels, features_data, f"Features - {milestone_label or milestone_month}"
    )

    status_points_total = {k: 0.0 for k in GITHUB_COLORS}
    status_counts_total = {k: 0 for k in GITHUB_COLORS}
    for item in active_items:
        st = _bucket_status(item.status)
        if st == "cancelled":
            continue
        if st == "duplicate" or _is_duplicate_item(item):
            status_points_total["duplicate"] += item.difficulty
            status_counts_total["duplicate"] += 1
            continue
        status_points_total[st] += item.difficulty
        status_counts_total[st] += 1

    total_pts = sum(status_points_total.values())
    total_cnt = sum(status_counts_total.values())

    total_table = {
        "count_total": total_cnt,
        "count_review": status_counts_total.get("review", 0),
        "count_done": status_counts_total.get("done", 0),
        "difficulty_total": total_pts,
        "difficulty_review": status_points_total.get("review", 0.0),
        "difficulty_done": status_points_total.get("done", 0.0),
        "done_count_percent": int((status_counts_total.get("done", 0) / max(total_cnt, 1)) * 100),
        "done_difficulty_percent": int(
            (status_points_total.get("done", 0.0) / max(total_pts, 1.0)) * 100
        ),
        "done_review_count_percent": int(
            (
                (status_counts_total.get("done", 0) + status_counts_total.get("review", 0))
                / max(total_cnt, 1)
            )
            * 100
        ),
        "done_review_difficulty_percent": int(
            (
                (status_points_total.get("done", 0.0) + status_points_total.get("review", 0.0))
                / max(total_pts, 1.0)
            )
            * 100
        ),
    }

    return {
        "burnup_svg": burnup_svg,
        "weekly_progress_svg": progress_svg,
        "milestone_labels_svg": features_svg,
        "tables": {"weekly": weekly_table, "total": total_table},
    }
