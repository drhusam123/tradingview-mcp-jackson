#!/usr/bin/env node
/** Phase 25 — MDE behavior memory graduation. */
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { evaluatePhase25MdeMemory } from './lib/phase25_mde_memory.mjs';
import { execSync } from 'child_process';
loadEnv();
const PYTHON = process.env.PYTHON_BIN || 'python3';
try {
  execSync(`"${PYTHON}" scripts/python/mde_pilot_stability.py '{"backfill_stability":true}'`, { cwd: PROJECT_ROOT });
} catch { /* */ }
const p = evaluatePhase25MdeMemory();
console.log(JSON.stringify(p, null, 2));
process.exit(p.phase25_ready ? 0 : 1);
