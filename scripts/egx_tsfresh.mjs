#!/usr/bin/env node
/**
 * Phase 77 — tsfresh Automated Feature Extraction runner
 *
 * Sections:
 *   symbols      — Extract features for today's top ML predictions
 *   explosions   — Extract + select features for all explosive_moves events
 *   compare      — Compare tsfresh vs manual features by mutual information
 *   report       — Quick report (compare + symbols)
 */
import { pythonTsfreshSymbols, pythonTsfreshExplosions, pythonTsfreshCompare,
         pythonTsfreshReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  📊 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

async function runSymbols() {
  banner('tsfresh — استخراج المميزات للأسهم الأعلى احتمالاً');
  const r = await pythonTsfreshSymbols({ min_prob: 0.65, max_syms: 20, lookback: 20 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   ✅ Symbols: ${r.n_symbols}  Features total: ${r.n_features_total}  Top: ${r.n_features_top}`);
  if (r.top_feature_names) {
    console.log('\n   Top Features by Variance:');
    r.top_feature_names.forEach((f, i) => console.log(`     ${i+1}. ${f}`));
  }
  if (r.symbol_features?.length) {
    console.log('\n   Sample Symbol Features:');
    r.symbol_features.slice(0, 5).forEach(s => {
      console.log(`\n   ${s.symbol}:`);
      Object.entries(s).filter(([k]) => k !== 'symbol').slice(0, 5).forEach(([k, v]) =>
        console.log(`     ${String(k).padEnd(40)} ${v}`));
    });
  }
}

async function runExplosions() {
  banner('tsfresh — استخراج مميزات الانفجارات (بطيء — 3-10 دقائق)');
  console.log('  ⏳ يستغرق وقتاً...');
  const r = await pythonTsfreshExplosions({ lookback: 20, max_events: 500, save_model: true });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   ✅ Events: ${r.n_events}`);
  console.log(`   Features extracted: ${r.n_features_total}`);
  console.log(`   Features selected:  ${r.n_features_selected}`);
  if (r.selected_features?.length) {
    console.log('\n   Top Selected Features:');
    r.selected_features.forEach((f, i) => console.log(`     ${i+1}. ${f}`));
  }
  if (r.saved_to) console.log(`\n   💾 ${r.saved_to}`);
}

async function runCompare() {
  banner('tsfresh vs Manual Features — مقارنة المعلومات المشتركة');
  const r = await pythonTsfreshCompare({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Events: ${r.n_events}   Top 3: ${r.top_3?.join(', ')}`);
  if (r.feature_importance?.length) {
    console.log('\n   Manual Feature Importance (Mutual Information with Explosion):');
    r.feature_importance.forEach((f, i) =>
      console.log(`     ${i+1}. ${String(f.feature).padEnd(30)} MI=${f.mutual_info}`));
  }
}

async function runReport() {
  await runCompare();
  await runSymbols();
}

const SECTIONS = { symbols: runSymbols, explosions: runExplosions,
                   compare: runCompare, report: runReport };

const fn = SECTIONS[section];
if (!fn) {
  console.error(`❌ Unknown section: ${section}`);
  console.error(`   Available: ${Object.keys(SECTIONS).join(', ')}`);
  process.exit(1);
}
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
