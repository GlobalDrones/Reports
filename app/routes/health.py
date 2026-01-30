from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health_check(request: Request):
    settings = request.app.state.settings
    started_at = getattr(request.app.state, "started_at", None)
    now = datetime.now(tz=timezone.utc)
    uptime_seconds = None
    if isinstance(started_at, datetime):
        uptime_seconds = int((now - started_at).total_seconds())

    db_path = Path(settings.data_dir) / "rsd.sqlite"
    projects = settings.list_projects()
    projects_summary = {
        slug: {
            "name": project.name,
            "teams": list(project.resolved_teams().keys()),
        }
        for slug, project in projects.items()
    }

    return {
        "status": "ok",
        "app_name": settings.app_name,
        "base_url": settings.base_url,
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else None,
        "uptime_seconds": uptime_seconds,
        "db": {
            "path": str(db_path),
            "exists": db_path.exists(),
        },
        "projects": projects_summary,
    }
