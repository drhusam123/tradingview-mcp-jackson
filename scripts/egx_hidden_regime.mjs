#!/usr/bin/env node
/**
 * Phase 75 — Hidden Regime Detection (HMM) runner
 *
 * Sections:
 *   fit          — Train GaussianHMM on market features (312+ days)
 *   detect       — Detect current market regime
 *   history      — Show regime sequence over last N days
 *   correlation  — Explosions-per-regime-day table
 *   report       — Full HMM report
 */
import { pythonHMMFit, pythonHMMDetect, pythonHMMExplosionCorr } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧠 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

async function runFit() {
  banner('HMM — تدريب النموذج على بيانات السوق');
  const r = await pythonHMMFit({ n_states: 6, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   ✅ States: ${r.n_states}  Days: ${r.n_days}  Converged: ${r.converged}`);
  console.log(`   Period: ${r.period}`);
  if (r.state_labels) {
    console.log('\n   State Labels:');
    Object.entries(r.state_labels).forEach(([k, v]) =>
      console.log(`     State ${k}: ${v}`));
  }
  if (r.state_distribution) {
    console.log('\n   State Distribution:');
    Object.entries(r.state_distribution).forEach(([k, v]) =>
      console.log(`     ${k}: ${v}%`));
  }
  if (r.saved_to) console.log(`\n   💾 Saved: ${r.saved_to}`);
}

async function runDetect() {
  banner('HMM — الكشف عن النظام الحالي');
  const r = await pythonHMMDetect({ lookback_days: 30 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   🎯 Current Regime: ${r.emoji || ''} ${r.hidden_regime}`);
  console.log(`   📅 As of: ${r.current_date}`);
  console.log(`   🔢 State ID: ${r.state_id}`);
  if (r.state_features) {
    console.log('\n   State Features:');
    Object.entries(r.state_features).forEach(([k, v]) =>
      console.log(`     ${String(k).padEnd(25)} ${v}`));
  }
  if (r.regime_context) {
    const ctx = r.regime_context;
    if (ctx.label) console.log(`\n   Context: ${ctx.label}  Trend: ${ctx.trend || '?'}  Vol: ${ctx.volatility || '?'}`);
  }
  if (r.recent_7d?.length) {
    const states = r.recent_7d.map(d => typeof d === 'object' ? (d.label || d.regime || JSON.stringify(d)) : d);
    console.log('\n   Recent 7 states: ' + states.join(' → '));
  }
}

async function runHistory() {
  banner('HMM — تاريخ الأنظمة (90 يوم)');
  const r = await pythonHMMDetect({ lookback_days: 90 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  if (r.recent_7d?.length) {
    const states = r.recent_7d.map(d => typeof d === 'object' ? (d.label || d.regime || JSON.stringify(d)) : d);
    console.log('  Recent 7 states: ' + states.join(' → '));
  }
  console.log(`\n  Current (${r.current_date}): ${r.emoji || ''} ${r.hidden_regime}`);
}

async function runCorrelation() {
  banner('HMM — ارتباط الانفجارات بالأنظمة');
  const r = await pythonHMMExplosionCorr({ lookback_days: 3 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Total explosions analysed: ${r.n_explosions}`);
  if (r.results?.length) {
    console.log('\n   Explosion Rate by Regime (explosions % of days):');
    console.log('   Regime                    Rate%   N-Explosions  Days');
    console.log('   ' + '─'.repeat(55));
    r.results.forEach(row =>
      console.log(`   ${String(row.regime).padEnd(26)} ${String(row.explosion_rate).padEnd(8)} ${String(row.n_explosions).padEnd(14)} ${row.total_days}`));
  }
  if (r.best_regime) console.log(`\n   🏆 Best Regime for Explosions: ${r.best_regime}  (${r.best_rate}%)`);
  const worst = r.results?.[r.results.length - 1];
  if (worst) console.log(`   ⚠️  Lowest Rate: ${worst.regime}  (${worst.explosion_rate}%)`);
}

async function runReport() {
  await runDetect();
  await runCorrelation();
}

const SECTIONS = { fit: runFit, detect: runDetect, history: runHistory,
                   correlation: runCorrelation, report: runReport };

const fn = SECTIONS[section];
if (!fn) {
  console.error(`❌ Unknown section: ${section}`);
  console.error(`   Available: ${Object.keys(SECTIONS).join(', ')}`);
  process.exit(1);
}
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
