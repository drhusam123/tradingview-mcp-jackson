/**
 * EGX Phase 19 — SHAP Explainability Engine (standalone runner)
 * ==============================================================
 * Trains LightGBM on counterfactual events, produces SHAP explanations
 * per-stock, and generates daily Telegram-ready insight digests.
 *
 * Usage:
 *   node scripts/egx_explain.mjs                     # daily explanations (default)
 *   node scripts/egx_explain.mjs --section train      # train LightGBM model
 *   node scripts/egx_explain.mjs --section stock       # explain specific stock
 *   node scripts/egx_explain.mjs --section importance  # global feature importance
 *   node scripts/egx_explain.mjs --section daily       # daily SHAP explanations
 *   node scripts/egx_explain.mjs --section report      # model report
 *   node scripts/egx_explain.mjs --section retrain     # retrain from scratch
 *   node scripts/egx_explain.mjs --ticker COMI         # focus on specific stock
 *   node scripts/egx_explain.mjs --notify              # send to Telegram
 */

import {
  pythonExplainTrain, pythonExplainStock, pythonExplainImportance,
  pythonExplainDaily, pythonExplainReport, pythonExplainRetrain,
} from '../src/egx/index.js';
import { sendTelegram } from '../src/egx/notify.js';

const SECTION = (() => {
  const i = process.argv.indexOf('--section');
  return i !== -1 ? process.argv[i + 1] : 'daily';
})();

const TICKER = (() => {
  const i = process.argv.indexOf('--ticker');
  return i !== -1 ? process.argv[i + 1] : null;
})();

const NOTIFY = process.argv.includes('--notify');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (n = 65)  => wl('═'.repeat(n));

sep();
wl('  🔬 EGX SHAP EXPLAINABILITY ENGINE (Phase 19)');
wl(`  ${new Date().toISOString()} | section: ${SECTION}${TICKER ? ` | ticker: ${TICKER}` : ''}`);
sep();
wl('');

const t0 = Date.now();

async function run() {
  let result;

  switch (SECTION) {
    case 'train': {
      wl('  🏋️  Training LightGBM explosion classifier...');
      wl('  Dataset: counterfactual_events | Target: explosion within 5 days');
      wl('  (Estimated: 30–120 seconds)\n');
      result = await pythonExplainTrain({ test_size: 0.2 });
      break;
    }
    case 'stock': {
      if (!TICKER) {
        wl('  ❌ --ticker required for stock explanation');
        process.exit(1);
      }
      wl(`  🧠 Explaining model decision for ${TICKER}...`);
      result = await pythonExplainStock({ ticker: TICKER });
      break;
    }
    case 'importance': {
      wl('  📊 Computing global feature importance (SHAP)...');
      result = await pythonExplainImportance({ top_n: 30 });
      break;
    }
    case 'daily': {
      wl('  📅 Generating daily SHAP explanations...');
      result = await pythonExplainDaily({ date: new Date().toISOString().slice(0, 10) });
      break;
    }
    case 'report': {
      wl('  📋 Generating model performance report...');
      result = await pythonExplainReport({});
      break;
    }
    case 'retrain': {
      wl('  🔄 Retraining model from scratch...');
      wl('  (Estimated: 60–180 seconds)\n');
      result = await pythonExplainRetrain({ test_size: 0.2, force: true });
      break;
    }
    default:
      wl(`  ❓ Unknown section: ${SECTION}`);
      process.exit(1);
  }

  if (!result || result.error) {
    wl(`  ❌ Explainability engine error: ${result?.error ?? 'no result returned'}`);
    process.exit(1);
  }

  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  // ── Display results ─────────────────────────────────────────────────────────
  switch (SECTION) {
    case 'train':
    case 'retrain': {
      wl(`  ✅ Model trained: ${elapsed}s`);
      wl(`  📊 Training samples:  ${result.n_train ?? '?'}`);
      wl(`  🧪 Test samples:      ${result.n_test ?? '?'}`);
      wl(`  🎯 AUC-ROC:           ${result.auc_roc?.toFixed(4) ?? '?'}`);
      wl(`  📈 Precision:         ${result.precision?.toFixed(4) ?? '?'}`);
      wl(`  📉 Recall:            ${result.recall?.toFixed(4) ?? '?'}`);
      wl(`  🔢 F1 Score:          ${result.f1_score?.toFixed(4) ?? '?'}`);
      wl(`  🌳 Backend:           ${result.backend ?? 'lightgbm'}`);
      const topFeats = result.top_features ?? [];
      if (topFeats.length) {
        wl('\n  🏆 Top 10 features:');
        for (const [i, f] of topFeats.slice(0, 10).entries())
          wl(`    ${(i+1).toString().padStart(2)}. ${(f.feature ?? '?').padEnd(35)} importance=${f.importance?.toFixed(4)}`);
      }
      break;
    }
    case 'stock': {
      wl(`  ✅ Stock explanation: ${elapsed}s`);
      wl(`  🏷️  Ticker: ${result.ticker ?? TICKER}`);
      wl(`  📊 Explosion probability: ${result.explosion_prob != null ? (result.explosion_prob * 100).toFixed(2) + '%' : '?'}`);
      wl(`  ⭐ Confidence: ${result.confidence ?? '?'}`);
      const factors = result.shap_factors ?? [];
      if (factors.length) {
        wl('\n  🔬 SHAP factor breakdown (top 10):');
        for (const f of factors.slice(0, 10)) {
          const dir = f.shap_value > 0 ? '▲' : '▼';
          wl(`    ${dir} ${(f.feature ?? '?').padEnd(35)} SHAP=${f.shap_value?.toFixed(4)}  val=${f.feature_value?.toFixed(4) ?? '?'}`);
        }
      }
      if (result.telegram_message) {
        wl('\n  📲 Telegram-ready message:');
        wl('  ─'.repeat(40));
        wl(result.telegram_message);
        wl('  ─'.repeat(40));
      }
      break;
    }
    case 'importance': {
      wl(`  ✅ Feature importance: ${elapsed}s`);
      const feats = result.features ?? [];
      wl(`  📊 Total features: ${feats.length}`);
      wl('\n  🏆 Top 20 most important features:');
      for (const [i, f] of feats.slice(0, 20).entries())
        wl(`    ${(i+1).toString().padStart(2)}. ${(f.feature ?? '?').padEnd(35)} SHAP=${f.mean_abs_shap?.toFixed(5)}`);
      break;
    }
    case 'daily': {
      const stocks = result.explained_stocks ?? [];
      wl(`  ✅ Daily explanations: ${elapsed}s`);
      wl(`  📅 Date: ${result.date ?? '?'}`);
      wl(`  🔬 Stocks explained: ${stocks.length}`);
      wl('\n  🏆 Top candidates by explosion probability:');
      const sorted = [...stocks].sort((a, b) => (b.explosion_prob ?? 0) - (a.explosion_prob ?? 0));
      for (const s of sorted.slice(0, 15)) {
        const prob = s.explosion_prob != null ? (s.explosion_prob * 100).toFixed(1) + '%' : '?';
        const top  = (s.shap_factors ?? []).slice(0, 2).map(f => f.feature).join(', ');
        wl(`    ${(s.ticker ?? '?').padEnd(10)} prob=${prob}  drivers=${top}`);
      }

      // Telegram notification
      if (NOTIFY && stocks.length) {
        wl('\n  📲 Sending daily SHAP digest to Telegram...');
        const now     = new Date();
        const dateStr = now.toLocaleDateString('en-GB', { weekday:'short', day:'numeric', month:'short', year:'numeric' });
        const topList = sorted.slice(0, 8).map((s, i) => {
          const prob = s.explosion_prob != null ? (s.explosion_prob * 100).toFixed(1) : '?';
          const top2 = (s.shap_factors ?? []).slice(0, 2).map(f => f.feature).join(' + ');
          return `   ${i+1}. <b>${s.ticker}</b> ${prob}%  ← ${top2}`;
        }).join('\n');

        const msg = `🔬 <b>EGX EXPLAINABILITY ENGINE — Phase 19</b>
📅 <b>${dateStr}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>TOP EXPLOSION CANDIDATES</b>
${topList}
━━━━━━━━━━━━━━━━━━━━━━━━
📊 ${stocks.length} stocks analyzed | ${elapsed}s
🤖 LightGBM + SHAP TreeExplainer`;

        try {
          await sendTelegram(msg, { parseMode: 'HTML' });
          wl('  ✅ Telegram digest sent');
        } catch (e) {
          wl(`  ⚠️  Telegram failed: ${e.message}`);
        }
      }
      break;
    }
    case 'report': {
      wl(`  ✅ Report generated: ${elapsed}s`);
      wl(`  📄 File: ${result.report_file ?? '?'}`);
      const perf = result.model_performance ?? {};
      if (perf.auc_roc != null) {
        wl('  📊 Model performance:');
        wl(`    AUC-ROC:    ${perf.auc_roc?.toFixed(4)}`);
        wl(`    Precision:  ${perf.precision?.toFixed(4)}`);
        wl(`    Recall:     ${perf.recall?.toFixed(4)}`);
        wl(`    F1:         ${perf.f1_score?.toFixed(4)}`);
        wl(`    Trained:    ${perf.trained_at ?? '?'}`);
        wl(`    Samples:    ${perf.n_samples ?? '?'}`);
      }
      break;
    }
  }
}

await run().catch(e => {
  wl(`  ❌ Fatal error: ${e.message}`);
  process.exit(1);
});

wl('');
sep();
wl('  ✅ Phase 19 SHAP Explainability Engine complete');
sep();
