#!/usr/bin/env node
/**
 * Phase 51 — Cross-Market Coupling Engine runner
 * "السوق لا يعيش في فراغ — Global Context"
 *
 * Sections: coverage | riskon | usdegp | coupling | macro | context | full
 */
import { pythonCrossMarketCoverage, pythonCrossMarketRiskOn, pythonCrossMarketUsdEgp,
         pythonCrossMarketCoupling, pythonCrossMarketMacro, pythonCrossMarketContext,
         pythonCrossMarketBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'macro';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🌍 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const MACRO_EMOJI  = { MACRO_BULL: '🚀', MACRO_NEUTRAL: '⚖️', MACRO_BEAR: '📉', MACRO_CRISIS: '🆘' };
const HEADWIND_EMOJI = { STRONG_TAILWIND: '🟢🟢', TAILWIND: '🟢', NEUTRAL: '⚖️', HEADWIND: '🔴', STRONG_HEADWIND: '🔴🔴' };
const RISK_EMOJI = { RISK_OFF: '🔴', CAUTIOUS: '🟠', NEUTRAL: '⚖️', RISK_ON: '🟢', STRONG_RISK_ON: '🚀' };

switch (section) {
  case 'coverage': {
    banner('Cross-Market: Data Coverage');
    const r = await pythonCrossMarketCoverage({});
    if (r?.error) { console.log(`\n   ❌ ${r.error}\n   💡 Run: npm run egx:fetch:cross`); break; }
    if (r?.assets) {
      console.log(`\n   Assets with data: ${r.n_assets_with_data ?? r.assets.length}`);
      console.log('\n   Asset        Bars    Oldest        Newest');
      console.log('   ' + '─'.repeat(52));
      (r.assets ?? []).forEach(a =>
        console.log(`   ${String(a.asset).padEnd(12)} ${String(a.bars).padStart(5)}  ${a.oldest}  →  ${a.newest}`));
    } else pp(r);
    break;
  }
  case 'riskon': {
    banner('Cross-Market: Risk-On/Risk-Off Score');
    const r = await pythonCrossMarketRiskOn({});
    if (r?.risk_on_score !== undefined) {
      const em = RISK_EMOJI[r.label] ?? '?';
      console.log(`\n   ${em} Risk score: ${r.risk_on_score?.toFixed(1)}/100  (${r.label})`);
      if (r.components) {
        console.log('\n   Component breakdown:');
        Object.entries(r.components).forEach(([k, v]) =>
          console.log(`   ${String(k).padEnd(12)} ${v >= 0 ? '+' : ''}${v?.toFixed(2)}`));
      }
      console.log(`\n   ${r.egx_implication ?? ''}`);
    } else pp(r);
    break;
  }
  case 'usdegp': {
    banner('Cross-Market: USD/EGP Regime');
    const r = await pythonCrossMarketUsdEgp({});
    if (r?.regime) {
      const em = r.regime === 'DEPRECIATING' ? '📉' : r.regime === 'APPRECIATING' ? '📈' : '⚖️';
      console.log(`\n   ${em} EGP regime: ${r.regime}`);
      if (r.current_rate) console.log(`   Rate: ${r.current_rate?.toFixed(2)}  |  Depreciation: ${(r.annualized_depreciation * 100)?.toFixed(1)}% p.a.`);
      console.log(`   EGX impact: ${r.egx_impact}`);
    } else pp(r);
    break;
  }
  case 'coupling': {
    banner('Cross-Market: Correlation Matrix vs EGX');
    const r = await pythonCrossMarketCoupling({});
    if (r?.correlations) {
      console.log('\n   Asset        Correlation  Impact on EGX');
      console.log('   ' + '─'.repeat(45));
      const sorted = Object.entries(r.correlations).sort((a,b) => Math.abs(b[1]) - Math.abs(a[1]));
      sorted.forEach(([k, v]) => {
        const bar = Math.abs(v) > 0.5 ? '████' : Math.abs(v) > 0.3 ? '██' : '█';
        const em = v > 0 ? '🟢' : '🔴';
        console.log(`   ${em} ${String(k).padEnd(12)} ${String(v?.toFixed(3)).padStart(7)}      ${bar}`);
      });
    } else pp(r);
    break;
  }
  case 'macro': {
    banner('Cross-Market: Macro Regime');
    const r = await pythonCrossMarketMacro({});
    if (r?.macro_regime) {
      const em = MACRO_EMOJI[r.macro_regime] ?? '?';
      const hw = HEADWIND_EMOJI[r.macro_headwind] ?? '?';
      console.log(`\n   ${em} Macro: ${r.macro_regime}  |  ${hw} ${r.macro_headwind}`);
      if (r.key_risks?.length) console.log(`\n   🔴 Key risks: ${r.key_risks.join(' | ')}`);
      if (r.key_tailwinds?.length) console.log(`   🟢 Tailwinds: ${r.key_tailwinds.join(' | ')}`);
      console.log(`\n   ${r.arabic_description ?? r.description ?? ''}`);
    } else pp(r);
    break;
  }
  case 'context': {
    banner('Cross-Market: Today\'s Session Context');
    const r = await pythonCrossMarketContext({});
    if (r?.session_bias) {
      const em = r.session_bias === 'BULLISH' ? '📈' : r.session_bias === 'BEARISH' ? '📉' : '⚖️';
      console.log(`\n   ${em} Session bias: ${r.session_bias}`);
      if (r.top_positive?.length) console.log(`   🟢 Positive drivers: ${r.top_positive.map(a => `${a.asset}(${a.change_pct?.toFixed(1)}%)`).join(', ')}`);
      if (r.top_negative?.length) console.log(`   🔴 Negative drivers: ${r.top_negative.map(a => `${a.asset}(${a.change_pct?.toFixed(1)}%)`).join(', ')}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Cross-Market: Full Build + Save');
    const r = await pythonCrossMarketBuildFull({});
    if (r?.macro_regime) {
      const em = MACRO_EMOJI[r.macro_regime] ?? '?';
      console.log(`\n   ${em} Macro: ${r.macro_regime}`);
      console.log(`   Risk-On: ${r.risk_on_score?.toFixed(1)}/100  |  EGP: ${r.usdegp_regime}`);
      console.log(`   Headwind: ${r.macro_headwind}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
