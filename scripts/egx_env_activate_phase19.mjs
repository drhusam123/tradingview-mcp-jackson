#!/usr/bin/env node
/**
 * Activate Phase 19 operator env bundle.
 *
 * Usage: node scripts/egx_env_activate_phase19.mjs [--dry-run] [--json]
 */
import { loadEnv } from './lib/load_env.mjs';
import { upsertEnvVars, PHASE19_ENV_DEFAULTS } from './lib/env_sync.mjs';

loadEnv();

const DRY_RUN = process.argv.includes('--dry-run');
const AS_JSON = process.argv.includes('--json');

const result = upsertEnvVars(PHASE19_ENV_DEFAULTS, { dryRun: DRY_RUN });

if (AS_JSON) {
  console.log(JSON.stringify({ ...result, vars: PHASE19_ENV_DEFAULTS }, null, 2));
} else {
  console.log('\n═══ Phase 19 Env Activation ═══');
  console.log(`  Path:    ${result.envPath}`);
  console.log(`  Updated: ${result.updated.join(', ')}`);
  if (result.created.length) console.log(`  Created: ${result.created.join(', ')}`);
  console.log('\n  Re-run: npm run egx:phase19:session-ops -- --apply-env\n');
}

process.exit(0);
