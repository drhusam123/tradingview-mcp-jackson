#!/usr/bin/env node
/**
 * Phase 48 — Capital Intelligence Engine runner
 * "من التحليل إلى التنفيذ — Survival-First Capital Allocation"
 *
 * Sections:
 *   exposure    — compute current portfolio exposure & headroom
 *   sizing      — size a position with uncertainty weighting  --ticker COMI --price 50 --shares 1000
 *   drawdown    — current drawdown state + risk adjustment
 *   explore     — exploration vs exploitation budget
 *   report      — full capital intelligence report (default)
 *   full        — report + save to DB (recommended)
 */
import { pythonCapitalExposure, pythonCapitalSizing, pythonCapitalDrawdown,
         pythonCapitalExploration, pythonCapitalReport, pythonCapitalBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const ticker  = args[args.indexOf('--ticker') + 1]  ?? 'COMI';
const price   = parseFloat(args[args.indexOf('--price') + 1]  ?? '50');
const shares  = parseInt(args[args.indexOf('--shares') + 1] ?? '1000');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💼 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const STATUS_EMOJI  = { SAFE: '🟢', CAUTION: '🟡', WARNING: '🟠', CRITICAL: '🔴', HALT: '⛔' };
const ACTION_EMOJI  = { BUY: '📈', SELL: '📉', HOLD: '⏸️', REDUCE: '⬇️', HALT: '🛑' };

switch (section) {
  case 'exposure': {
    banner('Capital Intelligence: Portfolio Exposure');
    const r = await pythonCapitalExposure({});
    if (r?.total_exposure_pct !== undefined) {
      const em = r.headroom_ok ? '✅' : '❌';
      console.log(`\n   ${em} Total exposure: ${(r.total_exposure_pct * 100)?.toFixed(1)}%  (max: ${(r.max_total_exposure * 100)?.toFixed(0)}%)`);
      console.log(`   Cash reserve: ${(r.cash_reserve_pct * 100)?.toFixed(1)}%  (min: ${(r.min_cash_reserve * 100)?.toFixed(0)}%)`);
      console.log(`   Open positions: ${r.n_open_positions}  (max: ${r.max_positions})`);
      console.log(`   Headroom: ${r.headroom_ok ? '✅ OK' : '❌ FULL'}`);
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'sizing': {
    banner(`Capital Intelligence: Position Sizing — ${ticker}`);
    const r = await pythonCapitalSizing({ symbol: ticker, price, shares });
    if (r?.recommended_shares !== undefined) {
      const em = ACTION_EMOJI[r.action] ?? '?';
      console.log(`\n   ${em} Action: ${r.action}  |  ${ticker} @ ${price}`);
      console.log(`   Uncertainty: ${(r.uncertainty * 100)?.toFixed(1)}%  →  Size factor: ${r.size_factor?.toFixed(2)}x`);
      console.log(`   Full size: ${r.full_size_shares?.toLocaleString()} shares  →  Recommended: ${r.recommended_shares?.toLocaleString()} shares`);
      console.log(`   Capital at risk: EGP ${r.capital_at_risk?.toFixed(0)}  (${(r.risk_pct * 100)?.toFixed(1)}%)`);
      console.log(`   Position value: EGP ${r.position_value?.toFixed(0)}`);
      console.log(`\n   ${r.reasoning ?? ''}`);
    } else pp(r);
    break;
  }
  case 'drawdown': {
    banner('Capital Intelligence: Drawdown State');
    const r = await pythonCapitalDrawdown({});
    if (r?.severity !== undefined) {
      const em = r.severity === 'NONE' ? '✅' : r.severity === 'MILD' ? '🟡' : r.severity === 'MODERATE' ? '🟠' : '🔴';
      console.log(`\n   ${em} Severity: ${r.severity}`);
      console.log(`   Current drawdown: ${(r.current_drawdown_pct * 100)?.toFixed(1)}%`);
      console.log(`   Size factor: ${r.recommended_size_factor?.toFixed(2)}x`);
      console.log(`   Halt: ${r.should_halt ? '⛔ YES' : '✅ NO'}`);
      if (r.active_rules?.length) console.log(`   Active rules: ${r.active_rules.join(', ')}`);
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'explore': {
    banner('Capital Intelligence: Exploration vs Exploitation Budget');
    const r = await pythonCapitalExploration({});
    if (r?.directive !== undefined) {
      const em = r.directive === 'EXPLOIT' ? '🎯' : r.directive === 'EXPLORE' ? '🔭' : '⚖️';
      console.log(`\n   ${em} Directive: ${r.directive}`);
      console.log(`   Exploration budget: ${(r.exploration_budget_pct * 100)?.toFixed(1)}%`);
      console.log(`   Win rate: ${(r.win_rate * 100)?.toFixed(1)}%  |  Avg edge: ${r.avg_edge?.toFixed(4)}`);
      console.log(`   Stagnation detected: ${r.stagnation ? '⚠️ YES' : '✅ NO'}`);
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Capital Intelligence: Full Report');
    const r = await pythonCapitalReport({});
    if (r?.exposure || r?.drawdown) {
      const ee = r.ee_regime;
      const eeEm = ee?.action_allowed ? '🟢' : ee?.regime === 'SANDBOX_ONLY' ? '🔴' : '🟡';
      console.log(`\n   ${eeEm} EE Regime: ${ee?.regime ?? 'N/A'}`);
      console.log(`   Deployable capital: EGP ${r.total_deployable_egp?.toFixed(0) ?? 0}`);
      console.log(`   Max single position: EGP ${r.max_single_position_egp?.toFixed(0) ?? 0}`);
      if (r.exposure) {
        const exp = r.exposure;
        console.log(`\n   💼 Exposure`);
        console.log(`   • Recommended: ${(exp.recommended_exposure_pct * 100)?.toFixed(0)}%  →  EGP ${exp.recommended_capital_egp?.toFixed(0)}`);
        console.log(`   • Max positions: ${exp.max_positions}  |  Bus directive: ${exp.bus_directive}`);
        if (exp.notes?.length) exp.notes.slice(0, 3).forEach(n => console.log(`   ℹ️  ${n}`));
      }
      if (r.drawdown) {
        const dd = r.drawdown;
        const ddEm = dd.severity === 'NONE' ? '✅' : dd.severity === 'MILD' ? '🟡' : '🔴';
        console.log(`\n   📉 Drawdown`);
        console.log(`   ${ddEm} Severity: ${dd.severity}  |  ${(dd.current_drawdown_pct * 100)?.toFixed(1)}% drawdown`);
        console.log(`   Size factor: ${dd.recommended_size_factor?.toFixed(2)}x`);
      }
      if (ee) {
        console.log(`\n   🔭 E&E`);
        console.log(`   Exploration budget: EGP ${ee.exploration_budget_egp?.toFixed(0)}`);
        console.log(`   ${ee.english_message ?? ''}`);
      }
      console.log(`\n   ${r.today_summary_en ?? ''}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Capital Intelligence: Full Build + Save');
    const r = await pythonCapitalBuildFull({});
    if (r?.status !== undefined) {
      const em = r.action_allowed ? '🟢' : r.status === 'SANDBOX_ONLY' ? '🔴' : '🟡';
      console.log(`\n   ${em} EE Regime: ${r.ee_regime ?? r.status}`);
      console.log(`   Deployable: EGP ${r.total_deployable_egp?.toFixed(0) ?? 0}`);
      console.log(`   Exposure: ${(r.recommended_exposure_pct * 100)?.toFixed(0)}%  |  Drawdown: ${r.drawdown_severity}`);
      console.log(`   MII: ${r.mii?.toFixed(1)}  |  Uncertainty: ${(r.uncertainty * 100)?.toFixed(0)}%  |  Bus: ${r.bus_directive}`);
      console.log(`\n   ${r.today_summary_en ?? r.english_message ?? ''}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
