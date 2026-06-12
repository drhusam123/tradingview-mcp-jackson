/**
 * Data pipeline lineage — records orchestrator step outcomes (Phase 5 governance).
 */
import Database from 'better-sqlite3';
import { DB_PATH } from './delivery_audit.mjs';

const ENSURE_SQL = `
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
`;

export function ensureLineageTable(db) {
  db.exec(ENSURE_SQL);
}

export function newRunId(prefix = 'tv_auto') {
  return `${prefix}_${Date.now()}`;
}

export function recordLineageStep(db, row) {
  ensureLineageTable(db);
  db.prepare(`
    INSERT INTO data_pipeline_lineage
      (run_id, pipeline, step, target_table, rows_affected, signal_date,
       status, started_at, finished_at, notes)
    VALUES
      (@run_id, @pipeline, @step, @target_table, @rows_affected, @signal_date,
       @status, @started_at, @finished_at, @notes)
  `).run({
    run_id: row.run_id,
    pipeline: row.pipeline ?? 'egx_tv_auto_update',
    step: row.step,
    target_table: row.target_table ?? null,
    rows_affected: row.rows_affected ?? null,
    signal_date: row.signal_date ?? null,
    status: row.status ?? 'OK',
    started_at: row.started_at,
    finished_at: row.finished_at ?? new Date().toISOString(),
    notes: row.notes ?? null,
  });
}

export function recordPipelineSummary(db, { runId, pipeline, signalDate, steps, status = 'OK' }) {
  const finished = new Date().toISOString();
  const okN = steps.filter(s => s.ok !== false).length;
  recordLineageStep(db, {
    run_id: runId,
    pipeline,
    step: 'pipeline_summary',
    target_table: null,
    rows_affected: okN,
    signal_date: signalDate,
    status,
    started_at: steps[0]?.started_at ?? finished,
    finished_at: finished,
    notes: JSON.stringify({
      total_steps: steps.length,
      ok: okN,
      failed: steps.length - okN,
      last_step: steps[steps.length - 1]?.name ?? null,
    }),
  });
}

export function latestLineageAgeHours(db, pipeline = 'egx_tv_auto_update') {
  ensureLineageTable(db);
  const row = db.prepare(`
    SELECT finished_at FROM data_pipeline_lineage
    WHERE pipeline=? AND step='pipeline_summary' AND status='OK'
    ORDER BY finished_at DESC LIMIT 1
  `).get(pipeline);
  if (!row?.finished_at) return null;
  return Math.round((Date.now() - Date.parse(row.finished_at)) / 36e5 * 10) / 10;
}

export function openLineageDb() {
  return new Database(DB_PATH);
}
