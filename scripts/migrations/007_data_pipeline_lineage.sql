-- L0 governance: track which pipeline step wrote which table and when.
CREATE TABLE IF NOT EXISTS data_pipeline_lineage (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT NOT NULL,
  pipeline      TEXT NOT NULL,
  step          TEXT NOT NULL,
  target_table  TEXT,
  rows_affected INTEGER,
  signal_date   TEXT,
  status        TEXT NOT NULL DEFAULT 'OK',
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_dpl_pipeline_finished
  ON data_pipeline_lineage(pipeline, finished_at DESC);

CREATE INDEX IF NOT EXISTS idx_dpl_signal_date
  ON data_pipeline_lineage(signal_date, pipeline);
