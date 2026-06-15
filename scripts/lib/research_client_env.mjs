/**
 * Phase 11 — resolve effective research→client env from graduation gates.
 * Never bypasses gates unless EGX_RESEARCH_ENV_FORCE=1 (operator override, logged).
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { evaluateGraduationReadiness } from './p6_graduation_gate.mjs';
import { evaluatePhase14Readiness } from './phase14_graduation.mjs';

export const RESEARCH_ENV_KEYS = [
  'MED_SHADOW',
  'MED_CLIENT_SIGNAL',
  'MED_OPP_BOOST',
  'MED_FEED_BOOST',
  'MED_FEED_PENALIZE',
  'MED_POSITION_SIZING_LIVE',
  'EGX_LRE_SHADOW',
  'EGX_LRE_OPP_BOOST',
  'EGX_LRE_FEED_BOOST',
  'EGX_MDE_SHADOW',
  'EGX_MDE_OPP_BOOST',
  'EGX_MDE_BEHAVIOR_MEMORY',
];

const SHADOW_BASE = {
  MED_SHADOW: '1',
  MED_CLIENT_SIGNAL: '0',
  MED_OPP_BOOST: '0',
  MED_FEED_BOOST: '0',
  MED_FEED_PENALIZE: '1',
  MED_POSITION_SIZING_LIVE: '0',
  EGX_LRE_SHADOW: '1',
  EGX_LRE_OPP_BOOST: '0',
  EGX_LRE_FEED_BOOST: '1',
  EGX_MDE_SHADOW: '1',
  EGX_MDE_OPP_BOOST: '0',
  EGX_MDE_BEHAVIOR_MEMORY: '0',
};

const GATE_GATED_KEYS = {
  MED_CLIENT_SIGNAL: 'med_client_signal',
  MED_FEED_BOOST: 'med_feed_boost',
  EGX_LRE_FEED_BOOST: 'lre_feed_boost',
};

function readMdePilotHints() {
  const p = join(PROJECT_ROOT, 'data/mde_shadow_promotion_hints.json');
  if (!existsSync(p)) return { eligible: false, pilot_count: 0 };
  try {
    const j = JSON.parse(readFileSync(p, 'utf8'));
    return {
      eligible: Boolean(j.pilot_eligible),
      pilot_count: j.pilot_symbols?.length ?? 0,
      mode: j.mode ?? 'shadow_hints_only',
    };
  } catch {
    return { eligible: false, pilot_count: 0 };
  }
}

function clampToGates(env, sources, readiness, force) {
  const clamps = [];
  if (force) return clamps;

  for (const [key, gateKey] of Object.entries(GATE_GATED_KEYS)) {
    const gate = readiness.gates[gateKey];
    if (!gate || gate.recommended === '1') continue;
    if (env[key] === '1' && sources[key] === 'env_override') {
      env[key] = '0';
      sources[key] = 'blocked_gate';
      clamps.push({
        key,
        reason: `${key}=1 blocked — ${gate.reason ?? 'gate not PASS'} (set EGX_RESEARCH_ENV_FORCE=1 to override)`,
      });
    }
  }

  // MDE opp boost never auto-enables in Phase 11
  if (env.EGX_MDE_OPP_BOOST === '1') {
    env.EGX_MDE_OPP_BOOST = '0';
    sources.EGX_MDE_OPP_BOOST = 'blocked_mde_shadow';
    clamps.push({
      key: 'EGX_MDE_OPP_BOOST',
      reason: 'MDE remains shadow-only — use mde_shadow_promotion_hints (no opp boost)',
    });
  }

  return clamps;
}

/** Build effective env map + metadata from graduation gates. */
export function resolveResearchClientEnv({ readiness = null, phase14 = null } = {}) {
  const r = readiness ?? evaluateGraduationReadiness();
  const p14 = phase14 ?? evaluatePhase14Readiness();
  const autoPromote = process.env.EGX_PHASE11_AUTO_PROMOTE === '1';
  const force = process.env.EGX_RESEARCH_ENV_FORCE === '1';

  const env = { ...SHADOW_BASE };
  const sources = Object.fromEntries(RESEARCH_ENV_KEYS.map(k => [k, 'default_shadow']));

  if (autoPromote && !force) {
    if (p14.gates.med_client_probe.recommended === '1') {
      env.MED_CLIENT_SIGNAL = '1';
      sources.MED_CLIENT_SIGNAL = 'phase14_shadow_pass';
    } else if (r.gates.med_client_signal.recommended === '1') {
      env.MED_CLIENT_SIGNAL = '1';
      sources.MED_CLIENT_SIGNAL = 'gate_p6_delivered_pass';
    }
    if (p14.gates.med_feed_ab.recommended === '1') {
      env.MED_FEED_BOOST = '1';
      env.MED_FEED_PENALIZE = '0';
      sources.MED_FEED_BOOST = 'phase14_ab_streak';
    } else if (r.gates.med_feed_boost.recommended === '1') {
      env.MED_FEED_BOOST = '1';
      sources.MED_FEED_BOOST = 'gate_med_graduation';
    }
    if (p14.gates.mde_behavior_memory.recommended === '1') {
      env.EGX_MDE_BEHAVIOR_MEMORY = '1';
      sources.EGX_MDE_BEHAVIOR_MEMORY = 'phase14_mde_stability';
    } else {
      const mdePilot = readMdePilotHints();
      if (process.env.EGX_MDE_PILOT_PROMOTE === '1' && mdePilot.eligible) {
        env.EGX_MDE_BEHAVIOR_MEMORY = process.env.EGX_MDE_BEHAVIOR_MEMORY ?? '0';
        sources.EGX_MDE_BEHAVIOR_MEMORY = 'mde_pilot_shadow';
      }
    }
    if (r.gates.lre_feed_boost.recommended === '1') {
      env.EGX_LRE_FEED_BOOST = '1';
      sources.EGX_LRE_FEED_BOOST = 'gate_lre_oos';
    }
  }

  for (const k of RESEARCH_ENV_KEYS) {
    if (process.env[k] !== undefined && process.env[k] !== '') {
      env[k] = String(process.env[k]);
      sources[k] = 'env_override';
    }
  }

  const clamps = clampToGates(env, sources, r, force);

  return {
    at: new Date().toISOString(),
    env,
    sources,
    clamps,
    auto_promote: autoPromote,
    force_override: force,
    readiness: {
      client_beta_ready: r.client_beta_ready,
      research_to_client_ready: r.research_to_client_ready,
      blockers: r.blockers,
    },
    phase14: {
      phase14_ready: p14.phase14_ready,
      feed_boost_ready: p14.feed_boost_ready,
      mde_memory_ready: p14.mde_memory_ready,
    },
    prefix: envToPrefix(env),
  };
}

export function envToPrefix(env) {
  return RESEARCH_ENV_KEYS.map(k => `${k}=${env[k] ?? SHADOW_BASE[k] ?? '0'}`).join(' ');
}

export function writeResearchClientEnvSnapshot(resolved = null) {
  const snap = resolved ?? resolveResearchClientEnv();
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(
    join(PROJECT_ROOT, 'data/research_client_env.json'),
    JSON.stringify(snap, null, 2),
  );
  return snap;
}
