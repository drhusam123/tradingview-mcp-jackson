import { appendFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

const DAILY = join(PROJECT_ROOT, 'logs', 'daily');
const ERRORS = join(PROJECT_ROOT, 'logs', 'errors');

export function ensureLogDirs() {
  for (const d of [DAILY, ERRORS, join(PROJECT_ROOT, 'logs', 'engines'), join(PROJECT_ROOT, 'logs', 'telegram')]) {
    mkdirSync(d, { recursive: true });
  }
}

export function logRun({ command, layer = 'general', status = 'ok', ms = 0, error = null, meta = {} } = {}) {
  ensureLogDirs();
  const row = {
    at: new Date().toISOString(),
    command,
    layer,
    status,
    ms,
    error: error ? String(error).slice(0, 500) : null,
    ...meta,
  };
  const file = status === 'error'
    ? join(ERRORS, `${layer}_${new Date().toISOString().slice(0, 10)}.jsonl`)
    : join(DAILY, `runs_${new Date().toISOString().slice(0, 10)}.jsonl`);
  appendFileSync(file, JSON.stringify(row) + '\n');
  return row;
}

export function logError(layer, err, impact = '') {
  return logRun({
    command: layer,
    layer,
    status: 'error',
    error: err?.stack || err?.message || String(err),
    meta: { impact, next_action: 'see logs/errors and re-run egx:health' },
  });
}
