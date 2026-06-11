#!/usr/bin/env node
/**
 * Discovery refresh — opp map + score + promotion (no full DMIDS).
 * Runs after closed loop or post-session to apply P6-tuned discovery.
 */
import { execFileSync } from 'child_process';

const NODE = process.execPath;
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { buildDiscoveryParams } from './lib/discovery_context.mjs';
import { latestReadySignalDate } from './lib/delivery_audit.mjs';
import { runDiscoveryQualityLoop } from './lib/discovery_quality_loop.mjs';
import { mergeStructuralLawsIntoRuntime } from './lib/structural_laws_bridge.mjs';
import { writeFileSync, mkdirSync } from 'fs';
import { PROJECT_ROOT } from './lib/load_env.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';

const PY = (script, ...args) => {
  const out = execFileSync(PYTHON3, [join(ROOT, script), ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    timeout: 1000 * 60 * 25,
  });
  return parsePythonJson(out);
};

const signalDate = latestReadySignalDate();
if (!signalDate) {
  console.error(JSON.stringify({ success: false, error: 'NO_OHLCV_DATE' }));
  process.exit(1);
}

const ctx = buildDiscoveryParams({ signalDate });
const paramsJson = JSON.stringify(ctx.params);
const scoreParams = JSON.stringify({ date: signalDate });
const promoParams = JSON.stringify({ date: signalDate, ...ctx.params });

const report = { signal_date: signalDate, at: new Date().toISOString(), stages: [] };

function stage(name, fn) {
  const t0 = Date.now();
  try {
    const result = fn();
    report.stages.push({ name, ok: true, ms: Date.now() - t0, result });
    return result;
  } catch (e) {
    report.stages.push({ name, ok: false, ms: Date.now() - t0, error: e.message?.slice(0, 200) });
    throw e;
  }
}

console.log(`\n═══ Discovery Refresh (tv → opp → score → promote) ═══`);
console.log(`  Date: ${signalDate} | feedback=${ctx.feedback.n_items} | strict=${ctx.params.strict_quality}\n`);

stage('tv_microstructure', () => {
  try {
    execFileSync(NODE, [join(ROOT, 'scripts/tv_microstructure_engine.mjs'), '--local-only', '--max-symbols', '30', '--date', signalDate], {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 900_000,
      stdio: 'pipe',
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, skipped: true, error: e.message?.slice(0, 120) };
  }
});

stage('counterfactual_atoms', () =>
  PY('scripts/python/counterfactual_atom_miner.py', JSON.stringify({ date: signalDate })));

stage('discovery_fabric', () => {
  try {
    execFileSync(NODE, [join(ROOT, 'scripts/egx_discovery_fabric.mjs')], {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 900_000,
      stdio: 'pipe',
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message?.slice(0, 120) };
  }
});

const opp = stage('opportunity_score_v2', () =>
  PY('scripts/python/opportunity_score_v2.py', 'run', paramsJson));
console.log(`  ✅ Opp v2: ${opp.symbols_scored} scored | qualified+ ${opp.qualified_plus ?? '?'}`);

const score = stage('score_all', () =>
  PY('scripts/python/signal_integration.py', 'score_all', scoreParams));
console.log(`  ✅ UES: scored=${score.n_scored ?? '?'} high=${score.n_high ?? '?'}`);

stage('cognitive_arbitration', () =>
  PY('scripts/python/cognitive_arbitration.py', 'arbitrate_all', '{}'));
stage('apply_arbitration_veto', () =>
  PY('scripts/python/signal_integration.py', 'apply_arbitration_veto', scoreParams));

const promo = stage('client_signal_promotion', () =>
  PY('scripts/python/client_signal_promotion.py', promoParams));
console.log(`  ✅ Promotion: ${promo.promoted ?? 0} actionable`);

let structural = null;
try {
  structural = mergeStructuralLawsIntoRuntime({ minSupportPct: 28 });
} catch { /* optional */ }

const quality = stage('discovery_quality', () => runDiscoveryQualityLoop(signalDate));
console.log(`  📊 Discovery quality: ${quality.discovery_quality_score}% (${quality.grade})`);

report.opportunity = opp;
report.score = score;
report.promotion = promo;
report.discovery_quality = quality;
report.structural_laws = structural;

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/discovery_refresh_last.json'), JSON.stringify(report, null, 2));

const failed = report.stages.filter(s => !s.ok).length;
console.log(failed ? '\n❌ Discovery refresh had failures\n' : '\n═══ Discovery Refresh OK ═══\n');
process.exit(failed ? 1 : 0);
