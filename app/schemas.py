from __future__ import annotations

from datetime import date

from pydantic import AnyUrl, BaseModel, conint


class TaskItem(BaseModel):
    task_url: AnyUrl
    start_date: date
    end_date: date | None = None


class ReportBase(BaseModel):
    week_id: str
    project_slug: str
    project_name: str
    team_slug: str
    team_name: str
    developer_name: str
    summary: str
    progress: str
    had_difficulties: bool
    difficulties_description: str | None = None
    next_steps: str
    tasks: list[TaskItem]
    had_deliveries: bool
    deliveries_notes: str | None = None
    deliveries_link: str | None = None
    deliveries_links: list[str] | None = None
    self_assessment: conint(ge=0)
    next_week_expectation: conint(ge=0)


class ReportOut(ReportBase):
    id: int
