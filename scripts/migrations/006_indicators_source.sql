-- indicators_cache.source column (local | tv) — idempotent marker migration.
-- Column is ensured at runtime by initSchema() and merge_technical_indicators.mjs.
CREATE TABLE IF NOT EXISTS _migration_006_marker (applied_at TEXT DEFAULT (datetime('now')));
