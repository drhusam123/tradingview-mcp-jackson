#!/usr/bin/env node
/**
 * Phase 83 — Regime Transition Prediction
 *
 * Sections:
 *   matrix      — Empirical transition probability matrix
 *   leading     — Leading indicators before each regime transition
 *   warning     — Real-time early warning system
 *   forecast    — Multi-horizon regime forecast (1d, 3d, 5d, 10d)
 *   volatility  — Volatility acceleration detection
 *   report      — Full regime transition report
 */
import { pythonRegimeTransMatrix, pythonRegimeTransLeading, pythonRegimeTransWarning,
         pythonRegimeTransForecast, pythonRegimeTransVolAccel, pythonRegimeTransReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
function banner(t) { console.log('\n' + '═'.repeat(62) + `\n  🔮 ${t}\n` + '═'.repeat(62)); }
function pct(v) { return v != null ? (v*100).toFixed(1)+'%' : 'N/A'; }

async function runMatrix() {
  banner('Transition Matrix — احتمالات تغيّر النظام');
  const r = await pythonRegimeTransMatrix({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  // Actual fields: n_transitions, n_dates, most_likely_next, avg_holding_days (nested objects)
  const nRegimes = r.n_regimes ?? Object.keys(r.most_likely_next || {}).length;
  console.log(`   Regimes: ${nRegimes}   Total transitions: ${r.n_transitions}   Days: ${r.n_dates ?? r.n_days}`);
  if (r.most_likely_next) {
    console.log('\n   P(next | current):');
    Object.entries(r.most_likely_next).forEach(([from, info]) => {
      const toRegime = typeof info === 'object' ? info.regime : info;
      const prob = typeof info === 'object' ? info.probability : null;
      console.log(`     ${String(from).padEnd(20)} → most likely: ${String(toRegime).padEnd(20)} (${pct(prob)})`);
    });
  } else if (r.transition_matrix) {
    console.log('\n   P(next | current):');
    Object.entries(r.transition_matrix).forEach(([from, tos]) => {
      const top = Object.entries(tos).sort(([,a],[,b]) => b-a)[0];
      console.log(`     ${String(from).padEnd(20)} → most likely: ${String(top?.[0]).padEnd(20)} (${pct(top?.[1])})`);
    });
  }
  if (r.avg_holding_days) {
    console.log('\n   Avg Holding Days per Regime:');
    Object.entries(r.avg_holding_days).forEach(([regime, info]) => {
      const days = typeof info === 'object' ? info.avg_days : info;
      const episodes = typeof info === 'object' ? `  (${info.n_episodes} episodes)` : '';
      console.log(`     ${String(regime).padEnd(20)} ${typeof days === 'number' ? days.toFixed(1) : days} days${episodes}`);
    });
  }
}

async function runLeading() {
  banner('Leading Indicators — ما يسبق تغيير النظام');
  const r = await pythonRegimeTransLeading({ lookback_days: 5 });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Transition types found: ${r.n_transition_types}\n`);
  r.transitions?.slice(0, 6).forEach(t => {
    console.log(`   ${t.from_regime} → ${t.to_regime}  (${t.n_occurrences} times)`);
    if (t.avg_precursors) {
      Object.entries(t.avg_precursors).slice(0, 3).forEach(([k, v]) =>
        console.log(`     ${String(k).padEnd(20)} avg=${typeof v === 'number' ? v.toFixed(3) : v}`));
    }
    console.log();
  });
}

async function runWarning() {
  banner('Early Warning — إنذار مبكر لتغيير النظام');
  const r = await pythonRegimeTransWarning({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  const LEVEL_EMOJI = { LOW: '🟢', MEDIUM: '🟡', HIGH: '🟠', CRITICAL: '🔴' };
  console.log(`\n   Current Regime:       ${r.current_regime}`);
  console.log(`   Warning Level:        ${LEVEL_EMOJI[r.warning_level] || ''} ${r.warning_level}`);
  console.log(`   Most Likely Next:     ${r.most_likely_next_regime}`);
  if (r.transition_probability != null) console.log(`   Transition Prob:      ${pct(r.transition_probability)}`);
  if (r.key_signals?.length) {
    console.log('\n   Key Warning Signals:');
    r.key_signals.forEach(s => console.log(`     ⚡ ${s}`));
  }
}

async function runForecast() {
  banner('Regime Forecast — توقع النظام المستقبلي');
  const r = await pythonRegimeTransForecast({ horizons: [1, 3, 5, 10] });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Current Regime: ${r.current_regime}   Expected duration remaining: ${r.expected_days_remaining?.toFixed(1)} days\n`);
  // Handle both array and dict-keyed forecast formats
  const forecasts = r.forecasts || (r.forecast_horizons
    ? Object.entries(r.forecast_horizons).map(([h, probs]) => {
        const top = Object.entries(probs).sort(([,a],[,b]) => b-a)[0];
        return { horizon: h, most_likely: top?.[0], probability: top?.[1] };
      })
    : []);
  forecasts.forEach(f => {
    console.log(`   In ${String(f.horizon+'d').padEnd(4)}: most likely = ${String(f.most_likely).padEnd(20)} (${pct(f.probability)})`);
    if (f.danger_regimes?.length) console.log(`         ⚠️  Danger: ${f.danger_regimes.join(', ')}`);
  });
}

async function runVolatility() {
  banner('Volatility Acceleration — كشف تسارع التقلب');
  const r = await pythonRegimeTransVolAccel({});
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   Vol 5d:   ${r.vol_5d?.toFixed(2)}%`);
  console.log(`   Vol 20d:  ${r.vol_20d?.toFixed(2)}%`);
  console.log(`   Acceleration: ${r.acceleration_ratio?.toFixed(2)}x  → ${r.acceleration_trend}`);
  if (r.garch_proxy) console.log(`   GARCH proxy (var ratio): ${r.garch_proxy?.toFixed(2)}`);
}

async function runReport() {
  await runWarning();
  await runForecast();
  await runVolatility();
}

const SECTIONS = { matrix: runMatrix, leading: runLeading, warning: runWarning,
                   forecast: runForecast, volatility: runVolatility, report: runReport };
const fn = SECTIONS[section];
if (!fn) { console.error(`❌ Unknown: ${section}  Available: ${Object.keys(SECTIONS).join(', ')}`); process.exit(1); }
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
