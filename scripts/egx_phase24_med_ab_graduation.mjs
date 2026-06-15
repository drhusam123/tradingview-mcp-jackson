#!/usr/bin/env node
/** Phase 24 — MED A/B graduation. */
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { evaluatePhase24MedAbGraduation } from './lib/phase24_med_ab_graduation.mjs';
import { execSync } from 'child_process';
loadEnv();
const PYTHON = process.env.PYTHON_BIN || 'python3';
try {
  execSync(`"${PYTHON}" scripts/python/med_feed_ab_pilot.py '{"backfill_historical":true}'`, { cwd: PROJECT_ROOT });
} catch { /* */ }
const p = evaluatePhase24MedAbGraduation();
console.log(JSON.stringify(p, null, 2));
process.exit(p.phase24_ready ? 0 : 1);
