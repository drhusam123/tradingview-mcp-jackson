#!/usr/bin/env node
/**
 * Perpetual discovery orchestrator — infinite loops with guardrails.
 *
 * Reads discovery_feedback + engine manifest → runs due engines → updates manifest.
 * Does NOT replace egx_discover (DMIDS); complements it with cadence + triggers.
 *
 * Usage:
 *   npm run egx:discovery:perpetual           # plan + run due engines
 *   npm run egx:discovery:perpetual -- --dry  # plan only
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './lib/load_env.mjs';
import { buildDiscoveryParams } from './lib/discovery_context.mjs';
import { planDiscoveryRun, readEngineManifest, DISCOVERY_ENGINES } from './lib/discovery_engine_registry.mjs';
import { runDiscoveryQualityLoop } from './lib/discovery_quality_loop.mjs';
import { latestReadySignalDate } from './lib/delivery_audit.mjs';

const DRY = process.argv.includes('--dry');
const tierArg = (() => {
  const i = process.argv.indexOf('--tier');
  return i >= 0 ? process.argv[i + 1] : 'daily';
})();
const NODE = process.execPath;
const signalDate = latestReadySignalDate();

const ctx = buildDiscoveryParams({ signalDate });
const { planned: allPlanned } = planDiscoveryRun({ feedbackQueue: ctx.feedback.queue || [] });
const tierOrder = { daily: 0, weekly: 1, research: 2, intraday: 3 };
const maxTier = tierOrder[tierArg] ?? 0;
const planned = allPlanned.filter(p => (tierOrder[p.layer] ?? 9) <= maxTier);

console.log('\n═══ Discovery Perpetual Orchestrator ═══');
console.log(`  Signal: ${signalDate} | tier=${tierArg} | feedback=${ctx.feedback.n_items} | planned=${planned.length}\n`);

if (!planned.length) {
  console.log('  Nothing due — all engines within cadence.\n');
  process.exit(0);
}

for (const p of planned) {
  console.log(`  ▶ ${p.id} (${p.reason}) → ${p.npm}`);
}

if (DRY) {
  console.log('\n  --dry: plan only, no execution.\n');
  process.exit(0);
}

const manifest = readEngineManifest();
const results = [];

for (const p of planned) {
  const t0 = Date.now();
  let ok = true;
  let error = null;
  try {
    execSync(`npm run ${p.npm}`, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: p.layer === 'research' ? 1_800_000 : 900_000,
      env: { ...process.env, FORCE_COLOR: '0' },
    });
  } catch (e) {
    ok = false;
    error = e.message?.slice(0, 200);
    console.log(`  ⚠️  ${p.id} failed: ${error}`);
  }
  manifest.engines = manifest.engines || {};
  manifest.engines[p.id] = {
    last_run_at: new Date().toISOString(),
    last_ok: ok,
    last_ms: Date.now() - t0,
    reason: p.reason,
    error,
  };
  results.push({ id: p.id, ok, ms: Date.now() - t0, error });
}

manifest.at = new Date().toISOString();
manifest.last_signal_date = signalDate;
manifest.registry_version = Object.keys(DISCOVERY_ENGINES).length;

let quality = null;
try {
  quality = runDiscoveryQualityLoop(signalDate);
} catch { /* optional */ }

const report = {
  at: manifest.at,
  signal_date: signalDate,
  planned: planned.map(p => p.id),
  results,
  discovery_quality: quality,
  feedback_items: ctx.feedback.n_items,
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/discovery_engine_manifest.json'), JSON.stringify(manifest, null, 2));
writeFileSync(join(PROJECT_ROOT, 'data/discovery_perpetual_last.json'), JSON.stringify(report, null, 2));

const failed = results.filter(r => !r.ok).length;
console.log(`\n═══ Perpetual run done: ${results.length - failed}/${results.length} OK ═══\n`);
process.exit(failed ? 1 : 0);
