from functools import lru_cache
import json
from pathlib import Path

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemberConfig(BaseModel):
    name: str
    email: str | None = None


class TeamConfig(BaseModel):
    name: str
    members: list[MemberConfig] = []

    @field_validator("members", mode="before")
    @classmethod
    def _normalize_members(cls, value):
        if value is None:
            return []
        normalized: list[dict[str, str | None]] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    normalized.append({"name": item, "email": None})
                elif isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    if not name:
                        continue
                    email_raw = item.get("email")
                    email = str(email_raw).strip() if email_raw else None
                    normalized.append({"name": name, "email": email})
        return normalized

    def member_names(self) -> list[str]:
        return [member.name for member in self.members]

    def member_emails(self) -> list[str]:
        return [member.email for member in self.members if member.email]


class ProjectConfig(BaseModel):
    name: str
    members: list[MemberConfig] | None = None
    teams: dict[str, TeamConfig] | None = None
    github_project_id: str | None = None

    @field_validator("members", mode="before")
    @classmethod
    def _normalize_members(cls, value):
        return TeamConfig._normalize_members(value)

    def resolved_teams(self) -> dict[str, TeamConfig]:
        if self.teams:
            return self.teams
        if self.members is not None:
            return {"default": TeamConfig(name=self.name, members=self.members)}
        return {"default": TeamConfig(name=self.name, members=[])}

    def project_emails(self) -> list[str]:
        emails: set[str] = set()
        for team in self.resolved_teams().values():
            for email in team.member_emails():
                if email:
                    emails.add(email)
        return sorted(emails)


class EnvSettings(BaseSettings):
    app_name: str
    base_url: str
    data_dir: str
    cors_origins: str = "*"
    github_token: str | None = None
    llm_api_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    model_config = SettingsConfigDict(env_file=None, env_prefix="", extra="ignore")


class ConfigFile(BaseModel):
    deliveries_link_url: str | None = None
    project_notifications_config: dict[str, object] | None = None
    project_milestone_urls: dict[str, object] | None = None
    projects: dict[str, ProjectConfig]

    @field_validator("project_milestone_urls", mode="before")
    @classmethod
    def _normalize_project_milestone_urls(cls, value):
        if value is None or value == "":
            return None
        if isinstance(value, dict):
            return value
        return value


class Settings(BaseModel):
    app_name: str
    base_url: str
    data_dir: str
    cors_origins: str = "*"
    github_token: str | None = None
    llm_api_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    deliveries_link_url: str | None = None
    project_notifications_config: dict[str, object] | None = None
    project_milestone_urls: dict[str, object] | None = None
    projects: dict[str, ProjectConfig]

    def list_projects(self) -> dict[str, ProjectConfig]:
        return self.projects

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

    def get_notifications_config(self) -> dict[str, object] | None:
        return self.project_notifications_config


def _load_config_file(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            "config.json not found. Create it from config.json.example and configure projects/notifications."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError("config.json is not a valid JSON file")
    return data if isinstance(data, dict) else {}


@lru_cache
def get_settings() -> Settings:
    env_settings = EnvSettings(
        _env_file=get_project_root() / ".env",
        _env_file_encoding="utf-8",
    )
    config = ConfigFile.model_validate(_load_config_file(get_project_root() / "config.json"))
    merged = {
        **env_settings.model_dump(),
        **config.model_dump(),
    }
    return Settings.model_validate(merged)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_views_dir() -> Path:
    return get_project_root() / "app" / "views"


def get_assets_dir() -> Path:
    return get_project_root() / "app" / "assets"


def get_public_dir() -> Path:
    return get_project_root() / "app" / "public"
