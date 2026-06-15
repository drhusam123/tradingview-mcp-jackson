/**
 * Phase 15 — client beta sign-off checklist.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { evaluatePhase14Readiness } from './phase14_graduation.mjs';
import { evaluateGraduationReadiness } from './p6_graduation_gate.mjs';
import { getLiveKpiDashboard } from './p6_live_kpi.mjs';
import { resolveResearchClientEnv } from './research_client_env.mjs';

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

export function evaluateClientBetaSignoff() {
  const p14 = evaluatePhase14Readiness();
  const readiness = evaluateGraduationReadiness();
  const env = resolveResearchClientEnv().env;
  const kpi = getLiveKpiDashboard();
  const medDelta = readJson('med_opp_delta_last.json');
  const medShadow = readJson('med_client_signal_shadow_last.json');
  const medAb = readJson('med_feed_ab_last.json');
  const mdeStability = readJson('mde_pilot_stability_last.json');
  const verify = readJson('full_verify_last.json');

  const checks = [
    {
      id: 'bootstrap_pass',
      required: true,
      pass: Boolean(readiness.client_beta_ready),
      detail: readiness.client_beta_ready ? 'historical bootstrap PASS' : 'bootstrap pending',
    },
    {
      id: 'phase14_ready',
      required: true,
      pass: Boolean(p14.phase14_ready),
      detail: 'MED shadow + probe ready',
    },
    {
      id: 'med_client_signal',
      required: true,
      pass: env.MED_CLIENT_SIGNAL === '1',
      detail: `MED_CLIENT_SIGNAL=${env.MED_CLIENT_SIGNAL}`,
    },
    {
      id: 'med_shadow_sessions',
      required: true,
      pass: Boolean(medShadow?.validation_pass),
      detail: `${medShadow?.shadow_sessions ?? 0}/${medShadow?.target_sessions ?? 5} sessions`,
    },
    {
      id: 'safety_veto',
      required: true,
      pass: process.env.EGX_SAFETY_VETO !== '0',
      detail: `EGX_SAFETY_VETO=${process.env.EGX_SAFETY_VETO ?? '1'}`,
    },
    {
      id: 'mde_opp_off',
      required: true,
      pass: env.EGX_MDE_OPP_BOOST === '0',
      detail: 'MDE shadow only',
    },
    {
      id: 'telegram_configured',
      required: true,
      pass: Boolean(process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_CHAT_ID),
      detail: process.env.TELEGRAM_BOT_TOKEN ? 'token set' : 'token missing',
    },
    {
      id: 'opp_delta_logged',
      required: true,
      pass: Boolean(medDelta?.success && (medDelta?.symbols_monitored ?? 0) > 0),
      detail: `${medDelta?.symbols_monitored ?? 0} symbols | avg Δ ${medDelta?.avg_delta_boost_vs_pen ?? '—'}`,
    },
    {
      id: 'live_session_probe',
      required: false,
      pass: (medDelta?.live_sessions_with_client_signal ?? 0) >= 1,
      detail: `${medDelta?.live_sessions_with_client_signal ?? 0} live MED sessions logged`,
    },
    {
      id: 'med_feed_ab',
      required: false,
      pass: true,
      detail: `track=${medAb?.production_track ?? '—'} streak=${medAb?.boost_win_streak ?? 0}/${medAb?.boost_streak_target ?? 5}`,
    },
    {
      id: 'med_feed_boost_gate',
      required: false,
      pass: Boolean(p14.feed_boost_ready),
      detail: p14.gates.med_feed_ab.reason,
    },
    {
      id: 'mde_memory_gate',
      required: false,
      pass: Boolean(p14.mde_memory_ready),
      detail: `${mdeStability?.days_active ?? 0}/${mdeStability?.target_days ?? 14}d pilot`,
    },
    {
      id: 'live_kpi_monitor',
      required: false,
      pass: Boolean(kpi?.ultra_safe),
      detail: kpi?.status_line ?? '—',
    },
    {
      id: 'verify_fast',
      required: false,
      pass: verify?.pass !== false,
      detail: verify?.pass === true ? 'PASS' : verify?.pass === false ? 'FAIL' : 'not run',
    },
    {
      id: 'prod_ready',
      required: false,
      pass: readJson('prod_ready_last.json')?.pass === true,
      detail: readJson('prod_ready_last.json')?.pass === true
        ? 'prod:ready PASS'
        : 'run npm run egx:phase16:production-graduation or egx:prod:ready:full',
    },
  ];

  const required = checks.filter(c => c.required);
  const requiredPass = required.every(c => c.pass);
  const optionalPass = checks.filter(c => !c.required && c.pass).length;
  const blockers = required.filter(c => !c.pass).map(c => `${c.id}: ${c.detail}`);

  return {
    at: new Date().toISOString(),
    client_beta_signed_off: requiredPass,
    required_pass: required.filter(c => c.pass).length,
    required_total: required.length,
    optional_pass: optionalPass,
    optional_total: checks.filter(c => !c.required).length,
    checks,
    blockers,
    env,
    live_kpi: kpi?.status_line,
    pending_promotions: [
      !p14.feed_boost_ready ? `MED_FEED_BOOST: ${p14.gates.med_feed_ab.reason}` : null,
      !p14.mde_memory_ready ? `EGX_MDE_BEHAVIOR_MEMORY: ${p14.gates.mde_behavior_memory.reason}` : null,
      (medDelta?.live_sessions_with_client_signal ?? 0) < 3
        ? `Live MED sessions: ${medDelta?.live_sessions_with_client_signal ?? 0}/3 recommended before full beta`
        : null,
    ].filter(Boolean),
  };
}

export function writeClientBetaSignoffSnapshot(payload = null) {
  const snap = payload ?? evaluateClientBetaSignoff();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/client_beta_signoff_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
