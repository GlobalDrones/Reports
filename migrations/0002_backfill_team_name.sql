UPDATE reports
SET project_slug = lower(replace(project_name, ' ', '-'))
WHERE (project_slug IS NULL OR project_slug = '') AND project_name IS NOT NULL;

UPDATE reports
SET team_name = project_name
WHERE (team_name IS NULL OR team_name = '') AND project_name IS NOT NULL;

UPDATE reports
SET team_slug = lower(replace(team_name, ' ', '-'))
WHERE (team_slug IS NULL OR team_slug = '') AND team_name IS NOT NULL;

UPDATE reports
SET project_name = team_name
WHERE (project_name IS NULL OR project_name = '') AND team_name IS NOT NULL;
