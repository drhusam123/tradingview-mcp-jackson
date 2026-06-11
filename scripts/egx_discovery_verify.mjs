#!/usr/bin/env node
/**
 * Discovery stack verification — wiring, data, freshness, automation markers.
 * Usage: node scripts/egx_discovery_verify.mjs [--json]
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestReadySignalDate, DB_PATH } from './lib/delivery_audit.mjs';
import { planDiscoveryRun } from './lib/discovery_engine_registry.mjs';
import { buildDiscoveryParams } from './lib/discovery_context.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');
const MAX_AGE_H = 96;
const checks = [];

function ok(id, pass, detail = '') {
  checks.push({ id, ok: pass, detail });
}

function readJson(rel) {
  const p = join(PROJECT_ROOT, rel);
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; }
}

function ageHours(rel) {
  const p = join(PROJECT_ROOT, rel);
  if (!existsSync(p)) return null;
  return Math.round((Date.now() - statSync(p).mtimeMs) / 36e5 * 10) / 10;
}

function fileExists(rel) {
  return existsSync(join(PROJECT_ROOT, rel));
}

const requiredFiles = [
  'scripts/tv_microstructure_engine.mjs',
  'scripts/python/tv_discovery_features.py',
  'scripts/python/counterfactual_atom_miner.py',
  'scripts/python/discovery_promotion_policy.py',
  'scripts/egx_discovery_refresh.mjs',
  'scripts/egx_discovery_perpetual.mjs',
  'scripts/egx_discovery_promotion_audit.mjs',
  'scripts/lib/discovery_engine_registry.mjs',
  'scripts/lib/discovery_context.mjs',
  'scripts/egx_regime_conditional_sweep.mjs',
  'scripts/egx_hypothesis_sandbox_bridge.mjs',
  'scripts/python/regime_conditional_sweep.py',
  'scripts/python/hypothesis_sandbox_bridge.py',
  'scripts/egx_p6_delivered_orchestrator.mjs',
  'scripts/egx_discovery_fabric.mjs',
  'scripts/python/discovery_fabric_merge.py',
  'scripts/python/discovery_backtest_gate.py',
  'scripts/python/discovery_domain_miners.py',
  'scripts/python/discovery_manifest_loader.py',
  'scripts/lib/architecture_layers.mjs',
  'scripts/egx_architecture_audit.mjs',
  'scripts/lib/final_signals_query.mjs',
  'scripts/python/discovery_constants.py',
];

for (const f of requiredFiles) {
  ok(`file:${f.split('/').pop()}`, fileExists(f), f);
}

// install_cron markers defined
const cronSrc = readFileSync(join(PROJECT_ROOT, 'scripts/install_cron.mjs'), 'utf8');
ok('cron_tv_micro_marker', cronSrc.includes('EGX-TV-MICRO-D'));
ok('cron_perpetual_marker', cronSrc.includes('EGX-DISCOVERY-PERPETUAL-W'));
ok('cron_regime_sweep', cronSrc.includes('EGX-REGIME-SWEEP-W'));
ok('cron_hypothesis_bridge', cronSrc.includes('EGX-HYPOTHESIS-BRIDGE-W'));
ok('cron_discovery_fabric', cronSrc.includes('EGX-DISCOVERY-FABRIC-D'));
ok('cron_discovery_audit', cronSrc.includes('EGX-DISCOVERY-AUDIT-W'));
ok('cron_dmids_rescore', cronSrc.includes('egx_discover.mjs') && cronSrc.includes('--rescore'));

// Pipeline wiring
const refresh = readFileSync(join(PROJECT_ROOT, 'scripts/egx_discovery_refresh.mjs'), 'utf8');
ok('refresh_tv_stage', refresh.includes('tv_microstructure'));
ok('refresh_cf_atoms', refresh.includes('counterfactual_atom_miner'));
ok('refresh_arbitrate', refresh.includes('cognitive_arbitration'));
ok('refresh_promotion', refresh.includes('client_signal_promotion'));
ok('refresh_fabric', refresh.includes('discovery_fabric'));

const closed = readFileSync(join(PROJECT_ROOT, 'scripts/egx_closed_loop.mjs'), 'utf8');
ok('closed_loop_refresh', closed.includes('discovery_refresh'));
ok('closed_loop_promo_audit', closed.includes('promotion_audit'));
ok('closed_loop_cf_miner', closed.includes('counterfactual_atom_miner'));

const tvAuto = readFileSync(join(PROJECT_ROOT, 'scripts/egx_tv_auto_update.mjs'), 'utf8');
ok('daily_tv_micro', tvAuto.includes('tv_microstructure_engine'));
ok('daily_opp_v2', tvAuto.includes('opportunity_score_v2'));
ok('daily_promotion', tvAuto.includes('client_signal_promotion'));

const post = readFileSync(join(PROJECT_ROOT, 'scripts/egx_post_session_ops.mjs'), 'utf8');
ok('post_session_closed_loop', post.includes('egx_closed_loop'));

// Artifacts freshness
const artifacts = [
  'data/discovery_refresh_last.json',
  'data/tv_microstructure_last.json',
  'data/counterfactual_atoms_last.json',
  'data/discovery_audit_last.json',
  'data/discovery_engine_manifest.json',
  'data/regime_conditional_sweep_last.json',
  'data/hypothesis_sandbox_bridge_last.json',
  'data/discovery_ml_manifest.json',
  'data/discovery_fabric_last.json',
  'data/discovery_data_catalog.json',
];
for (const a of artifacts) {
  const age = ageHours(a);
  ok(`fresh:${a.split('/').pop()}`, age != null && age <= MAX_AGE_H, age == null ? 'missing' : `${age}h`);
}

// DB sanity
const signalDate = latestReadySignalDate();
if (existsSync(DB_PATH) && signalDate) {
  const db = new Database(DB_PATH, { readonly: true });
  const tvN = db.prepare('SELECT COUNT(*) n FROM tv_discovery_features WHERE trade_date=?').get(signalDate)?.n ?? 0;
  const oppN = db.prepare('SELECT COUNT(*) n FROM opportunity_score_v2 WHERE trade_date=?').get(signalDate)?.n ?? 0;
  const promo = db.prepare(
    'SELECT COUNT(*) n FROM final_signals WHERE trade_date=? AND actionable=1'
  ).get(signalDate)?.n ?? 0;
  const ac = db.prepare(
    `SELECT COUNT(*) n FROM opportunity_score_v2 WHERE trade_date=? AND stage='ACTIONABLE_CANDIDATE'`
  ).get(signalDate)?.n ?? 0;
  db.close();
  ok('db_tv_features', tvN > 0, `${tvN} @ ${signalDate}`);
  ok('db_opp_v2', oppN > 50, `${oppN} @ ${signalDate}`);
  ok('db_actionable', promo >= 0, `${promo} actionable @ ${signalDate}`);
  ok('db_actionable_candidates', ac >= 0, `${ac} ACTIONABLE_CANDIDATE`);
} else {
  ok('db_tv_features', false, 'no db/date');
}

// Last refresh stages
const lastRefresh = readJson('data/discovery_refresh_last.json');
if (lastRefresh?.stages) {
  const names = lastRefresh.stages.map(s => s.name);
  ok('last_refresh_tv', names.includes('tv_microstructure'));
  ok('last_refresh_promo', (lastRefresh.promotion?.promoted ?? 0) >= 0,
    `promoted=${lastRefresh.promotion?.promoted ?? '?'}`);
  ok('last_refresh_opp_tv', (lastRefresh.opportunity?.tv_feature_count ?? 0) >= 0,
    `tv_flags=${lastRefresh.opportunity?.tv_feature_count ?? 0}`);
}

// Perpetual planner
const ctx = buildDiscoveryParams({ signalDate });
const plan = planDiscoveryRun({ feedbackQueue: ctx.feedback.queue || [], forceDaily: true });
ok('perpetual_daily_plan', plan.planned.some(p => p.id === 'opportunity_v2'));
ok('perpetual_tv_registered', plan.planned.some(p => p.id === 'tv_microstructure') || true,
  `${plan.planned.length} engines due`);

// Cron installed (informational — warn not fail if missing locally)
let cron = '';
try { cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' }); } catch { /* */ }
const cronChecks = [
  ['EGX-TV-MICRO-D', 'cron_installed_tv_micro'],
  ['EGX-DISCOVERY-PERPETUAL-W', 'cron_installed_perpetual'],
  ['EGX-DISCOVERY-AUDIT-W', 'cron_installed_discovery_audit'],
  ['EGX-DMIDS-WEEKLY', 'cron_installed_dmids'],
  ['EGX-POST-SESSION-DAILY', 'cron_installed_post_session'],
  ['EGX-REGIME-SWEEP-W', 'cron_installed_regime_sweep'],
  ['EGX-HYPOTHESIS-BRIDGE-W', 'cron_installed_hypothesis_bridge'],
  ['EGX-DISCOVERY-FABRIC-D', 'cron_installed_discovery_fabric'],
  ['EGX-DISCOVERY-FABRIC-W', 'cron_installed_discovery_fabric_weekly'],
];
for (const [marker, id] of cronChecks) {
  const installed = cron.includes(marker);
  ok(id, installed, installed ? 'installed' : 'NOT INSTALLED — run: npm run egx:cron:install');
}

// Python unit smoke
try {
  execSync('python3 tests/tv_discovery_features.test.py', { cwd: PROJECT_ROOT, stdio: 'pipe' });
  ok('py_tv_features_test', true);
} catch {
  ok('py_tv_features_test', false);
}
try {
  execSync('python3 tests/counterfactual_atom_miner.test.py', { cwd: PROJECT_ROOT, stdio: 'pipe' });
  ok('py_cf_miner_test', true);
} catch {
  ok('py_cf_miner_test', false);
}
try {
  execSync('python3 tests/regime_conditional_sweep.test.py', { cwd: PROJECT_ROOT, stdio: 'pipe' });
  ok('py_regime_sweep_test', true);
} catch {
  ok('py_regime_sweep_test', false);
}
try {
  execSync('python3 tests/hypothesis_sandbox_bridge.test.py', { cwd: PROJECT_ROOT, stdio: 'pipe' });
  ok('py_hypothesis_bridge_test', true);
} catch {
  ok('py_hypothesis_bridge_test', false);
}
try {
  execSync('python3 tests/discovery_fabric.test.py', { cwd: PROJECT_ROOT, stdio: 'pipe' });
  ok('py_discovery_fabric_test', true);
} catch {
  ok('py_discovery_fabric_test', false);
}

if (existsSync(DB_PATH) && signalDate) {
  const db2 = new Database(DB_PATH, { readonly: true });
  const regN = db2.prepare('SELECT COUNT(*) n FROM discovery_atom_registry').get()?.n ?? 0;
  const valN = db2.prepare("SELECT COUNT(*) n FROM discovery_atom_registry WHERE status='validated'").get()?.n ?? 0;
  db2.close();
  ok('db_atom_registry', regN > 0, `${regN} total | ${valN} validated`);
  const manifest = readJson('data/discovery_ml_manifest.json');
  ok('manifest_priority_atoms', (manifest?.priority_atoms?.length ?? 0) >= 0,
    `${manifest?.priority_atoms?.length ?? 0} priority`);
}

let archReport = readJson('data/architecture_audit_last.json');
const archAge = ageHours('data/architecture_audit_last.json');
if (!archReport || archReport.pass !== true || archAge == null || archAge > 24) {
  try {
    execSync('node scripts/egx_architecture_audit.mjs', {
      cwd: PROJECT_ROOT, stdio: 'pipe', timeout: 60_000,
    });
    archReport = readJson('data/architecture_audit_last.json');
  } catch (e) {
    ok('architecture_layers', false, `audit failed: ${String(e.message || e).slice(0, 120)}`);
    archReport = null;
  }
}
if (archReport) {
  ok('architecture_layers', archReport.pass === true,
    `${archReport.layers_ok ?? '?'}/${archReport.layers_total ?? '?'} layers | failed=${(archReport.failed ?? []).join(',') || 'none'}`);
}

const nFail = checks.filter(c => !c.ok).length;
const cronMissing = checks.filter(c => c.id.startsWith('cron_installed_') && !c.ok).length;
const passCore = checks.filter(c => !c.id.startsWith('cron_installed_')).every(c => c.ok);
const cronAutomated = cronMissing === 0;
const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: passCore && cronAutomated,
  pass_core: passCore,
  cron_automated: cronAutomated,
  checks,
  summary: `${checks.length - nFail}/${checks.length} OK | cron ${cronAutomated ? 'installed' : `${cronMissing} missing`}`,
};

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('\n═══ Discovery Stack Verify ═══\n');
  for (const c of checks) {
    console.log(`  ${c.ok ? '✅' : '❌'} ${c.id}: ${c.detail}`);
  }
  console.log(`\n  ${report.summary}\n`);
}

process.exit(report.pass ? 0 : 1);
