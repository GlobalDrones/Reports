from __future__ import annotations

import json
from datetime import date
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from pydantic import conint

from app import db
from app.schemas import ReportOut, TaskItem

router = APIRouter()


@router.post("/{project_slug}/reports", response_model=ReportOut)
async def create_report(
    request: Request,
    project_slug: str,
    developer_name: Annotated[str, Form(...)],
    summary: Annotated[str, Form(...)],
    self_assessment: Annotated[conint(ge=1, le=5), Form(...)],
    next_week_expectation: Annotated[conint(ge=1, le=5), Form(...)],
    tasks_json: Annotated[str, Form(...)],
    team_slug: Annotated[str | None, Form()] = None,
    week_id: Annotated[str | None, Form()] = None,
    progress: Annotated[str, Form()] = "",
    had_difficulties: Annotated[bool, Form()] = False,
    difficulties_description: Annotated[str | None, Form()] = None,
    next_steps: Annotated[str, Form()] = "",
    had_deliveries: Annotated[bool, Form()] = False,
    deliveries_notes: Annotated[str | None, Form()] = None,
    deliveries_link: Annotated[str | None, Form()] = None,
    deliveries_links_json: Annotated[str | None, Form()] = None,
    overwrite: Annotated[bool, Form()] = False,
):
    if not week_id:
        iso = date.today().isocalendar()
        week_id = f"{iso.year}-W{iso.week:02d}"

    try:
        tasks_data = json.loads(tasks_json)
        if not tasks_data or len(tasks_data) == 0:
            raise HTTPException(
                status_code=400,
                detail="At least one task must be provided.",
            )
        tasks = [TaskItem(**task) for task in tasks_data]
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tasks format: {str(exc)}",
        )

    settings = request.app.state.settings
    try:
        project_slug, project = settings.get_project(project_slug)
        team_slug, team = settings.get_team(project_slug, team_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if developer_name not in team.members:
        raise HTTPException(
            status_code=400,
            detail="Developer does not belong to the selected team.",
        )

    tasks_payload = []
    for task in tasks:
        task_data = task.model_dump()
        task_data["task_url"] = str(task.task_url)
        task_data["start_date"] = str(task.start_date)
        if task.end_date:
            task_data["end_date"] = str(task.end_date)
        end_date = task_data.get("end_date")
        days_spent = 0
        if end_date:
            try:
                start_dt = datetime.fromisoformat(str(task_data["start_date"]))
                end_dt = datetime.fromisoformat(str(end_date))
                delta_days = (end_dt.date() - start_dt.date()).days + 1
                days_spent = max(delta_days, 0)
            except ValueError:
                days_spent = 0
        task_data["days_spent"] = days_spent
        tasks_payload.append(task_data)

    existing_id = db.get_existing_report_id(
        settings,
        week_id,
        project_slug,
        team_slug,
        developer_name,
    )
    if existing_id and not overwrite:
        raise HTTPException(
            status_code=409,
            detail="Já existe um relatório para essa semana e pessoa.",
        )

    deliveries_links: list[str] = []
    if deliveries_links_json:
        try:
            parsed_links = json.loads(deliveries_links_json)
            if isinstance(parsed_links, list):
                deliveries_links = [str(item) for item in parsed_links if item]
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid deliveries links: {exc}")
    if deliveries_link and not deliveries_links:
        deliveries_links = [deliveries_link]
    deliveries_link_payload = json.dumps(deliveries_links) if deliveries_links else ""

    payload = {
        "week_id": week_id,
        "project_slug": project_slug,
        "project_name": project.name,
        "team_slug": team_slug,
        "team_name": team.name,
        "developer_name": developer_name,
        "summary": summary,
        "progress": progress,
        "had_difficulties": 1 if had_difficulties else 0,
        "difficulties_description": difficulties_description or "",
        "next_steps": next_steps,
        "tasks": tasks_payload,
        "had_deliveries": 1 if had_deliveries else 0,
        "deliveries_notes": deliveries_notes or "",
        "deliveries_link": deliveries_link_payload,
        "deliveries_links": deliveries_links,
        "self_assessment": int(self_assessment),
        "next_week_expectation": int(next_week_expectation),
    }
    report_id = db.create_report(settings, payload)
    created = {"id": report_id, **payload}
    if deliveries_links:
        created["deliveries_link"] = deliveries_links[0]
    return created


async def create_report_default(
    request: Request,
    developer_name: Annotated[str, Form(...)],
    summary: Annotated[str, Form(...)],
    self_assessment: Annotated[conint(ge=1, le=5), Form(...)],
    next_week_expectation: Annotated[conint(ge=1, le=5), Form(...)],
    tasks_json: Annotated[str, Form(...)],
    team_slug: Annotated[str | None, Form()] = None,
    week_id: Annotated[str | None, Form()] = None,
    progress: Annotated[str, Form()] = "",
    had_difficulties: Annotated[bool, Form()] = False,
    difficulties_description: Annotated[str | None, Form()] = None,
    next_steps: Annotated[str, Form()] = "",
    had_deliveries: Annotated[bool, Form()] = False,
    deliveries_notes: Annotated[str | None, Form()] = None,
    deliveries_link: Annotated[str | None, Form()] = None,
    deliveries_links_json: Annotated[str | None, Form()] = None,
    overwrite: Annotated[bool, Form()] = False,
):
    settings = request.app.state.settings
    project_slug, _ = settings.get_project(None)
    return await create_report(
        request=request,
        project_slug=project_slug,
        developer_name=developer_name,
        summary=summary,
        self_assessment=self_assessment,
        next_week_expectation=next_week_expectation,
        tasks_json=tasks_json,
        team_slug=team_slug,
        week_id=week_id,
        progress=progress,
        had_difficulties=had_difficulties,
        difficulties_description=difficulties_description,
        next_steps=next_steps,
        had_deliveries=had_deliveries,
        deliveries_notes=deliveries_notes,
        deliveries_link=deliveries_link,
        deliveries_links_json=deliveries_links_json,
        overwrite=overwrite,
    )


@router.get("/{project_slug}/reports", response_model=list[ReportOut])
def list_reports(request: Request, project_slug: str, week: str, team: str | None = None):
    settings = request.app.state.settings
    try:
        project_slug, project = settings.get_project(project_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    team_slug: str | None = None
    if team is not None:
        try:
            team_slug, _ = settings.get_team(project_slug, team)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    elif project.resolved_teams() and len(project.resolved_teams()) == 1:
        team_slug = next(iter(project.resolved_teams().keys()))

    reports = db.list_reports(settings, week, project_slug, team_slug)
    return reports


@router.get("/api/reports", response_model=list[ReportOut])
def list_reports_default(request: Request, week: str, team: str | None = None):
    settings = request.app.state.settings
    project_slug, project = settings.get_project(None)
    team_slug: str | None = None
    if team is not None:
        try:
            team_slug, _ = settings.get_team(project_slug, team)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    elif len(project.resolved_teams()) == 1:
        team_slug = next(iter(project.resolved_teams().keys()))
    reports = db.list_reports(settings, week, project_slug, team_slug)
    return reports
