/**
 * Phase 17 — auto-apply research promotions to .env when gates PASS.
 * Requires production_graduated + EGX_PROMOTION_AUTO_APPLY=1.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { upsertEnvVars } from './env_sync.mjs';
import { evaluatePhase14Readiness } from './phase14_graduation.mjs';
import { evaluateProductionGraduation } from './production_graduation.mjs';
import { evaluateGraduationReadiness } from './p6_graduation_gate.mjs';
import { evaluateLreOosGate } from './lre_oos_gate.mjs';
import { resolveResearchClientEnv, writeResearchClientEnvSnapshot } from './research_client_env.mjs';

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

/** Map gate recommendations → env vars safe to apply. */
export function buildPromotionEnvPatch(p14 = null, readiness = null) {
  const gates = (p14 ?? evaluatePhase14Readiness()).gates;
  const r = readiness ?? evaluateGraduationReadiness();
  const lre = evaluateLreOosGate();
  const patch = {};

  if (gates.med_feed_ab.recommended === '1') {
    patch.MED_FEED_BOOST = '1';
    patch.MED_FEED_PENALIZE = '0';
  }
  if (gates.mde_behavior_memory.recommended === '1') {
    patch.EGX_MDE_BEHAVIOR_MEMORY = '1';
  }
  if (lre.recommended === '1' || lre.pass || r.gates.lre_feed_boost.recommended === '1') {
    patch.EGX_LRE_FEED_BOOST = '1';
  }

  // Safety: never auto-enable MDE opp boost or bypass MED_CLIENT_SIGNAL gates
  delete patch.EGX_MDE_OPP_BOOST;

  return patch;
}

export function evaluatePromotionActivation(signalDate = null) {
  const p14 = evaluatePhase14Readiness();
  const prod = evaluateProductionGraduation(signalDate);
  const lreGate = evaluateLreOosGate();
  const correlation = readJson('med_live_delivery_correlation_last.json');
  const patch = buildPromotionEnvPatch(p14);
  const autoApply = process.env.EGX_PROMOTION_AUTO_APPLY === '1';

  const gates = {
    production_graduated: {
      pass: prod.production_graduated,
      detail: prod.production_graduated ? 'graduated' : prod.blockers.join('; ') || 'pending',
    },
    med_feed_boost: {
      pass: p14.gates.med_feed_ab.pass,
      recommended: p14.gates.med_feed_ab.recommended,
      reason: p14.gates.med_feed_ab.reason,
      streak: p14.gates.med_feed_ab.boost_streak,
    },
    mde_behavior_memory: {
      pass: p14.gates.mde_behavior_memory.pass,
      recommended: p14.gates.mde_behavior_memory.recommended,
      reason: p14.gates.mde_behavior_memory.reason,
      days: p14.gates.mde_behavior_memory.days_active,
    },
    lre_feed_boost: {
      pass: lreGate.pass,
      recommended: lreGate.recommended,
      reason: lreGate.reason,
      oos: `${lreGate.oos_closed}/${lreGate.oos_target}`,
    },
    delivery_correlation: {
      pass: Boolean(correlation?.success),
      detail: correlation?.summary ?? 'not run',
    },
  };

  const promotions_ready = Object.keys(patch).length > 0;
  const can_apply = autoApply && prod.production_graduated && promotions_ready;

  const blockers = [];
  if (!prod.production_graduated) blockers.push('production not graduated');
  if (!promotions_ready) blockers.push('no promotion gates PASS (keep penalize + MDE shadow)');
  if (!autoApply) blockers.push('EGX_PROMOTION_AUTO_APPLY=0');

  return {
    at: new Date().toISOString(),
    auto_apply_enabled: autoApply,
    promotions_ready,
    can_apply,
    env_patch: patch,
    gates,
    blockers,
    correlation_summary: correlation?.summary ?? null,
    effective_env: resolveResearchClientEnv().env,
    verdict: can_apply
      ? 'APPLY'
      : promotions_ready && !prod.production_graduated
        ? 'BLOCKED_GRADUATION'
        : 'MONITOR',
  };
}

export function applyPromotionEnv({ dryRun = false, signalDate = null } = {}) {
  const ev = evaluatePromotionActivation(signalDate);
  if (!ev.can_apply) {
    return { applied: false, ...ev };
  }
  const result = upsertEnvVars(ev.env_patch, { dryRun });
  for (const [k, v] of Object.entries(ev.env_patch)) {
    process.env[k] = v;
  }
  writeResearchClientEnvSnapshot(resolveResearchClientEnv());
  return { applied: true, env: result, ...ev };
}

export function writePromotionActivationSnapshot(payload = null) {
  const snap = payload ?? evaluatePromotionActivation();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/promotion_activation_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
