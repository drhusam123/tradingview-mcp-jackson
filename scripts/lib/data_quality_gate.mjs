/**
 * Layer-2 data quality gate — fast daily enforce before ML/scoring.
 */
import { spawnSync } from 'child_process';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
const SCRIPT = join(PROJECT_ROOT, 'scripts/python/data_quality_gate.py');

/** Run gate_daily and return parsed JSON. Throws on subprocess/parse failure. */
export function runDailyQualityGate(params = {}) {
  const r = spawnSync(
    PYTHON,
    [SCRIPT, 'gate_daily', JSON.stringify(params)],
    { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 120_000 },
  );
  if (r.error) throw r.error;
  const raw = (r.stdout || '').trim();
  if (!raw) throw new Error(r.stderr || 'data quality gate produced no output');
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`data quality gate invalid JSON: ${raw.slice(0, 200)}`);
  }
  if (r.status !== 0 && parsed.success !== false) {
    throw new Error(r.stderr || `gate_daily exit ${r.status}`);
  }
  return parsed;
}

/**
 * Enforce gate — exits process when blocked (for critical pipeline steps).
 * @returns {object} gate result when pass
 */
export function enforceDailyQualityGate(params = {}, { exitOnBlock = true } = {}) {
  const gate = runDailyQualityGate(params);
  if (gate.blocked && exitOnBlock) {
    const msg = [
      'Layer-2 data quality gate BLOCKED',
      gate.reason,
      gate.latest_date ? `latest=${gate.latest_date}` : '',
      gate.trust_score != null ? `trust=${gate.trust_score} (${gate.trust_status})` : '',
      gate.session_violations?.critical
        ? `session_critical=${gate.session_violations.critical}` : '',
    ].filter(Boolean).join(' | ');
    const err = new Error(msg);
    err.gate = gate;
    throw err;
  }
  return gate;
}
