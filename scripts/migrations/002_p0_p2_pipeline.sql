-- P0–P2 production gates: audit tables and indexes

CREATE TABLE IF NOT EXISTS pipeline_step_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  step_order INTEGER,
  step_name TEXT NOT NULL,
  command TEXT,
  status TEXT NOT NULL,
  duration_sec REAL,
  error TEXT,
  started_at TEXT,
  finished_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_step_runs_run
  ON pipeline_step_runs(run_id, step_order);

CREATE TABLE IF NOT EXISTS final_signals (
  trade_date           TEXT NOT NULL,
  symbol               TEXT NOT NULL,
  setup_type           TEXT,
  score                REAL,
  entry_price          REAL,
  entry_high           REAL,
  stop_loss            REAL,
  t1_target            REAL,
  t2_target            REAL,
  r_ratio              REAL,
  source_rules         REAL,
  source_ues           REAL,
  source_pine          REAL,
  source_ml            REAL,
  regime               TEXT,
  confidence           REAL,
  actionable           INTEGER DEFAULT 0,
  veto_reason          TEXT,
  source_breakdown     TEXT,
  updated_at           TEXT DEFAULT (datetime('now')),
  UNIQUE(trade_date, symbol)
);

CREATE INDEX IF NOT EXISTS idx_final_signals_actionable
  ON final_signals(trade_date, actionable, score DESC);

CREATE INDEX IF NOT EXISTS idx_final_signals_symbol_date
  ON final_signals(symbol, trade_date DESC);
