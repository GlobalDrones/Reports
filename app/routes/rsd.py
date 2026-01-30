from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request
from fastapi import Form
from fastapi.responses import FileResponse

from app import db
from app.report_pdf import render_pdf
from app.web import render_form, render_forms_landing, render_reports_download

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _ensure_rsd_dir(settings) -> Path:
    rsd_dir = Path(settings.data_dir) / "rsd"
    rsd_dir.mkdir(parents=True, exist_ok=True)
    return rsd_dir


def _safe_pdf_path(settings, filename: str) -> Path:
    base_dir = _ensure_rsd_dir(settings).resolve()
    candidate = (base_dir / filename).resolve()
    if base_dir not in candidate.parents and candidate != base_dir:
        raise HTTPException(status_code=400, detail="Invalid file path")
    return candidate


@router.get("/form")
def get_form(request: Request, team: str | None = None):
    try:
        return render_forms_landing(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/reports")
def get_reports_download(request: Request):
    try:
        return render_reports_download(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/reports/download")
def download_reports(
    request: Request,
    week_id: str = Form(...),
    project_slug: str = Form(...),
    team_slug: str | None = Form(None),
    milestone_month: str | None = Form(None),
):
    settings = request.app.state.settings
    if project_slug == "__all__":
        if team_slug:
            raise HTTPException(status_code=400, detail="Team filter not supported for all projects")

        reports: list[dict] = []
        reports_by_project: dict[str, dict[str, list[dict]]] = {}
        for slug, project in settings.list_projects().items():
            teams = project.resolved_teams()
            project_reports_by_team = {
                team.name: db.list_reports_by_team(settings, week_id, slug, team_slug)
                for team_slug, team in teams.items()
            }
            project_reports = db.list_reports(settings, week_id, slug, None)
            if project_reports:
                reports.extend(project_reports)
                reports_by_project[project.name] = project_reports_by_team

        if not reports:
            return render_reports_download(
                request,
                status_message=(
                    "Nenhum relatório encontrado para a semana selecionada. "
                    "Preencha os formulários e tente novamente."
                ),
                status_type="warning",
            )

        period_label, _ = _iso_week_label(week_id)
        file_title, output_name = _build_weekly_filename(week_id, "todos-projetos", None)
        output_path = _safe_pdf_path(settings, output_name)

        if not output_path.exists():
            render_pdf(
                week_id,
                reports,
                {},
                output_path,
                period_label,
                project_slug="__all__",
                file_title=file_title,
                milestone_month=None,
                reports_by_project=reports_by_project,
            )

        return FileResponse(output_path, filename=output_path.name, media_type="application/pdf")

    try:
        project_slug, project = settings.get_project(project_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if team_slug:
        try:
            team_slug, team_obj = settings.get_team(project_slug, team_slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        reports = db.list_reports(settings, week_id, project_slug, team_slug)
        reports_by_team = {team_obj.name: reports} if reports else {}
    else:
        teams = project.resolved_teams()
        reports_by_team = {
            team.name: db.list_reports_by_team(settings, week_id, project_slug, slug)
            for slug, team in teams.items()
        }
        reports = db.list_reports(settings, week_id, project_slug, None)

    if not reports:
        return render_reports_download(
            request,
            status_message=(
                "Nenhum relatório encontrado para a semana selecionada. "
                "Preencha os formulários e tente novamente."
            ),
            status_type="warning",
        )

    period_label, _ = _iso_week_label(week_id)
    file_title, output_name = _build_weekly_filename(week_id, project_slug, team_slug)
    output_path = _safe_pdf_path(settings, output_name)

    if not output_path.exists():
        render_pdf(
            week_id,
            reports,
            reports_by_team,
            output_path,
            period_label,
            project_slug=project_slug,
            file_title=file_title,
            milestone_month=milestone_month,
        )

    return FileResponse(output_path, filename=output_path.name, media_type="application/pdf")


def _build_form_endpoint(project_slug: str):
    def _endpoint(
        request: Request,
        team: str | None = None,
    ):
        try:
            return render_form(request, None, project_slug, team)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return _endpoint


def register_project_form_routes(app: FastAPI, settings) -> None:
    projects = settings.list_projects()
    for project_slug in projects.keys():
        app.add_api_route(
            f"/{project_slug}/form",
            _build_form_endpoint(project_slug),
            methods=["GET"],
        )
        app.add_api_route(
            f"/{project_slug}/forms",
            _build_form_endpoint(project_slug),
            methods=["GET"],
        )


@router.post("/rsd/generate")
def generate_rsd(
    request: Request,
    background_tasks: BackgroundTasks,
    week: str | None = None,
    project_slug: str | None = None,
    team: str | None = None,
    milestone_month: str | None = None,
    end_date: str | None = None,
    range_days: int = 7,
    range_minutes: int | None = None,
    end_datetime: str | None = None,
    cutoff_weekday: int | None = None,
):
    request_id = uuid4().hex
    settings = request.app.state.settings
    period_label = week or ""
    if week:
        try:
            period_label, _ = _iso_week_label(week)
        except (ValueError, IndexError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid week: {exc}")

    if week:
        try:
            if project_slug is None:
                project_slug, project = settings.get_project(None)
            else:
                project_slug, project = settings.get_project(project_slug)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        team_slug: str | None = None
        if team is not None:
            try:
                team_slug, team_obj = settings.get_team(project_slug, team)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            teams_map = {
                team_obj.name: db.list_reports_by_team(settings, week, project_slug, team_slug)
            }
        else:
            teams = project.resolved_teams()
            if len(teams) == 1:
                team_slug = next(iter(teams.keys()))
                team_obj = teams[team_slug]
                teams_map = {
                    team_obj.name: db.list_reports_by_team(settings, week, project_slug, team_slug)
                }
            else:
                teams_map = {
                    team.name: db.list_reports_by_team(settings, week, project_slug, slug)
                    for slug, team in teams.items()
                }

        reports = db.list_reports(settings, week, project_slug, team_slug)
        if not reports:
            raise HTTPException(status_code=404, detail="No reports for week")

        reports_by_team = teams_map
        file_title, output_name = _build_weekly_filename(
            week,
            project_slug,
            team_slug if team is not None and team_slug is not None else None,
        )
        output_path = _safe_pdf_path(settings, output_name)
        logger.info(
            "rsd.pdf.queue",
            extra={"request_id": request_id, "output_path": str(output_path)},
        )
        background_tasks.add_task(
            render_pdf,
            week,
            reports,
            reports_by_team,
            output_path,
            period_label,
            project_slug=project_slug,
            file_title=file_title,
            milestone_month=milestone_month,
        )
        return {"status": "queued", "pdf": str(output_path), "request_id": request_id}

    try:
        if project_slug is None:
            project_slug, project = settings.get_project(None)
        else:
            project_slug, project = settings.get_project(project_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if team is not None:
        try:
            team_slug, team_obj = settings.get_team(project_slug, team)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    else:
        teams = project.resolved_teams()
        if len(teams) != 1:
            raise HTTPException(status_code=400, detail="Provide the project team")
        team_slug = next(iter(teams.keys()))
        team_obj = teams[team_slug]

    if end_datetime:
        try:
            end_dt = datetime.fromisoformat(end_datetime)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid end_datetime: {exc}")
    elif end_date:
        try:
            end_dt = datetime.combine(date.fromisoformat(end_date), time(23, 59))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid end_date: {exc}")
    else:
        end_dt = datetime.now()

    if cutoff_weekday is not None:
        if cutoff_weekday < 0 or cutoff_weekday > 6:
            raise HTTPException(status_code=400, detail="cutoff_weekday must be 0-6")
        delta = (end_dt.weekday() - cutoff_weekday) % 7
        end_dt = end_dt - timedelta(days=delta)

    if range_minutes is not None:
        if range_minutes < 1:
            raise HTTPException(status_code=400, detail="range_minutes must be >= 1")
        start_dt = end_dt - timedelta(minutes=range_minutes)
    else:
        if range_days < 1:
            raise HTTPException(status_code=400, detail="range_days must be >= 1")
        start_dt = end_dt - timedelta(days=range_days - 1)

    reports = db.list_reports_in_datetime_range(
        settings,
        project_slug,
        team_slug,
        start_dt.isoformat(sep=" "),
        end_dt.isoformat(sep=" "),
    )
    if not reports:
        raise HTTPException(status_code=404, detail="No reports for range")

    reports_by_team = {team_obj.name: reports}
    if range_minutes is not None:
        period_label = (
            f"{start_dt.strftime('%d/%m/%y %H:%M')} a {end_dt.strftime('%d/%m/%y %H:%M')}"
        )
        period_file_label = (
            f"{start_dt.strftime('%d-%m-%y_%H%M')}_a_{end_dt.strftime('%d-%m-%y_%H%M')}"
        )
    else:
        period_label = f"{start_dt.strftime('%d/%m/%y')} a {end_dt.strftime('%d/%m/%y')}"
        period_file_label = f"{start_dt.strftime('%d-%m-%y')}_a_{end_dt.strftime('%d-%m-%y')}"
    output_name = f"rsd-{project_slug}-{team_slug}-{period_file_label}.pdf"
    file_title = output_name.removesuffix(".pdf")
    output_path = _safe_pdf_path(settings, output_name)
    logger.info(
        "rsd.pdf.queue",
        extra={"request_id": request_id, "output_path": str(output_path)},
    )
    background_tasks.add_task(
        render_pdf,
        period_label,
        reports,
        reports_by_team,
        output_path,
        period_label,
        project_slug=project_slug,
        file_title=file_title,
        milestone_month=None,
    )
    return {"status": "queued", "pdf": str(output_path), "request_id": request_id}


@router.get("/rsd/{week}.pdf")
def download_rsd(request: Request, week: str):
    settings = request.app.state.settings
    request_id = uuid4().hex
    try:
        project_slug, _ = settings.get_project(None)
        _, pdf_filename = _build_weekly_filename(week, project_slug, None)
        pdf_path = _safe_pdf_path(settings, pdf_filename)
    except HTTPException:
        logger.warning("rsd.pdf.invalid_path", extra={"request_id": request_id})
        raise
    if not pdf_path.exists():
        projects = settings.list_projects()
        if len(projects) == 1:
            project_slug = next(iter(projects.keys()))
            try:
                _, pdf_filename = _build_weekly_filename(week, project_slug, None)
            except (ValueError, IndexError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid week: {exc}")
            pdf_path = _safe_pdf_path(settings, pdf_filename)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    logger.info(
        "rsd.pdf.download",
        extra={"request_id": request_id, "pdf_path": str(pdf_path)},
    )
    return FileResponse(pdf_path, filename=pdf_path.name, media_type="application/pdf")


@router.get("/rsd/{project_slug}/{week}.pdf")
def download_rsd_project(request: Request, project_slug: str, week: str):
    settings = request.app.state.settings
    request_id = uuid4().hex
    try:
        _, pdf_filename = _build_weekly_filename(week, project_slug, None)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid week: {exc}")
    pdf_path = _safe_pdf_path(settings, pdf_filename)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    logger.info(
        "rsd.pdf.download",
        extra={"request_id": request_id, "pdf_path": str(pdf_path)},
    )
    return FileResponse(pdf_path, filename=pdf_path.name, media_type="application/pdf")


@router.get("/rsd/{project_slug}/{team_slug}/{week}.pdf")
def download_rsd_team(request: Request, project_slug: str, team_slug: str, week: str):
    settings = request.app.state.settings
    request_id = uuid4().hex
    try:
        _, pdf_filename = _build_weekly_filename(week, project_slug, team_slug)
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid week: {exc}")
    pdf_path = _safe_pdf_path(settings, pdf_filename)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF not found")
    logger.info(
        "rsd.pdf.download",
        extra={"request_id": request_id, "pdf_path": str(pdf_path)},
    )
    return FileResponse(pdf_path, filename=pdf_path.name, media_type="application/pdf")
