/**
 * Phase 14 — promotion gates from shadow pilots (historical backfill + streak tracking).
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { evaluateGraduationReadiness } from './p6_graduation_gate.mjs';

const AB_STREAK_TARGET = parseInt(process.env.EGX_MED_AB_STREAK_TARGET ?? '5', 10);
const MDE_STABILITY_DAYS = parseInt(process.env.EGX_MDE_PILOT_STABILITY_DAYS ?? '14', 10);

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

export function evaluatePhase14Readiness() {
  const readiness = evaluateGraduationReadiness();
  const medShadow = readJson('med_client_signal_shadow_last.json');
  const medAb = readJson('med_feed_ab_last.json');
  const mdePilot = readJson('mde_pilot_shadow_last.json');
  const mdeStability = readJson('mde_pilot_stability_last.json');
  const probe = readJson('med_client_signal_probe_last.json');
  const kpi = readJson('p6_live_kpi_last.json');

  const shadowPass = Boolean(medShadow?.validation_pass);
  const bootstrapPass = Boolean(readiness.client_beta_ready);

  const abStreak = medAb?.boost_win_streak ?? 0;
  const abStreakPass = abStreak >= AB_STREAK_TARGET;

  const pilotDays = mdeStability?.days_active ?? 0;
  const mdeStabilityPass = Boolean(mdeStability?.stability_pass)
    || process.env.EGX_MDE_PILOT_SKIP_STABILITY === '1';

  const gates = {
    med_client_shadow: {
      pass: shadowPass,
      sessions: medShadow?.shadow_sessions ?? 0,
      target: medShadow?.target_sessions ?? 5,
      wr_t5: medShadow?.win_rate_t5_pct,
    },
    med_client_probe: {
      pass: Boolean(probe?.probe_active) || (shadowPass && bootstrapPass),
      env: 'MED_CLIENT_SIGNAL',
      recommended: (shadowPass || bootstrapPass) ? '1' : '0',
      reason: shadowPass
        ? 'Shadow validation PASS — MED_CLIENT_SIGNAL probe enabled'
        : `Shadow ${medShadow?.shadow_sessions ?? 0}/${medShadow?.target_sessions ?? 5} sessions`,
    },
    med_feed_ab: {
      pass: abStreakPass,
      boost_streak: abStreak,
      target_streak: AB_STREAK_TARGET,
      env: 'MED_FEED_BOOST',
      recommended: abStreakPass ? '1' : '0',
      reason: abStreakPass
        ? `A/B boost won ${abStreak} sessions in a row`
        : `Boost streak ${abStreak}/${AB_STREAK_TARGET} (keep penalize)`,
    },
    mde_behavior_memory: {
      pass: mdeStabilityPass && process.env.EGX_MDE_PILOT_PROMOTE === '1',
      days_active: pilotDays,
      target_days: MDE_STABILITY_DAYS,
      env: 'EGX_MDE_BEHAVIOR_MEMORY',
      recommended: mdeStabilityPass && process.env.EGX_MDE_PILOT_PROMOTE === '1' ? '1' : '0',
      reason: mdeStabilityPass
        ? 'MDE pilot stability met'
        : `MDE pilot ${pilotDays}/${MDE_STABILITY_DAYS} days`,
    },
  };

  return {
    at: new Date().toISOString(),
    phase14_ready: gates.med_client_probe.pass,
    feed_boost_ready: gates.med_feed_ab.pass,
    mde_memory_ready: gates.mde_behavior_memory.pass,
    gates,
    readiness,
    live_kpi: kpi?.status_line ?? null,
    auto_promote: process.env.EGX_PHASE11_AUTO_PROMOTE === '1',
  };
}

export function writePhase14Snapshot(payload = null) {
  const snap = payload ?? evaluatePhase14Readiness();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/phase14_graduation_last.json'), JSON.stringify(snap, null, 2));
  return snap;
}
