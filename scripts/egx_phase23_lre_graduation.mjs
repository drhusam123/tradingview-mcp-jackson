#!/usr/bin/env node
/** Phase 23 — LRE OOS graduation. */
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { evaluatePhase23LreGraduation } from './lib/phase23_lre_graduation.mjs';
import { execSync } from 'child_process';
loadEnv();
try { execSync('npm run egx:lre:status', { cwd: PROJECT_ROOT, stdio: 'inherit' }); } catch { /* */ }
const p = evaluatePhase23LreGraduation();
console.log(JSON.stringify(p, null, 2));
process.exit(p.phase23_ready ? 0 : 1);
