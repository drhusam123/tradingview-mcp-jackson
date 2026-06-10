#!/usr/bin/env node
/**
 * Phase 80 — Triple Barrier Labeling + Meta Labeling + Purged CV
 *
 * Sections:
 *   label       — Triple barrier labeling for all explosive_moves events
 *   meta        — Meta-labeling: when to trust the primary ML model
 *   purged_cv   — Purged K-Fold Cross Validation (leakage-free)
 *   stability   — Feature importance stability across CV folds
 *   bet_sizing  — Kelly-based bet sizing from barrier results
 *   report      — Full MLFinLab-style research report
 */
import { pythonTBLabel, pythonTBMetaLabel, pythonTBPurgedCV, pythonTBStability,
         pythonTBBetSizing, pythonTBReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(62) + `\n  🎯 ${t}\n` + '═'.repeat(62)); }
function pct(v)    { return v != null ? (v*100).toFixed(1)+'%' : 'N/A'; }

async function runLabel() {
  banner('Triple Barrier Labeling — تصنيف الأحداث بالحواجز الثلاثة');
  console.log('  ⏳ يعالج 13,000+ حدث...');
  const r = await pythonTBLabel({ upper_pct: 0.07, lower_pct: 0.04, max_holding_days: 10, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  const nLabeled = r.total_labeled ?? r.n_labeled ?? 0;
  const nSkipped = r.skipped ?? r.n_skipped ?? 0;
  console.log(`\n   ✅ Events labeled: ${nLabeled}  Skipped: ${nSkipped}`);
  if (r.distribution) {
    const d = r.distribution;
    const upper  = d['upper_hit (+1)'] ?? d.upper  ?? 0;
    const lower  = d['lower_hit (-1)'] ?? d.lower  ?? 0;
    const timeout= d['time_stop (0)']  ?? d.timeout ?? 0;
    console.log('\n   Label Distribution:');
    console.log(`     +1 Upper barrier hit (profit):  ${upper}   (${d.upper_hit_pct?.toFixed(1) ?? pct(upper/nLabeled)}%)`);
    console.log(`     -1 Lower barrier hit (loss):    ${lower}   (${d.lower_hit_pct?.toFixed(1) ?? pct(lower/nLabeled)}%)`);
    console.log(`      0 Time stop (vertical):        ${timeout} (${d.time_stop_pct?.toFixed(1) ?? pct(timeout/nLabeled)}%)`);
  }
  if (r.saved_to) console.log(`\n   💾 ${r.saved_to}`);
}

async function runMeta() {
  banner('Meta Labeling — متى تثق بنموذج ML الأساسي؟');
  const r = await pythonTBMetaLabel({ threshold: 0.5, is_end: '2025-12-31', oos_start: '2026-01-30' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  // Field names: primary_precision_is/oos, meta_precision_is/oos, lift_oos
  const primaryIS  = r.primary_precision_is  ?? r.primary_is_prec;
  const primaryOOS = r.primary_precision_oos ?? r.primary_oos_prec;
  const metaIS     = r.meta_precision_is     ?? r.meta_is_prec;
  const metaOOS    = r.meta_precision_oos    ?? r.meta_oos_prec;
  const liftOOS    = r.lift_oos              ?? r.lift_pct;
  console.log(`\n   Primary model:  IS=${pct(primaryIS)} | OOS=${pct(primaryOOS)}`);
  console.log(`   Meta model:     IS=${pct(metaIS)}    | OOS=${pct(metaOOS)}`);
  if (liftOOS != null) console.log(`   Lift vs primary (OOS): ${liftOOS > 0 ? '+' : ''}${(liftOOS*100).toFixed(1)}%`);
  if (r.top_features?.length) {
    console.log('\n   Meta Model Top Features:');
    r.top_features.forEach((f,i) => {
      // top_features is array of [name, importance] pairs or {feature, importance} dicts
      const [name, imp] = Array.isArray(f) ? f : [f.feature, f.importance];
      console.log(`     ${i+1}. ${String(name).padEnd(28)} imp=${typeof imp === 'number' ? imp.toFixed(0) : imp}`);
    });
  }
  const savedTo = r.meta_model_saved ?? r.saved_to;
  if (savedTo) console.log(`\n   💾 Meta model: ${savedTo}`);
}

async function runPurgedCV() {
  banner('Purged K-Fold CV — اختبار متقاطع بدون تسرب زمني');
  const r = await pythonTBPurgedCV({ n_splits: 5, embargo_days: 30, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  // Actual fields: n_folds, purged_mean_precision, purged_std_precision,
  //                standard_kfold_mean_precision, leakage_delta, per_fold
  const nSplits = r.n_folds     ?? r.n_splits;
  const mean    = r.purged_mean_precision ?? r.mean_precision;
  const std     = r.purged_std_precision  ?? r.std_precision;
  const stdCV   = r.standard_kfold_mean_precision ?? r.standard_cv_precision;
  const delta   = r.leakage_delta ?? r.leakage_pct;
  console.log(`\n   Splits: ${nSplits}  Embargo: ${r.embargo_days} days`);
  console.log(`   Purged CV Precision:  ${pct(mean)} ± ${pct(std)}`);
  if (stdCV != null) console.log(`   Standard CV (leaky): ${pct(stdCV)} (potential leakage bias)`);
  if (delta != null) console.log(`   Leakage delta:       ${delta > 0 ? '+' : ''}${(delta*100).toFixed(2)}% (purged - standard)`);
  const folds = r.per_fold ?? r.fold_results ?? [];
  if (folds.length) {
    console.log('\n   Per-Fold Results:');
    folds.forEach((f,i) => console.log(`     Fold ${f.fold ?? i+1}: precision=${pct(f.precision)}  n_test=${f.n_test}  signals=${f.n_signals ?? 'N/A'}`));
  }
}

async function runStability() {
  banner('Feature Importance Stability — ثبات المميزات عبر الـ Folds');
  const r = await pythonTBStability({ n_splits: 5, embargo_days: 30 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  // Actual structure: stable_features = string[], feature_stability = [{feature, mean_importance, cv, stable}]
  const detail = r.feature_stability ?? [];
  const stableNames = new Set(Array.isArray(r.stable_features) ? r.stable_features : []);
  const stableDetail = detail.filter(f => f.stable !== false && stableNames.has(f.feature));
  const unstableDetail = detail.filter(f => f.stable === false || (!stableNames.has(f.feature) && detail.length > 0));
  if (stableDetail.length || stableNames.size) {
    console.log(`\n   ✅ Stable Features (CV < ${(r.stability_threshold ?? 0.3)*100}%):  ${stableNames.size} total`);
    (stableDetail.length ? stableDetail : [...stableNames].map(f => ({feature:f}))).forEach((f,i) =>
      console.log(`     ${i+1}. ${String(f.feature).padEnd(28)} mean=${f.mean_importance != null ? f.mean_importance.toFixed(0) : 'N/A'}  cv=${f.cv != null ? (f.cv*100).toFixed(1) : 'N/A'}%`));
  }
  if (unstableDetail.length || (r.unstable_features?.length)) {
    const unstableList = unstableDetail.length ? unstableDetail : (r.unstable_features ?? []).map(f => ({feature: f}));
    console.log('\n   ⚠️  Unstable Features (CV > 30%):');
    unstableList.forEach(f => console.log(`     ${String(f.feature ?? f).padEnd(28)} cv=${f.cv != null ? (f.cv*100).toFixed(1) : 'N/A'}%`));
  }
  if (!stableDetail.length && !stableNames.size) console.log('   No feature data available.');
}

async function runBetSizing() {
  banner('Kelly Bet Sizing — حجم المركز المثالي');
  const r = await pythonTBBetSizing({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Win Rate:    ${pct(r.win_rate)}`);
  console.log(`   Payoff (b):  ${(r.payoff_ratio_b || r.payoff_ratio)?.toFixed(2)}x  (avg_win/avg_loss)`);
  console.log(`   Full Kelly:  ${pct(r.kelly_fraction || r.kelly_f)}`);
  console.log(`   Half Kelly:  ${pct(r.half_kelly)} ← recommended`);
  console.log(`   EV/trade:    ${(r.expected_value_per_trade || r.ev_per_trade)?.toFixed(3)}`);
  if (r.recommendation) console.log(`\n   📌 ${r.recommendation}`);
  const regime_sizing = r.by_regime || r.regime_sizing;
  if (regime_sizing && Object.keys(regime_sizing).length > 1) {
    console.log('\n   Regime-Specific Sizing:');
    Object.entries(regime_sizing).forEach(([regime, s]) =>
      console.log(`     ${String(regime).padEnd(20)} win=${pct(s.win_rate)} kelly=${pct(s.half_kelly)}`));
  }
}

async function runReport() {
  await runLabel();
  await runMeta();
  await runBetSizing();
}

const SECTIONS = { label: runLabel, meta: runMeta, purged_cv: runPurgedCV,
                   stability: runStability, bet_sizing: runBetSizing, report: runReport };
const fn = SECTIONS[section];
if (!fn) { console.error(`❌ Unknown: ${section}  Available: ${Object.keys(SECTIONS).join(', ')}`); process.exit(1); }
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
