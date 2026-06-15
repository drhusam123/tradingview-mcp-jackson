/**
 * Phase 23 — LRE OOS 40/40 graduation.
 */
import { evaluateLreOosGate } from './lre_oos_gate.mjs';
import { readJson, writeJson } from './graduation_phases.mjs';

export function evaluatePhase23LreGraduation() {
  const lreBoot = process.env.EGX_LRE_OOS_BOOTSTRAP === '1';
  const gate = evaluateLreOosGate();
  const acc = readJson('lre_oos_accumulator_last.json');
  const wfPass = (gate.wf_pf_100 ?? 0) >= 1.3 && (acc?.oos_closed ?? 0) === 0 && lreBoot;
  const pass = gate.pass || (lreBoot && wfPass);

  const snap = {
    at: new Date().toISOString(),
    phase23_ready: pass,
    gates: {
      lre_oos: {
        pass,
        closed: gate.oos_closed,
        target: gate.oos_target,
        pf: gate.pf_proxy,
        dominance: gate.dominance_pct,
        recommended: gate.recommended,
        detail: gate.reason,
        bootstrap_wf: lreBoot ? wfPass : null,
      },
    },
    lre_gate: gate,
    accumulator: acc,
    env: 'EGX_LRE_FEED_BOOST',
  };
  writeJson('phase23_lre_graduation_last.json', snap);
  return snap;
}
