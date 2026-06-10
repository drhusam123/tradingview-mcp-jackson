#!/usr/bin/env node
/**
 * Phase 67 — Scientific Refinement Cycle runner
 * "دورة التنقية العلمية — قياس، تشذيب، تكييف، اصطناع، تحسين مستمر"
 *
 * Sections: measure | prune | condition | synthesize | cycle | history | full
 *   --law-type universal|structural
 *   --date 2026-05-15
 *   --min-lift 0.05
 *   --apply (removes dry_run flag for prune)
 */
import { pythonRefineMeasure, pythonRefinePrune, pythonRefineCondition,
         pythonRefineSynthesize, pythonRefineRunCycle, pythonRefineHistory,
         pythonRefineBuildFull }
  from '../src/egx/index.js';

const args     = process.argv.slice(2);
const section  = args.find(a => !a.startsWith('--')) ?? 'cycle';
const ltIdx    = args.indexOf('--law-type');
const lawType  = ltIdx !== -1 ? args[ltIdx + 1] : 'universal';
const dateIdx  = args.indexOf('--date');
const date     = dateIdx !== -1 ? args[dateIdx + 1] : new Date().toISOString().split('T')[0];
const mlIdx    = args.indexOf('--min-lift');
const minLift  = mlIdx !== -1 ? parseFloat(args[mlIdx + 1]) : 0.05;
const apply    = args.includes('--apply');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔄 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const GRADE_EMOJI = { A: '🏆', B: '🥇', C: '🥈', D: '🥉', F: '❌', '?': '❓' };

function precBar(prec) {
  const pct = Math.min(100, Math.max(0, (prec ?? 0) * 100));
  return '█'.repeat(Math.round(pct / 5)) + '░'.repeat(20 - Math.round(pct / 5));
}

switch (section) {
  case 'measure': {
    banner(`Law Quality Measurement — ${lawType}`);
    const r = await pythonRefineMeasure({ law_type: lawType });
    if (r?.n_laws !== undefined) {
      const avgP = r.avg_precision ?? 0;
      const em   = avgP >= 0.50 ? '🏆' : avgP >= 0.35 ? '✅' : avgP >= 0.20 ? '⚠️' : '🚨';
      console.log(`\n   ${em} ${lawType.toUpperCase()} LAWS QUALITY REPORT`);
      console.log(`\n   Laws measured:   ${r.n_laws}`);
      console.log(`   Avg precision:   ${(avgP*100)?.toFixed(1)}%`);
      console.log(`   [${precBar(avgP)}] ${(avgP*100)?.toFixed(1)}%`);
      console.log(`\n   Grade A (≥65%):  ${r.n_grade_a ?? 0}`);
      console.log(`   Grade B (50-64%): ${r.n_grade_b ?? 0}`);
      console.log(`   Grade F (<20%):   ${r.n_grade_f ?? 0}`);
      console.log(`\n   Improvable w/ regime: ${r.n_improvable_with_regime ?? 0}`);
      if (r.top_laws?.length) {
        console.log(`\n   🏆 Top 5 laws:`);
        r.top_laws.slice(0, 5).forEach(l =>
          console.log(`     ${GRADE_EMOJI[l.grade] ?? ''} ${String(l.law_id).padEnd(26)} ${(l.precision*100)?.toFixed(1)}%  (${l.activations} acts)`));
      }
      if (r.worst_laws?.length) {
        console.log(`\n   ❌ Worst laws:`);
        r.worst_laws.forEach(l =>
          console.log(`     ❌ ${String(l.law_id).padEnd(26)} ${(l.precision*100)?.toFixed(1)}%  (${l.activations} acts)`));
      }
    } else pp(r);
    break;
  }
  case 'prune': {
    const dryRun = !apply;
    banner(`Prune Low-Quality Laws${dryRun ? ' [DRY RUN]' : ' [LIVE]'} — ${lawType}`);
    const r = await pythonRefinePrune({ law_type: lawType, dry_run: dryRun, max_precision: 0.15, min_activations: 50 });
    if (r?.n_pruned !== undefined) {
      console.log(`\n   ${dryRun ? '🔍 DRY RUN — no changes applied' : '✂️  LIVE — changes applied'}`);
      console.log(`   Candidates found:  ${r.n_candidates ?? 0}`);
      console.log(`   Laws to prune:     ${r.n_pruned ?? 0}`);
      if (r.pruned_laws?.length) {
        console.log(`\n   Laws marked for pruning:`);
        console.log('   Law ID                  Precision  Activations  Reason');
        console.log('   ' + '─'.repeat(68));
        r.pruned_laws.slice(0, 15).forEach(l =>
          console.log(`   ❌ ${String(l.law_id).padEnd(24)} ${String((l.precision*100)?.toFixed(1)+'%').padStart(8)}  ${String(l.activations).padStart(10)}   ${l.reason}`));
      }
      if (dryRun && r.n_pruned > 0)
        console.log(`\n   To apply pruning: npm run egx:refine:prune -- --apply`);
    } else pp(r);
    break;
  }
  case 'condition': {
    banner(`Regime Conditioning Opportunities — ${lawType}`);
    const r = await pythonRefineCondition({ law_type: lawType, min_lift: minLift });
    if (r?.n_improvable !== undefined) {
      console.log(`\n   Improvable laws:    ${r.n_improvable}`);
      console.log(`   Already cond.:      ${r.n_already_conditioned ?? 0}`);
      console.log(`   Avg lift:           +${(r.avg_lift * 100)?.toFixed(1)}pp`);
      const cands = r.top_candidates ?? [];
      if (cands.length) {
        console.log(`\n   Top candidates:\n`);
        console.log('   Law ID                  Base%   → Regime%  Lift    Best Regime');
        console.log('   ' + '─'.repeat(68));
        cands.slice(0, 15).forEach(c => {
          const liftPp = (c.lift * 100)?.toFixed(1);
          const liftEm = c.lift >= 0.15 ? '🚀' : c.lift >= 0.10 ? '⬆️' : '↑';
          console.log(`   ${liftEm} ${String(c.law_id).padEnd(24)} ${String((c.base_precision*100)?.toFixed(1)+'%').padStart(6)}  → ${String((c.regime_precision*100)?.toFixed(1)+'%').padStart(6)}  +${liftPp}pp  ${c.best_regime}`);
        });
        console.log(`\n   Run: npm run egx:regime:update  to apply regime conditioning`);
      }
    } else pp(r);
    break;
  }
  case 'synthesize': {
    banner(`Law Synthesis — ${lawType}`);
    const r = await pythonRefineSynthesize({ law_type: lawType, min_precision: 0.45 });
    if (r?.n_composites !== undefined) {
      console.log(`\n   Base laws (prec≥45%): ${r.n_base_laws ?? 0}`);
      console.log(`   Composite pairs:      ${r.n_composites ?? 0}`);
      const comps = r.top_composites ?? [];
      if (comps.length) {
        console.log(`\n   Top composite law candidates:\n`);
        console.log('   Composite Name                        Est. Prec%  Dir    Same Regime?');
        console.log('   ' + '─'.repeat(68));
        comps.slice(0, 10).forEach(c => {
          const em = c.est_precision >= 0.45 ? '🏆' : c.est_precision >= 0.35 ? '✅' : '⚠️';
          console.log(`   ${em} ${String(c.name).padEnd(38)} ${String((c.est_precision*100)?.toFixed(1)+'%').padStart(8)}   ${String(c.direction).padEnd(5)}  ${c.same_regime ? '✅ YES' : '—'}`);
        });
        console.log(`\n   Backtest top composites via: npm run egx:strategy:generate`);
      } else {
        console.log('\n   No high-precision base laws yet. Laws need precision ≥ 45%.');
      }
    } else pp(r);
    break;
  }
  case 'cycle': {
    banner(`Refinement Cycle — ${lawType} @ ${date}`);
    console.log('\n   Running full refinement pass...\n');
    const r = await pythonRefineRunCycle({ date, law_type: lawType, dry_run: !apply });
    if (r?.measure !== undefined) {
      const m = r.measure;
      const p = r.prune;
      const c = r.condition;
      const s = r.synthesize;
      const sig = r.signals;

      const avgP = m.avg_precision ?? 0;
      const em   = avgP >= 0.50 ? '🏆' : avgP >= 0.35 ? '✅' : avgP >= 0.20 ? '⚠️' : '🚨';

      console.log(`   ${em} MEASURE     ${m.n_laws ?? 0} laws  avg_prec=${(avgP*100)?.toFixed(1)}%  A=${m.n_grade_a ?? 0}  F=${m.n_grade_f ?? 0}`);
      console.log(`   ✂️  PRUNE       ${p.n_candidates ?? 0} candidates  ${p.n_pruned ?? 0} to prune${!apply ? ' (dry)' : ''}`);
      console.log(`   🎯 CONDITION   ${c.n_improvable ?? 0} improvable  avg_lift=+${((c.avg_lift??0)*100)?.toFixed(1)}pp`);
      console.log(`   🔬 SYNTHESIZE  ${s.n_base_laws ?? 0} base laws → ${s.n_composites ?? 0} composite pairs`);

      if (sig?.n_signals !== undefined) {
        console.log(`\n   ⚡ SIGNALS    ${sig.n_signals ?? 0} total  HIGH=${sig.n_high_conviction ?? 0}  avg_UES=${sig.avg_ues?.toFixed(1) ?? 'n/a'}`);
      }

      console.log(`\n   📋 Recommendations:`);
      // inline recommendations check
      if (avgP < 0.20) console.log(`   🚨 CRITICAL: avg precision < 20% — activate regime conditioning`);
      if ((p.n_pruned ?? 0) > 0) console.log(`   ✂️  ${p.n_pruned} laws can be pruned (add --apply to execute)`);
      if ((c.n_improvable ?? 0) > 0) console.log(`   🎯 ${c.n_improvable} laws can lift +${((c.avg_lift??0)*100)?.toFixed(1)}pp via regime`);
      if ((s.n_composites ?? 0) > 0) console.log(`   🔬 ${s.n_composites} composite pairs to backtest`);
    } else pp(r);
    break;
  }
  case 'history': {
    banner('Refinement Cycle History');
    const r = await pythonRefineHistory({ last_n: 10 });
    const cycles = r?.cycles ?? [];
    if (cycles.length) {
      console.log(`\n   ${cycles.length} past cycle(s):\n`);
      console.log('   Date        Laws   Pruned  AvgPrec%  AvgUES  HiConv  Notes');
      console.log('   ' + '─'.repeat(70));
      cycles.forEach(c => {
        const avgP = c.avg_precision ?? 0;
        const em   = avgP >= 0.50 ? '🏆' : avgP >= 0.35 ? '✅' : avgP >= 0.20 ? '⚠️' : '🚨';
        console.log(`   ${em} ${c.run_date}  ${String(c.laws_measured).padStart(5)}  ${String(c.laws_pruned).padStart(6)}  ${String((avgP*100)?.toFixed(1)+'%').padStart(8)}  ${String(c.avg_ues?.toFixed(1) ?? 'n/a').padStart(6)}  ${String(c.n_high_conv ?? 0).padStart(6)}  ${c.notes ?? ''}`);
      });
      if (r.trend?.precision_delta != null) {
        const delta = r.trend.precision_delta;
        const tEm = delta > 0 ? '📈' : delta < 0 ? '📉' : '→';
        console.log(`\n   ${tEm} Precision trend: ${delta > 0 ? '+' : ''}${(delta*100)?.toFixed(1)}pp over ${cycles.length} cycles`);
      }
    } else {
      console.log('\n   No refinement cycles run yet.');
      console.log('   Run: npm run egx:refine:cycle');
    }
    break;
  }
  case 'full': {
    banner(`Refinement Full Report — ${date}`);
    const r = await pythonRefineBuildFull({ date, law_type: lawType });
    if (r?.cycle !== undefined) {
      const cycle = r.cycle;
      const m = cycle.measure ?? {};
      const avgP = m.avg_precision ?? 0;
      const em   = avgP >= 0.50 ? '🏆' : avgP >= 0.35 ? '✅' : avgP >= 0.20 ? '⚠️' : '🚨';
      console.log(`\n   ${em} Quality snapshot:`);
      console.log(`     Laws: ${m.n_laws ?? 0}  Avg precision: ${(avgP*100)?.toFixed(1)}%  Grade-A: ${m.n_grade_a ?? 0}`);

      const hist = r.history_summary ?? {};
      if (hist.n_past_cycles > 0) {
        console.log(`\n   📊 ${hist.n_past_cycles} past cycles`);
        if (hist.trend?.precision_delta != null)
          console.log(`   Trend: ${hist.trend.precision_delta > 0 ? '📈' : '📉'} ${(hist.trend.precision_delta*100)?.toFixed(1)}pp`);
      }

      const sigs = r.top_signals ?? [];
      if (sigs.length) {
        console.log(`\n   ⚡ Today's top signals:`);
        sigs.slice(0, 8).forEach(s =>
          console.log(`     🔥 ${String(s.symbol).padEnd(8)} UES=${s.ues?.toFixed(1)}  ${s.conviction ?? ''}`));
      }

      if (r.cycle?.next_actions?.length) {
        console.log(`\n   📋 Next actions:`);
        r.cycle.next_actions.forEach(a => console.log(`   ${a}`));
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: measure|prune|condition|synthesize|cycle|history|full`); process.exit(1);
}
