#!/usr/bin/env node
/**
 * Phase 39 — Uncertainty Quantification Engine runner
 * "ما نعرفه / ما لا نستطيع معرفته — Epistemic vs Aleatoric"
 *
 * Sections:
 *   epistemic   — epistemic (knowledge) uncertainty for a symbol  --ticker COMI
 *   aleatoric   — aleatoric (market noise) uncertainty  --ticker COMI
 *   ood         — out-of-distribution detection (default)
 *   propagate   — error propagation through the pipeline
 *   report      — full uncertainty report
 *   full        — report + save to DB (recommended)
 */
import { pythonUncertaintyEpistemic, pythonUncertaintyAleatoric, pythonUncertaintyOOD,
         pythonUncertaintyPropagate, pythonUncertaintyReport,
         pythonUncertaintyBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'ood';
const ticker  = args[args.indexOf('--ticker') + 1] ?? 'COMI';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🎲 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const OOD_EMOJI = { EXTREME_OOD: '🚨', HIGH_OOD: '🔴', MODERATE_OOD: '🟡', IN_DISTRIBUTION: '✅' };

switch (section) {
  case 'epistemic': {
    banner(`Uncertainty: Epistemic (Knowledge) — ${ticker}`);
    const r = await pythonUncertaintyEpistemic({ symbol: ticker });
    if (r?.epistemic_uncertainty !== undefined) {
      const conf = r.confidence_in_knowledge ?? (1 - r.epistemic_uncertainty);
      console.log(`\n   ${ticker}: epistemic uncertainty = ${(r.epistemic_uncertainty*100).toFixed(1)}%`);
      console.log(`   Confidence in knowledge: ${(conf*100).toFixed(1)}%`);
      console.log(`\n   Components (REDUCIBLE with more data):`);
      Object.entries(r.components ?? {}).forEach(([k, v]) =>
        console.log(`   ${String(k).padEnd(25)} ${(v*100).toFixed(1)}%`));
      if (r.reducible_by?.length)
        console.log(`\n   Can be reduced by: ${r.reducible_by.join(', ')}`);
      console.log(`\n   ${r.interpretation}`);
    } else pp(r);
    break;
  }
  case 'aleatoric': {
    banner(`Uncertainty: Aleatoric (Market Noise) — ${ticker}`);
    const r = await pythonUncertaintyAleatoric({ symbol: ticker });
    if (r?.aleatoric_uncertainty !== undefined) {
      console.log(`\n   ${ticker}: aleatoric uncertainty = ${(r.aleatoric_uncertainty*100).toFixed(1)}%`);
      console.log(`   ⚠️  IRREDUCIBLE — cannot be fixed with more data\n`);
      console.log('   Components:');
      Object.entries(r.components ?? {}).forEach(([k, v]) =>
        console.log(`   ${String(k).padEnd(25)} ${(v*100).toFixed(1)}%`));
      console.log(`\n   ${r.interpretation}`);
    } else pp(r);
    break;
  }
  case 'ood': {
    banner('Uncertainty: Out-of-Distribution Detection');
    const r = await pythonUncertaintyOOD({});
    if (r?.ood_score !== undefined) {
      const em = OOD_EMOJI[r.ood_level] ?? '?';
      console.log(`\n   ${em} OOD Score: ${(r.ood_score*100).toFixed(1)}%  |  Level: ${r.ood_level}`);
      console.log(`   Most similar historical episode: ${r.most_similar_episode_date ?? 'N/A'}`);
      console.log(`   Similarity to nearest: ${(r.similarity*100)?.toFixed(1)}%`);
      console.log(`   Episodes checked: ${r.n_episodes_checked}`);
      console.log(`\n   📋 ${r.interpretation}`);
      console.log(`   Action: ${r.action}`);
    } else pp(r);
    break;
  }
  case 'propagate': {
    banner('Uncertainty: Error Propagation Pipeline');
    const r = await pythonUncertaintyPropagate({});
    if (r?.total_uncertainty !== undefined) {
      console.log(`\n   Total uncertainty: ${(r.total_uncertainty*100).toFixed(1)}%`);
      console.log(`   Pipeline confidence: ${(r.pipeline_confidence*100).toFixed(1)}%`);
      console.log(`   Bottleneck: ${r.bottleneck}\n`);
      console.log('   Stage-by-stage propagation:');
      (r.stages ?? []).forEach(s =>
        console.log(`   ${String(s.stage).padEnd(25)} u:${(s.uncertainty*100).toFixed(1)}%  propagated:${(s.propagated*100).toFixed(1)}%`));
      console.log(`\n   📋 ${r.recommendation}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Uncertainty: Full Report');
    const r = await pythonUncertaintyReport({});
    if (r?.total_market_uncertainty !== undefined) {
      const em = OOD_EMOJI[r.ood?.ood_level] ?? '?';
      console.log(`\n   Epistemic (reducible):  ${(r.market_epistemic*100).toFixed(1)}%  (${(r.uncertainty_budget?.epistemic_fraction*100)?.toFixed(0)}% of budget)`);
      console.log(`   Aleatoric (noise):      ${(r.market_aleatoric*100).toFixed(1)}%  (${(r.uncertainty_budget?.aleatoric_fraction*100)?.toFixed(0)}% of budget)`);
      console.log(`   Total uncertainty:      ${(r.total_market_uncertainty*100).toFixed(1)}%`);
      if (r.ood)
        console.log(`\n   ${em} OOD Level: ${r.ood.ood_level}  (score: ${(r.ood.ood_score*100).toFixed(1)}%)`);
      if (r.propagation)
        console.log(`   Pipeline confidence: ${(r.propagation.pipeline_confidence*100).toFixed(1)}%  |  Bottleneck: ${r.propagation.bottleneck}`);
      console.log(`\n   ${r.interpretation}`);
      console.log(`   📋 ${r.trading_recommendation}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Uncertainty: Full Build + Save');
    const r = await pythonUncertaintyBuildFull({});
    if (r?.total_uncertainty !== undefined) {
      console.log(`\n   Total uncertainty: ${(r.total_uncertainty*100).toFixed(1)}%`);
      console.log(`   Pipeline confidence: ${(r.pipeline_confidence*100).toFixed(1)}%`);
      console.log(`   OOD level: ${r.ood_level}`);
      console.log(`   ${r.interpretation}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
