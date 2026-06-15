#!/usr/bin/env node
/** Phase 22 — P6 delivered graduation. */
import { loadEnv } from './lib/load_env.mjs';
import { evaluatePhase22P6Delivered } from './lib/phase22_p6_delivered.mjs';
loadEnv();
const p = evaluatePhase22P6Delivered();
console.log(JSON.stringify(p, null, 2));
process.exit(p.phase22_ready ? 0 : 1);
