from __future__ import annotations

import json
from typing import Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_views_dir
from app.milestones import list_milestone_months


templates = Jinja2Templates(directory=get_views_dir())


def render_form(
    request: Request,
    week: Optional[str] = None,
    project_slug: Optional[str] = None,
    team_slug: Optional[str] = None,
):
    settings = request.app.state.settings
    project_slug, project = settings.get_project(project_slug)
    teams = project.resolved_teams()

    if team_slug is None:
        if len(teams) == 1:
            team_slug = next(iter(teams.keys()))
    elif team_slug not in teams:
        raise ValueError("Invalid team for the specified project")

    team_payload = [
        {
            "slug": slug,
            "name": team.name,
            "members": team.members,
        }
        for slug, team in teams.items()
    ]

    return templates.TemplateResponse(
        "report_form.html",
        {
            "request": request,
            "week": week or "",
            "project_name": project.name,
            "project_slug": project_slug,
            "team_slug": team_slug or "",
            "deliveries_link_url": settings.deliveries_link_url or "",
            "teams": json.dumps(team_payload),
        },
    )


def render_forms_landing(
    request: Request,
    week: Optional[str] = None,
):
    settings = request.app.state.settings
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        base_url = "http://localhost:3456"

    projects_payload = []
    for project_slug, project in settings.list_projects().items():
        teams = project.resolved_teams()
        team_links = []
        for team_slug, team in teams.items():
            query = f"?team={team_slug}"
            team_links.append(
                {
                    "slug": team_slug,
                    "name": team.name,
                    "url": f"{base_url}/{project_slug}/form{query}",
                }
            )
        projects_payload.append(
            {
                "slug": project_slug,
                "name": project.name,
                "url": f"{base_url}/{project_slug}/form",
                "teams": team_links,
            }
        )

    return templates.TemplateResponse(
        "forms_landing.html",
        {
            "request": request,
            "week": week or "",
            "projects": projects_payload,
        },
    )


def render_reports_download(
    request: Request,
    status_message: str | None = None,
    status_type: str = "info",
):
    settings = request.app.state.settings
    projects_payload = [
        {
            "slug": "__all__",
            "name": "Todos os projetos",
            "teams": [],
            "milestone_months": [],
        }
    ]
    for project_slug, project in settings.list_projects().items():
        teams = project.resolved_teams()
        team_payload = [
            {
                "slug": slug,
                "name": team.name,
            }
            for slug, team in teams.items()
        ]
        milestone_months = list_milestone_months(settings.project_milestone_urls, project_slug)
        projects_payload.append(
            {
                "slug": project_slug,
                "name": project.name,
                "teams": team_payload,
                "milestone_months": milestone_months,
            }
        )

    return templates.TemplateResponse(
        "reports_download.html",
        {
            "request": request,
            "projects": projects_payload,
            "status_message": status_message,
            "status_type": status_type,
        },
    )
