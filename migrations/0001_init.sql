CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_id TEXT NOT NULL,
    project_slug TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL,
    team_slug TEXT NOT NULL DEFAULT '',
    team_name TEXT NOT NULL DEFAULT '',
    developer_name TEXT NOT NULL,
    summary TEXT NOT NULL,
    progress TEXT NOT NULL,
    had_difficulties INTEGER NOT NULL DEFAULT 0,
    difficulties_description TEXT,
    next_steps TEXT NOT NULL,
    had_deliveries INTEGER NOT NULL,
    deliveries_notes TEXT,
    self_assessment INTEGER NOT NULL,
    next_week_expectation INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    task_url TEXT NOT NULL,
    start_date TEXT NOT NULL,
    days_spent INTEGER NOT NULL,
    days_remaining INTEGER,
    remaining_notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS delivery_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reports_week ON reports (week_id);
CREATE INDEX IF NOT EXISTS idx_reports_project ON reports (project_name);
CREATE INDEX IF NOT EXISTS idx_reports_project_slug ON reports (project_slug);
CREATE INDEX IF NOT EXISTS idx_reports_team_slug ON reports (team_slug);
CREATE INDEX IF NOT EXISTS idx_tasks_report ON tasks (report_id);
CREATE INDEX IF NOT EXISTS idx_delivery_files_report ON delivery_files (report_id);
