/**
 * Ingest P6 learning directives into research_directives (Phase 26 table).
 * Closes loop: outcomes → learning_loop → research/evolution priorities.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';

const ENSURE_SQL = `
CREATE TABLE IF NOT EXISTS research_directives (
  directive_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at      TEXT,
  directive_type  TEXT,
  target          TEXT,
  priority        REAL DEFAULT 0.5,
  rationale       TEXT,
  status          TEXT DEFAULT 'PENDING',
  result          TEXT,
  completed_at    TEXT
);
`;

function priorityScore(p) {
  const m = { HIGH: 0.9, MEDIUM: 0.6, LOW: 0.3 };
  return m[(p || '').toUpperCase()] ?? 0.5;
}

export function ingestP6Directives(directives = [], { source = 'P6_CLOSED_LOOP' } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, error: 'NO_DB', ingested: 0 };
  if (!directives.length) return { ok: true, ingested: 0 };

  const d = new Database(DB_PATH);
  d.exec(ENSURE_SQL);

  const exists = d.prepare(`
    SELECT 1 FROM research_directives
    WHERE directive_type = ? AND target = ? AND status = 'PENDING'
    LIMIT 1
  `);

  const ins = d.prepare(`
    INSERT INTO research_directives (created_at, directive_type, target, priority, rationale, status)
    VALUES (datetime('now'), ?, ?, ?, ?, 'PENDING')
  `);

  let ingested = 0;
  for (const dir of directives) {
    const type = source;
    const target = dir.id || dir.action?.slice(0, 80) || 'unknown';
    if (exists.get(type, target)) continue;
    const rationale = [dir.action, dir.metric, dir.symbols?.join(',')].filter(Boolean).join(' | ');
    ins.run(type, target, priorityScore(dir.priority), rationale);
    ingested += 1;
  }

  d.close();
  return { ok: true, ingested, total: directives.length };
}
