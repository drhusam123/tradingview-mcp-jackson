-- Universe hygiene metadata (Phase 1 — delisted / renamed symbol tracking)
CREATE TABLE IF NOT EXISTS stock_universe (
  symbol           TEXT PRIMARY KEY,
  name             TEXT,
  sector           TEXT,
  last_fetch       TEXT,
  total_bars       INTEGER DEFAULT 0,
  earliest_bar     INTEGER,
  latest_bar       INTEGER,
  status           TEXT DEFAULT 'pending',
  successor_symbol TEXT,
  archived_at      TEXT,
  hygiene_reason   TEXT
);
CREATE INDEX IF NOT EXISTS idx_universe_status ON stock_universe(status);
