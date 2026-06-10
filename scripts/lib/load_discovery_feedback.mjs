import Database from 'better-sqlite3';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { DB_PATH } from './delivery_audit.mjs';

export function loadDiscoveryFeedback() {
  const p = join(PROJECT_ROOT, 'data/discovery_feedback_last.json');
  if (!existsSync(p)) return { queue: [], n_items: 0 };
  try {
    const data = JSON.parse(readFileSync(p, 'utf8'));
    return { ...data, queue: data.queue || [], n_items: data.n_items ?? data.queue?.length ?? 0 };
  } catch {
    return { queue: [], n_items: 0 };
  }
}

export function readPendingResearchDirectives(limit = 12) {
  if (!existsSync(DB_PATH)) return [];
  const d = new Database(DB_PATH, { readonly: true });
  try {
    return d.prepare(`
      SELECT directive_id, directive_type, target, priority, rationale, created_at
      FROM research_directives
      WHERE status = 'PENDING'
      ORDER BY priority DESC, created_at DESC
      LIMIT ?
    `).all(limit);
  } catch {
    return [];
  } finally {
    d.close();
  }
}
