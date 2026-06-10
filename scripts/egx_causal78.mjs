#!/usr/bin/env node
/**
 * Phase 78 — Causal Discovery (Granger + Lag + MI) runner
 *
 * Sections:
 *   granger      — Granger causality tests for all market drivers
 *   lag          — Optimal lag structure for predictors
 *   mi           — Mutual information matrix between drivers + explosions
 *   report       — Full causal discovery report
 */
import { pythonCausalGranger, pythonCausalLag, pythonCausalMI,
         pythonCausalReport } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔗 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

async function runGranger() {
  banner('Granger Causality — هل breadth يسبب الانفجارات؟');
  const r = await pythonCausalGranger({ max_lag: 5, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Days: ${r.n_days}  Period: ${r.period}`);
  console.log(`   Summary: ${r.summary}\n`);
  if (r.results) {
    console.log('   Driver                   Lag   F-stat   p-value   Strength');
    console.log('   ' + '─'.repeat(60));
    r.results.forEach(res => {
      if (res.error) {
        console.log(`   ${String(res.driver).padEnd(25)} ERROR: ${res.error}`);
      } else {
        const mark = res.causal ? '✅' : '  ';
        console.log(`   ${mark} ${String(res.driver).padEnd(23)} ${String(res.best_lag).padEnd(5)} ${String(res.f_stat).padEnd(8)} ${String(res.p_value).padEnd(9)} ${res.strength}`);
      }
    });
  }
  if (r.causal_drivers?.length) {
    console.log(`\n   ✅ Causal Drivers: ${r.causal_drivers.join(', ')}`);
  }
}

async function runLag() {
  banner('Lag Analysis — ما هو أفضل تأخر للتنبؤ؟');
  const r = await pythonCausalLag({ max_lag: 10, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Days: ${r.n_days}\n`);
  if (r.results) {
    r.results.forEach(res => {
      console.log(`   📈 ${res.driver}`);
      console.log(`      Best lag: ${res.best_lag}d  Corr: ${res.best_corr}`);
      console.log(`      ${res.interpretation}`);
      if (res.top_lags) {
        console.log(`      Top lags: ${res.top_lags.map(l => `lag${l.lag}=${l.corr}`).join(', ')}`);
      }
      console.log();
    });
  }
}

async function runMI() {
  banner('Mutual Information Matrix — علاقات غير خطية');
  const r = await pythonCausalMI({ start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`   Days: ${r.n_days}   Top driver: ${r.top_driver}\n`);
  if (r.mi_scores) {
    console.log('   Driver                   Mutual Info');
    console.log('   ' + '─'.repeat(40));
    r.mi_scores.forEach(s =>
      console.log(`   ${String(s.driver).padEnd(25)} ${s.mutual_info}`));
  }
}

async function runReport() {
  await runGranger();
  await runLag();
  await runMI();
}

const SECTIONS = { granger: runGranger, lag: runLag, mi: runMI, report: runReport };

const fn = SECTIONS[section];
if (!fn) {
  console.error(`❌ Unknown section: ${section}`);
  console.error(`   Available: ${Object.keys(SECTIONS).join(', ')}`);
  process.exit(1);
}
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
