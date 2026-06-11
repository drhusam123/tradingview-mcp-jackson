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

// 17. Directive stats
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
