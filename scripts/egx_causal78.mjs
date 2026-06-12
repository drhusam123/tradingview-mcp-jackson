#!/usr/bin/env node
/**
 * Phase 78 — Causal Discovery (Granger + Lag + MI) runner
 * Writes data/causal_discovery_last.json for discovery fabric miners.
 *
 * Sections:
 *   granger      — Granger causality tests for all market drivers
 *   lag          — Optimal lag structure for predictors
 *   mi           — Mutual information matrix between drivers + explosions
 *   report       — Full causal discovery report
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { pythonCausalGranger, pythonCausalLag, pythonCausalMI } from '../src/egx/index.js';
import { PROJECT_ROOT } from './lib/load_env.mjs';

const args = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔗 ${t}\n` + '═'.repeat(60)); }
const artifact = { at: new Date().toISOString(), causal_drivers: [], priority_pairs: [], sections: {} };

async function runGranger() {
  banner('Granger Causality — هل breadth يسبب الانفجارات؟');
  const r = await pythonCausalGranger({ max_lag: 5, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return r; }
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
    artifact.causal_drivers = [...new Set([...artifact.causal_drivers, ...r.causal_drivers])];
  }
  artifact.sections.granger = {
    n_days: r.n_days,
    summary: r.summary,
    causal_drivers: r.causal_drivers || [],
  };
  return r;
}

async function runLag() {
  banner('Lag Analysis — ما هو أفضل تأخر للتنبؤ؟');
  const r = await pythonCausalLag({ max_lag: 10, start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return r; }
  console.log(`   Days: ${r.n_days}\n`);
  const pairs = [];
  if (r.results) {
    r.results.forEach(res => {
      console.log(`   📈 ${res.driver}`);
      console.log(`      Best lag: ${res.best_lag}d  Corr: ${res.best_corr}`);
      console.log(`      ${res.interpretation}`);
      if (res.top_lags) {
        console.log(`      Top lags: ${res.top_lags.map(l => `lag${l.lag}=${l.corr}`).join(', ')}`);
      }
      console.log();
      if (res.driver && res.best_lag != null) {
        pairs.push(['lag', res.driver, res.best_lag]);
      }
    });
  }
  artifact.priority_pairs = [...artifact.priority_pairs, ...pairs.slice(0, 6)];
  artifact.sections.lag = { n_days: r.n_days, top: (r.results || []).slice(0, 5) };
  return r;
}

async function runMI() {
  banner('Mutual Information Matrix — علاقات غير خطية');
  const r = await pythonCausalMI({ start_date: '2022-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return r; }
  console.log(`   Days: ${r.n_days}   Top driver: ${r.top_driver}\n`);
  if (r.mi_scores) {
    console.log('   Driver                   Mutual Info');
    console.log('   ' + '─'.repeat(40));
    r.mi_scores.forEach(s =>
      console.log(`   ${String(s.driver).padEnd(25)} ${s.mutual_info}`));
    if (r.top_driver) {
      artifact.causal_drivers = [...new Set([...artifact.causal_drivers, r.top_driver])];
    }
  }
  artifact.sections.mi = { n_days: r.n_days, top_driver: r.top_driver };
  return r;
}

function writeArtifact() {
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/causal_discovery_last.json'), JSON.stringify(artifact, null, 2));
  console.log(`\n  📁 causal_discovery_last.json — drivers=${artifact.causal_drivers.length} pairs=${artifact.priority_pairs.length}`);
}

async function runReport() {
  await runGranger();
  await runLag();
  await runMI();
  writeArtifact();
}

const SECTIONS = { granger: runGranger, lag: runLag, mi: runMI, report: runReport };

const fn = SECTIONS[section];
if (!fn) {
  console.error(`❌ Unknown section: ${section}`);
  console.error(`   Available: ${Object.keys(SECTIONS).join(', ')}`);
  process.exit(1);
}

fn()
  .then(() => {
    if (section !== 'report') writeArtifact();
  })
  .catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
