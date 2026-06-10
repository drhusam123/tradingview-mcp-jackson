#!/usr/bin/env node
/**
 * Phase 73 — Portfolio Optimizer runner
 * "تحسين المحفظة — Kelly + Max Sharpe + Risk Parity"
 *
 * Sections: kelly | sharpe | parity | report
 *   --capital 100000
 *   --min-prob 0.65
 *   --max-pos 0.30
 */
import { pythonPortKelly, pythonPortMaxSharpe, pythonPortRiskParity, pythonPortReport }
  from '../src/egx/index.js';

const args     = process.argv.slice(2);
const section  = args.find(a => !a.startsWith('--')) ?? 'report';
const capIdx   = args.indexOf('--capital');
const capital  = capIdx !== -1 ? parseFloat(args[capIdx + 1]) : 100_000;
const probIdx  = args.indexOf('--min-prob');
const minProb  = probIdx !== -1 ? parseFloat(args[probIdx + 1]) : 0.65;
const maxPIdx  = args.indexOf('--max-pos');
const maxPos   = maxPIdx !== -1 ? parseFloat(args[maxPIdx + 1]) : 0.30;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💼 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

function printPositions(positions = [], label) {
  if (!positions.length) { console.log('   No positions.'); return; }
  console.log(`\n   ${label ?? ''} — ${positions.length} positions:\n`);
  console.log('   Symbol    Weight%   Amount EGP   Prob%');
  console.log('   ' + '─'.repeat(50));
  positions.slice(0, 15).forEach(p => {
    const wt  = ((p.weight ?? 0) * 100).toFixed(1);
    const amt = (p.amount_egp ?? 0).toLocaleString();
    const prob = ((p.prob ?? 0) * 100).toFixed(1);
    console.log(`   📊 ${String(p.symbol).padEnd(8)} ${String(wt+'%').padStart(7)}   ${String(amt+' EGP').padStart(12)}   ${prob}%`);
  });
}

switch (section) {
  case 'kelly': {
    banner(`Kelly Sizing — min_prob=${minProb}, capital=${capital.toLocaleString()} EGP`);
    const r = await pythonPortKelly({ min_prob: minProb, total_capital: capital, max_position: maxPos });
    if (r?.positions?.length) {
      printPositions(r.positions, 'Half-Kelly');
      console.log(`\n   Cash reserve: ${r.cash_reserve?.toLocaleString()} EGP`);
    } else pp(r);
    break;
  }
  case 'sharpe': {
    banner(`Max Sharpe Portfolio — min_prob=${minProb}, capital=${capital.toLocaleString()} EGP`);
    const r = await pythonPortMaxSharpe({ min_prob: minProb, total_capital: capital, max_position: maxPos });
    if (r?.positions?.length) {
      console.log(`\n   Expected Return:    ${r.expected_return}%`);
      console.log(`   Expected Volatility: ${r.expected_volatility}%`);
      console.log(`   Sharpe Ratio:       ${r.sharpe_ratio}`);
      printPositions(r.positions, 'Max Sharpe');
      console.log(`\n   Cash reserve: ${r.cash_reserve?.toLocaleString()} EGP`);
    } else pp(r);
    break;
  }
  case 'parity': {
    banner(`Risk Parity — min_prob=${minProb}, capital=${capital.toLocaleString()} EGP`);
    const r = await pythonPortRiskParity({ min_prob: minProb, total_capital: capital });
    if (r?.positions?.length) {
      printPositions(r.positions.map(p => ({...p, prob: p.prob})), 'Risk Parity');
      console.log(`\n   Cash reserve: ${r.cash_reserve?.toLocaleString()} EGP`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner(`Portfolio Report — ${capital.toLocaleString()} EGP @ min_prob=${minProb}`);
    const r = await pythonPortReport({ min_prob: minProb, total_capital: capital, max_position: maxPos });

    if (r?.kelly?.positions?.length) {
      // Kelly summary
      const k = r.kelly;
      console.log(`\n   🎰 Half-Kelly: ${k.n_positions} positions`);
      k.positions.slice(0, 5).forEach(p =>
        console.log(`     📊 ${String(p.symbol).padEnd(8)} ${((p.weight||0)*100).toFixed(1)}%  ${(p.amount_egp||0).toLocaleString()} EGP`)
      );
    }

    if (r?.max_sharpe?.positions?.length) {
      const s = r.max_sharpe;
      console.log(`\n   📈 Max Sharpe: ${s.n_positions} positions  Sharpe=${s.sharpe_ratio}  E[R]=${s.expected_return}%`);
      s.positions.slice(0, 5).forEach(p =>
        console.log(`     📊 ${String(p.symbol).padEnd(8)} ${((p.weight||0)*100).toFixed(1)}%  ${(p.amount_egp||0).toLocaleString()} EGP`)
      );
    }

    if (r?.risk_parity?.positions?.length) {
      const rp = r.risk_parity;
      console.log(`\n   ⚖️  Risk Parity: ${rp.n_positions} positions`);
      rp.positions.slice(0, 5).forEach(p =>
        console.log(`     📊 ${String(p.symbol).padEnd(8)} ${((p.weight||0)*100).toFixed(1)}%  ${(p.amount_egp||0).toLocaleString()} EGP`)
      );
    }
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: kelly|sharpe|parity|report`); process.exit(1);
}
