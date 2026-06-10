#!/usr/bin/env node
/**
 * Phase 34 — Cognitive Arbitration Layer runner
 * "الدستور الحاكم — من ينتصر عند تعارض الإشارات؟"
 *
 * Sections:
 *   symbol      — arbitrate one symbol   --ticker COMI
 *   all         — arbitrate all symbols in universe
 *   decisions   — top ENTER decisions for today (recommended)
 *   constitution — show current constitution weights
 *   full        — all + decisions + constitution
 */
import { pythonArbitrateSymbol, pythonArbitrateAll, pythonArbitrateDailyDecisions,
         pythonArbitrateConstitution, pythonArbitrateBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'decisions';
const ticker  = args[args.indexOf('--ticker') + 1] ?? 'COMI';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  ⚖️  ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const DECISION_EMOJI = { ENTER: '✅', WAIT: '⏳', AVOID: '⛔' };

switch (section) {
  case 'symbol': {
    banner(`Arbitration: Decision for ${ticker}`);
    const r = await pythonArbitrateSymbol({ symbol: ticker });
    if (r?.decision) {
      const e = DECISION_EMOJI[r.decision] ?? '?';
      console.log(`\n   ${e} [${r.decision}]  confidence: ${r.confidence?.toFixed(0)}%  score: ${r.score?.toFixed(1)}`);
      console.log(`   Regime: ${r.regime}  EWI: ${r.ewi?.toFixed(1)}`);
      if (r.veto_triggered) {
        console.log(`\n   🚫 VETO: ${r.veto_reason}`);
      } else {
        console.log(`\n   📝 ${r.reasoning}`);
        console.log(`   Dominant source: ${r.dominant_source}`);
        if (r.suggested_size_pct)
          console.log(`   Suggested size: ${r.suggested_size_pct?.toFixed(1)}% of capital`);
        if (r.blocking_factors?.length)
          console.log(`\n   ⛔ Blocking: ${r.blocking_factors.join(', ')}`);
        if (r.soft_warnings?.length)
          console.log(`   ⚠️  Warnings: ${r.soft_warnings.join(', ')}`);
      }
      if (r.signal_breakdown) {
        console.log('\n   Signal Breakdown:');
        Object.entries(r.signal_breakdown).forEach(([k, v]) =>
          console.log(`     ${k.padEnd(28)} score:${(v.score??0).toFixed(0).padStart(4)}  w:${v.weight?.toFixed(2)}  ${v.raw??''}`));
      }
    } else pp(r);
    break;
  }
  case 'all': {
    banner('Arbitration: All Symbols');
    const r = await pythonArbitrateAll({});
    if (r?.decisions) {
      console.log(`\n   Regime: ${r.regime}  EWI: ${r.ewi?.toFixed(1)}`);
      console.log(`   ✅ ENTER: ${r.n_enter}  ⏳ WAIT: ${r.n_wait}  ⛔ AVOID: ${r.n_avoid}  🚫 VETO: ${r.n_veto}`);
      const enters = r.decisions.filter(d => d.decision === 'ENTER').slice(0, 10);
      if (enters.length) {
        console.log('\n   Top ENTER decisions:');
        enters.forEach(d =>
          console.log(`   ${String(d.symbol).padEnd(10)} conf:${d.confidence?.toFixed(0).padStart(3)}%  score:${d.score?.toFixed(1)}`));
      }
    } else pp(r);
    break;
  }
  case 'decisions': {
    banner('Arbitration: Daily ENTER Decisions');
    const r = await pythonArbitrateDailyDecisions({});
    if (r?.top_decisions) {
      console.log(`\n   📅 ${r.date}  Regime: ${r.regime}  ENTER count: ${r.n_enter}`);
      if (!r.top_decisions.length) {
        console.log('\n   ⛔ No ENTER decisions today — market conditions unfavorable');
      } else {
        console.log('\n   Actionable Decisions:');
        r.top_decisions.forEach(d =>
          console.log(`   ✅ ${String(d.symbol).padEnd(10)} conf:${d.confidence?.toFixed(0)}%  size:${d.suggested_size_pct?.toFixed(1)}%  ${d.reasoning}`));
      }
      if (r.portfolio_stats) {
        console.log(`\n   Portfolio: ${r.portfolio_stats.total_allocation_pct?.toFixed(1)}% deployed`);
      }
    } else pp(r);
    break;
  }
  case 'constitution': {
    banner('Arbitration: Current Decision Constitution');
    const r = await pythonArbitrateConstitution({});
    if (r?.regime) {
      console.log(`\n   Regime: ${r.regime}  EWI: ${r.ewi?.toFixed(1)}`);
      console.log(`   Market Posture: ${r.market_posture}`);
      console.log(`   Philosophy: ${r.dominant_philosophy}`);
      console.log('\n   Active Weights:');
      Object.entries(r.constitution_weights ?? {}).forEach(([k, v]) =>
        console.log(`     ${k.padEnd(28)} ${(v * 100).toFixed(0)}%`));
      if (r.veto_rules_active?.length) {
        console.log('\n   🚫 Active Veto Rules:');
        r.veto_rules_active.forEach(vr => console.log(`   • ${vr}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Arbitration: Full Build');
    const r = await pythonArbitrateBuildFull({});
    if (r?.decisions?.top_decisions) {
      console.log(`\n   ✅ ENTER: ${r.arbitration?.n_enter}  EWI: ${r.arbitration?.ewi?.toFixed(1)}`);
      console.log(`   Posture: ${r.constitution?.market_posture}`);
      r.decisions.top_decisions?.slice(0, 5).forEach(d =>
        console.log(`   ${String(d.symbol).padEnd(10)} ${d.confidence?.toFixed(0)}%  ${d.reasoning}`));
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
