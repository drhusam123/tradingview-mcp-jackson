#!/usr/bin/env node
/**
 * Phase 84 — Feature Store (SQLite-backed, versioned)
 *
 * Sections:
 *   refresh   — Compute + store today's features for all symbols
 *   get       — Fast online feature lookup for a symbol
 *   drift     — Feature drift detection between periods
 *   lineage   — Feature lineage and provenance report
 *   report    — Full feature store report
 */
import { pythonFSRefresh, pythonFSGet, pythonFSDrift, pythonFSLineage,
         pythonFSReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const symbol  = args[args.indexOf('--symbol') + 1];
function banner(t) { console.log('\n' + '═'.repeat(62) + `\n  🗄️  ${t}\n` + '═'.repeat(62)); }

async function runRefresh() {
  banner('Feature Store Refresh — تحديث المميزات لجميع الأسهم');
  console.log('  ⏳ يحسب ويخزن مميزات اليوم...');
  const r = await pythonFSRefresh({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   ✅ Symbols: ${r.n_symbols}   Features/symbol: ${r.n_features}   Version: ${r.version}`);
  console.log(`   Total rows stored: ${r.n_rows_stored || r.n_symbols * r.n_features}`);
  console.log(`   Stored at: ${r.stored_at}`);
  if (r.saved_to) console.log(`   DB: ${r.saved_to}`);
}

async function runGet() {
  const sym = symbol || 'COMI';
  banner(`Feature Lookup — ${sym}`);
  const r = await pythonFSGet({ symbol: sym });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Symbol: ${r.symbol}   Version: ${r.version}   Age: ${r.age_days} days old`);
  console.log('\n   Features:');
  Object.entries(r.features || {}).forEach(([k, v]) =>
    console.log(`     ${String(k).padEnd(30)} ${typeof v === 'number' ? v.toFixed(4) : v}`));
}

async function runDrift() {
  banner('Feature Drift Report — كشف انجراف المميزات');
  const r = await pythonFSDrift({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Period A: ${r.period_a}   Period B: ${r.period_b}`);
  console.log(`   Verdict: ${r.verdict}\n`);
  if (r.feature_drift?.length) {
    console.log('   Feature             Drift Score   Status');
    console.log('   ' + '─'.repeat(45));
    r.feature_drift.forEach(f => {
      const flag = f.status === 'DRIFTED' ? '🔴' : '✅';
      console.log(`   ${flag} ${String(f.feature).padEnd(22)} ${String(f.drift_score?.toFixed(2)).padEnd(13)} ${f.status}`);
    });
  }
}

async function runLineage() {
  banner('Feature Lineage — مصدر وتاريخ كل مميزة');
  const r = await pythonFSLineage({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Versions: ${r.n_versions}   Features tracked: ${r.n_features_tracked}`);
  console.log(`   Period: ${r.oldest_version} → ${r.newest_version}\n`);
  r.lineage?.slice(0, 10).forEach(l =>
    console.log(`   ${String(l.feature_name || 'all').padEnd(25)} v=${l.version}  src=${l.source_table}  n=${l.n_records}`));
}

async function runReport() {
  await runDrift();
  await runLineage();
}

const SECTIONS = { refresh: runRefresh, get: runGet, drift: runDrift, lineage: runLineage, report: runReport };
const fn = SECTIONS[section];
if (!fn) { console.error(`❌ Unknown: ${section}  Available: ${Object.keys(SECTIONS).join(', ')}`); process.exit(1); }
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
