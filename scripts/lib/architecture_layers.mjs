/**
 * EGX Discovery Fabric — canonical layer graph (L0→L11).
 * Single source of truth for upstream/downstream wiring audits.
 */
export const LAYER_GRAPH = [
  {
    id: 'L0',
    name: 'Market data',
    anchors: ['ohlcv_history', 'stock_universe'],
    upstream: [],
    downstream: ['L1', 'L2', 'L3'],
    producers: ['egx_tv_auto_update', 'daily_update.mjs', 'tv_universe_sync.mjs', 'tv_data_reconcile.mjs'],
  },
  {
    id: 'L1',
    name: 'Indicators',
    anchors: ['indicators_cache'],
    upstream: ['L0'],
    downstream: ['L2', 'L3', 'L4', 'L7'],
    producers: ['rebuild_indicators.mjs'],
  },
  {
    id: 'L2',
    name: 'Microstructure & regime',
    anchors: ['pine_analytics', 'tv_discovery_features', 'closing_pressure_daily', 'market_breadth_enhanced'],
    upstream: ['L0', 'L1'],
    downstream: ['L4', 'L7', 'L11'],
    producers: ['fetch_pine_analytics', 'tv_microstructure_engine', 'egx_cross_market'],
  },
  {
    id: 'L3',
    name: 'Scans & setups',
    anchors: ['scans', 'setup_performance'],
    upstream: ['L0', 'L1'],
    downstream: ['L5', 'L7'],
    producers: ['scan_today.mjs'],
  },
  {
    id: 'L4',
    name: 'ML features & predictions',
    anchors: ['explosion_predictions', 'meta_label_scores', 'conformal_scores'],
    upstream: ['L0', 'L1', 'L2'],
    downstream: ['L5', 'L7', 'L11'],
    producers: ['egx_explosion_ml', 'ml_advanced.py', 'egx_mladv:daily'],
  },
  {
    id: 'L5',
    name: 'UES scoring & final_signals',
    anchors: ['final_signals'],
    upstream: ['L3', 'L4', 'L7'],
    downstream: ['L6', 'L10'],
    producers: ['signal_integration score_all', 'client_signal_promotion'],
  },
  {
    id: 'L6',
    name: 'Cognitive arbitration',
    anchors: ['arbitration_decisions'],
    upstream: ['L5'],
    downstream: ['L5', 'L10'],
    producers: ['cognitive_arbitration', 'apply_arbitration_veto'],
  },
  {
    id: 'L7',
    name: 'Opportunity map',
    anchors: ['opportunity_score_v2', 'quant_discovery_rules'],
    upstream: ['L1', 'L2', 'L4', 'L11'],
    downstream: ['L5'],
    producers: ['opportunity_score_v2.py', 'discovery_refresh'],
  },
  {
    id: 'L8',
    name: 'Outcomes & learning',
    anchors: ['recommendation_outcomes', 'bayesian_wr'],
    upstream: ['L5', 'L10'],
    downstream: ['L11', 'closed_loop'],
    producers: ['egx_learning_loop', 'delivered_outcomes'],
  },
  {
    id: 'L9',
    name: 'Research sandbox',
    anchors: ['sandbox_hypotheses', 'grid_runs', 'walkforward_results'],
    upstream: ['L8'],
    downstream: ['L11'],
    producers: ['hypothesis_sandbox_bridge', 'regime_conditional_sweep'],
    optional: true,
  },
  {
    id: 'L10',
    name: 'Client delivery',
    anchors: ['notification_delivery_audit'],
    upstream: ['L5', 'L6'],
    downstream: ['L8'],
    producers: ['egx_telegram_cron', 'telegram_send_cards'],
    optional: true,
  },
  {
    id: 'L11',
    name: 'Discovery fabric',
    anchors: ['discovery_atom_registry', 'discovery_fabric_runs'],
    upstream: ['L2', 'L4', 'L8', 'L9'],
    downstream: ['L7', 'L4'],
    producers: ['egx_discovery_fabric', 'discovery_domain_miners'],
  },
];

/** Ordered pipeline for discovery_refresh (data must exist before downstream). */
export const REFRESH_PIPELINE = [
  'L2_tv_microstructure',
  'L8_counterfactual_atoms',
  'L11_discovery_fabric',
  'L4_ml_upstream_align',
  'L7_opportunity_score_v2',
  'L5_score_all',
  'L6_cognitive_arbitration',
  'L6_apply_arbitration_veto',
  'L5_client_signal_promotion',
  'L7_discovery_quality',
];

export const CLOSED_LOOP_PIPELINE = [
  'L8_sync_delivered_outcomes',
  'L8_proof_snapshot',
  'L8_learning_loop',
  'L11_discovery_fabric',
  'L7_discovery_refresh',
  'L8_directive_resolve',
  'L9_p6_research_context',
];
