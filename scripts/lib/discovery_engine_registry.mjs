/**
 * Discovery engine registry — cadence, triggers, and loop contracts.
 * Perpetual orchestrator picks engines from feedback + time since last run.
 */
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

export const DISCOVERY_ENGINES = {
  opportunity_v2: {
    id: 'opportunity_v2',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:discovery:refresh',
    outputs: ['opportunity_score_v2', 'final_signals.actionable'],
    feeds: ['promotion', 'opportunity_quality', 'intelligence_prioritizer'],
  },
  quant_rules: {
    id: 'quant_rules',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:discover:quant',
    outputs: ['quant_discovery_rules'],
    feeds: ['score_all', 'structural_laws_bridge'],
    triggers: ['DISCOVERY_QUALITY_LOW', 'INVESTIGATE_PATTERN', 'UPRANK_BEHAVIORAL'],
  },
  dmids: {
    id: 'dmids',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:discover:rescore',
    outputs: ['structural_laws', 'dmids_profiles'],
    feeds: ['quant_rules', 'opportunity_v2'],
    triggers: ['DISCOVERY_QUALITY_LOW'],
  },
  strategy_sweep: {
    id: 'strategy_sweep',
    layer: 'research',
    cadence_hours: 720,
    npm: 'egx:discovery:strategy:sweep',
    outputs: ['param_sweep_results'],
    feeds: ['quant_rules', 'evolution'],
    triggers: ['INVESTIGATE_PATTERN', 'UPRANK_BEHAVIORAL'],
  },
  strategy_wf: {
    id: 'strategy_wf',
    layer: 'research',
    cadence_hours: 720,
    npm: 'egx:discovery:strategy:wf',
    outputs: ['walk_forward_results'],
    feeds: ['quant_rules'],
    triggers: ['DISCOVERY_QUALITY_LOW'],
  },
  strategy_ml: {
    id: 'strategy_ml',
    layer: 'research',
    cadence_hours: 336,
    npm: 'egx:discovery:strategy:ml',
    outputs: ['ml_signal_candidates'],
    feeds: ['predict_ensemble', 'score_all'],
    triggers: ['PROMOTION_GAP', 'MISSED_HIGH_OPP'],
  },
  strategy_patterns: {
    id: 'strategy_patterns',
    layer: 'research',
    cadence_hours: 336,
    npm: 'egx:discovery:strategy:patterns',
    outputs: ['egx_pattern_signals'],
    feeds: ['opportunity_v2', 'quant_rules'],
    triggers: ['INVESTIGATE_PATTERN'],
  },
  promotion_audit: {
    id: 'promotion_audit',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:discovery:promotion:audit',
    outputs: ['discovery_promotion_audit_last.json'],
    feeds: ['discovery_feedback'],
    triggers: ['PROMOTION_GAP'],
  },
  closed_loop: {
    id: 'closed_loop',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:closed:loop',
    runnable: false,
    outputs: ['p6_research_context.json', 'discovery_feedback_last.json'],
    feeds: ['all'],
    notes: 'Invoked by post_session_ops — not re-run from perpetual',
  },
  tv_microstructure: {
    id: 'tv_microstructure',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:discovery:tv:micro',
    outputs: ['tv_discovery_features', 'pine_analytics'],
    feeds: ['opportunity_v2', 'quant_rules'],
    triggers: ['TV_EDGE_GAP', 'MISSED_HIGH_OPP'],
  },
  counterfactual_atoms: {
    id: 'counterfactual_atoms',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:discovery:counterfactual:atoms',
    outputs: ['counterfactual_atoms_last.json'],
    feeds: ['quant_rules'],
    triggers: ['INVESTIGATE_PATTERN', 'DISCOVERY_QUALITY_LOW'],
  },
  regime_conditional_sweep: {
    id: 'regime_conditional_sweep',
    layer: 'research',
    cadence_hours: 168,
    npm: 'egx:discovery:regime:sweep',
    outputs: ['regime_conditional_sweep_last.json', 'regime_sweep_results'],
    feeds: ['quant_rules', 'opportunity_v2'],
    triggers: ['DISCOVERY_QUALITY_LOW', 'INVESTIGATE_PATTERN'],
  },
  hypothesis_sandbox_bridge: {
    id: 'hypothesis_sandbox_bridge',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:discovery:hypothesis:bridge',
    outputs: ['hypothesis_sandbox_bridge_last.json'],
    feeds: ['quant_rules', 'discovery_feedback'],
    triggers: ['INVESTIGATE_PATTERN', 'UPRANK_BEHAVIORAL'],
  },
  discovery_fabric: {
    id: 'discovery_fabric',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:discovery:fabric',
    outputs: ['discovery_atom_registry', 'discovery_ml_manifest.json'],
    feeds: ['quant_rules', 'opportunity_v2', 'egx_ml_trainer'],
    triggers: ['DISCOVERY_QUALITY_LOW', 'INVESTIGATE_PATTERN', 'PROMOTION_GAP'],
  },
  discovery_fabric_light: {
    id: 'discovery_fabric_light',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:discovery:fabric:light',
    outputs: ['discovery_atom_registry', 'discovery_ml_manifest.json'],
    feeds: ['opportunity_v2', 'quant_rules'],
    notes: 'EOD merge+gate without hydrate — used by tv_auto_update',
  },
  causal_discovery: {
    id: 'causal_discovery',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:causal78',
    outputs: ['causal_discovery_last.json'],
    feeds: ['discovery_fabric', 'quant_rules', 'opportunity_v2'],
    triggers: ['DISCOVERY_QUALITY_LOW', 'INVESTIGATE_PATTERN'],
  },
  egx_x_pro: {
    id: 'egx_x_pro',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:xpro',
    outputs: ['egx_x_pro_daily', 'egx_signal_tracker'],
    feeds: ['opportunity_v2', 'discovery_fabric', 'ml_feature_bridge'],
    triggers: ['MISSED_HIGH_OPP', 'TV_EDGE_GAP'],
  },
};

const MANIFEST_PATH = join(PROJECT_ROOT, 'data/discovery_engine_manifest.json');

export function readEngineManifest() {
  if (!existsSync(MANIFEST_PATH)) return { engines: {}, at: null };
  try {
    return JSON.parse(readFileSync(MANIFEST_PATH, 'utf8'));
  } catch {
    return { engines: {}, at: null };
  }
}

/** Select engines due by cadence or feedback queue triggers. */
export function planDiscoveryRun({ feedbackQueue = [], forceDaily = true } = {}) {
  const manifest = readEngineManifest();
  const now = Date.now();
  const triggerTypes = new Set(feedbackQueue.map(q => q.type));
  const planned = [];

  for (const eng of Object.values(DISCOVERY_ENGINES)) {
    if (!eng.npm || eng.runnable === false) continue;
    const last = manifest.engines?.[eng.id]?.last_run_at;
    const lastMs = last ? Date.parse(last) : 0;
    const dueByTime = !lastMs || (now - lastMs) >= eng.cadence_hours * 3600_000;
    const dueByTrigger = (eng.triggers || []).some(t => triggerTypes.has(t));
    const force = forceDaily && eng.layer === 'daily';
    if (dueByTime || dueByTrigger || force) {
      planned.push({
        id: eng.id,
        npm: eng.npm,
        reason: dueByTrigger ? 'trigger' : force ? 'daily' : 'cadence',
        layer: eng.layer,
        outputs: eng.outputs,
      });
    }
  }

  // Daily refresh always first; heavy research after closed loop context
  const order = { daily: 0, weekly: 1, research: 2, intraday: 3 };
  planned.sort((a, b) => (order[a.layer] ?? 9) - (order[b.layer] ?? 9));
  return { planned, manifest_at: manifest.at, n_triggers: triggerTypes.size };
}
