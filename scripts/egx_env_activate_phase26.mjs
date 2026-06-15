#!/usr/bin/env node
/** Activate Phase 26 / full graduation env bundle. */
import { loadEnv } from './lib/load_env.mjs';
import { upsertEnvVars, PHASE26_ENV_DEFAULTS } from './lib/env_sync.mjs';
loadEnv();
const r = upsertEnvVars(PHASE26_ENV_DEFAULTS);
console.log(`\nPhase 26 env → ${r.envPath}`);
console.log(`Run: npm run egx:graduation:complete -- --apply-env\n`);
