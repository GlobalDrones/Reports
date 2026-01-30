from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app import db
from app.integrations.teams import send_teams_message
from app.report_pdf import render_pdf


def _iso_week_id(target: date | None = None) -> str:
    target = target or date.today()
    iso = target.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _iso_week_label(week_id: str) -> tuple[str, str]:
    year_str, week_str = week_id.split("-W")
    year = int(year_str)
    week = int(week_str)
    start = date.fromisocalendar(year, week, 1)
    end = date.fromisocalendar(year, week, 7)
    label = f"{start.strftime('%d/%m/%y')} a {end.strftime('%d/%m/%y')}"
    return label, ""


def _build_weekly_filename(week_id: str, project_slug: str, team_slug: str | None) -> tuple[str, str]:
    year_str, week_str = week_id.split("-W")
    year = int(year_str)
    week = int(week_str)
    friday = date.fromisocalendar(year, week, 5)
    date_label = friday.strftime("%Y_%m_%d")
    base = f"{date_label}-w{week:02d}-{project_slug}"
    if team_slug:
        base = f"{base}-{team_slug}"
    return base, f"{base}.pdf"


def _parse_project_teams_config(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _iter_channels(project_config: dict[str, Any]) -> list[dict[str, Any]]:
    if not project_config:
        return []
    channels = project_config.get("channels")
    if isinstance(channels, list) and channels:
        return [c for c in channels if isinstance(c, dict)]
    return [project_config]


def _normalize_schedules(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _build_report(
    settings,
    project_slug: str,
    team_slug: str | None,
    week_id: str,
) -> tuple[str, Path] | None:
    try:
        _, project = settings.get_project(project_slug)
    except ValueError:
        return None

    period_label, _ = _iso_week_label(week_id)

    if team_slug:
        try:
            _, team_obj = settings.get_team(project_slug, team_slug)
        except ValueError:
            return None
        reports = db.list_reports(settings, week_id, project_slug, team_slug)
        reports_by_team = {team_obj.name: reports} if reports else {}
        file_title, output_name = _build_weekly_filename(week_id, project_slug, team_slug)
    else:
        teams = project.resolved_teams()
        reports_by_team = {
            team.name: db.list_reports_by_team(settings, week_id, project_slug, slug)
            for slug, team in teams.items()
        }
        reports = db.list_reports(settings, week_id, project_slug, None)
        file_title, output_name = _build_weekly_filename(week_id, project_slug, None)

    if not reports:
        return None

    output_path = Path(settings.data_dir) / "rsd" / output_name
    render_pdf(
        week_id,
        reports,
        reports_by_team,
        output_path,
        period_label,
        project_slug=project_slug,
        file_title=file_title,
        milestone_month=None,
    )
    return output_name, output_path


def _send_project_message(
    settings, project_slug: str, webhook_url: str, team_slug: str | None, week_id: str
) -> None:
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        return

    output = _build_report(settings, project_slug, team_slug, week_id)
    if not output:
        return

    output_name, _ = output
    if team_slug:
        link_url = f"{base_url}/rsd/{project_slug}/{team_slug}/{week_id}.pdf"
    else:
        link_url = f"{base_url}/rsd/{project_slug}/{week_id}.pdf"

    title = f"Relatório publicado - {project_slug}"
    text = f"O PDF do relatório da semana {week_id} para a equipe {team_slug or project_slug} já está disponível. Clique no botão abaixo para abrir o PDF."
    send_teams_message(webhook_url, title, text, link_url, button_name="Abrir PDF")


def _build_collect_message(
    settings,
    project_slug: str,
    team_slug: str | None,
    week_id: str,
    title: str | None,
    text: str | None,
) -> tuple[str, str, str] | None:
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        return None

    try:
        settings.get_project(project_slug)
    except ValueError:
        return None

    if team_slug:
        try:
            settings.get_team(project_slug, team_slug)
        except ValueError:
            return None

    form_link = f"{base_url}/{project_slug}/form?week={week_id}"
    if team_slug:
        form_link = f"{form_link}&team={team_slug}"

    message_title = title or "Solicitação: preenchimento do relatório semanal"
    message_text = text or (
        f"Pessoal, não esqueçam de preencher o relatório da semana {week_id}. "
        "Clique no botão abaixo para abrir o formulário."
    )
    return message_title, message_text, form_link


def _send_collect_message(
    settings,
    project_slug: str,
    webhook_url: str,
    team_slug: str | None,
    week_id: str,
    title: str | None,
    text: str | None,
) -> None:
    payload = _build_collect_message(settings, project_slug, team_slug, week_id, title, text)
    if not payload:
        return
    message_title, message_text, form_link = payload
    send_teams_message(
        webhook_url,
        message_title,
        message_text,
        form_link,
        button_name="Abrir formulário",
    )


def start_scheduler(app) -> None:
    settings = app.state.settings
    project_config = _parse_project_teams_config(settings.project_teams_config)
    if not project_config:
        return

    state = {"sent": set()}
    app.state.teams_scheduler = state

    def _loop() -> None:
        while True:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            weekday = now.weekday()
            week_id = _iso_week_id(now.date())

            for project_slug, config in project_config.items():
                if not isinstance(config, dict):
                    continue

                for channel in _iter_channels(config):
                    if not channel.get("enabled", False):
                        continue
                    webhook_url = channel.get("webhook_url")
                    if not webhook_url:
                        continue

                    team_slug = channel.get("team_slug")
                    channel_name = channel.get("name", "channel")

                    publish_schedules = _normalize_schedules(
                        channel.get("publish_pdf") or channel.get("schedules")
                    )
                    for schedule in publish_schedules:
                        days = schedule.get("days", [])
                        times = schedule.get("times", [])
                        if weekday not in days or current_time not in times:
                            continue

                        key = (
                            f"publish:{project_slug}:{team_slug or 'all'}:{channel_name}:"
                            f"{now.date()}:{current_time}"
                        )
                        if key in state["sent"]:
                            continue

                        _send_project_message(settings, project_slug, webhook_url, team_slug, week_id)
                        state["sent"].add(key)

                    form_request_config = channel.get("form_request") or channel.get("collect")
                    if isinstance(form_request_config, dict):
                        collect_title = form_request_config.get("title")
                        collect_text = form_request_config.get("text")
                        collect_schedules = _normalize_schedules(form_request_config.get("schedules"))
                    else:
                        collect_title = channel.get("form_request_title") or channel.get("collect_title")
                        collect_text = channel.get("form_request_text") or channel.get("collect_text")
                        collect_schedules = _normalize_schedules(
                            channel.get("form_request_schedules") or channel.get("collect_schedules")
                        )

                    for schedule in collect_schedules:
                        days = schedule.get("days", [])
                        times = schedule.get("times", [])
                        if weekday not in days or current_time not in times:
                            continue

                        key = (
                            f"collect:{project_slug}:{team_slug or 'all'}:{channel_name}:"
                            f"{now.date()}:{current_time}"
                        )
                        if key in state["sent"]:
                            continue

                        _send_collect_message(
                            settings,
                            project_slug,
                            webhook_url,
                            team_slug,
                            week_id,
                            collect_title,
                            collect_text,
                        )
                        state["sent"].add(key)

            time.sleep(30)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
