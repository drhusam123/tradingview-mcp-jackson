#!/usr/bin/env node
/**
 * Phase 79 — Regime-Specific ML Models runner
 *
 * Sections:
 *   assign      — Build OHLCV-based regime labels for full history 2021-2026
 *   train       — Train separate LightGBM per regime (UP/DOWN/HIGH_VOL/CHOPPY)
 *   evaluate    — Compare regime-specific vs global model on OOS data
 *   predict     — Predict using current regime's model (auto-detect regime)
 *   adversarial — Adversarial validation: detect distribution shift 2024→2026
 *   importance  — Feature importance per regime (what matters in each state)
 *   report      — Full regime-specific research report
 */
import {
  pythonRegimeMLAssign, pythonRegimeMLTrain, pythonRegimeMLEvaluate,
  pythonRegimeMLPredict, pythonRegimeMLAdversarial, pythonRegimeMLImportance,
  pythonRegimeMLReport,
} from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(62) + `\n  🎯 ${t}\n` + '═'.repeat(62)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }
function pct(v)    { return v != null ? (v * 100).toFixed(1) + '%' : 'N/A'; }

async function runAssign() {
  banner('Regime Assignment — تصنيف كل يوم تاريخي (2021→2026)');
  const r = await pythonRegimeMLAssign({ start_date: '2021-01-01', window: 20 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   ✅ Dates labelled: ${r.n_dates}   Period: ${r.period}`);
  console.log('\n   Regime Distribution:');
  Object.entries(r.distribution || {})
    .sort(([,a],[,b]) => b - a)
    .forEach(([regime, n]) => console.log(`     ${String(regime).padEnd(20)} ${n} days`));
  if (r.saved_to) console.log(`\n   💾 ${r.saved_to}`);
}

async function runTrain() {
  banner('Training Regime-Specific Models');
  console.log('  ⏳ تدريب 4 نماذج مستقلة...');
  const r = await pythonRegimeMLTrain({ min_samples: 50, is_end: '2025-12-31', oos_start: '2026-01-30' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   ✅ Trained: ${r.regimes_trained}  Skipped: ${r.regimes_skipped}`);
  console.log(`   IS period: ${r.is_period}   OOS period: ${r.oos_period}\n`);
  console.log('   Regime           IS-samples  IS-Prec  OOS-Prec  OOS-Sig');
  console.log('   ' + '─'.repeat(58));
  r.results?.forEach(res => {
    if (res.skipped) {
      console.log(`   ⚠️  ${String(res.regime).padEnd(15)} ${res.n_is} samples — SKIPPED (${res.reason})`);
    } else {
      console.log(`   ✅ ${String(res.regime).padEnd(15)} ${String(res.n_is).padEnd(11)} ${pct(res.is_precision).padEnd(8)} ${pct(res.oos_precision).padEnd(9)} ${res.oos_signals}`);
      if (res.top_features) {
        const top2 = res.top_features.slice(0, 2).map(f => f.feature).join(', ');
        console.log(`      Top features: ${top2}`);
      }
    }
  });
}

async function runEvaluate() {
  banner('Regime vs Global Model — مقارنة الأداء');
  const r = await pythonRegimeMLEvaluate({ oos_start: '2026-01-30' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   OOS period: ${r.oos_period}`);
  console.log(`   Verdict: ${r.verdict}\n`);
  console.log('   Regime           N-OOS  Global-Prec  Regime-Prec  Lift%    Winner');
  console.log('   ' + '─'.repeat(68));
  r.results?.forEach(res => {
    if (res.skipped) {
      console.log(`   ⚠️  ${String(res.regime).padEnd(15)} N=${res.n_oos} — insufficient OOS data`);
    } else {
      const lift = res.precision_lift_pct != null ? `${res.precision_lift_pct > 0 ? '+' : ''}${res.precision_lift_pct}%` : 'N/A';
      const mark = res.winner === 'REGIME' ? '🏆' : '  ';
      console.log(`   ${mark} ${String(res.regime).padEnd(15)} ${String(res.n_oos).padEnd(6)} ${pct(res.global_precision).padEnd(12)} ${pct(res.regime_precision).padEnd(12)} ${String(lift).padEnd(8)} ${res.winner}`);
    }
  });
  console.log(`\n   Regime wins: ${r.regime_model_wins}/${(r.results||[]).filter(r=>!r.skipped).length}`);
}

async function runPredict() {
  banner('Predict with Current Regime Model');
  const r = await pythonRegimeMLPredict({ min_prob: 0.60, top_n: 20 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   🎯 Current Regime: ${r.current_regime}`);
  console.log(`   Model: ${r.model_source}   Predictions: ${r.n_predictions}\n`);
  if (r.predictions?.length) {
    console.log('   Symbol        Global-Prob  Regime-Prob  Boost');
    console.log('   ' + '─'.repeat(50));
    r.predictions.forEach(p => {
      const boost = p.regime_boost > 0 ? `+${p.regime_boost.toFixed(3)}` : p.regime_boost.toFixed(3);
      console.log(`   ${String(p.symbol).padEnd(14)} ${pct(p.global_prob).padEnd(12)} ${pct(p.regime_prob).padEnd(12)} ${boost}`);
    });
  } else {
    console.log('   No predictions above threshold');
  }
}

async function runAdversarial() {
  banner('Adversarial Validation — كشف الـ Distribution Shift');
  const r = await pythonRegimeMLAdversarial({ period_a_end: '2025-12-31', period_b_start: '2026-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Period A: ${r.period_a}  N=${r.n_period_a}`);
  console.log(`   Period B: ${r.period_b}  N=${r.n_period_b}`);
  console.log(`\n   Adversarial AUC: ${r.adversarial_auc} ± ${r.auc_std}`);
  console.log(`   Drift Level:     ${r.drift_level}`);
  console.log(`   Verdict:         ${r.verdict}`);
  console.log(`\n   Interpretation: ${r.interpretation}`);
  if (r.top_drift_features?.length) {
    console.log('\n   Most Drifted Features:');
    r.top_drift_features.forEach((f, i) =>
      console.log(`     ${i+1}. ${String(f.feature).padEnd(28)} drift_imp=${f.drift_importance}`));
  }
}

async function runImportance() {
  banner('Feature Importance Per Regime');
  const r = await pythonRegimeMLImportance({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  Object.entries(r.regime_importance || {}).forEach(([regime, data]) => {
    if (!data.available) {
      console.log(`\n   ${regime}: ❌ no model`);
      return;
    }
    console.log(`\n   ${regime}:`);
    data.top_features?.forEach((f, i) =>
      console.log(`     ${i+1}. ${String(f.feature).padEnd(28)} imp=${f.importance}`));
  });
}

async function runReport() {
  await runAssign();
  await runTrain();
  await runEvaluate();
  await runAdversarial();
}

const SECTIONS = {
  assign: runAssign, train: runTrain, evaluate: runEvaluate,
  predict: runPredict, adversarial: runAdversarial,
  importance: runImportance, report: runReport,
};

const fn = SECTIONS[section];
if (!fn) {
  console.error(`❌ Unknown section: ${section}`);
  console.error(`   Available: ${Object.keys(SECTIONS).join(', ')}`);
  process.exit(1);
}
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
