from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app import db
from app.integrations.email import send_email_message
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


def _parse_project_notifications_config(raw: dict[str, Any] | str | None) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
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


def _normalize_recipients(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _resolve_recipients(settings, project_slug: str, team_slug: str | None, channel: dict[str, Any]) -> list[str]:
    explicit = _normalize_recipients(
        channel.get("recipients")
        or channel.get("emails")
        or channel.get("email")
        or channel.get("to")
    )
    if explicit:
        return explicit

    try:
        _, project = settings.get_project(project_slug)
    except ValueError:
        return []

    if team_slug:
        try:
            _, team = settings.get_team(project_slug, team_slug)
        except ValueError:
            return []
        return team.member_emails()

    return project.project_emails()


def _build_report(
    settings,
    project_slug: str,
    team_slug: str | None,
    week_id: str,
) -> tuple[str, str, Path] | None:
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
    return file_title, output_name, output_path


def _send_project_message(
    settings,
    project_slug: str,
    recipients: list[str],
    team_slug: str | None,
    week_id: str,
    title: str | None,
    text: str | None,
) -> None:
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        return

    output = _build_report(settings, project_slug, team_slug, week_id)
    if not output:
        return

    file_title, output_name, output_path = output
    if team_slug:
        link_url = f"{base_url}/rsd/{project_slug}/{team_slug}/{week_id}.pdf"
    else:
        link_url = f"{base_url}/rsd/{project_slug}/{week_id}.pdf"

    message_title = title or f"Relatório publicado - {file_title}"
    message_text = text or (
        f"O PDF do relatório da semana {week_id} para a equipe {team_slug or project_slug} "
        "já está disponível."
    )
    body = f"{message_text}\n\nLink: {link_url}"
    send_email_message(settings, recipients, message_title, body, attachments=[output_path])


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
    recipients: list[str],
    team_slug: str | None,
    week_id: str,
    title: str | None,
    text: str | None,
) -> None:
    payload = _build_collect_message(settings, project_slug, team_slug, week_id, title, text)
    if not payload:
        return
    message_title, message_text, form_link = payload
    body = f"{message_text}\n\nLink: {form_link}"
    send_email_message(settings, recipients, message_title, body)


def start_scheduler(app) -> None:
    settings = app.state.settings
    project_config = _parse_project_notifications_config(settings.get_notifications_config())
    if not project_config:
        return

    state = {"sent": set()}
    app.state.email_scheduler = state

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
                    recipients = _resolve_recipients(settings, project_slug, channel.get("team_slug"), channel)
                    if not recipients:
                        continue

                    team_slug = channel.get("team_slug")
                    channel_name = channel.get("name", "channel")

                    publish_config = channel.get("publish")
                    if isinstance(publish_config, dict):
                        publish_title = publish_config.get("title")
                        publish_text = publish_config.get("text")
                        publish_schedules = _normalize_schedules(
                            publish_config.get("schedules")
                            or channel.get("publish_pdf")
                            or channel.get("schedules")
                        )
                    else:
                        publish_title = channel.get("publish_title")
                        publish_text = channel.get("publish_text")
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

                        publish_week_id = (
                            _iso_week_id(now.date() - timedelta(days=7))
                            if schedule.get("previous_week")
                            else week_id
                        )

                        all_publish_recipients = list(
                            dict.fromkeys(recipients + settings.global_publish_recipients)
                        )
                        _send_project_message(
                            settings,
                            project_slug,
                            all_publish_recipients,
                            team_slug,
                            publish_week_id,
                            publish_title,
                            publish_text,
                        )
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
                            recipients,
                            team_slug,
                            week_id,
                            collect_title,
                            collect_text,
                        )
                        state["sent"].add(key)

            time.sleep(30)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
