from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.config import Settings


def _db_path(settings: Settings) -> Path:
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "rsd.sqlite"


def get_connection(settings: Settings) -> sqlite3.Connection:
    conn = sqlite3.connect(
        _db_path(settings),
        timeout=30,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migrations_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "migrations"


def run_migrations(settings: Settings) -> None:
    migrations_dir = _migrations_dir()
    migrations_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(migrations_dir.glob("*.sql"))

    with get_connection(settings) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for file_path in files:
            version = file_path.name.split("_", 1)[0]
            if version in applied:
                continue

            sql = file_path.read_text(encoding="utf-8")
            statements = [stmt.strip() for stmt in sql.split(";") if stmt.strip()]
            try:
                conn.execute("BEGIN")
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (?)",
                    (version,),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def init_db(settings: Settings) -> None:
    run_migrations(settings)


def create_report(settings: Settings, payload: dict[str, Any]) -> int:
    with get_connection(settings) as conn:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(reports)").fetchall()}

        report_id = None
        row = conn.execute(
            """
            SELECT id
            FROM reports
            WHERE week_id = ? AND project_slug = ? AND team_slug = ? AND developer_name = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                payload["week_id"],
                payload["project_slug"],
                payload["team_slug"],
                payload["developer_name"],
            ),
        ).fetchone()
        if row:
            report_id = int(row["id"])

        columns = [
            "week_id",
            "project_slug",
            "project_name",
            "team_slug",
            "team_name",
            "developer_name",
            "summary",
            "progress",
            "had_difficulties",
            "difficulties_description",
            "next_steps",
            "had_deliveries",
            "deliveries_notes",
            "deliveries_link",
            "self_assessment",
            "next_week_expectation",
        ]
        values = [
            payload["week_id"],
            payload["project_slug"],
            payload["project_name"],
            payload["team_slug"],
            payload["team_name"],
            payload["developer_name"],
            payload["summary"],
            payload.get("progress", ""),
            int(payload.get("had_difficulties", False)),
            payload.get("difficulties_description", ""),
            payload.get("next_steps", ""),
            int(payload.get("had_deliveries", False)),
            payload.get("deliveries_notes", ""),
            payload.get("deliveries_link", ""),
            payload["self_assessment"],
            payload["next_week_expectation"],
        ]

        if "team" in existing:
            columns.append("team")
            values.append(payload["project_name"])
        if "team_name" in existing:
            columns.append("team_name")
            values.append(payload["project_name"])
        if "author" in existing:
            columns.append("author")
            values.append(payload["developer_name"])
        if "blockers" in existing:
            columns.append("blockers")
            values.append("")
        if "delivered" in existing:
            columns.append("delivered")
            values.append(payload.get("had_deliveries", 0))

        if report_id is None:
            placeholders = ", ".join(["?"] * len(columns))
            column_sql = ", ".join(columns)
            cursor = conn.execute(
                f"INSERT INTO reports ({column_sql}) VALUES ({placeholders})",
                tuple(values),
            )
            report_id = cursor.lastrowid
        else:
            set_clause = ", ".join([f"{col} = ?" for col in columns])
            conn.execute(
                f"UPDATE reports SET {set_clause} WHERE id = ?",
                tuple(values + [report_id]),
            )
            conn.execute("DELETE FROM tasks WHERE report_id = ?", (report_id,))

        tasks = payload.get("tasks", [])
        for task in tasks:
            task_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "difficulty" in task_cols:
                conn.execute(
                    """
                    INSERT INTO tasks (report_id, task_url, start_date, end_date, days_spent, difficulty)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        task["task_url"],
                        task["start_date"],
                        task.get("end_date"),
                        task.get("days_spent"),
                        task.get("difficulty"),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO tasks (report_id, task_url, start_date, end_date, days_spent)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        report_id,
                        task["task_url"],
                        task["start_date"],
                        task.get("end_date"),
                        task.get("days_spent"),
                    ),
                )

        conn.commit()
        return int(report_id)


def _hydrate_reports(
    conn: sqlite3.Connection,
    rows: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    if not rows:
        return []

    report_ids = [row["id"] for row in rows]
    placeholders = ", ".join(["?"] * len(report_ids))

    tasks_rows = conn.execute(
        f"SELECT * FROM tasks WHERE report_id IN ({placeholders}) ORDER BY created_at ASC",
        tuple(report_ids),
    ).fetchall()
    tasks_by_report: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks_rows:
        tasks_by_report[int(task["report_id"])].append(dict(task))

    reports: list[dict[str, Any]] = []
    for row in rows:
        report = dict(row)
        report_id = int(report["id"])
        report["tasks"] = tasks_by_report.get(report_id, [])
        deliveries_link = report.get("deliveries_link")
        if isinstance(deliveries_link, str) and deliveries_link.strip():
            try:
                parsed = json.loads(deliveries_link)
                if isinstance(parsed, list):
                    report["deliveries_links"] = [str(item) for item in parsed if item]
                    if report["deliveries_links"]:
                        report["deliveries_link"] = report["deliveries_links"][0]
            except json.JSONDecodeError:
                report["deliveries_links"] = [deliveries_link]
        reports.append(report)

    return reports


def get_existing_report_id(
    settings: Settings,
    week_id: str,
    project_slug: str,
    team_slug: str,
    developer_name: str,
) -> int | None:
    with get_connection(settings) as conn:
        row = conn.execute(
            """
            SELECT id
            FROM reports
            WHERE week_id = ? AND project_slug = ? AND team_slug = ? AND developer_name = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (week_id, project_slug, team_slug, developer_name),
        ).fetchone()
        return int(row["id"]) if row else None


def list_reports(
    settings: Settings,
    week_id: str,
    project_slug: str | None = None,
    team_slug: str | None = None,
) -> list[dict[str, Any]]:
    with get_connection(settings) as conn:
        clauses = ["week_id = ?"]
        params: list[Any] = [week_id]
        if project_slug is not None:
            clauses.append("project_slug = ?")
            params.append(project_slug)
        if team_slug is not None:
            clauses.append("team_slug = ?")
            params.append(team_slug)

        where_sql = " AND ".join(clauses)
        rows = conn.execute(
            f"SELECT * FROM reports WHERE {where_sql} ORDER BY created_at ASC",
            tuple(params),
        ).fetchall()

        return _hydrate_reports(conn, rows)


def list_teams(settings: Settings, week_id: str, project_slug: str) -> list[str]:
    with get_connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT team_name
            FROM reports
            WHERE week_id = ? AND project_slug = ?
            ORDER BY team_name ASC
            """,
            (week_id, project_slug),
        ).fetchall()
        return [row["team_name"] for row in rows]


def list_reports_by_team(
    settings: Settings,
    week_id: str,
    project_slug: str,
    team_slug: str,
) -> list[dict[str, Any]]:
    with get_connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reports
            WHERE week_id = ? AND project_slug = ? AND team_slug = ?
            ORDER BY created_at ASC
            """,
            (week_id, project_slug, team_slug),
        ).fetchall()

        return _hydrate_reports(conn, rows)


def list_reports_in_range(
    settings: Settings,
    project_slug: str,
    team_slug: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    with get_connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reports
            WHERE project_slug = ? AND team_slug = ?
              AND date(created_at) BETWEEN date(?) AND date(?)
            ORDER BY created_at ASC
            """,
            (project_slug, team_slug, start_date, end_date),
        ).fetchall()

        return _hydrate_reports(conn, rows)


def list_reports_in_datetime_range(
    settings: Settings,
    project_slug: str,
    team_slug: str,
    start_datetime: str,
    end_datetime: str,
) -> list[dict[str, Any]]:
    with get_connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT * FROM reports
            WHERE project_slug = ? AND team_slug = ?
              AND datetime(created_at) BETWEEN datetime(?) AND datetime(?)
            ORDER BY created_at ASC
            """,
            (project_slug, team_slug, start_datetime, end_datetime),
        ).fetchall()

        return _hydrate_reports(conn, rows)
