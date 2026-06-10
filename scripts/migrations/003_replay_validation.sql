-- Replay validation + promotion audit

CREATE TABLE IF NOT EXISTS replay_validation (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  replay_date TEXT,
  passed INTEGER DEFAULT 0,
  pnl_pct REAL,
  entry_price REAL,
  exit_price REAL,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_replay_validation_date
  ON replay_validation(trade_date, passed);
