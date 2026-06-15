/**
 * Phase 18 — weekly full prod:ready gate (CDP + tests).
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { execSync } from 'child_process';
import { PROJECT_ROOT } from './load_env.mjs';

const STALE_DAYS = parseInt(process.env.EGX_WEEKLY_PROD_READY_DAYS ?? '7', 10);

function readProdReady() {
  const p = join(PROJECT_ROOT, 'data/prod_ready_last.json');
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

export function evaluateWeeklyProdReady() {
  const last = readProdReady();
  const force = process.env.EGX_WEEKLY_PROD_READY_FORCE === '1';
  let ageDays = null;
  if (last?.at) {
    ageDays = Math.round((Date.now() - new Date(last.at).getTime()) / 86_400_000);
  }

  const stale = last == null || ageDays >= STALE_DAYS;
  const needsFull = force || stale;
  const lastWasFull = last?.steps?.some(s => s.label === 'Full stack verify' && s.ok)
    && !last?.steps?.some(s => s.label?.includes('skip'));

  return {
    at: new Date().toISOString(),
    last_at: last?.at ?? null,
    last_pass: last?.pass === true,
    age_days: ageDays,
    stale_days_threshold: STALE_DAYS,
    needs_full_run: needsFull,
    force,
    recommendation: needsFull
      ? 'Run npm run egx:prod:ready:full (weekly CDP + tests)'
      : `prod:ready fresh (${ageDays}d ago)`,
  };
}

export function runWeeklyProdReadyFull({ dryRun = false } = {}) {
  const ev = evaluateWeeklyProdReady();
  if (!ev.needs_full_run) {
    return { ran: false, skipped: true, ...ev };
  }
  if (dryRun) {
    return { ran: false, dry_run: true, ...ev };
  }

  const NODE = process.execPath;
  try {
    execSync(`"${NODE}" scripts/egx_prod_ready.mjs`, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: 900_000,
    });
    const after = readProdReady();
    return {
      ran: true,
      pass: after?.pass === true,
      ...ev,
      after_at: after?.at,
    };
  } catch (e) {
    return {
      ran: true,
      pass: false,
      error: e.message?.slice(0, 120),
      ...ev,
    };
  }
}

export function writeWeeklyProdReadySnapshot(payload = null) {
  const snap = payload ?? evaluateWeeklyProdReady();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/weekly_prod_ready_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
