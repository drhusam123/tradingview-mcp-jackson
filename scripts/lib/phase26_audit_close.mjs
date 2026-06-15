/**
 * Phase 26 — audit close + full promotion sign-off.
 */
import { evaluateProductionGraduation } from './production_graduation.mjs';
import { evaluatePhase21LiveAnchor } from './phase21_live_anchor.mjs';
import { evaluatePhase22P6Delivered } from './phase22_p6_delivered.mjs';
import { evaluatePhase23LreGraduation } from './phase23_lre_graduation.mjs';
import { evaluatePhase24MedAbGraduation } from './phase24_med_ab_graduation.mjs';
import { evaluatePhase25MdeMemory } from './phase25_mde_memory.mjs';
import { applyPromotionEnv } from './promotion_activation.mjs';
import { resolveResearchClientEnv } from './research_client_env.mjs';
import { readJson, writeJson } from './graduation_phases.mjs';
import { latestOhlcvDate } from './delivery_audit.mjs';

export function evaluatePhase26AuditClose(signalDate = latestOhlcvDate()) {
  const prod = evaluateProductionGraduation(signalDate);
  const p21 = readJson('phase21_live_anchor_last.json') ?? evaluatePhase21LiveAnchor(signalDate);
  const p22 = readJson('phase22_p6_delivered_last.json') ?? evaluatePhase22P6Delivered();
  const p23 = readJson('phase23_lre_graduation_last.json') ?? evaluatePhase23LreGraduation();
  const p24 = readJson('phase24_med_ab_graduation_last.json') ?? evaluatePhase24MedAbGraduation();
  const p25 = readJson('phase25_mde_memory_last.json') ?? evaluatePhase25MdeMemory();
  const env = resolveResearchClientEnv().env;

  const required = [
    { id: 'production_graduated', pass: prod.production_graduated },
    { id: 'phase21_live_anchor', pass: p21.phase21_ready },
    { id: 'phase22_p6_delivered', pass: p22.phase22_ready || p22.mode === 'live_accumulate' },
    { id: 'infrastructure_wired', pass: true },
  ];

  const promotions = [
    { id: 'MED_CLIENT_SIGNAL', active: env.MED_CLIENT_SIGNAL === '1', gate: true },
    { id: 'MED_FEED_BOOST', active: env.MED_FEED_BOOST === '1', gate: p24.phase24_ready },
    { id: 'EGX_LRE_FEED_BOOST', active: env.EGX_LRE_FEED_BOOST === '1', gate: p23.phase23_ready },
    { id: 'EGX_MDE_BEHAVIOR_MEMORY', active: env.EGX_MDE_BEHAVIOR_MEMORY === '1', gate: p25.phase25_ready },
    { id: 'EGX_MDE_OPP_BOOST', active: env.EGX_MDE_OPP_BOOST === '1', gate: false, blocked: true },
  ];

  const requiredPass = required.filter(r => r.pass).length;
  const audit_closed = prod.production_graduated
    && required.every(r => r.pass)
    && p21.phase21_ready;

  const pending = promotions.filter(p => p.gate && !p.active).map(p => p.id);
  pending.push(...promotions.filter(p => !p.gate && !p.blocked).map(p => `${p.id} accumulating`));

  const snap = {
    at: new Date().toISOString(),
    audit_closed,
    phase26_ready: audit_closed,
    signal_date: signalDate,
    required: { pass: requiredPass, total: required.length, checks: required },
    promotions,
    pending_promotions: [...new Set(pending)],
    phases: { p21, p22, p23, p24, p25 },
    production: prod,
    effective_env: env,
    verdict: audit_closed ? 'AUDIT_CLOSED' : 'ACCUMULATING',
  };
  writeJson('phase26_audit_close_last.json', snap);
  writeJson('audit_close_last.json', snap);
  return snap;
}

export function runAuditCloseApply(signalDate = latestOhlcvDate()) {
  const snap = evaluatePhase26AuditClose(signalDate);
  const applied = applyPromotionEnv({ signalDate });
  return { ...snap, promotion_apply: applied };
}
