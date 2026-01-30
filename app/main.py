import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_assets_dir, get_public_dir, get_settings
from app.db import init_db
from app.routes import health, reports, rsd, teams
from app.scheduler import start_scheduler

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.state.settings = settings

    _configure_logging()

    app.include_router(health.router)
    app.include_router(reports.router)
    app.include_router(rsd.router)
    app.include_router(teams.router)

    rsd.register_project_form_routes(app, settings)

    static_dir = get_assets_dir()
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    public_dir = get_public_dir()
    app.mount("/public", StaticFiles(directory=public_dir), name="public")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()
        ]
        if hasattr(settings, "cors_origins") and settings.cors_origins
        else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        app.state.started_at = datetime.now(tz=timezone.utc)
        _init_db_with_retry(settings, max_attempts=5, delay=2)
        _start_scheduler_safely(app)
        _log_form_routes(settings)

    return app


def _configure_logging() -> None:
    class _FormsLinkFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.name == "app.main" and record.getMessage().startswith("forms.link")

    for logger_name in (
        "httpx",
        "fontTools",
        "fontTools.subset",
        "weasyprint",
        "app.report_pdf",
        "openai",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    app_logger = logging.getLogger("app.main")
    app_logger.setLevel(logging.INFO)
    app_logger.addFilter(_FormsLinkFilter())


def _init_db_with_retry(
    settings,
    max_attempts: int = 5,
    delay: int = 2,
) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "db.init.attempt",
                extra={"attempt": attempt, "max_attempts": max_attempts},
            )
            init_db(settings)
            logger.info("db.init.success")
            return
        except Exception as exc:
            logger.exception(
                "db.init.failure",
                extra={"attempt": attempt, "max_attempts": max_attempts},
            )
            if attempt == max_attempts:
                logger.critical("db.init.exhausted", extra={"max_attempts": max_attempts})
                raise RuntimeError(
                    f"Failed to initialize DB after {max_attempts} attempts: {exc}"
                ) from exc
            wait_time = delay * (2 ** (attempt - 1))
            logger.info("db.init.retry_wait", extra={"wait_seconds": wait_time})
            time.sleep(wait_time)


def _start_scheduler_safely(app: FastAPI) -> None:
    try:
        logger.info("scheduler.start")
        start_scheduler(app)
        logger.info("scheduler.start.success")
    except Exception:
        logger.exception("scheduler.start.failure")
        logger.warning("scheduler.start.degraded")


def _log_form_routes(settings) -> None:
    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        port = os.getenv("PORT", "3456")
        base_url = f"http://0.0.0.0:{port}"

    projects = settings.list_projects()
    if not projects:
        logger.info("routes.forms.none")
        return

    logger.info("forms.links")
    reports_url = f"{base_url}/reports"
    logger.info("forms.link %s", reports_url)
    for project_slug, project in projects.items():
        teams = project.resolved_teams()
        form_url = f"{base_url}/{project_slug}/form"
        logger.info("forms.link %s", form_url)
        if len(teams) > 1:
            for team_slug in teams.keys():
                team_url = f"{base_url}/{project_slug}/form?team={team_slug}"
                logger.info("forms.link %s", team_url)


app = create_app()
