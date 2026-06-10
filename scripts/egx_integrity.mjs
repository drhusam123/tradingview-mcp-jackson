#!/usr/bin/env node
/**
 * Phase 20 — Historical Integrity Engine runner
 *
 * Usage:
 *   node scripts/egx_integrity.mjs [section] [options]
 *
 * Sections:
 *   scan           — scan all symbols for data quality (slow, ~5 min)
 *   scan:symbol    — scan a single symbol  --ticker COMI
 *   breadth        — compute market breadth history
 *   report         — show integrity report (tier distribution)
 *   confidence     — get per-symbol confidence penalties
 *   anomalies      — flag price/volume anomalies
 *   full           — run all commands
 *
 * Options:
 *   --ticker SYMBOL
 */
import { pythonIntegrityScanAll, pythonIntegrityScanSymbol,
         pythonIntegrityBreadth, pythonIntegrityReport,
         pythonIntegrityConfidence, pythonIntegrityAnomalies } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const ticker  = args[args.indexOf('--ticker') + 1];

function banner(text) {
  console.log('\n' + '═'.repeat(60));
  console.log(`  📊 ${text}`);
  console.log('═'.repeat(60));
}

function pp(obj) { console.log(JSON.stringify(obj, null, 2)); }

async function runIntegrityScan() {
  banner('Integrity: Scanning all symbols…');
  console.log('⚠️  This takes ~5 minutes for 252 symbols');
  const r = await pythonIntegrityScanAll({});
  pp(r);
}

async function runIntegrityScanSymbol(sym) {
  banner(`Integrity: Scanning symbol ${sym}`);
  const r = await pythonIntegrityScanSymbol({ symbol: sym });
  pp(r);
}

async function runBreadth() {
  banner('Market Breadth: Computing A/D history…');
  const r = await pythonIntegrityBreadth({});
  pp(r);
}

async function runReport() {
  banner('Integrity Report: Tier distribution + best/worst');
  const r = await pythonIntegrityReport({});
  if (r?.tier_distribution) {
    console.log('\n📊 Tier Distribution:');
    for (const [tier, count] of Object.entries(r.tier_distribution)) {
      console.log(`   ${tier.padEnd(20)} ${count} symbols`);
    }
  }
  if (r?.worst_10) {
    console.log('\n⚠️  Worst 10 Symbols:');
    r.worst_10.forEach(s => console.log(`   ${s.symbol?.padEnd(10)} score: ${(s.score ?? 0).toFixed(1)}  tier: ${s.tier}`));
  }
  if (r?.best_10) {
    console.log('\n✅ Best 10 Symbols:');
    r.best_10.forEach(s => console.log(`   ${s.symbol?.padEnd(10)} score: ${(s.score ?? 0).toFixed(1)}`));
  }
}

async function runConfidence() {
  banner('Integrity: Confidence penalties');
  const r = await pythonIntegrityConfidence({});
  pp(r);
}

async function runAnomalies() {
  banner('Integrity: Flagging anomalies');
  const r = await pythonIntegrityAnomalies({});
  if (r?.anomalies?.length) {
    console.log(`\n⚠️  ${r.anomalies.length} anomalies found:`);
    r.anomalies.slice(0, 20).forEach(a =>
      console.log(`   ${a.symbol?.padEnd(10)} ${a.date}  return: ${(a.return_pct ?? 0).toFixed(1)}%  type: ${a.anomaly_type}`));
  }
  pp(r);
}

async function runFull() {
  await runReport();
  await runBreadth();
  await runAnomalies();
}

switch (section) {
  case 'scan':          await runIntegrityScan(); break;
  case 'scan:symbol':   await runIntegrityScanSymbol(ticker ?? 'COMI'); break;
  case 'breadth':       await runBreadth(); break;
  case 'report':        await runReport(); break;
  case 'confidence':    await runConfidence(); break;
  case 'anomalies':     await runAnomalies(); break;
  case 'full':          await runFull(); break;
  default:
    console.log(`Unknown section: ${section}`);
    console.log('Usage: node scripts/egx_integrity.mjs [scan|scan:symbol|breadth|report|confidence|anomalies|full]');
    process.exit(1);
}
