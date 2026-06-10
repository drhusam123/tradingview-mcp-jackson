-- Bootstrap migration tracking (idempotent)
CREATE TABLE IF NOT EXISTS schema_migrations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  version     TEXT NOT NULL UNIQUE,
  filename    TEXT NOT NULL,
  applied_at  TEXT DEFAULT (datetime('now'))
);
