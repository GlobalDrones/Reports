from functools import lru_cache
from pathlib import Path
import os

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TeamConfig(BaseModel):
    name: str
    members: list[str]


class ProjectConfig(BaseModel):
    name: str
    members: list[str] | None = None
    teams: dict[str, TeamConfig] | None = None

    def resolved_teams(self) -> dict[str, TeamConfig]:
        if self.teams:
            return self.teams
        if self.members is not None:
            return {"default": TeamConfig(name=self.name, members=self.members)}
        return {"default": TeamConfig(name=self.name, members=[])}


class Settings(BaseSettings):
    app_name: str
    base_url: str
    data_dir: str
    deliveries_link_url: str | None = None
    github_token: str | None = None
    github_project_id: str | None = None
    project_github_ids: dict[str, str] | None = None
    llm_api_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    project_milestone_urls: dict[str, object] | None = None
    project_teams_config: str | None = None
    projects: dict[str, ProjectConfig] | None = None
    project_name: str | None = None
    project_members: list[str] | None = None
    cors_origins: str = "*"

    model_config = SettingsConfigDict(env_file=None, env_prefix="")

    @field_validator("project_milestone_urls", mode="before")
    @classmethod
    def _normalize_project_milestone_urls(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            return value
        return value

    @field_validator("project_github_ids", mode="before")
    @classmethod
    def _normalize_project_github_ids(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            return value
        return value

    def list_projects(self) -> dict[str, ProjectConfig]:
        if self.projects:
            return self.projects
        if self.project_name:
            return {
                "default": ProjectConfig(name=self.project_name, members=self.project_members or [])
            }
        return {}

    def get_project(self, project_slug: str | None = None) -> tuple[str, ProjectConfig]:
        projects = self.list_projects()
        if not projects:
            raise ValueError("No project configured in ENV")
        if project_slug is None:
            project_slug = next(iter(projects.keys()))
        project = projects.get(project_slug)
        if project is None:
            raise ValueError(f"Invalid project: {project_slug}")
        return project_slug, project

    def get_team(self, project_slug: str, team_slug: str | None = None) -> tuple[str, TeamConfig]:
        _, project = self.get_project(project_slug)
        teams = project.resolved_teams()
        if team_slug is None:
            if len(teams) == 1:
                team_slug = next(iter(teams.keys()))
            else:
                raise ValueError("Team not provided for project with multiple teams")
        team = teams.get(team_slug)
        if team is None:
            raise ValueError(f"Invalid team: {team_slug}")
        return team_slug, team


def _is_json_multiline_start(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("{") or stripped.startswith("[")


def _balance_brackets(text: str) -> int:
    return text.count("{") + text.count("[") - text.count("}") - text.count("]")


def _load_env_multiline_json(path: Path) -> None:
    if not path.exists():
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1

        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "=" not in raw:
            continue

        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            continue

        if key in os.environ:
            continue

        if _is_json_multiline_start(value):
            buffer = value
            balance = _balance_brackets(buffer)
            while balance > 0 and i < len(lines):
                buffer = f"{buffer}\n{lines[i]}"
                i += 1
                balance = _balance_brackets(buffer)
            os.environ[key] = buffer
            continue

        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
            cleaned = cleaned[1:-1]
        os.environ[key] = cleaned


@lru_cache
def get_settings() -> Settings:
    _load_env_multiline_json(get_project_root() / ".env")
    return Settings()


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_views_dir() -> Path:
    return get_project_root() / "app" / "views"


def get_assets_dir() -> Path:
    return get_project_root() / "app" / "assets"


def get_public_dir() -> Path:
    return get_project_root() / "app" / "public"
