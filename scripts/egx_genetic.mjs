#!/usr/bin/env node
/**
 * Phase 76 — Genetic Strategy Evolution runner
 *
 * Sections:
 *   evolve       — Run DEAP genetic algorithm (80 pop, 40 gen)
 *   top          — Show top evolved strategies from saved file
 *   validate     — Validate best strategy on recent data
 *   report       — Full evolution report
 */
import { pythonGeneticEvolve, pythonGeneticTop, pythonGeneticValidate } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const topN    = parseInt(args[args.indexOf('--top') + 1] ?? '5');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

async function runEvolve() {
  banner('Genetic Evolution — تطور الاستراتيجيات تلقائياً');
  console.log('  ⏳ يستغرق 3-10 دقائق...');
  const r = await pythonGeneticEvolve({ pop_size: 80, n_gen: 40, split_date: '2025-01-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  console.log(`\n   ✅ Generations: ${r.n_generations}  Population: ${r.pop_size}`);
  console.log(`   Best fitness: ${r.best_fitness?.toFixed(4)}`);
  if (r.best_rules) {
    console.log('\n   Best Strategy Rules:');
    r.best_rules.forEach(rule =>
      console.log(`     ${rule.feature} ${rule.direction} ${rule.threshold}`));
  }
  if (r.is_precision != null) {
    console.log(`\n   IS  precision: ${(r.is_precision*100).toFixed(1)}%  hits: ${r.is_hits}`);
    console.log(`   OOS precision: ${(r.oos_precision*100).toFixed(1)}%  hits: ${r.oos_hits}`);
    console.log(`   Stability:     ${(r.stability*100).toFixed(1)}%`);
  }
  if (r.saved_to) console.log(`\n   💾 ${r.saved_to}`);
}

async function runTop() {
  banner('Top Evolved Strategies');
  const r = await pythonGeneticTop({ n: topN });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  if (!r.strategies?.length) { console.log('  No evolved strategies found. Run evolve first.'); return; }
  r.strategies.forEach((s, i) => {
    console.log(`\n  #${i+1} fitness=${s.fitness?.toFixed(4)} IS=${(s.is_precision*100).toFixed(1)}% OOS=${(s.oos_precision*100).toFixed(1)}%`);
    s.rules?.forEach(rule =>
      console.log(`     ${rule.feature} ${rule.direction} ${rule.threshold}`));
  });
}

async function runValidate() {
  banner('Validate Best Strategy — اختبار على بيانات حديثة');
  const r = await pythonGeneticValidate({ start_date: '2025-06-01' });
  if (!r?.success) { console.error('❌', r?.error || r); return; }
  pp(r);
}

async function runReport() {
  await runTop();
}

const SECTIONS = { evolve: runEvolve, top: runTop, validate: runValidate, report: runReport };

const fn = SECTIONS[section];
if (!fn) {
  console.error(`❌ Unknown section: ${section}`);
  console.error(`   Available: ${Object.keys(SECTIONS).join(', ')}`);
  process.exit(1);
}
fn().catch(e => { console.error('❌ Fatal:', e.message); process.exit(1); });
