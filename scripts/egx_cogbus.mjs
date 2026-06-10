#!/usr/bin/env node
/**
 * Phase 42 — Central Cognitive Bus runner
 * "مصدر الحقيقة الواحد — كل شيء يمر من هنا"
 *
 * Sections:
 *   signals     — collect latest signal from all 18 phases
 *   coherence   — compute cognitive coherence score (default)
 *   directive   — get the Bus's global directive (ENGAGE/WAIT/AVOID/HALT)
 *   read        — full bus read: signals + coherence + directive
 *   matrix      — contradiction matrix between all phases
 *   full        — read + save to DB (recommended)
 */
import { pythonBusCollectSignals, pythonBusCoherence, pythonBusDirective,
         pythonBusRead, pythonBusContradictions,
         pythonBusBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'read';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🚌 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const DIR_EMOJI = { BULLISH: '📈', BEARISH: '📉', NEUTRAL: '➡️', UNKNOWN: '❓' };
const COH_EMOJI = { HIGH_COHERENCE: '✅', MODERATE_COHERENCE: '⚠️', LOW_COHERENCE: '🔴' };
const DIR_EMOJI2 = { ENGAGE: '🟢', WAIT: '🟡', AVOID: '🟠', DEFENSIVE: '⚠️', HALT: '🔴' };

switch (section) {
  case 'signals': {
    banner('Cognitive Bus: Collect Signals from All Phases');
    const r = await pythonBusCollectSignals({});
    if (r?.signals) {
      console.log(`\n   Available: ${r.n_available}  Missing: ${r.n_missing}`);
      console.log(`   Collected at: ${r.collected_at}\n`);
      console.log('   Phase Signals:');
      r.signals.forEach(s => {
        if (s.available) {
          const dir = DIR_EMOJI[s.direction] ?? '?';
          console.log(`   P${String(s.phase).padEnd(2)} ${String(s.name).padEnd(24)} ${dir} ${s.direction.padEnd(8)} val:${(s.value*100).toFixed(0)}%  [${s.urgency}]`);
        }
      });
    } else pp(r);
    break;
  }
  case 'coherence': {
    banner('Cognitive Bus: Cognitive Coherence');
    const r = await pythonBusCoherence({});
    if (r?.coherence_score !== undefined) {
      const em = COH_EMOJI[r.coherence_level] ?? '?';
      console.log(`\n   ${em} Coherence: ${r.coherence_score?.toFixed(1)}/100  (${r.coherence_level})`);
      console.log(`   Narrative: ${DIR_EMOJI[r.narrative_direction] ?? '?'} ${r.narrative_direction}`);
      console.log(`   Bullish: ${r.n_bullish}  Bearish: ${r.n_bearish}  Neutral: ${r.n_neutral}`);
      console.log(`   Agreement: ${(r.direction_coherence_fraction*100)?.toFixed(0)}%`);
      if (r.contradiction_pairs?.length) {
        console.log('\n   ⚠️  Contradictions:');
        r.contradiction_pairs.slice(0, 5).forEach(p =>
          console.log(`   P${p.phase_a}(${p.phase_a_direction}) ↔ P${p.phase_b}(${p.phase_b_direction})`));
      }
    } else pp(r);
    break;
  }
  case 'directive': {
    banner('Cognitive Bus: Global Directive');
    const r = await pythonBusDirective({});
    if (r?.directive) {
      const em = DIR_EMOJI2[r.directive] ?? '?';
      console.log(`\n   ${em} Directive: ${r.directive}  (confidence: ${(r.confidence*100)?.toFixed(0)}%)`);
      console.log(`   Reason: ${r.reason}`);
      if (r.key_signals?.length) {
        console.log('\n   Key contributing signals:');
        r.key_signals.forEach(s =>
          console.log(`   P${s.phase}: ${s.direction}  val:${(s.value*100).toFixed(0)}%`));
      }
    } else pp(r);
    break;
  }
  case 'read': {
    banner('Cognitive Bus: Full Read');
    const r = await pythonBusRead({});
    if (r?.directive) {
      const dem = DIR_EMOJI2[r.directive?.directive] ?? '?';
      const cem = COH_EMOJI[r.coherence?.coherence_level] ?? '?';
      console.log(`\n   ${dem} Directive: ${r.directive?.directive}  |  Confidence: ${(r.directive?.confidence*100)?.toFixed(0)}%`);
      console.log(`   ${cem} Coherence: ${r.coherence?.coherence_score?.toFixed(1)}/100  (${r.coherence?.coherence_level})`);
      console.log(`   Narrative: ${DIR_EMOJI[r.coherence?.narrative_direction] ?? '?'} ${r.coherence?.narrative_direction}`);
      console.log(`   Global confidence: ${(r.global_confidence*100)?.toFixed(0)}%`);
      console.log(`\n   Available signals: ${r.signals?.filter(s => s.available)?.length ?? 0}`);
      console.log(`   Generated: ${r.generated_at}`);
    } else pp(r);
    break;
  }
  case 'matrix': {
    banner('Cognitive Bus: Contradiction Matrix');
    const r = await pythonBusContradictions({});
    if (r?.agreement_ratio !== undefined) {
      console.log(`\n   Agreement ratio: ${(r.agreement_ratio*100)?.toFixed(0)}%`);
      if (r.most_contradicting_pairs?.length) {
        console.log('\n   Most contradicting pairs:');
        r.most_contradicting_pairs.forEach(p =>
          console.log(`   ⚠️  P${p.phase_a} ↔ P${p.phase_b}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Cognitive Bus: Full Build + Save');
    const r = await pythonBusBuildFull({});
    if (r?.directive) {
      const em = DIR_EMOJI2[r.directive] ?? '?';
      console.log(`\n   ${em} Directive: ${r.directive}`);
      console.log(`   Coherence: ${r.coherence_score?.toFixed(1)}/100  (${r.coherence_level})`);
      console.log(`   Narrative: ${r.narrative_direction}`);
      console.log(`   Contradictions: ${r.n_contradictions}`);
      console.log(`   Global confidence: ${(r.global_confidence*100)?.toFixed(0)}%`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
