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
