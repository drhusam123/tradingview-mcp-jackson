#!/usr/bin/env node
/**
 * Phase 63 — Explosion ML runner
 * "تعلم الآلة للتنبؤ بالانفجارات — LightGBM على 13,462 حركة"
 *
 * Sections: train | predict | evaluate | importance | status | full
 *   --symbol COMI
 *   --date 2026-05-15
 *   --top-n 20
 */
import { pythonMLTrain, pythonMLOptunaTune, pythonMLPredictToday, pythonMLPredictSymbol,
         pythonMLEvaluate, pythonMLFeatureImportance, pythonMLBuildFull,
         pythonMLShapExplain }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'status';
const symIdx  = args.indexOf('--symbol');
const symbol  = symIdx !== -1 ? args[symIdx + 1] : null;
const dateIdx = args.indexOf('--date');
const date    = dateIdx !== -1 ? args[dateIdx + 1] : new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Africa/Cairo',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}).format(new Date());
const tnIdx   = args.indexOf('--top-n');
const topN    = tnIdx !== -1 ? parseInt(args[tnIdx + 1]) : 20;
const allowStale = args.includes('--allow-stale');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🤖 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const CONV_EMOJI = { HIGH: '🔥', MED: '✅', LOW: '⚠️', NONE: '❌' };
const modelEval = r => r?.latest_evaluation ? { ...r.latest_evaluation, ...r } : (r ?? {});
const fmt = (v, digits = 4) => typeof v === 'number' ? v.toFixed(digits) : 'n/a';
const gateLabel = p => p?.client_ready ? 'FINAL' : 'ML-only';
const gateNote = 'ML scores are research inputs only; client recommendations require final_signals actionable=1.';

switch (section) {
  case 'train': {
    banner('Train LightGBM — Explosion Prediction');
    console.log('\n   Training on historical explosion events (Python bridge timeout: 10 min)...');
    const r = await pythonMLTrain({});
    if (r?.success || r?.auc_oos !== undefined || r?.n_train !== undefined) {
      console.log(`\n   ✅ Model trained!`);
      console.log(`   Train samples: ${r.n_train ?? '?'}`);
      console.log(`   OOS samples:   ${r.n_oos ?? '?'}`);
      console.log(`   AUC train:     ${fmt(r.auc_train)}`);
      console.log(`   AUC OOS:       ${fmt(r.auc_oos)}`);
      console.log(`   Precision@50:  ${fmt(r.precision_at_50)}`);
      console.log(`   Precision@10:  ${fmt(r.precision_at_10)}`);
      console.log(`   Precision@20:  ${fmt(r.precision_at_20)}`);
      console.log(`   Abstain <      ${fmt(r.decision_policy?.abstain_prob)}`);
      console.log(`   MED threshold: ${fmt(r.decision_policy?.medium_prob)}`);
      console.log(`   HIGH threshold:${fmt(r.decision_policy?.high_prob)}`);
      console.log(`   Edge allowed:  ${r.edge_claim_allowed ? 'yes' : 'no'}`);
      if (r.model_saved)           console.log(`   Model saved:   ${r.model_saved}`);
    } else pp(r);
    break;
  }
  case 'predict': {
    banner(`Explosion Predictions — Top ${topN} @ ${date}`);
    const r = symbol
      ? await pythonMLPredictSymbol({ symbol, date, allow_stale: allowStale })
      : await pythonMLPredictToday({ date, top_n: topN, allow_stale: allowStale });

    if (symbol && r?.symbol) {
      const prob = r.explosion_prob ?? r.probability ?? 0;
      const em = prob >= 0.7 ? '🔥🔥' : prob >= 0.5 ? '🔥' : prob >= 0.3 ? '⚠️' : '❌';
      console.log(`\n   ${em} ${r.symbol}`);
      console.log(`   Explosion Prob:  ${(prob * 100)?.toFixed(1)}%`);
      console.log(`   Model Tier:      ${r.model_confidence_tier ?? r.confidence_tier ?? 'LOW'}`);
      console.log(`   Client Gate:     ${gateLabel(r)} (${r.reliability_flag ?? 'UNKNOWN'})`);
      console.log(`   ML Gate Reason:  ${r.ml_gate_reason ?? 'n/a'}`);
      console.log(`   Prediction:      ${r.client_ready ? (r.prediction ?? 'FINAL-GATED ML INPUT') : 'RESEARCH ONLY'}`);
      if (r.top_features?.length) {
        console.log(`\n   Key drivers:`);
        r.top_features.slice(0, 6).forEach(f => {
          const name = typeof f === 'string' ? f : f.feature ?? f.name ?? '?';
          const val  = typeof f === 'object' ? (f.value ?? '') : '';
          console.log(`     • ${name}${val !== '' ? `  = ${typeof val === 'number' ? val?.toFixed(3) : val}` : ''}`);
        });
      }
    } else if (r?.predictions?.length || r?.top_predictions?.length) {
      const preds = r.predictions ?? r.top_predictions ?? [];
      console.log(`\n   ${preds.length} symbols with ML scores. ${gateNote}\n`);
      console.log(`   Scored: ${r.n_symbols_scored ?? '?'} | Abstained: ${r.n_abstained ?? 0} | Stored: ${r.n_stored_db ?? '?'}`);
      console.log(`   Model tiers: HIGH ${r.n_model_high ?? 0} | MED ${r.n_model_medium ?? 0} | LOW ${r.n_model_low ?? 0} | Client-ready ${r.n_client_ready ?? 0}`);
      if (r.decision_policy) {
        console.log(`   Policy: abstain<${fmt(r.decision_policy.abstain_prob)} | MED>=${fmt(r.decision_policy.medium_prob)} | HIGH>=${fmt(r.decision_policy.high_prob)}`);
      }
      console.log('\n   Symbol    Prob%    Model    Gate       Reason');
      console.log('   ' + '─'.repeat(72));
      preds.slice(0, topN).forEach(p => {
        const prob = (p.explosion_prob ?? p.probability ?? 0) * 100;
        const em = prob >= 70 ? '🔥🔥' : prob >= 50 ? '🔥' : prob >= 30 ? '⚠️' : '❌';
        const modelTier = p.model_confidence_tier ?? p.model_tier ?? p.confidence_tier ?? '';
        console.log(`   ${em} ${String(p.symbol).padEnd(8)} ${String(prob?.toFixed(1)+'%').padStart(6)}   ${String(modelTier).padEnd(7)} ${String(gateLabel(p)).padEnd(10)} ${p.ml_gate_reason ?? ''}`);
      });
    } else pp(r);
    break;
  }
  case 'evaluate': {
    banner('Evaluate Model — OOS Performance');
    const r = await pythonMLEvaluate({});
    const e = modelEval(r);
    if (e?.auc_oos !== undefined || e?.precision_at_50 !== undefined) {
      const auc = e.auc_oos ?? 0;
      const aucEm = auc >= 0.75 ? '🏆' : auc >= 0.65 ? '✅' : auc >= 0.55 ? '⚠️' : '❌';
      console.log(`\n   ${aucEm} OOS Evaluation:`);
      console.log(`   AUC OOS:        ${fmt(e.auc_oos)}`);
      console.log(`   Precision@50:   ${fmt(e.precision_at_50)}`);
      console.log(`   Precision@70:   ${fmt(e.precision_at_70)}`);
      console.log(`   Precision@10:   ${fmt(e.precision_at_10)}`);
      console.log(`   Precision@20:   ${fmt(e.precision_at_20)}`);
      console.log(`   Top10% Prec:    ${fmt(e.precision_at_top10pct)}`);
      console.log(`   Baseline Prec:  ${fmt(e.oos_baseline_precision)}`);
      console.log(`   Recall@50:      ${fmt(e.recall_at_50)}`);
      console.log(`   Abstain <       ${fmt(e.abstain_threshold)}`);
      console.log(`   MED threshold:  ${fmt(e.recommended_threshold_medium)}`);
      console.log(`   HIGH threshold: ${fmt(e.recommended_threshold_high)}`);
      console.log(`   Quality:        ${r.model_quality ?? 'UNKNOWN'}`);
      console.log(`   Edge allowed:   ${r.edge_claim_allowed ? 'yes' : 'no'}`);
      if (e.quality)        console.log(`   Evidence gate:  ${e.quality.ok ? 'passed' : 'blocked'}`);
    } else pp(r);
    break;
  }
  case 'importance': {
    banner('Feature Importance — Explosion ML');
    const r = await pythonMLFeatureImportance({});
    const top = r?.features ?? r?.top_features ?? r?.importance ?? [];
    if (top.length) {
      console.log(`\n   Top ${Math.min(top.length, 20)} features driving explosion prediction:\n`);
      console.log('   Rank  Feature                         Importance');
      console.log('   ' + '─'.repeat(55));
      const maxImp = typeof top[0] === 'object' ? (top[0].importance ?? top[0].gain ?? 1) : 1;
      top.slice(0, 20).forEach((f, i) => {
        const name = typeof f === 'string' ? f : f.feature ?? f.name ?? '?';
        const imp  = typeof f === 'object' ? (f.importance ?? f.gain ?? 0) : 0;
        const bar  = '█'.repeat(Math.round((imp / maxImp) * 20));
        console.log(`   ${String(i+1).padStart(4)}. ${String(name).padEnd(30)} ${String(imp?.toFixed(1)).padStart(8)}  ${bar}`);
      });
    } else pp(r);
    break;
  }
  case 'status': {
    banner('ML Model Status');
    const r = await pythonMLEvaluate({});
    if (r?.error?.includes('No model') || r?.error?.includes('not found') || r?.model_exists === false) {
      console.log('\n   ❌ No trained model found.');
      console.log('   Run: npm run egx:ml:train  to train the LightGBM model');
    } else if (r?.latest_evaluation || r?.auc_oos !== undefined) {
      const e = modelEval(r);
      const auc = e.auc_oos ?? 0;
      const aucEm = auc >= 0.75 ? '🏆' : auc >= 0.65 ? '✅' : '⚠️';
      console.log(`\n   ${aucEm} Model loaded and evaluated`);
      console.log(`   AUC OOS:        ${fmt(e.auc_oos)}`);
      console.log(`   Precision@50:   ${fmt(e.precision_at_50)}`);
      console.log(`   Precision@10:   ${fmt(e.precision_at_10)}`);
      console.log(`   Precision@20:   ${fmt(e.precision_at_20)}`);
      console.log(`   Quality:        ${r.model_quality ?? 'UNKNOWN'}`);
      console.log(`   Edge allowed:   ${r.edge_claim_allowed ? 'yes' : 'no'}`);
      console.log(`\n   Run: npm run egx:ml:predict  to score ML inputs (${gateNote})`);
    } else {
      console.log('\n   ℹ️  Model status unknown.');
      pp(r);
    }
    break;
  }
  case 'tune': {
    banner(`Optuna Hyperparameter Tuning — ${topN} trials`);
    console.log('\n   Running Optuna TPE search (may take 5-15 min)...');
    const r = await pythonMLOptunaTune({ n_trials: topN });
    if (r?.best_auc !== undefined) {
      console.log(`\n   ✅ Tuning complete!`);
      console.log(`   Best AUC:      ${r.best_auc}`);
      console.log(`   Trials run:    ${r.n_trials}`);
      console.log(`   Saved to:      ${r.saved_to}`);
      if (r.best_params) {
        const p = r.best_params;
        console.log(`\n   Best params:`);
        console.log(`     learning_rate:   ${p.learning_rate?.toFixed(4)}`);
        console.log(`     num_leaves:      ${p.num_leaves}`);
        console.log(`     min_data_in_leaf: ${p.min_data_in_leaf}`);
        console.log(`     feature_fraction: ${p.feature_fraction?.toFixed(2)}`);
        console.log(`     lambda_l1:       ${p.lambda_l1?.toFixed(4)}`);
      }
      if (r.top_5_trials?.length) {
        console.log('\n   Top 5 trials:');
        console.log('   Trial   AUC      LR        Leaves');
        console.log('   ' + '─'.repeat(40));
        r.top_5_trials.forEach(t => {
          console.log(`     #${String(t.trial).padEnd(4)} ${t.auc}   ${String(t.lr).padEnd(8)} ${t.leaves}`);
        });
      }
      console.log('\n   💡 Now retrain with: npm run egx:ml:train  (uses tuned params automatically)');
    } else pp(r);
    break;
  }
  case 'shap': {
    banner(`SHAP Explainability — Top ${topN} Predictions`);
    const r = await pythonMLShapExplain({ top_n: topN, symbol: symbol ?? undefined });
    if (r?.predictions?.length) {
      console.log(`\n   ${r.n_explained} symbols explained:\n`);
      console.log('   Symbol    Prob%    Top Driver                   SHAP');
      console.log('   ' + '─'.repeat(65));
      r.predictions.forEach(p => {
        const prob  = (p.probability * 100).toFixed(1);
        const d     = p.top_drivers[0] ?? {};
        const em    = p.probability >= 0.7 ? '🔥🔥' : p.probability >= 0.5 ? '🔥' : '⚠️';
        const sign  = d.shap_value > 0 ? '+' : '';
        console.log(`   ${em} ${String(p.symbol).padEnd(8)} ${String(prob+'%').padStart(6)}   ${String(d.feature ?? '?').padEnd(28)} ${sign}${d.shap_value?.toFixed(3)}`);
      });
      if (r.global_feature_importance?.length) {
        console.log('\n   🌍 Global Feature Importance (mean |SHAP|):');
        console.log('   ' + '─'.repeat(50));
        r.global_feature_importance.slice(0, 8).forEach((f, i) => {
          const bar = '█'.repeat(Math.round(f.mean_abs_shap / r.global_feature_importance[0].mean_abs_shap * 20));
          console.log(`   ${String(i+1).padStart(2)}. ${String(f.feature).padEnd(28)} ${String(f.mean_abs_shap.toFixed(4)).padStart(8)}  ${bar}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Explosion ML Full — ${date}`);
    const r = await pythonMLBuildFull({ date, top_n: topN });
    if (r?.model_status !== undefined || r?.top_predictions !== undefined) {
      const ms = r.model_status ?? {};
      console.log(`\n   Model:  ${ms.trained ? '✅ Trained' : '❌ Not trained'}`);
      if (ms.roc_auc)       console.log(`   AUC:    ${ms.roc_auc?.toFixed(4)}`);
      if (ms.precision)     console.log(`   Prec:   ${ms.precision?.toFixed(4)}`);
      const preds = r.top_predictions ?? [];
      if (preds.length) {
        console.log(`\n   🔥 Top explosion candidates today:`);
        preds.slice(0, 10).forEach(p => {
          const prob = (p.explosion_prob ?? p.probability ?? 0) * 100;
          console.log(`     ${prob >= 70 ? '🔥' : '⚠️'} ${String(p.symbol).padEnd(8)} ${prob?.toFixed(1)}%`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: train|tune|predict|evaluate|importance|shap|status|full`); process.exit(1);
}
