#!/usr/bin/env node
/**
 * Phase 28 — Unified Daily Synthesis (THE CROWN JEWEL)
 *
 * Sections:
 *   run            — run full 9-section synthesis (default)
 *   brief          — print formatted daily brief
 *   report         — get last report as JSON
 *   section        — get single section  --section 5_explosion_watch
 *   status         — check data source availability
 *
 * Options:
 *   --date YYYY-MM-DD    target date (default: today)
 *   --section <key>      section key for 'section' command
 */
import { pythonSynthesisBuild, pythonSynthesisDailyBrief,
         pythonSynthesisGetReport, pythonSynthesisGetSection,
         pythonSynthesisStatus } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'run';
const date    = args[args.indexOf('--date')    + 1];
const secKey  = args[args.indexOf('--section') + 1] ?? '5_explosion_watch';

function banner(t) {
  console.log('\n' + '='.repeat(70));
  console.log('  [CROWN JEWEL]  ' + t);
  console.log('='.repeat(70));
}
function pp(o) { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'run': {
    banner('Unified Daily Synthesis -- Running full 9-section intelligence report...');
    const params = date ? { date } : {};
    const r = await pythonSynthesisBuild(params);
    if (r?.success) {
      const s = r.summary;
      console.log('\n[OK] Report: ' + r.report_id + '  (' + r.duration_s + 's)');
      console.log('   Date: ' + s.date + '  Regime: ' + s.regime);
      console.log('   Top candidate: ' + (s.top_explosion_candidate ?? 'N/A') + '  Score: ' + s.top_explosion_score);
      console.log('   Causal chains: ' + s.n_causal_chains + '  Active laws: ' + s.n_active_laws);
      console.log('   Open directives: ' + s.n_open_directives + '  Feasible picks: ' + s.n_feasible_picks);
      console.log('\n   ' + s.narrative_ar);
      console.log('   ' + s.narrative_en);
      if (r.key_risks?.length) {
        console.log('\n[RISKS]');
        r.key_risks.forEach(risk => console.log('   - ' + risk));
      }
      if (r.section_errors?.length) {
        console.log('\n[SECTION ERRORS]');
        r.section_errors.forEach(e => console.log('   ' + e.section + ': ' + e.error));
      }
    } else {
      console.error('[FAIL] Synthesis failed:', r?.error);
      pp(r);
    }
    break;
  }
  case 'brief': {
    banner('Daily Brief');
    const r = await pythonSynthesisDailyBrief({});
    if (r?.brief_text) console.log(r.brief_text);
    else pp(r);
    break;
  }
  case 'report': {
    banner('Last Report JSON');
    const r = await pythonSynthesisGetReport({});
    pp(r);
    break;
  }
  case 'section': {
    banner('Section: ' + secKey);
    const r = await pythonSynthesisGetSection({ section: secKey });
    pp(r);
    break;
  }
  case 'status': {
    banner('Data Source Status');
    const r = await pythonSynthesisStatus({});
    if (r?.data_sources) {
      console.log('\n   Readiness: ' + r.readiness_pct + '%  (' + r.n_available + '/' + r.n_total + ' sources)');
      if (r.last_synthesis?.date)
        console.log('   Last synthesis: ' + r.last_synthesis.date + ' at ' + (r.last_synthesis.created_at ?? '').slice(0,16));
      console.log('\nData Sources:');
      Object.entries(r.data_sources).forEach(([table, info]) => {
        const icon = info.available ? '[OK]' : '[--]';
        console.log('   ' + icon + ' ' + (info.label ?? table).padEnd(30) + ' ' + (info.n_rows ?? 0).toLocaleString() + ' rows');
      });
    } else pp(r);
    break;
  }
  default:
    console.log('Unknown section: ' + section);
    console.log('Usage: node scripts/egx_synthesis.mjs [run|brief|report|section|status]');
    process.exit(1);
}
