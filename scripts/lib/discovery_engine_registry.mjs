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
  egx_market_discovery: {
    id: 'egx_market_discovery',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:mde',
    outputs: [
      'egx_market_discovery_daily',
      'mde_shadow_last.json',
      'discovery_mde_manifest.json',
      'mde_shadow_attribution_last.json',
    ],
    feeds: ['discovery_fabric'],
    npm_attribution: 'egx:mde:attribution',
    notes: 'Phase 2 shadow — fabric mde_* atoms + OOS attribution; additive only; no opp/promotion/UES',
  },
  mde_signal_provider: {
    id: 'mde_signal_provider',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:mde:signal-provider',
    outputs: [
      'mde_shadow_signals_daily',
      'mde_signal_provider_last.json',
      'gate_audit_snapshots.shadow_mde_*',
    ],
    feeds: ['discovery_fabric', 'score_all_shadow'],
    requires: ['egx_market_discovery'],
    notes: 'Phase 2.10E — COMP_001B + PRDC_SPECIAL shadow provider; RESEARCH_EDGE_ONLY; no client path',
  },
  egx_liquidity_rotation: {
    id: 'egx_liquidity_rotation',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:daily',
    outputs: [
      'lre_explosion_events',
      'lre_explosion_archaeology.json',
      'lre_explosion_families.json',
      'lre_pre_explosion_fingerprints.json',
      'lre_radar_last.json',
      'lre_daily_scores_last.json',
      'lre_rotation_graph_last.json',
      'lre_daily_scores',
      'lre_market_daily',
    ],
    feeds: ['discovery_fabric'],
    notes: 'LRE-1.0 archaeology + LRE-2.0 daily radar; shadow only; no client/UES impact',
  },
  lre_signal_provider: {
    id: 'lre_signal_provider',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:signal-provider',
    outputs: [
      'lre_shadow_signals_daily',
      'lre_signal_provider_last.json',
      'gate_audit_snapshots.shadow_lre_*',
    ],
    feeds: ['discovery_fabric', 'score_all_shadow'],
    requires: ['egx_liquidity_rotation'],
    notes: 'LRE-2.0 shadow provider — 5 lists observe only; no client path',
  },
  lre_forward_paper: {
    id: 'lre_forward_paper',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:paper-trading',
    outputs: [
      'lre_forward_paper_trades.json',
      'lre_ignition_forward_monitor.json',
      'lre_top_eps_special_track.json',
      'lre_3_0_historical_replay.json',
      'lre_client_grade_gate_status.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['egx_liquidity_rotation', 'lre_signal_provider'],
    notes: 'LRE-3.0 paper gate — ignition replay; RESEARCH_EDGE_ONLY until PF≥2',
  },
  lre_filter_tightening: {
    id: 'lre_filter_tightening',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:filter-tightening',
    outputs: [
      'lre_3_1_filter_replay.json',
      'lre_3_1_sanitized_replay.json',
      'lre_3_1_mode_comparison.json',
      'lre_3_1_forward_candidates_last.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['egx_liquidity_rotation', 'lre_forward_paper'],
    notes: 'LRE-3.1 A-family tight filter + stop-prone audit; shadow only',
  },
  lre_stage_rebuild: {
    id: 'lre_stage_rebuild',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:stage-rebuild',
    outputs: [
      'lre_3_2_stage_rebuild_replay.json',
      'lre_3_2_threshold_diagnostic.json',
      'lre_3_2_entry_timing_diagnostic.json',
      'lre_3_2_stop_diagnostic.json',
      'lre_3_2_candidate_review.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['lre_filter_tightening'],
    notes: 'LRE-3.2 sub-stage rebuild + timing/stop diagnostic; shadow only',
  },
  lre_dual_gate: {
    id: 'lre_dual_gate',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:dual-gate',
    outputs: [
      'lre_mde_dual_gate_audit',
      'lre_mde_dual_gate_audit_last.json',
      'lre_mde_sequence_audit.json',
      'lre_mde_oos_results.json',
      'lre_mde_candidate_review_last.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['lre_stage_rebuild', 'mde_signal_provider'],
    notes: 'LRE-3.3 dual-gate observe-only — LRE radar + MDE confirmation audit; no client path',
  },
  lre_confluence_robustness: {
    id: 'lre_confluence_robustness',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:confluence-robustness',
    outputs: [
      'lre_3_4_confluence_dominance_detox.json',
      'lre_3_4_leave_one_symbol_out.json',
      'lre_3_4_leave_one_sector_out.json',
      'lre_3_4_bootstrap_results.json',
      'lre_3_4_entry_cost_stop_robustness.json',
      'lre_3_4_candidate_review.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['lre_dual_gate'],
    notes: 'LRE-3.4 confluence dominance detox + bootstrap; shadow only',
  },
  lre_shadow_pilot: {
    id: 'lre_shadow_pilot',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:shadow-pilot-design',
    outputs: [
      'lre_dual_gate_shadow_pilot',
      'lre_3_5_pilot_caps_replay.json',
      'lre_3_5_shadow_pilot_last.json',
      'lre_3_5_bucket_distribution.json',
      'lre_3_5_current_candidates_review.json',
      'lre_3_5_forward_ledger_last.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['lre_confluence_robustness'],
    notes: 'LRE-3.5 capped dual-gate shadow pilot design; no client path',
  },
  lre_walk_forward_pilot: {
    id: 'lre_walk_forward_pilot',
    layer: 'weekly',
    cadence_hours: 168,
    npm: 'egx:lre:walk-forward-pilot',
    outputs: [
      'lre_walk_forward_shadow_pilot',
      'lre_3_6a_walk_forward_results.json',
      'lre_3_6a_walk_forward_ledger.json',
      'lre_3_6a_caps_comparison.json',
      'lre_3_6a_threshold_leakage_audit.json',
      'lre_3_6a_bucket_results.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['lre_shadow_pilot'],
    notes: 'LRE-3.6A historical walk-forward capped shadow pilot; no client path',
  },
  lre_research_feed: {
    id: 'lre_research_feed',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:research-feed',
    outputs: [
      'lre_research_feed_daily',
      'lre_research_feed_last.json',
      'discovery_lre_manifest.json',
      'lre_learning_snapshot.json',
    ],
    feeds: ['discovery_fabric', 'opportunity_v2', 'intelligence_prioritizer'],
    requires: ['lre_walk_forward_pilot', 'egx_liquidity_rotation'],
    notes: 'LRE-4.0 unified research feed — additive only; no client path',
  },
  lre_forward_shadow: {
    id: 'lre_forward_shadow',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:forward-shadow',
    outputs: ['lre_forward_shadow_ledger', 'lre_3_6b_forward_shadow_last.json'],
    feeds: ['discovery_fabric'],
    requires: ['lre_research_feed'],
    notes: 'LRE-3.6B live forward shadow ledger after walk-forward window',
  },
  lre_dual_gate_daily: {
    id: 'lre_dual_gate_daily',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:dual-gate-daily',
    outputs: ['lre_mde_dual_gate_audit', 'lre_dual_gate_daily_last.json'],
    feeds: ['lre_research_feed'],
    requires: ['egx_liquidity_rotation', 'egx_market_discovery'],
    notes: 'Incremental causal dual-gate upsert for trade_date',
  },
  lre_research_acceptance: {
    id: 'lre_research_acceptance',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:acceptance',
    outputs: ['lre_4_0_acceptance_last.json'],
    requires: ['lre_research_feed'],
    notes: 'LRE-4.0 invariant gate — actionable unchanged, no client path',
  },
  lre_research_status: {
    id: 'lre_research_status',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:lre:status',
    outputs: ['lre_4_0_status_last.json'],
    requires: ['lre_research_feed', 'lre_forward_shadow'],
    notes: 'LRE-4.0 health + live OOS graduation tracker',
  },
  med_daily_chain: {
    id: 'med_daily_chain',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:med:phase3',
    outputs: [
      'med_daily_scores',
      'med_research_feed',
      'med_analogue_scores_daily',
      'med_0_3_daily_chain_last.json',
      'med_0_3_discovery_report.json',
      'med_false_edge_feed_last.json',
      'discovery_med_manifest.json',
    ],
    feeds: ['discovery_fabric'],
    requires: ['lre_research_feed', 'egx_market_discovery'],
    notes: 'MED-0.3 EGX-calibrated shadow field — discovery atoms + false-edge feed',
  },
  med_forward_shadow: {
    id: 'med_forward_shadow',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:med:forward-shadow',
    outputs: ['med_forward_shadow_ledger', 'med_forward_shadow_last.json'],
    feeds: ['discovery_fabric'],
    requires: ['med_daily_chain'],
    notes: 'MED live forward ledger from 2026-06-12',
  },
  med_research_status: {
    id: 'med_research_status',
    layer: 'daily',
    cadence_hours: 24,
    npm: 'egx:med:phase3:verify',
    outputs: ['med_0_3_status_last.json', 'med_0_3_acceptance_last.json'],
    requires: ['med_daily_chain', 'med_forward_shadow'],
    notes: 'MED graduation tracker — shadow only',
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
