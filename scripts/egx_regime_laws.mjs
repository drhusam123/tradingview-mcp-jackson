#!/usr/bin/env node
/**
 * Phase 64 — Regime-Conditional Laws runner
 * "قوانين مشروطة بالنظام — تفعيل القانون في بيئته المثالية فقط"
 *
 * Sections: analyze | signals | matrix | update | full
 *   --law-type universal|structural
 *   --date 2026-05-15
 *   --min-lift 0.05
 */
import { pythonRegimeAnalyze, pythonRegimeSignals, pythonRegimeLawMatrix,
         pythonRegimeUpdate, pythonRegimeBuildFull, pythonRegimePopulateMut }
  from '../src/egx/index.js';

const args     = process.argv.slice(2);
const section  = args.find(a => !a.startsWith('--')) ?? 'matrix';
const ltIdx    = args.indexOf('--law-type');
const lawType  = ltIdx !== -1 ? args[ltIdx + 1] : 'universal';
const dateIdx  = args.indexOf('--date');
const date     = dateIdx !== -1 ? args[dateIdx + 1] : new Date().toISOString().split('T')[0];
const mlIdx    = args.indexOf('--min-lift');
const minLift  = mlIdx !== -1 ? parseFloat(args[mlIdx + 1]) : 0.05;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🎯 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const REGIME_EMOJI = {
  BULL: '🐂', BEAR: '🐻', SIDEWAYS: '↔️', VOLATILE: '⚡', TRENDING: '📈',
  LOW_BREADTH: '📉', HIGH_BREADTH: '📈', MIXED: '〰️',
};

switch (section) {
  case 'analyze': {
    banner(`Regime Condition Analysis — ${lawType}`);
    const r = await pythonRegimeAnalyze({ law_type: lawType, min_lift: minLift });
    if (r?.n_improvable !== undefined || r?.top_candidates !== undefined) {
      const cands = r.top_candidates ?? r.candidates ?? [];
      console.log(`\n   Analyzable laws: ${r.n_laws ?? '?'}`);
      console.log(`   Improvable:      ${r.n_improvable ?? cands.length}`);
      console.log(`   Min lift:        ${(minLift * 100)?.toFixed(0)}pp`);
      if (cands.length) {
        console.log(`\n   Top improvement candidates:\n`);
        console.log('   Law ID                  Base%   Regime%  Lift%   Best Regime');
        console.log('   ' + '─'.repeat(65));
        cands.slice(0, 15).forEach(c => {
          const regEm = REGIME_EMOJI[c.best_regime] ?? '?';
          console.log(`   ${String(c.law_id).padEnd(24)} ${String((c.base_precision*100)?.toFixed(1)).padStart(6)}% ${String((c.regime_precision*100)?.toFixed(1)).padStart(7)}%  ${String((c.lift*100)?.toFixed(1)).padStart(5)}%  ${regEm} ${c.best_regime}`);
        });
      }
      if (r.avg_lift != null)
        console.log(`\n   Avg lift: +${(r.avg_lift * 100)?.toFixed(1)}pp`);
    } else pp(r);
    break;
  }
  case 'signals': {
    banner(`Conditioned Signals — ${date}`);
    const r = await pythonRegimeSignals({ date, law_type: lawType });
    const sigs = r?.signals ?? r?.conditioned_signals ?? [];
    if (sigs.length) {
      console.log(`\n   ${sigs.length} regime-conditioned signal(s):\n`);
      console.log('   Symbol    Law ID                  Regime        Prec%   Dir');
      console.log('   ' + '─'.repeat(65));
      sigs.slice(0, 20).forEach(s => {
        const regEm = REGIME_EMOJI[s.regime ?? s.current_regime] ?? '?';
        const prec  = (s.conditioned_precision ?? s.precision ?? 0) * 100;
        console.log(`   📊 ${String(s.symbol).padEnd(8)} ${String(s.law_id).padEnd(24)} ${regEm} ${String(s.regime ?? s.current_regime ?? '?').padEnd(12)} ${String(prec?.toFixed(1)+'%').padStart(6)}  ${s.direction ?? '?'}`);
      });
      if (r?.current_regime)
        console.log(`\n   Market Regime Today: ${REGIME_EMOJI[r.current_regime] ?? ''} ${r.current_regime}`);
    } else {
      console.log(`\n   No conditioned signals for ${date}.`);
      console.log('   Ensure regime_law_conditions table is populated: npm run egx:regime:update');
    }
    break;
  }
  case 'matrix': {
    banner(`Law-Regime Matrix — ${lawType}`);
    const r = await pythonRegimeLawMatrix({ law_type: lawType });
    const matrix = r?.matrix ?? r?.law_matrix ?? [];
    if (matrix.length) {
      console.log(`\n   ${matrix.length} laws in matrix:\n`);
      console.log('   Law ID                  Base%   Best Regime    Best%   Lift%   Grade');
      console.log('   ' + '─'.repeat(72));
      matrix.slice(0, 20).forEach(l => {
        const regEm = REGIME_EMOJI[l.best_regime] ?? '?';
        const base  = (l.base_precision ?? l.precision_value ?? 0) * 100;
        const best  = (l.best_regime_precision ?? 0) * 100;
        const lift  = best - base;
        const grade = best >= 60 ? '🏆' : best >= 45 ? '✅' : best >= 30 ? '⚠️' : '❌';
        console.log(`   ${String(l.law_id).padEnd(24)} ${String(base?.toFixed(1)+'%').padStart(6)}  ${regEm} ${String(l.best_regime ?? '?').padEnd(12)} ${String(best?.toFixed(1)+'%').padStart(6)}  ${String(lift?.toFixed(1)+'pp').padStart(6)}  ${grade}`);
      });
      if (r?.summary) {
        const s = r.summary;
        console.log(`\n   Summary: avg_lift=+${(s.avg_lift*100)?.toFixed(1)}pp  n_high_regime=${s.n_high_precision ?? 0}`);
      }
    } else pp(r);
    break;
  }
  case 'update': {
    banner(`Update Regime Conditions — ${lawType}`);
    const r = await pythonRegimeUpdate({ law_type: lawType, date });
    if (r?.n_updated !== undefined || r?.n_conditions !== undefined) {
      console.log(`\n   ✅ Conditions updated`);
      console.log(`   Laws processed: ${r.n_processed ?? r.n_laws ?? '?'}`);
      console.log(`   Conditions set: ${r.n_updated ?? r.n_conditions ?? 0}`);
      if (r.current_regime)
        console.log(`   Current regime: ${REGIME_EMOJI[r.current_regime] ?? ''} ${r.current_regime}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Regime Laws Full — ${lawType} @ ${date}`);
    const r = await pythonRegimeBuildFull({ law_type: lawType, date });
    if (r?.n_improvable !== undefined || r?.signals !== undefined) {
      console.log(`\n   Laws analyzed:     ${r.n_laws ?? '?'}`);
      console.log(`   Improvable:        ${r.n_improvable ?? 0}`);
      console.log(`   Avg lift:          +${((r.avg_lift ?? 0)*100)?.toFixed(1)}pp`);
      const sigs = r.signals ?? r.top_signals ?? [];
      if (sigs.length) {
        console.log(`\n   🎯 Top conditioned signals today:`);
        sigs.slice(0, 8).forEach(s => {
          const regEm = REGIME_EMOJI[s.regime ?? s.current_regime] ?? '?';
          const prec  = (s.conditioned_precision ?? s.precision ?? 0) * 100;
          console.log(`     ${regEm} ${String(s.symbol ?? '').padEnd(8)} ${s.law_id}  prec=${prec?.toFixed(1)}%`);
        });
      }
    } else pp(r);
    break;
  }
  case 'populate_mut': {
    banner('Populate MUT_ Law Regime Conditioning');
    const r = await pythonRegimePopulateMut({ min_lift: minLift });
    if (r?.success) {
      if (r.message) {
        console.log(`\n   ✅ ${r.message}`);
      } else {
        console.log(`\n   Laws found:   ${r.laws_found ?? 0}`);
        console.log(`   Updated:      ${r.updated ?? 0}`);
        console.log(`   Skipped:      ${r.skipped ?? 0}`);
      }
      if (r.results?.length) {
        console.log(`\n   Law                                    Base%   Best Regime   Regime%   Lift`);
        console.log('   ' + '─'.repeat(78));
        r.results.slice(0, 15).forEach(l => {
          const regEm = REGIME_EMOJI[l.best_regime] ?? '⚖️';
          console.log(`   ${String(l.law_name).padEnd(38)} ${String((l.precision*100)?.toFixed(1)+'%').padStart(6)}  ${regEm} ${String(l.best_regime).padEnd(10)} ${String((l.regime_precision*100)?.toFixed(1)+'%').padStart(7)}  +${l.lift_pp}pp`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: analyze|signals|matrix|update|full|populate_mut`); process.exit(1);
}
