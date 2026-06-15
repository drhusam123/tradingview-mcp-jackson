#!/usr/bin/env node
/**
 * Activate Phase 14 operator env bundle in .env (local only, never committed).
 *
 * Usage: node scripts/egx_env_activate_phase14.mjs [--dry-run] [--json]
 */
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { upsertEnvVars, PHASE14_ENV_DEFAULTS } from './lib/env_sync.mjs';

const DRY_RUN = process.argv.includes('--dry-run');
const AS_JSON = process.argv.includes('--json');

loadEnv();

const result = upsertEnvVars(PHASE14_ENV_DEFAULTS, { dryRun: DRY_RUN });

for (const [k, v] of Object.entries(PHASE14_ENV_DEFAULTS)) {
  process.env[k] = v;
}

const out = {
  ok: true,
  dry_run: DRY_RUN,
  env_path: result.envPath,
  updated: result.updated,
  created: result.created,
  vars: PHASE14_ENV_DEFAULTS,
};

if (AS_JSON) {
  console.log(JSON.stringify(out, null, 2));
} else {
  console.log('\n═══ Phase 14 Env Activation ═══');
  console.log(`  File: ${result.envPath}`);
  console.log(`  Mode: ${DRY_RUN ? 'dry-run' : 'applied'}\n`);
  for (const [k, v] of Object.entries(PHASE14_ENV_DEFAULTS)) {
    console.log(`  ${k}=${v}`);
  }
  console.log('\n  Re-run: npm run egx:phase14:graduation\n');
}

process.exit(0);
