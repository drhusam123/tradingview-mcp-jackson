#!/usr/bin/env node
/**
 * Phase 33 — Regime Transition Forecaster runner
 *
 * Sections:
 *   probability  — compute transition probability (5/10/20 day)
 *   precursors   — detect historical precursor patterns
 *   ewi          — early warning index (fast)
 *   alert        — full transition alert (recommended)
 *   full         — run all 4 sequentially
 */
import { pythonTransitionProbability, pythonTransitionPrecursors,
         pythonTransitionEWI, pythonTransitionAlert,
         pythonTransitionBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'alert';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🌊 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const EWI_BARS = { STABLE: '░', CAUTION: '▒', ELEVATED: '▓', CRITICAL: '█' };

switch (section) {
  case 'probability': {
    banner('Transition: Computing Regime Transition Probabilities…');
    const r = await pythonTransitionProbability({});
    if (r?.early_warning_index !== undefined) {
      const bar = EWI_BARS[r.ewi_level] ?? '░';
      console.log(`\n   Early Warning Index: ${r.early_warning_index?.toFixed(1)} / 100  [${r.ewi_level}]  ${bar.repeat(Math.round(r.early_warning_index/10))}`);
      console.log(`   Current Regime:    ${r.current_regime}`);
      console.log(`   Most Likely Next:  ${r.most_likely_next_regime}`);
      console.log(`   Confidence:        ${r.confidence}`);
      console.log('\n   Transition Probabilities:');
      console.log(`     5-day:  ${(r.probabilities?.transition_5d  * 100)?.toFixed(0)}%`);
      console.log(`     10-day: ${(r.probabilities?.transition_10d * 100)?.toFixed(0)}%`);
      console.log(`     20-day: ${(r.probabilities?.transition_20d * 100)?.toFixed(0)}%`);
      console.log('\n   Signal Breakdown:');
      if (r.signal_breakdown) {
        Object.entries(r.signal_breakdown).forEach(([k, v]) =>
          console.log(`     ${k.padEnd(25)} score: ${v.score?.toFixed(2)}  — ${v.raw_value}`));
      }
    } else pp(r);
    break;
  }
  case 'precursors': {
    banner('Transition: Detecting Precursor Patterns…');
    const r = await pythonTransitionPrecursors({});
    if (r?.active_precursors) {
      console.log(`\n   Precursor Consensus: ${r.precursor_consensus}`);
      console.log(`   Recommended:         ${r.recommended_action}\n`);
      if (r.active_precursors.length === 0) {
        console.log('   ✅ No active precursors detected — regime stable');
      } else {
        r.active_precursors.forEach(p =>
          console.log(`   ⚠️  ${p.pattern_name?.padEnd(35)} conf: ${p.confidence?.toFixed(2)}  acc: ${p.historical_accuracy?.toFixed(2)}  lead: ${p.lead_time_days}d\n      ${p.description}`));
      }
    } else pp(r);
    break;
  }
  case 'ewi': {
    banner('Transition: Early Warning Index (fast)');
    const r = await pythonTransitionEWI({});
    if (r?.ewi !== undefined) {
      const bar = EWI_BARS[r.ewi_level] ?? '░';
      console.log(`\n   EWI: ${r.ewi?.toFixed(1)} / 100  [${r.ewi_level}]  ${bar.repeat(Math.round(r.ewi/10))}`);
      if (r.signal_scores) {
        console.log('\n   Signals:');
        Object.entries(r.signal_scores).forEach(([k, v]) =>
          console.log(`     ${k.padEnd(30)} ${(v * 100).toFixed(0)}%`));
      }
    } else pp(r);
    break;
  }
  case 'alert': {
    banner('Transition: Full Regime Alert');
    const r = await pythonTransitionAlert({});
    if (r?.headline) {
      const levelEmoji = { WATCH: '👀', WARNING: '⚠️', ALERT: '🚨', CRITICAL: '🔴' };
      console.log(`\n   ${levelEmoji[r.alert_level] ?? '📊'} [${r.alert_level}] ${r.headline}`);
      console.log(`   📅 ${r.date}`);
      console.log(`   EWI: ${r.early_warning_index?.toFixed(1)}/100`);
      if (r.key_signals?.length) {
        console.log('\n   🔑 Key Signals:');
        r.key_signals.forEach(s => console.log(`   • ${s}`));
      }
      if (r.recommended_actions?.length) {
        console.log('\n   📋 Recommended Actions:');
        r.recommended_actions.forEach(a => console.log(`   → ${a}`));
      }
      console.log(`\n   📜 ${r.historical_context}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Transition: Full Build (all 4 commands)');
    const r = await pythonTransitionBuildFull({});
    if (r?.alert?.headline) {
      console.log(`\n   Alert: [${r.alert.alert_level}] ${r.alert.headline}`);
      console.log(`   EWI:   ${r.ewi?.ewi?.toFixed(1)}/100  [${r.ewi?.ewi_level}]`);
      console.log(`   P(10d transition): ${(r.probability?.probabilities?.transition_10d * 100)?.toFixed(0)}%`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
