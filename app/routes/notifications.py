from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.integrations.email import send_email_message

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _parse_notifications_config(raw: dict | str | None) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_recipients(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _resolve_recipients(settings, project_slug: str | None, team: str | None) -> list[str]:
    if not project_slug:
        return []

    config = _parse_notifications_config(settings.get_notifications_config())
    project = config.get(project_slug)
    if not isinstance(project, dict):
        try:
            _, project_obj = settings.get_project(project_slug)
        except ValueError:
            return []
        if team:
            try:
                _, team_obj = settings.get_team(project_slug, team)
            except ValueError:
                return []
            return team_obj.member_emails()
        return project_obj.project_emails()

    channels = project.get("channels")
    if isinstance(channels, list) and channels:
        for channel in channels:
            if not isinstance(channel, dict):
                continue
            if not channel.get("enabled", False):
                continue
            channel_team = channel.get("team_slug")
            if team and channel_team != team:
                continue
            recipients = _normalize_recipients(
                channel.get("recipients")
                or channel.get("emails")
                or channel.get("email")
                or channel.get("to")
            )
            if recipients:
                return recipients
        return []

    if project.get("enabled", False):
        explicit = _normalize_recipients(
            project.get("recipients")
            or project.get("emails")
            or project.get("email")
            or project.get("to")
        )
        if explicit:
            return explicit
        try:
            _, project_obj = settings.get_project(project_slug)
        except ValueError:
            return []
        if team:
            try:
                _, team_obj = settings.get_team(project_slug, team)
            except ValueError:
                return []
            return team_obj.member_emails()
        return project_obj.project_emails()
    return []


@router.post("/collect")
def notify_collect(
    request: Request,
    week: str,
    project_slug: str | None = None,
    team: str | None = None,
    title: str | None = None,
    text: str | None = None,
    recipients: str | None = None,
):
    settings = request.app.state.settings
    target_recipients = _normalize_recipients(recipients) or _resolve_recipients(
        settings, project_slug, team
    )
    if not target_recipients:
        raise HTTPException(
            status_code=400,
            detail=(
                "Email recipients not configured. Provide recipients or configure project_notifications_config."
            ),
        )

    base_url = settings.base_url.rstrip("/")
    if project_slug:
        form_link = f"{base_url}/{project_slug}/form?week={week}"
        if team:
            form_link = f"{form_link}&team={team}"
    else:
        form_link = f"{base_url}/form?week={week}"

    message_title = title or "Solicitação: preenchimento do relatório semanal"
    message_text = text or f"Pessoal, não esqueçam de preencher o relatório da semana {week}."
    body = f"{message_text}\n\nLink: {form_link}"
    send_email_message(settings, target_recipients, message_title, body)
    return {
        "status": "sent",
        "type": "collect",
        "link": form_link,
    }


@router.post("/publish")
def notify_publish(
    request: Request,
    week: str,
    project_slug: str | None = None,
    team: str | None = None,
    title: str | None = None,
    text: str | None = None,
    recipients: str | None = None,
):
    settings = request.app.state.settings
    target_recipients = _normalize_recipients(recipients) or _resolve_recipients(
        settings, project_slug, team
    )
    if not target_recipients:
        raise HTTPException(
            status_code=400,
            detail=(
                "Email recipients not configured. Provide recipients or configure project_notifications_config."
            ),
        )

    base_url = settings.base_url.rstrip("/")
    if project_slug and team:
        pdf_link = f"{base_url}/rsd/{project_slug}/{team}/{week}.pdf"
    elif project_slug:
        pdf_link = f"{base_url}/rsd/{project_slug}/{week}.pdf"
    else:
        pdf_link = f"{base_url}/rsd/{week}.pdf"

    message_title = title or "Relatório publicado"
    message_text = text or (
        f"O PDF do relatório da semana {week} para a equipe {team or project_slug} já está disponível."
    )
    body = f"{message_text}\n\nLink: {pdf_link}"
    send_email_message(settings, target_recipients, message_title, body)
    return {
        "status": "sent",
        "type": "publish",
        "link": pdf_link,
    }
