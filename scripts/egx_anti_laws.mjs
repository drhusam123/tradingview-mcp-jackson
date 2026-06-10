#!/usr/bin/env node
/**
 * Phase 35 — Anti-Laws Engine runner
 * "قوانين الفشل — متى لا تدخل"
 *
 * Sections:
 *   extract     — extract anti-laws from historical failure data
 *   library     — build/view anti-law library
 *   scan        — scan one symbol for active anti-laws  --ticker COMI
 *   daily       — daily scan of all symbols (recommended)
 *   report      — anti-law landscape report
 *   full        — extract + library + daily + report
 */
import { pythonAntiLawsExtract, pythonAntiLawsBuildLibrary, pythonAntiLawsScanSymbol,
         pythonAntiLawsDailyScan, pythonAntiLawsReport, pythonAntiLawsBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'daily';
const ticker  = args[args.indexOf('--ticker') + 1] ?? 'COMI';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🚫 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const SAFETY_EMOJI = { SAFE: '✅', CAUTION: '⚠️', DANGER: '🔴', VETO: '🚫' };

switch (section) {
  case 'extract': {
    banner('Anti-Laws: Extracting from historical failures…');
    const r = await pythonAntiLawsExtract({});
    if (r?.n_anti_laws_extracted !== undefined) {
      console.log(`\n   Extracted: ${r.n_anti_laws_extracted} anti-laws`);
      console.log(`   Veto laws:  ${r.n_veto_laws}`);
      if (r.top_5_by_precision) {
        console.log('\n   Most Dangerous Anti-Laws:');
        r.top_5_by_precision.forEach(a =>
          console.log(`   🚫 ${String(a.anti_law_type).padEnd(25)} precision: ${(a.anti_precision*100).toFixed(0)}%  freq:${a.frequency}  veto:${a.is_veto ? '✅' : '❌'}`));
      }
    } else pp(r);
    break;
  }
  case 'library': {
    banner('Anti-Laws: Library Overview');
    const r = await pythonAntiLawsBuildLibrary({});
    if (r?.library_stats) {
      console.log(`\n   Total anti-laws: ${r.library_stats.total}`);
      console.log(`   Avg precision:   ${(r.library_stats.avg_anti_precision * 100)?.toFixed(1)}%`);
      console.log(`   Veto laws:       ${r.library_stats.n_veto}`);
      console.log('\n   By Type:');
      Object.entries(r.by_type ?? {}).forEach(([type, stats]) =>
        console.log(`   ${String(type).padEnd(28)} n:${stats.count}  avg_prec:${(stats.avg_precision*100).toFixed(0)}%`));
      if (r.most_dangerous) {
        console.log('\n   🏴 Most Dangerous:');
        r.most_dangerous.slice(0, 5).forEach(a =>
          console.log(`   ${String(a.anti_law_type).padEnd(25)} prec:${(a.anti_precision*100).toFixed(0)}%  loss:-${a.avg_loss?.toFixed(1)}%`));
      }
    } else pp(r);
    break;
  }
  case 'scan': {
    banner(`Anti-Laws: Scanning ${ticker}`);
    const r = await pythonAntiLawsScanSymbol({ symbol: ticker });
    if (r?.safety_level) {
      const e = SAFETY_EMOJI[r.safety_level] ?? '?';
      console.log(`\n   ${e} ${ticker}: ${r.safety_level}  (${r.n_triggered} anti-laws triggered)`);
      if (r.triggered_anti_laws?.length) {
        console.log('\n   Triggered Anti-Laws:');
        r.triggered_anti_laws.forEach(a =>
          console.log(`   🚫 ${String(a.name).padEnd(25)} conf:${(a.confidence*100).toFixed(0)}%  ${a.description}  loss:-${a.historical_loss_avg?.toFixed(1)}%`));
        if (r.strongest_anti_law)
          console.log(`\n   Strongest: ${r.strongest_anti_law.name}`);
      } else {
        console.log('   ✅ No anti-laws active — clean entry');
      }
    } else pp(r);
    break;
  }
  case 'daily': {
    banner('Anti-Laws: Daily Scan — All Symbols');
    const r = await pythonAntiLawsDailyScan({});
    if (r?.n_veto !== undefined) {
      console.log(`\n   📅 ${r.date}`);
      console.log(`   🚫 VETO:    ${r.n_veto} symbols — avoid`);
      console.log(`   ⚠️  CAUTION: ${r.n_caution} symbols — reduce`);
      console.log(`   ✅ SAFE:    ${r.n_safe} symbols — clear`);
      console.log(`   Market failure risk: ${(r.anti_law_market_breadth * 100)?.toFixed(0)}% of market has active anti-laws`);
      if (r.veto_symbols?.length) {
        console.log('\n   🚫 VETO Symbols (avoid today):');
        r.veto_symbols.slice(0, 15).forEach(s => console.log(`   • ${s}`));
      }
      if (r.most_dangerous_pattern)
        console.log(`\n   Most dangerous pattern: ${r.most_dangerous_pattern}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Anti-Laws: Full Landscape Report');
    const r = await pythonAntiLawsReport({});
    if (r?.market_failure_risk) {
      console.log(`\n   Library size:     ${r.library_size}`);
      console.log(`   Most active type: ${r.most_active_type}`);
      console.log(`   Market risk:      ${r.market_failure_risk}`);
      if (r.highest_risk_symbols?.length) {
        console.log('\n   Highest Risk Symbols:');
        r.highest_risk_symbols.slice(0, 10).forEach(s =>
          console.log(`   🔴 ${String(s.symbol).padEnd(10)} ${s.active_anti_laws?.join(', ')}`));
      }
      if (r.key_warnings?.length) {
        console.log('\n   Key Warnings:');
        r.key_warnings.forEach(w => console.log(`   ⚠️  ${w}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Anti-Laws: Full Build');
    const r = await pythonAntiLawsBuildFull({});
    if (r?.scan?.n_veto !== undefined) {
      console.log(`\n   Extracted: ${r.extraction?.n_anti_laws_extracted} laws`);
      console.log(`   Veto symbols today: ${r.scan?.n_veto}`);
      console.log(`   Market risk: ${r.report?.market_failure_risk}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
