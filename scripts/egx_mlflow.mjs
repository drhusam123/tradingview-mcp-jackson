#!/usr/bin/env node
/**
 * Phase 81 — MLflow Experiment Tracking
 *
 * Sections:
 *   init      — Initialize MLflow experiments
 *   log       — Log a full training run to MLflow
 *   log_regime — Log all regime-specific models to MLflow
 *   compare   — Compare last N runs by OOS precision
 *   register  — Register best model to Model Registry
 *   report    — Full MLflow status report
 */
import { pythonMLflowInit, pythonMLflowLogRun, pythonMLflowLogRegime,
         pythonMLflowCompare, pythonMLflowRegister, pythonMLflowReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
function banner(t) { console.log('\n' + '═'.repeat(62) + `\n  📊 ${t}\n` + '═'.repeat(62)); }
function pct(v) { return v != null ? (v*100).toFixed(1)+'%' : 'N/A'; }

async function runInit() {
  banner('MLflow Init — تهيئة تتبع التجارب');
  const r = await pythonMLflowInit({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   ✅ MLruns path: ${r.mlruns_path || r.tracking_uri}`);
  const exps = r.experiments;
  if (exps && typeof exps === 'object') {
    Object.entries(exps).forEach(([name, e]) =>
      console.log(`   Experiment: ${name}  ID: ${e.id}`));
  }
}

async function runLog() {
  banner('MLflow Log Run — تسجيل تجربة تدريب جديدة');
  console.log('  ⏳ تدريب وتسجيل...');
  const r = await pythonMLflowLogRun({ is_end: '2025-12-31', oos_start: '2026-01-30' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   ✅ Run ID: ${r.run_id}`);
  console.log(`   IS precision:  ${pct(r.is_precision)}  (${r.n_is} samples)`);
  console.log(`   OOS precision: ${pct(r.oos_precision)}  (${r.oos_signals} signals)`);
  console.log(`   MLflow URL:    ${r.mlflow_url || 'http://localhost:5000'}`);
}

async function runLogRegime() {
  banner('MLflow Log Regime Models');
  const r = await pythonMLflowLogRegime({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  r.runs?.forEach(run => console.log(`   ${String(run.regime).padEnd(20)} run_id=${run.run_id}  oos=${pct(run.oos_precision)}`));
}

async function runCompare() {
  banner('MLflow Compare Runs — مقارنة التجارب');
  const r = await pythonMLflowCompare({ n: 10 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log('\n   Run                              OOS-Prec  IS-Prec   Date');
  console.log('   ' + '─'.repeat(65));
  r.runs?.forEach(run => {
    console.log(`   ${String(run.run_name || run.run_id?.slice(0,8)).padEnd(33)} ${pct(run.oos_precision).padEnd(9)} ${pct(run.is_precision).padEnd(9)} ${run.start_time || ''}`);
  });
}

async function runRegister() {
  banner('MLflow Register Best Model');
  const r = await pythonMLflowRegister({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   ✅ Registered: ${r.model_name} v${r.model_version}`);
  console.log(`   Best run OOS: ${pct(r.oos_precision)}`);
}

async function runReport() {
  await runInit();
  await runCompare();
}

const SECTIONS = { init: runInit, log: runLog, log_regime: runLogRegime,
                   compare: runCompare, register: runRegister, report: runReport };
const fn = SECTIONS[section];
if (!fn) { console.error(`❌ Unknown: ${section}  Available: ${Object.keys(SECTIONS).join(', ')}`); process.exit(1); }
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
