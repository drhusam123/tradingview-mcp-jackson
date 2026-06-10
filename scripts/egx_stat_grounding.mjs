#!/usr/bin/env node
/**
 * Phase 36 — Statistical Grounding Engine runner
 * "هل الـ edge حقيقي أم وهمي؟"
 *
 * Sections:
 *   grade       — grade all laws (A/B/C/D/F) — run this first
 *   test        — full statistical test for one law  --law momentum_breakout
 *   bootstrap   — confidence intervals for one law   --law momentum_breakout
 *   oos         — out-of-sample validation for all laws
 *   expectancy  — execution-adjusted expectancy report
 *   fdr         — Benjamini-Hochberg FDR multiple testing correction
 *   full        — grade + oos + expectancy (recommended)
 */
import { pythonStatGradeAllLaws, pythonStatTestLaw, pythonStatBootstrapLaw,
         pythonStatOOSValidation, pythonStatExpectancyReport,
         pythonStatFDR, pythonStatBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'grade';
const lawName = args[args.indexOf('--law') + 1] ?? null;
const nBoot   = parseInt(args[args.indexOf('--n') + 1] ?? '1000');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  📊 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const GRADE_EMOJI = { A: '🟢', B: '🔵', C: '🟡', D: '🟠', F: '🔴' };

switch (section) {
  case 'grade': {
    banner('Statistical: Grading All Laws');
    const r = await pythonStatGradeAllLaws({});
    if (r?.grade_distribution) {
      console.log(`\n   Graded: ${r.n_graded} laws`);
      console.log(`   Grounding Score: ${r.grounding_score?.toFixed(1)}/100`);
      console.log('\n   Grade Distribution:');
      Object.entries(r.grade_distribution).forEach(([g, n]) =>
        console.log(`   ${GRADE_EMOJI[g] ?? '?'} ${g}:  ${n} laws`));
      console.log(`\n   Should Retire: ${r.n_should_retire} laws  |  Significant: ${r.n_significant} laws`);
      if (r.top_A_laws?.length) {
        console.log('\n   🟢 Top Grade A Laws:');
        r.top_A_laws.slice(0, 8).forEach(l =>
          console.log(`   ${String(l.law_name ?? l.law_id).padEnd(35)} prec:${(l.precision*100).toFixed(0)}%  EAE:${l.eae?.toFixed(4)}`));
      }
      if (r.bottom_laws_to_retire?.length) {
        console.log('\n   🔴 Laws to Retire (D/F):');
        r.bottom_laws_to_retire.slice(0, 5).forEach(l =>
          console.log(`   ${String(l.law_name ?? l.law_id).padEnd(35)} grade:${l.grade}  ${l.recommendation}`));
      }
    } else pp(r);
    break;
  }
  case 'test': {
    if (!lawName) { console.log('Usage: node egx_stat_grounding.mjs test --law <law_name>'); process.exit(1); }
    banner(`Statistical: Full Test for "${lawName}"`);
    const r = await pythonStatTestLaw({ law_name: lawName });
    if (r?.grade) {
      console.log(`\n   ${GRADE_EMOJI[r.grade] ?? '?'} Grade: ${r.grade}  |  Precision: ${(r.precision*100)?.toFixed(1)}%  |  n: ${r.n_samples}`);
      console.log(`\n   Significance Test:`);
      console.log(`     p-value: ${r.significance?.p_value?.toFixed(4)}  z: ${r.significance?.z_score?.toFixed(2)}  significant: ${r.significance?.is_significant ? '✅' : '❌'}`);
      console.log(`\n   Bootstrap CI (95%):`);
      console.log(`     ${(r.bootstrap?.ci_low_95*100)?.toFixed(1)}% — ${(r.bootstrap?.ci_high_95*100)?.toFixed(1)}%  (width: ${(r.bootstrap?.ci_width*100)?.toFixed(1)}pp)`);
      console.log(`\n   OOS Validation:`);
      console.log(`     Degradation: ${(r.oos?.oos_degradation*100)?.toFixed(0)}%  Risk: ${r.oos?.overfitting_risk}`);
      console.log(`\n   Stress Test:`);
      console.log(`     Stressed precision: ${(r.stress?.stressed_precision*100)?.toFixed(1)}%  Drop: ${(r.stress?.stress_drop*100)?.toFixed(0)}%`);
      console.log(`\n   Execution-Adjusted Expectancy: ${r.eae?.eae?.toFixed(4)}  (${r.eae?.eae > 0 ? '✅ positive' : '❌ negative after costs'})`);
      console.log(`\n   📋 Recommendation: ${r.recommendation}`);
    } else pp(r);
    break;
  }
  case 'bootstrap': {
    if (!lawName) { console.log('Usage: node egx_stat_grounding.mjs bootstrap --law <law_name>'); process.exit(1); }
    banner(`Statistical: Bootstrap CI for "${lawName}"`);
    const r = await pythonStatBootstrapLaw({ law_name: lawName, n_bootstrap: nBoot });
    if (r?.ci_low_95 !== undefined) {
      console.log(`\n   Law: ${r.law_name}`);
      console.log(`   Point estimate: ${(r.precision*100)?.toFixed(1)}%`);
      console.log(`   95% CI: [${(r.ci_low_95*100)?.toFixed(1)}%, ${(r.ci_high_95*100)?.toFixed(1)}%]`);
      console.log(`   CI width: ${(r.ci_width*100)?.toFixed(1)}pp  (${r.interpretation})`);
    } else pp(r);
    break;
  }
  case 'oos': {
    banner('Statistical: Out-of-Sample Validation');
    const r = await pythonStatOOSValidation({});
    if (r?.n_robust !== undefined) {
      console.log(`\n   Robust (degradation < 15%): ${r.n_robust}`);
      console.log(`   Fragile (15-30%):           ${r.n_fragile}`);
      console.log(`   Overfit (> 30%):            ${r.n_overfit}`);
      console.log(`   Avg OOS degradation:        ${(r.avg_oos_degradation*100)?.toFixed(1)}%`);
      if (r.robust_laws?.length) {
        console.log('\n   ✅ Most Robust Laws:');
        r.robust_laws.slice(0, 5).forEach(l =>
          console.log(`   ${String(l.law_name).padEnd(35)} degradation: ${(l.oos_degradation*100).toFixed(0)}%`));
      }
      if (r.fragile_laws?.length) {
        console.log('\n   ⚠️  Fragile Laws (watch):');
        r.fragile_laws.slice(0, 5).forEach(l =>
          console.log(`   ${String(l.law_name).padEnd(35)} degradation: ${(l.oos_degradation*100).toFixed(0)}%`));
      }
    } else pp(r);
    break;
  }
  case 'expectancy': {
    banner('Statistical: Execution-Adjusted Expectancy');
    const r = await pythonStatExpectancyReport({});
    if (r?.n_positive_eae !== undefined) {
      console.log(`\n   ✅ Positive EAE: ${r.n_positive_eae} laws`);
      console.log(`   ❌ Negative EAE: ${r.n_negative_eae} laws`);
      console.log(`   Avg EAE: ${r.avg_eae?.toFixed(4)}`);
      if (r.best_eae_laws?.length) {
        console.log('\n   Best Expectancy Laws:');
        r.best_eae_laws.slice(0, 8).forEach(l =>
          console.log(`   ${GRADE_EMOJI[l.grade] ?? '?'} ${String(l.law).padEnd(35)} EAE:${l.eae?.toFixed(4)}  prec:${(l.precision*100).toFixed(0)}%`));
      }
      if (r.laws_to_retire?.length) {
        console.log('\n   ❌ Retire These (negative EAE):');
        r.laws_to_retire.slice(0, 5).forEach(l =>
          console.log(`   ${String(l.law).padEnd(35)} EAE:${l.eae?.toFixed(4)}  ${l.reason}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Statistical: Full Grounding Build');
    const r = await pythonStatBuildFull({});
    if (r?.grounding?.grade_distribution) {
      console.log(`\n   Grounding Score: ${r.grounding?.grounding_score?.toFixed(1)}/100`);
      console.log(`   Grade A+B: ${(r.grounding?.grade_distribution?.A ?? 0) + (r.grounding?.grade_distribution?.B ?? 0)} laws confirmed`);
      console.log(`   Should retire: ${r.grounding?.n_should_retire}`);
      console.log(`   ${r.grounding_summary ?? r.status}`);
    } else pp(r);
    break;
  }
  case 'fdr': {
    banner('Statistical: Benjamini-Hochberg FDR Correction');
    const r = await pythonStatFDR({});
    if (r?.n_laws !== undefined) {
      console.log(`\n   Total laws tested: ${r.n_laws}`);
      console.log(`   Significant (raw p<0.05):      ${r.n_significant_raw}`);
      console.log(`   Significant (after FDR):       ${r.n_significant_fdr}`);
      console.log(`   False discoveries avoided:     ${r.false_discoveries_avoided}`);
      console.log(`   Method: ${r.correction_method}  (α=${r.fdr_threshold_alpha})`);
      console.log(`\n   ${r.interpretation}`);
      if (r.results?.length) {
        console.log('\n   Top Laws after FDR:');
        r.results.filter(x => x.significant_fdr).slice(0, 8).forEach(l =>
          console.log(`   ✅ ${String(l.law_name).padEnd(35)} p:${l.p_value?.toFixed(4)}`));
        const rejected = r.results.filter(x => x.significant_raw && !x.significant_fdr);
        if (rejected.length) {
          console.log('\n   ❌ Rejected by FDR (were false positives):');
          rejected.slice(0, 5).forEach(l =>
            console.log(`   ❌ ${String(l.law_name).padEnd(35)} p:${l.p_value?.toFixed(4)}`));
        }
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
