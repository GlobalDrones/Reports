from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.integrations.teams import send_teams_message

router = APIRouter(prefix="/teams", tags=["teams"])


def _parse_project_teams_config(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_webhook(settings, project_slug: str | None, team: str | None) -> str | None:
    if not project_slug:
        return None

    config = _parse_project_teams_config(settings.project_teams_config)
    project = config.get(project_slug)
    if not isinstance(project, dict):
        return None

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
            webhook_url = channel.get("webhook_url")
            if webhook_url:
                return webhook_url
        return None

    if project.get("enabled", False):
        return project.get("webhook_url")
    return None


@router.post("/notify/collect")
def notify_collect(
    request: Request,
    week: str,
    project_slug: str | None = None,
    team: str | None = None,
    title: str | None = None,
    text: str | None = None,
    webhook_url: str | None = None,
):
    settings = request.app.state.settings
    target_webhook = webhook_url or _resolve_webhook(settings, project_slug, team)
    if not target_webhook:
        raise HTTPException(
            status_code=400,
            detail="Webhook not configured. Provide webhook_url or configure PROJECT_TEAMS_CONFIG.",
        )

    base_url = settings.base_url.rstrip("/")
    if project_slug:
        form_link = f"{base_url}/{project_slug}/form?week={week}"
        if team:
            form_link = f"{form_link}&team={team}"
    else:
        form_link = f"{base_url}/form?week={week}"
    send_teams_message(
        target_webhook,
        title=title or "Solicitação: preenchimento do relatório semanal",
        text=text or f"Pessoal, não esqueçam de preencher o relatório da semana {week}. Clique no botão abaixo para abrir o formulário.",
        link_url=form_link,
        button_name="Abrir formulário",
    )
    return {
        "status": "sent",
        "type": "collect",
        "link": form_link,
    }


@router.post("/notify/publish")
def notify_publish(
    request: Request,
    week: str,
    project_slug: str | None = None,
    team: str | None = None,
    title: str | None = None,
    text: str | None = None,
    webhook_url: str | None = None,
):
    settings = request.app.state.settings
    target_webhook = webhook_url or _resolve_webhook(settings, project_slug, team)
    if not target_webhook:
        raise HTTPException(
            status_code=400,
            detail="Webhook not configured. Provide webhook_url or configure PROJECT_TEAMS_CONFIG.",
        )

    base_url = settings.base_url.rstrip("/")
    if project_slug and team:
        pdf_link = f"{base_url}/rsd/{project_slug}/{team}/{week}.pdf"
    elif project_slug:
        pdf_link = f"{base_url}/rsd/{project_slug}/{week}.pdf"
    else:
        pdf_link = f"{base_url}/rsd/{week}.pdf"
    send_teams_message(
        target_webhook,
        title=title or "Relatório publicado",
        text=text or f"O PDF do relatório da semana {week} para a equipe {team or project_slug} já está disponível. Clique no botão abaixo para abrir o PDF.",
        link_url=pdf_link,
        button_name="Abrir PDF",
    )
    return {
        "status": "sent",
        "type": "publish",
        "link": pdf_link,
    }
