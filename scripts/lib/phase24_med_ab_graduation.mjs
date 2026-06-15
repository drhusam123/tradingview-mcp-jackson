/**
 * Phase 24 — MED A/B feed boost graduation.
 */
import { evaluatePhase14Readiness } from './phase14_graduation.mjs';
import { readJson, writeJson } from './graduation_phases.mjs';

export function evaluatePhase24MedAbGraduation() {
  const p14 = evaluatePhase14Readiness();
  const ab = readJson('med_feed_ab_last.json');
  const pass = Boolean(p14.gates.med_feed_ab.pass);

  const snap = {
    at: new Date().toISOString(),
    phase24_ready: pass,
    gates: {
      med_feed_ab: {
        pass,
        streak: p14.gates.med_feed_ab.boost_streak,
        target: p14.gates.med_feed_ab.target_streak,
        recommended: p14.gates.med_feed_ab.recommended,
        detail: p14.gates.med_feed_ab.reason,
        production_track: ab?.production_track ?? 'penalize',
      },
    },
    env: { MED_FEED_BOOST: pass ? '1' : '0', MED_FEED_PENALIZE: pass ? '0' : '1' },
    note: pass ? 'Enable MED_FEED_BOOST' : 'Keep penalize — boost track not dominant',
  };
  writeJson('phase24_med_ab_graduation_last.json', snap);
  return snap;
}
