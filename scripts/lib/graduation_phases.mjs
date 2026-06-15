/**
 * Phase 21–26 — shared graduation utilities.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

export function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

export function writeJson(name, payload) {
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data', name), JSON.stringify(payload, null, 2));
  return payload;
}

export const PHASE_SCRIPTS = {
  21: 'egx_phase21_live_anchor.mjs',
  22: 'egx_phase22_p6_delivered.mjs',
  23: 'egx_phase23_lre_graduation.mjs',
  24: 'egx_phase24_med_ab_graduation.mjs',
  25: 'egx_phase25_mde_memory.mjs',
  26: 'egx_phase26_audit_close.mjs',
};
