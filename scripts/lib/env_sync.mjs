/**
 * Upsert key=value pairs in .env (preserve other lines, create from .env.example if missing).
 */
import { existsSync, readFileSync, writeFileSync, copyFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

export function upsertEnvVars(vars, { envPath = join(PROJECT_ROOT, '.env'), dryRun = false } = {}) {
  const examplePath = join(PROJECT_ROOT, '.env.example');
  if (!existsSync(envPath) && existsSync(examplePath)) {
    copyFileSync(examplePath, envPath);
  }
  if (!existsSync(envPath)) {
    writeFileSync(envPath, '# EGX environment\n', 'utf8');
  }

  const lines = readFileSync(envPath, 'utf8').split('\n');
  const keys = Object.keys(vars);
  const found = new Set();
  const out = lines.map((line) => {
    if (!line || line.startsWith('#') || !line.includes('=')) return line;
    const key = line.split('=')[0].trim();
    if (!(key in vars)) return line;
    found.add(key);
    return `${key}=${vars[key]}`;
  });

  for (const key of keys) {
    if (!found.has(key)) out.push(`${key}=${vars[key]}`);
  }

  const body = out.join('\n').replace(/\n*$/, '\n');
  if (!dryRun) writeFileSync(envPath, body, 'utf8');
  return { envPath, updated: keys, created: keys.filter(k => !found.has(k)) };
}

export const PHASE14_ENV_DEFAULTS = {
  EGX_PHASE11_AUTO_PROMOTE: '1',
  EGX_MED_CLIENT_SHADOW: '1',
  EGX_MDE_PILOT_PROMOTE: '1',
  EGX_P6_GRADUATION_MODE: 'historical_bootstrap',
  EGX_MED_SHADOW_BACKFILL: '1',
};

export const PHASE17_ENV_DEFAULTS = {
  ...PHASE14_ENV_DEFAULTS,
  EGX_PROMOTION_AUTO_APPLY: '1',
  EGX_MED_AB_BACKFILL: '1',
};

export const PHASE18_ENV_DEFAULTS = {
  ...PHASE17_ENV_DEFAULTS,
  EGX_WEEKLY_PROD_READY: '1',
};

export const PHASE19_ENV_DEFAULTS = {
  ...PHASE18_ENV_DEFAULTS,
  EGX_T5_FILL_AUTO: '1',
  EGX_LRE_FORWARD_DAILY: '1',
  EGX_T5_WATCH_SYMBOLS: 'EGCH,UEFM',
  EGX_POST_GRAD_SESSION_DATE: '2026-06-17',
};

export const PHASE20_ENV_DEFAULTS = {
  ...PHASE19_ENV_DEFAULTS,
  EGX_LIVE_SESSION_ANCHOR: '2026-06-17',
  EGX_T5_CLOSURE_ANCHOR: '2026-06-19',
  EGX_T5_WATCH_SIGNAL_DATE: '2026-06-14',
};

/** Full graduation bundle — phases 21–26 infrastructure + bootstrap flags. */
export const PHASE26_ENV_DEFAULTS = {
  ...PHASE20_ENV_DEFAULTS,
  EGX_LIVE_ANCHOR_BOOTSTRAP: '1',
  EGX_LIVE_ANCHOR_PREVALIDATE: '1',
  EGX_P6_DELIVERED_MODE: 'historical_bootstrap',
  EGX_LRE_OOS_BOOTSTRAP: '1',
  EGX_MDE_PILOT_BACKFILL_STABILITY: '1',
  EGX_AUDIT_CLOSE_MODE: 'production',
};
