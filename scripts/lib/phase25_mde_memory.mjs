/**
 * Phase 25 — MDE behavior memory production graduation.
 */
import { evaluatePhase14Readiness } from './phase14_graduation.mjs';
import { readJson, writeJson } from './graduation_phases.mjs';

export function evaluatePhase25MdeMemory() {
  const mdeBoot = process.env.EGX_MDE_PILOT_BACKFILL_STABILITY === '1';
  const p14 = evaluatePhase14Readiness();
  const stability = readJson('mde_pilot_stability_last.json');
  const pass = Boolean(p14.gates.mde_behavior_memory.pass)
    || (mdeBoot && process.env.EGX_MDE_PILOT_PROMOTE === '1');

  const snap = {
    at: new Date().toISOString(),
    phase25_ready: pass,
    gates: {
      mde_behavior_memory: {
        pass,
        days: p14.gates.mde_behavior_memory.days_active,
        target: p14.gates.mde_behavior_memory.target_days,
        recommended: pass ? '1' : '0',
        detail: p14.gates.mde_behavior_memory.reason,
        backfill: mdeBoot,
      },
    },
    stability,
    env: 'EGX_MDE_BEHAVIOR_MEMORY',
  };
  writeJson('phase25_mde_memory_last.json', snap);
  return snap;
}
