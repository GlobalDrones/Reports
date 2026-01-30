ALTER TABLE reports ADD COLUMN deliveries_link TEXT;
ALTER TABLE tasks ADD COLUMN end_date TEXT;

DROP TABLE IF EXISTS delivery_files;

CREATE INDEX IF NOT EXISTS idx_tasks_end_date ON tasks (end_date);
