#!/usr/bin/env node
/**
 * Phase 47 — Multi-Horizon Intelligence Engine runner
 * "تحليل متعدد الأفق — Intraday / Swing / Weekly / Monthly / Quarterly"
 *
 * Sections:
 *   analyze     — analyze a specific horizon  --horizon SWING
 *   multi       — full multi-view across all 5 horizons (default)
 *   conflict    — detect inter-horizon conflicts and arbitration
 *   dominant    — find the dominant signal horizon
 *   full        — dominant + conflict + save to DB (recommended)
 */
import { pythonHorizonAnalyze, pythonHorizonMultiView, pythonHorizonConflict,
         pythonHorizonDominant, pythonHorizonBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'multi';
const horizon = args[args.indexOf('--horizon') + 1] ?? 'SWING';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🌐 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const DIR_EMOJI = { BUY: '📈', SELL: '📉', NEUTRAL: '⚖️', MIXED: '🔀' };
const CONF_EMOJI = { HIGH: '🟢', MEDIUM: '🟡', LOW: '🔴', VERY_LOW: '⛔' };

switch (section) {
  case 'analyze': {
    banner(`Multi-Horizon: Analyze — ${horizon}`);
    const r = await pythonHorizonAnalyze({ horizon });
    if (r?.direction) {
      const de = DIR_EMOJI[r.direction] ?? '?';
      const ce = CONF_EMOJI[r.confidence_level] ?? '?';
      console.log(`\n   ${de} Direction: ${r.direction}  |  ${ce} Confidence: ${r.confidence_level}`);
      console.log(`   Horizon: ${r.horizon}  (${r.arabic ?? ''})  ${r.description ?? ''}`);
      console.log(`   Signal strength: ${(r.signal_strength * 100)?.toFixed(0)}%`);
      console.log(`   Uncertainty: ${(r.uncertainty * 100)?.toFixed(1)}%`);
      console.log(`   Laws contributing: ${r.n_laws}`);
      if (r.top_laws?.length) {
        console.log('\n   Top laws:');
        r.top_laws.slice(0, 5).forEach(l =>
          console.log(`   • ${String(l.name ?? l.law_name).padEnd(35)} ${l.direction ?? ''} (${(l.precision ?? l.eae ?? 0).toFixed(3)})`));
      }
    } else pp(r);
    break;
  }
  case 'multi': {
    banner('Multi-Horizon: Full Multi-View');
    const r = await pythonHorizonMultiView({});
    if (r?.horizons) {
      console.log('\n   Horizon          Direction    Score   Regime           Uncertainty  Laws(B/Be)');
      console.log('   ' + '─'.repeat(80));
      Object.entries(r.horizons).forEach(([k, h]) => {
        const de = DIR_EMOJI[h.direction] ?? '?';
        const score = h.direction_score != null ? (h.direction_score >= 0 ? '+' : '') + h.direction_score?.toFixed(2) : 'N/A';
        const unc   = h.horizon_uncertainty != null ? (h.horizon_uncertainty * 100)?.toFixed(0) + '%' : 'N/A';
        const laws  = `${h.n_bullish_laws ?? 0}/${h.n_bearish_laws ?? 0}`;
        console.log(`   ${de} ${String(k).padEnd(14)} ${String(h.direction).padEnd(8)}  ${String(score).padStart(6)}  ${String(h.regime_used ?? '').padEnd(16)} ${String(unc).padStart(6)}   ${laws}`);
      });
      if (r.overall_alignment !== undefined) {
        const de = DIR_EMOJI[r.dominant_horizon?.direction] ?? '?';
        console.log(`\n   ${de} Dominant: ${r.dominant_horizon?.horizon ?? 'N/A'}  |  Alignment: ${(r.overall_alignment * 100)?.toFixed(0)}%`);
        console.log(`   Inter-horizon conflicts: ${r.n_conflicts}`);
      }
    } else pp(r);
    break;
  }
  case 'conflict': {
    banner('Multi-Horizon: Inter-Horizon Conflict Detection');
    const r = await pythonHorizonConflict({});
    if (r?.n_conflicts !== undefined) {
      const em = r.n_conflicts === 0 ? '✅' : r.n_conflicts <= 2 ? '🟡' : '🔴';
      console.log(`\n   ${em} Conflicts: ${r.n_conflicts}  |  Alignment: ${(r.alignment_score * 100)?.toFixed(0)}%`);
      console.log(`   Recommended action: ${r.recommended_action ?? 'N/A'}`);
      if (r.conflicts?.length) {
        console.log('\n   Conflict details:');
        r.conflicts.forEach(c =>
          console.log(`   ⚡ ${String(c.horizon_a).padEnd(10)} (${c.a_direction}) vs ${String(c.horizon_b).padEnd(8)} (${c.b_direction}) → ${c.resolution}`));
      }
    } else pp(r);
    break;
  }
  case 'dominant': {
    banner('Multi-Horizon: Dominant Signal');
    const r = await pythonHorizonDominant({});
    if (r?.dominant_horizon) {
      const de = DIR_EMOJI[r.direction] ?? '?';
      const ce = CONF_EMOJI[r.confidence_level] ?? '?';
      console.log(`\n   ${de} Dominant Horizon: ${r.dominant_horizon}`);
      console.log(`   Direction: ${r.direction}  |  ${ce} Confidence: ${r.confidence_level}`);
      console.log(`   Signal strength: ${(r.signal_strength * 100)?.toFixed(0)}%`);
      console.log(`   Reasoning: ${r.reasoning ?? ''}`);
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Multi-Horizon: Full Build + Save');
    const r = await pythonHorizonBuildFull({});
    if (r?.dominant_horizon) {
      const de = DIR_EMOJI[r.direction] ?? '?';
      console.log(`\n   ${de} Dominant: ${r.dominant_horizon}  →  ${r.direction}`);
      console.log(`   Conflict: ${r.conflict_level}  |  Agreement: ${(r.agreement_score * 100)?.toFixed(0)}%`);
      console.log(`   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
