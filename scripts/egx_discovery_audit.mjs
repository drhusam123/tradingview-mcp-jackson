#!/usr/bin/env node
/**
 * Discovery phase audit — checks closed-loop wiring and pipeline gaps.
 */
import { existsSync, readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { buildDiscoveryParams, discoveryContextSummary } from './lib/discovery_context.mjs';
import { countDirectiveStats } from './lib/directive_resolver.mjs';
import { latestStructuralLawsFile } from './lib/structural_laws_bridge.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const checks = [];

function pass(id, detail) {
  checks.push({ id, ok: true, detail });
}
function fail(id, detail) {
  checks.push({ id, ok: false, detail });
}

function readText(rel) {
  const p = join(ROOT, rel);
  return existsSync(p) ? readFileSync(p, 'utf8') : '';
}

// 1. No duplicate bare quant in daily pipeline
const tvAuto = readText('scripts/egx_tv_auto_update.mjs');
if (tvAuto.includes("quant_discovery.py run '{}'")) {
  fail('no_duplicate_quant', 'egx_tv_auto_update still runs bare quant_discovery without feedback');
} else {
  pass('no_duplicate_quant', 'Daily pipeline uses research_director feedback-aware quant only');
}

// 2. skip_ues_score in research director path
if (tvAuto.includes('skip_ues_score')) {
  pass('skip_early_score', 'research_director defers score_all until post-scan');
} else {
  fail('skip_early_score', 'research_director may score before scan_today');
}

// 3. Opportunity v2 before promotion in daily pipeline
const oppIdx = tvAuto.indexOf('opportunity_score_v2.py');
const promoIdx = tvAuto.indexOf('client_signal_promotion.py');
const scoreIdx = tvAuto.indexOf('score_all');
if (oppIdx > scoreIdx && promoIdx > oppIdx) {
  pass('opp_before_promotion', 'opportunity_score_v2 runs after score_all and before promotion');
} else {
  fail('opp_before_promotion', 'Pipeline order: score → opp → promotion broken');
}

// 4. P6 context wired to promotion
if (tvAuto.includes('buildDiscoveryParams') && tvAuto.includes('promotionParams')) {
  pass('p6_promotion_context', 'client_signal_promotion receives discovery context');
} else {
  fail('p6_promotion_context', 'Promotion missing P6 discovery params');
}

// 5. Discovery context files
const ctx = buildDiscoveryParams();
const summary = discoveryContextSummary(ctx);
pass('discovery_context', `feedback=${summary.feedback_items} directives=${summary.pending_directives}`);

// 6. PROMOTION_GAP handler in Python loader
const loader = readText('scripts/python/discovery_feedback_loader.py');
if (loader.includes('PROMOTION_GAP') && loader.includes('load_promotion_tuning')) {
  pass('promotion_gap_handler', 'discovery_feedback_loader handles PROMOTION_GAP');
} else {
  fail('promotion_gap_handler', 'PROMOTION_GAP tuning missing');
}

// 7. Quant consumes p6 hints
const quant = readText('scripts/python/quant_discovery.py');
if (quant.includes('apply_p6_research_hints')) {
  pass('quant_p6_hints', 'quant_discovery applies P6 priorities/directives');
} else {
  fail('quant_p6_hints', 'quant_discovery ignores P6 hints');
}

// 8. Structural laws bridge
const structFile = latestStructuralLawsFile();
if (structFile) {
  pass('structural_laws_kb', `Latest KB: ${structFile.replace(`${ROOT}/`, '')}`);
} else {
  fail('structural_laws_kb', 'No structural_laws knowledge base file');
}

// 9. Weekly discover uses discovery context
const discover = readText('scripts/egx_discover.mjs');
if (discover.includes('buildDiscoveryParams')) {
  pass('weekly_discover_context', 'egx_discover.mjs uses unified discovery context');
} else {
  fail('weekly_discover_context', 'Weekly discover missing P6 context');
}

// 10. Discovery quality gate module
const qualityGate = readText('scripts/python/discovery_quality_gate.py');
if (qualityGate.includes('filter_quant_candidates') && qualityGate.includes('SWEET_SPOT_ATOMS')) {
  pass('discovery_quality_gate', 'TRADING_LESSONS quality gate active');
} else {
  fail('discovery_quality_gate', 'discovery_quality_gate.py missing');
}

// 11. Opportunity TRADING_LESSONS boosts
const oppPy = readText('scripts/python/opportunity_score_v2.py');
if (oppPy.includes('LOWER_THIRD_CLOSE') && oppPy.includes('VOL_SWEET_SPOT')) {
  pass('opp_lessons_boost', 'Opportunity v2 applies lower-third + vol sweet-spot');
} else {
  fail('opp_lessons_boost', 'Opportunity v2 missing TRADING_LESSONS boosts');
}

// 12. Promotion policy bridge (opp v2 stages + arbitration override)
const promoPolicy = readText('scripts/python/discovery_promotion_policy.py');
if (promoPolicy.includes('OPP_V2_PROMOTABLE_STAGES') && promoPolicy.includes('arbitration_allows_discovery_override')) {
  pass('promotion_policy_bridge', 'discovery_promotion_policy closes PROMOTION_GAP');
} else {
  fail('promotion_policy_bridge', 'discovery_promotion_policy.py incomplete');
}

// 13. Arbitration liquidity SSOT (was reading missing liquidity_profiles table)
const arbPy = readText('scripts/python/cognitive_arbitration.py');
if (arbPy.includes('liquidity_profile') && arbPy.includes('_normalize_liquidity_tier')) {
  pass('arbitration_liquidity_ssot', 'cognitive_arbitration reads liquidity_profile');
} else {
  fail('arbitration_liquidity_ssot', 'Arbitration still using stale liquidity source');
}

// 14. Discovery refresh includes arbitration before promotion
const refresh = readText('scripts/egx_discovery_refresh.mjs');
if (refresh.includes('cognitive_arbitration') && refresh.includes('apply_arbitration_veto') && refresh.includes('latestReadySignalDate')) {
  pass('refresh_arbitration_order', 'discovery refresh: score → arbitrate → promote');
} else {
  fail('refresh_arbitration_order', 'Discovery refresh missing arbitration step');
}

// 15. Phase 2 — TV microstructure + counterfactual atoms
const tvMicro = readText('scripts/tv_microstructure_engine.mjs');
const tvFeat = readText('scripts/python/tv_discovery_features.py');
const cfMiner = readText('scripts/python/counterfactual_atom_miner.py');
if (tvMicro.includes('tv_discovery_features') && tvFeat.includes('derive_atoms')) {
  pass('tv_microstructure_engine', 'TV sensing → tv_discovery_features atoms');
} else {
  fail('tv_microstructure_engine', 'TV microstructure engine missing');
}
if (cfMiner.includes('REASON_TO_BOOST') && readText('scripts/python/quant_discovery.py').includes('load_counterfactual_seeds')) {
  pass('counterfactual_atom_miner', 'learning_loop → quant_discovery seed atoms');
} else {
  fail('counterfactual_atom_miner', 'Counterfactual atom miner not wired');
}
const oppPy2 = readText('scripts/python/opportunity_score_v2.py');
if (oppPy2.includes('TV_ATOM_BOOSTS') && oppPy2.includes('tv_discovery_features')) {
  pass('opp_tv_boosts', 'opportunity_score_v2 consumes TV discovery features');
} else {
  fail('opp_tv_boosts', 'Opportunity v2 missing TV boosts');
}

// 16. Perpetual orchestrator + engine registry
const registry = readText('scripts/lib/discovery_engine_registry.mjs');
const perpetual = readText('scripts/egx_discovery_perpetual.mjs');
if (registry.includes('DISCOVERY_ENGINES') && perpetual.includes('planDiscoveryRun')) {
  pass('perpetual_orchestrator', 'discovery engine registry + perpetual loop wired');
} else {
  fail('perpetual_orchestrator', 'Perpetual discovery orchestrator missing');
}

// 17. EOD light fabric in tv_auto_update
if (tvAuto.includes('egx_discovery_fabric.mjs --light')) {
  pass('eod_light_fabric', 'tv_auto_update runs light fabric before opportunity_score_v2');
} else {
  fail('eod_light_fabric', 'EOD pipeline missing light fabric before opp_v2');
}

// 18. TV micro wide universe
if (tvMicro.includes('final_signals') && tvMicro.includes('--wide')) {
  pass('tv_micro_wide_universe', 'TV micro scans actionable + volume leaders + wide mode');
} else {
  fail('tv_micro_wide_universe', 'TV micro still opp-only symbol selection');
}

// 19. Causal + X-Pro in engine registry
if (registry.includes('causal_discovery') && registry.includes('egx_x_pro')) {
  pass('registry_causal_xpro', 'causal_discovery + egx_x_pro registered');
} else {
  fail('registry_causal_xpro', 'causal or x_pro missing from DISCOVERY_ENGINES');
}

// 20. Unified quant runner
if (existsSync(join(ROOT, 'scripts/lib/run_quant_discovery.mjs'))) {
  pass('unified_quant_runner', 'run_quant_discovery.mjs SSOT for quant_discovery.py');
} else {
  fail('unified_quant_runner', 'run_quant_discovery.mjs missing');
}

// 21. DMIDS fabric bridge miners
const minersPy = readText('scripts/python/discovery_domain_miners.py');
if (minersPy.includes('mine_egx_x_pro') && minersPy.includes('causal_discovery_last.json')) {
  pass('fabric_miners_extended', 'x_pro + causal JSON wired into domain miners');
} else {
  fail('fabric_miners_extended', 'domain miners missing x_pro/causal bridge');
}

// 22. JSON orphan order — counterfactual before fabric in EOD
const cfIdx = tvAuto.indexOf('counterfactual_atom_miner');
const fabIdx = tvAuto.indexOf('egx_discovery_fabric.mjs');
if (cfIdx >= 0 && fabIdx > cfIdx) {
  pass('json_orphan_order', 'counterfactual_atom_miner runs before discovery_fabric in EOD');
} else {
  fail('json_orphan_order', 'EOD must run counterfactual before fabric merge (F6)');
}

// 23. TRADING_LESSONS miners (F8 / Level A)
if (
  minersPy.includes('mine_institutional_retest')
  && minersPy.includes('mine_volume_accumulation')
  && minersPy.includes('mine_quality_universe_v3')
) {
  pass('trading_lessons_miners', 'institutional retest + vol accumulation + quality v3 miners');
} else {
  fail('trading_lessons_miners', 'Missing A1/A2/A3 domain miners');
}

// 24. DMIDS fabric bridge
const fabricJs = readText('scripts/egx_discovery_fabric.mjs');
if (fabricJs.includes('mergeStructuralLawsIntoRuntime') && minersPy.includes('mine_dmids_structural')) {
  pass('dmids_fabric_bridge', 'DMIDS structural laws merged into fabric runtime');
} else {
  fail('dmids_fabric_bridge', 'DMIDS ↔ fabric bridge incomplete (F2)');
}

// 25. L11 documented
const layerDoc = readText('docs/LAYER_REGISTRY.md');
if (layerDoc.includes('| L11') || layerDoc.includes('L11')) {
  pass('layer_registry_l11', 'LAYER_REGISTRY documents Discovery Fabric L11');
} else {
  fail('layer_registry_l11', 'LAYER_REGISTRY missing L11 (F10)');
}

// 26. Near-ATH + delivery feedback miners
if (minersPy.includes('mine_near_ath_300') && minersPy.includes('mine_delivery_feedback')) {
  pass('context_miners_b2_b4', 'near_ath_300 + delivery_feedback miners wired');
} else {
  fail('context_miners_b2_b4', 'Missing B2/B4 miners');
}

// 27. Level A/B miners (A4, F9, B3) + opp A5 direct link
if (minersPy.includes('mine_peer_rs_leader') && minersPy.includes('mine_session_microstructure')) {
  pass('level_ab_miners', 'peer_rs_leader + session_microstructure miners wired');
} else {
  fail('level_ab_miners', 'Missing A4/F9 session miners');
}
if (minersPy.includes('mine_defensive_sector_rotation')) {
  pass('defensive_sector_miner', 'B3 defensive sector rotation miner (banks/services)');
} else {
  fail('defensive_sector_miner', 'Missing B3 defensive sector miner');
}
if (oppPy.includes('POST_BREAKOUT_VOL_COLLAPSE')) {
  pass('opp_post_breakout_vol', 'opportunity_score_v2 penalizes post-breakout vol collapse (A5)');
} else {
  fail('opp_post_breakout_vol', 'A5 not linked in opportunity_score_v2');
}

// 28. Level C miners (C1–C4)
if (
  minersPy.includes('mine_precursor_sequence')
  && minersPy.includes('mine_cross_market_leadlag')
  && minersPy.includes('mine_dom_regime')
  && minersPy.includes('mine_ensemble_disagreement')
) {
  pass('level_c_miners', 'precursor + leadlag + dom_regime + ensemble disagreement miners');
} else {
  fail('level_c_miners', 'Missing C1–C4 domain miners');
}

// 29. Directive stats
const dirs = countDirectiveStats();
pass('directive_stats', `pending=${dirs.pending} completed=${dirs.completed}`);

const nFail = checks.filter(c => !c.ok).length;
const report = {
  at: new Date().toISOString(),
  pass: checks.filter(c => c.ok).length,
  fail: nFail,
  ok: nFail === 0,
  checks,
  context: summary,
};

writeFileSync(join(ROOT, 'data/discovery_audit_last.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
process.exit(nFail > 0 ? 1 : 0);
