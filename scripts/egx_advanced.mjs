/**
 * EGX Advanced Analysis
 * =====================
 * تحليلات متقدمة تُشغَّل حسب الطلب (ليست يومية — تأخذ 2-5 دقائق):
 *
 *   🔬 SHAP Analysis        — feature importance حقيقية بـ SHAP (~90s)
 *   🌊 Regime Detection     — Trending/Ranging/HighVol لكل سهم (~15s)
 *   🎯 Ensemble Signal      — Meta-signal يجمع Rules + ML + Calendar (~3s)
 *   💧 Active Universe      — فلتر السيولة الحقيقية من OHLCV (~5s)
 *
 * التشغيل:
 *   npm run egx:advanced           ← كل شيء
 *   npm run egx:advanced:shap      ← SHAP فقط
 *   npm run egx:advanced:regime    ← Regime فقط
 *   npm run egx:advanced:ensemble  ← Ensemble فقط
 *   npm run egx:advanced:universe  ← Active Universe فقط
 *   node scripts/egx_advanced.mjs --section shap
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import {
  pythonShapAnalysis, pythonRegimeDetection,
  pythonEnsembleSignal, pythonActiveUniverse,
  pythonSectorRotation, pythonPairsTrading,
  pythonEnsembleSignal as _ens,
  eventSignals,
  pythonStateTransitions,
  pythonConditionalTransitions,
  pythonAdaptiveMemory,
  pythonEvolvingStructure,
  pythonMarketEvolution,
  pythonMacroRegime,
  pythonBehavioralForces,
  pythonDurationAnalysis,
  pythonSectorMarkov,
  pythonLatentCompress,
  pythonInvariantDiscovery,
  pythonFailurePrecursors,
  pythonTemporalStability,
  pythonQuantLoop,
  pythonForceFieldNow,
  pythonForceInteractions,
  pythonForceEvolution,
  pythonMarketMemory,
  pythonFailurePhysics,
  pythonForceAttractors,
  pythonForceFieldFull,
  pythonPropagationNow,
  pythonContagionChains,
  pythonSectorTransmission,
  pythonInstabilityCascades,
  pythonRoleClassification,
  pythonDiffusionAnalysis,
  pythonRegimeNetworks,
  pythonPropagationFull,
  pythonEnergyNow,
  pythonEnergyFlow,
  pythonEnergyAccumulation,
  pythonEnergyTransformation,
  pythonEnergyPersistence,
  pythonRegimeEnergy,
  pythonFailurePhysicsEnergy,
  pythonEnergyInvariants,
  pythonEnergyFull,
  pythonCausalNow,
  pythonCausalChains,
  pythonFeedbackLoops,
  pythonTemporalMemory,
  pythonSectorCausalRoles,
  pythonCausalFailure,
  pythonRegimeCausality,
  pythonCausalInvariants,
  pythonCausalFull,
  // Phase 6 — Decision Engine
  pythonDecisionNow,
  pythonOpportunityScan,
  pythonPortfolioOptimize,
  pythonUncertaintyMap,
  pythonRegimeDecisions,
  pythonInactionAnalysis,
  pythonDecisionFailure,
  pythonAdaptiveThresholds,
  pythonDecisionFull,
  // Phase 7 — Self-Evolving Intelligence
  pythonMetaStatus,
  pythonDecayScan,
  pythonHypothesisGen,
  pythonArchCompete,
  pythonTaxonomyAudit,
  pythonRegimeIntelligence,
  pythonEvolutionMemory,
  pythonMetaDecision,
  pythonSelfRewrite,
  pythonEvolutionFull,
  // Phase 8 — World–Market Coupling Engine
  pythonCouplingNow,
  pythonFxImpact,
  pythonWorldMacroRegimes,
  pythonLiquidityCycle,
  pythonSectorCoupling,
  pythonShockMemory,
  pythonContagionScan,
  pythonCouplingStability,
  pythonAdaptiveWorldModel,
  pythonCouplingFull,
  // Phase 9 — Cognitive Orchestrator
  pythonOrchHealth,
  pythonOrchNow,
  pythonOrchArbitrate,
  pythonOrchConfidence,
  pythonOrchConflicts,
  pythonOrchPosture,
  pythonOrchWatch,
  pythonOrchSync,
  pythonOrchReport,
  pythonOrchFull,
  // Phase 10 — Market Operating System
  pythonOsPipelineRun,
  pythonOsPipelineStatus,
  pythonOsDashboard,
  pythonOsAlertScan,
  pythonOsArchive,
  pythonOsHealth,
  pythonOsResilience,
  pythonOsObservability,
  pythonOsReplay,
  pythonOsFull,
  // Phase 11 — Telegram Report
  pythonTgFormatDaily,
  pythonTgFormatAlert,
  pythonTgFormatPosture,
  pythonTgFormatDelta,
  pythonTgTestFormat,
  // Phase 12 — DMIDS
  pythonDmidsStatus, pythonDmidsProfiles, pythonDmidsExplode,
  pythonDmidsPrecursors, pythonDmidsSectors, pythonDmidsKnowledge,
  pythonDmidsReport, pythonDmidsFull,
  // Phase 13 — DHVD
  pythonDhvdStatus, pythonDhvdValidateLaws, pythonDhvdFamilies,
  pythonDhvdRegimes, pythonDhvdFalseBreakouts, pythonDhvdHypotheses,
  pythonDhvdReport, pythonDhvdFull,
  // Phase 14 — Law Synthesis Engine
  pythonLsStatus, pythonLsStability, pythonLsCounterfactuals,
  pythonLsMutations, pythonLsInteractions, pythonLsNetwork,
  pythonLsPhysics, pythonLsRegimeSystems, pythonLsReport, pythonLsFull,
  // Phase 15 — Self-Learning Market Evolution
  pythonEvoStatus, pythonEvoExperience, pythonEvoConfidence,
  pythonEvoReinforce, pythonEvoFailures, pythonEvoStocks,
  pythonEvoHypotheses, pythonEvoRegimes, pythonEvoFull,
  // Phase 16 — Autonomous Market Cognition Engine
  pythonCogStatus, pythonCogStockDNA, pythonCogSectorDNA,
  pythonCogExplosions, pythonCogLaws, pythonCogMemory,
  pythonCogEvolve, pythonCogReport, pythonCogFull,
  // Phase 17 — Graph Contagion Engine
  pythonGraphBuild, pythonGraphPagerank, pythonGraphCommunity,
  pythonGraphContagion, pythonGraphCascade, pythonGraphCentrality,
  pythonGraphSpillover, pythonGraphFull,
  // Phase 18 — RL Environment & Walk-Forward
  pythonRLStateVector, pythonRLBacktest, pythonRLWalkForward,
  pythonRLOptimize, pythonRLReport,
  // Phase 19 — SHAP Explainability Engine
  pythonExplainTrain, pythonExplainStock, pythonExplainImportance,
  pythonExplainDaily, pythonExplainReport, pythonExplainRetrain,
  // Phase 5 PCMCI extension
  pythonCausalPCMCI,
  // Phase 20 — Historical Integrity Engine
  pythonIntegrityScanAll, pythonIntegrityBreadth, pythonIntegrityReport, pythonIntegrityAnomalies,
  // Phase 21 — Unified Market Cognition Graph
  pythonUMCGBuild, pythonUMCGMetrics, pythonUMCGCommunities, pythonUMCGFragility,
  pythonUMCGSnapshot, pythonUMCGGetSnapshot,
  // Phase 22 — Causal Discovery Engine
  pythonCausalTransferEntropy, pythonCausalLaggedInference, pythonCausalStability,
  pythonCausalRegime, pythonCausalMacroTransmission, pythonCausalBuildFull,
  // Phase 23 — Failure Memory Engine
  pythonFailureAnalyzeAll, pythonFailureDailyScan, pythonFailureReport, pythonFailureBuildFull,
  // Phase 24 — Explosion Physics Engine
  pythonExplosionReadiness, pythonExplosionSignatures,
  pythonExplosionFalseAnatomy, pythonExplosionWatchlist, pythonExplosionBuildFull,
  // Phase 25 — Market DNA Engine
  pythonDNABuild, pythonDNAMutations, pythonDNAClusters,
  pythonDNAProfile, pythonDNASectorRefresh, pythonDNABuildFull,
  // Phase 26 — Adaptive Research Loop
  pythonResearchAssessLaws, pythonResearchDiscover, pythonResearchMutate,
  pythonResearchDirectives, pythonResearchEvolution, pythonResearchLawTree,
  // Phase 27 — Execution Reality Engine
  pythonExecutionLiquidityProfiles, pythonExecutionAdjustReturns,
  pythonExecutionScanFeasibility, pythonExecutionProfile,
  // Phase 28 — Unified Daily Synthesis
  pythonSynthesisBuild, pythonSynthesisDailyBrief,
  pythonSynthesisGetReport, pythonSynthesisStatus,
  // Phase 29 — Intelligence Prioritization Layer
  pythonPrioritizerRun, pythonPrioritizerTopInsights, pythonPrioritizerAnomaly,
  pythonPrioritizerScoreSymbol, pythonPrioritizerDailyBrief, pythonPrioritizerBuildFull,
  // Phase 30 — Episodic Market Memory
  pythonEpisodicEncode, pythonEpisodicFindSimilar, pythonEpisodicAnalogy,
  pythonEpisodicGetEpisode, pythonEpisodicBuildFull,
  // Phase 31 — Meta-Learning Engine
  pythonMetaAnalyzeHypotheses, pythonMetaFailureContexts, pythonMetaPredictabilityMap,
  pythonMetaDirectives, pythonMetaBuildFull,
  // Phase 32 — Portfolio Cognition System
  pythonPortfolioOrchestrate, pythonPortfolioSizePositions, pythonPortfolioRiskBudget,
  pythonPortfolioAdaptiveConcentration, pythonPortfolioBuild, pythonPortfolioBuildFull,
  // Phase 33 — Regime Transition Forecaster
  pythonTransitionProbability, pythonTransitionPrecursors, pythonTransitionEWI,
  pythonTransitionAlert, pythonTransitionBuildFull,
  // Phase 34 — Cognitive Arbitration Layer
  pythonArbitrateSymbol, pythonArbitrateAll, pythonArbitrateDailyDecisions,
  pythonArbitrateConstitution, pythonArbitrateBuildFull,
  // Phase 35 — Anti-Laws Engine
  pythonAntiLawsExtract, pythonAntiLawsBuildLibrary, pythonAntiLawsScanSymbol,
  pythonAntiLawsDailyScan, pythonAntiLawsReport, pythonAntiLawsBuildFull,
  // Phase 36 — Statistical Grounding Engine
  pythonStatGradeAllLaws, pythonStatTestLaw, pythonStatBootstrapLaw,
  pythonStatOOSValidation, pythonStatExpectancyReport, pythonStatBuildFull,
  // Phase 37 — Intelligence Reliability Observatory
  pythonObservatoryEngineHealth, pythonObservatoryTrustability, pythonObservatoryDetectFailures,
  pythonObservatoryAgreement, pythonObservatoryReport, pythonObservatoryBuildFull,
  // Phase 38 — Cognitive Compression Engine
  pythonCompressionForces, pythonCompressionRisks, pythonCompressionOpps,
  pythonCompressionBriefing, pythonCompressionMII, pythonCompressionBuildFull,
  // Phase 39 — Uncertainty Quantification Engine
  pythonUncertaintyEpistemic, pythonUncertaintyAleatoric, pythonUncertaintyOOD,
  pythonUncertaintyPropagate, pythonUncertaintyReport, pythonUncertaintyBuildFull,
  // Phase 40 — Autonomous Research Sandbox
  pythonSandboxGenerate, pythonSandboxBacktest, pythonSandboxRunCycle,
  pythonSandboxReport, pythonSandboxBuildFull,
  // Phase 41 — Governance Constitution Engine
  pythonGovernanceAudit, pythonGovernanceHaltCheck,
  pythonGovernanceReport, pythonGovernanceBuildFull,
  // Phase 42 — Central Cognitive Bus
  pythonBusRead, pythonBusDirective, pythonBusCoherence, pythonBusBuildFull,
  // Phase 43 — Guided Research Pressure Zones
  pythonPressureIdentify, pythonPressureCycle, pythonPressureBuildFull,
  // Phase 44 — Execution Reality Engine
  pythonExecRealityCheck, pythonExecCalendar, pythonExecBuildFull,
  // Phase 37 Extension
  pythonObservatoryEnhanced, pythonObservatoryEntropy,
  filterPortfolio,
} from '../src/egx/index.js';

const SECTION = (() => {
  const i = process.argv.indexOf('--section');
  return i >= 0 ? process.argv[i + 1] : 'all';
})();

const JSON_MODE = process.argv.includes('--json');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 65) => wl(c.repeat(n));
const h2  = (t) => { wl(); sep('─', 65); wl(`  ▶ ${t}`); sep('─', 65); };
const ok  = (s) => `  ✅ ${s}`;
const warn= (s) => `  ⚠️  ${s}`;
const err = (s) => `  ❌ ${s}`;
const fmt = (v, d = 2) => v != null ? (+v).toFixed(d) : '—';

// ── Helpers ───────────────────────────────────────────────────────────────

function printTable(rows, cols, widths) {
  const header = cols.map((c, i) => c.padEnd(widths[i])).join(' │ ');
  wl(`  ${header}`);
  wl('  ' + '─'.repeat(header.length));
  for (const row of rows) {
    wl('  ' + cols.map((c, i) => String(row[c] ?? '—').padEnd(widths[i])).join(' │ '));
  }
}

// ─────────────────────────────────────────────────────────────────────────
// 1. SHAP Analysis
// ─────────────────────────────────────────────────────────────────────────

async function runShap() {
  h2('SHAP Feature Importance (TreeExplainer)');
  wl('  🔬 هذا التحليل يأخذ ~90s — يحسب Garman-Klass + Amihud + ATR Rank...');
  wl('  📌 SHAP أدق من feature_importances_ العادية (impurity bias مُصحَّح)');
  wl();

  const t0 = Date.now();
  const r  = await pythonShapAnalysis(3.0, 8000);

  if (!r.success) {
    wl(err(`فشل SHAP: ${r.error}`));
    return;
  }

  wl(ok(`${r.n_samples?.toLocaleString()} عينة | ${r.n_features} features | Positive rate: ${r.positive_rate}%`));
  wl(ok(`Target: ${r.target_def}`));
  wl();

  wl('  Feature               SHAP (mean|φ|)   Contribution%   الوصف');
  wl('  ' + '─'.repeat(70));
  for (const f of (r.shap_importance ?? []).slice(0, 12)) {
    const bar = '█'.repeat(Math.round(f.contribution_pct / 2)).padEnd(25);
    wl(`  ${f.feature.padEnd(18)} ${fmt(f.mean_abs_shap, 4).padEnd(16)} ${String(f.contribution_pct + '%').padEnd(14)}  ${bar}`);
  }

  wl();
  wl(ok(`Top 3: ${(r.top_3_features ?? []).join(' → ')}`));
  wl(`  💡 ${r.key_insight}`);
  wl(`  📝 ${r.vs_simple_ml}`);
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 2. Regime Detection
// ─────────────────────────────────────────────────────────────────────────

async function runRegime() {
  h2('Market Regime Detection (ADX + ATR Rank + EMA Slope)');

  const t0 = Date.now();
  const r  = await pythonRegimeDetection();

  if (!r.success) {
    wl(err(`فشل Regime Detection: ${r.error}`));
    return;
  }

  const dist = r.regime_distribution ?? {};
  const pcts = r.regime_pcts ?? {};
  const total = r.total_symbols ?? 0;

  wl(`  السوق الكلي: ${r.market_regime}`);
  wl(`  ${r.market_recommendation}`);

  // الماكرو المدمج من TradingView Live
  const mc = r.macro_context ?? {};
  if (mc.real_interest_rate != null) {
    wl();
    wl('  🌍 السياق الكلي (TradingView Live):');
    for (const note of (mc.notes ?? [])) {
      wl(`     • ${note}`);
    }
    if (mc.inflation_pct) wl(`     📈 تضخم: ${mc.inflation_pct?.toFixed(1)}%  |  💵 USD/EGP: ${mc.usd_egp?.toFixed(2)}  |  ⚖️  ${mc.strategic_bias}`);
  }

  wl();
  wl('  Regime Distribution:');
  const regimes = [
    ['TRENDING_UP',   '📈', '✅'],
    ['TRENDING_DOWN', '📉', '⚠️ '],
    ['RANGING',       '↔️ ', '⚠️ '],
    ['HIGH_VOL',      '⚡', '🚨'],
    ['NEUTRAL',       '◾', ''],
  ];
  for (const [regime, icon, flag] of regimes) {
    const n = dist[regime] ?? 0;
    if (n === 0) continue;
    const bar = '█'.repeat(Math.round((n / total) * 30)).padEnd(30);
    wl(`  ${icon} ${regime.padEnd(15)} ${String(n).padStart(3)} (${String(pcts[regime] ?? 0) + '%'}) ${bar} ${flag}`);
  }

  wl();

  const up = r.trending_up_top10 ?? [];
  if (up.length > 0) {
    wl('  📈 أقوى أسهم TRENDING_UP (ADX ↑):');
    for (const s of up.slice(0, 8)) {
      wl(`    ${s.symbol.padEnd(6)} ADX=${fmt(s.adx, 1).padEnd(5)} Slope=${fmt(s.ema_slope, 1)}%`);
    }
  }

  const ranging = r.ranging_top10 ?? [];
  if (ranging.length > 0) {
    wl();
    wl('  ↔️  أسهم RANGING (تجنب Trend-Following):');
    wl(`    ${ranging.slice(0, 10).map(s => s.symbol).join(' • ')}`);
  }

  wl();
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 3. Ensemble Signal
// ─────────────────────────────────────────────────────────────────────────

async function runEnsemble() {
  h2('Ensemble Signal v2 — Event Engine + Hybrid Regime + Rules + ML + Macro');

  const t0 = Date.now();
  const r  = await pythonEnsembleSignal();

  if (!r.success) {
    wl(err(`فشل Ensemble: ${r.error}`));
    return;
  }

  wl(`  📅 التاريخ: ${r.date}`);
  wl(`  🗓️  السياق الموسمي: ${(r.calendar_context ?? []).join(' | ')}`);

  // ── Hybrid Regime v2 ────────────────────────────────────────────────────
  const r2 = r.regime_v2 ?? {};
  const r1 = r.market_regime ?? {};
  const confIcon = r2.confidence === 'HIGH' ? '🟢' : r2.confidence === 'MEDIUM' ? '🟡' : '🔴';
  wl();
  wl('  ┌── Hybrid Regime Engine v2 ─────────────────────────────────────────┐');
  wl(`  │  Regime: ${(r2.regime_v2 ?? '?').padEnd(9)} | Confidence: ${confIcon} ${r2.confidence ?? '?'}                           │`);
  wl(`  │  Breadth:  ${String(r2.breadth_pct ?? '?').padEnd(5)}% فوق MA20 → ${r2.breadth_regime ?? '?'} ${r2.breadth_note?.slice(0,30) ?? ''}     │`);
  wl(`  │  Volatility: ${r2.vol_note?.slice(0,52) ?? 'طبيعي'}     │`);
  wl(`  │  Momentum:   mom5=${r1.mkt_mom5 ?? '?'}% → ${r2.mom_regime ?? '?'} (lagging check)                  │`);
  wl(`  │  ${r2.leading_note?.slice(0,68) ?? ''}  │`);
  wl(`  │  Blended Multiplier: ×${fmt(r.regime_multiplier, 2)} | RSI Threshold: ≤${r2.rsi_threshold ?? 30}              │`);
  wl('  └────────────────────────────────────────────────────────────────────┘');

  // ── الماكرو ──────────────────────────────────────────────────────────────
  const md    = r.macro_data ?? {};
  const rr_en = md.real_interest_rate != null ? +md.real_interest_rate : null;
  if (rr_en != null) {
    const icon = rr_en < -5 ? '🟢' : rr_en < 0 ? '🟡' : rr_en < 5 ? '🟠' : '🔴';
    wl();
    wl(`  ${icon} الماكرو: تضخم=${(+md.inflation_pct || 0).toFixed(1)}% | فائدة حقيقية=${rr_en.toFixed(1)}% | ${md.strategic_bias}`);
    for (const note of (r.macro_context ?? []).slice(0, 2)) {
      wl(`     ↳ ${note}`);
    }
  }
  wl();

  // ── إشارات الإجمالية ──────────────────────────────────────────────────
  const counts = r.signal_counts ?? {};
  wl(`  إحصاء: 🔥 STRONG_BUY=${counts.STRONG_BUY ?? 0} | 🟢 BUY=${counts.BUY ?? 0} | 👀 WATCH=${counts.WATCH ?? 0} | ◾ NEUTRAL=${counts.NEUTRAL ?? 0}`);
  wl(`  ${r.note}`);

  // ── التسلسل الذهبي (Event Engine) ────────────────────────────────────
  const gold = r.gold_reversal_signals ?? [];
  if (gold.length > 0) {
    wl();
    wl(`  ⭐ HIGH_PROB_REVERSAL — التسلسل الذهبي (WR5~62%):`);
    wl('  Symbol  Score  EventState            Events                RSI   Mom5');
    wl('  ' + '─'.repeat(72));
    for (const s of gold) {
      wl(
        `  ${String(s.symbol).padEnd(7)}` +
        ` ${fmt(s.composite_score, 0).padEnd(6)}` +
        ` ${String(s.event_state ?? '').padEnd(22)}` +
        ` ${(s.event_seq ?? []).join('+').padEnd(22)}` +
        ` ${fmt(s.rsi14, 1).padEnd(5)}` +
        ` ${fmt(s.momentum_5d, 1)}`
      );
    }
  }

  const strong = r.strong_buy ?? [];
  if (strong.length > 0) {
    wl();
    wl('  🔥 STRONG_BUY (Score ≥ 75):');
    wl('  Symbol  Score  Rule  ML%  Event             RSI   Mom5   OBV');
    wl('  ' + '─'.repeat(65));
    for (const s of strong) {
      wl(
        `  ${String(s.symbol).padEnd(7)}` +
        ` ${fmt(s.composite_score, 0).padEnd(6)}` +
        ` ${fmt(s.rule_score, 0).padEnd(5)}` +
        ` ${fmt(s.ml_proxy_pct, 0).padEnd(4)}` +
        ` ${String(s.event_state ?? 'NEUTRAL').padEnd(18)}` +
        ` ${fmt(s.rsi14, 1).padEnd(5)}` +
        ` ${fmt(s.momentum_5d, 1).padEnd(6)}` +
        ` ${s.obv ?? ''}`
      );
    }
  }

  const buys = r.buy ?? [];
  if (buys.length > 0) {
    wl();
    wl('  🟢 BUY (Score 60-75):');
    wl(`  ${buys.slice(0, 10).map(s => `${s.symbol}(${fmt(s.composite_score, 0)}/${s.event_state?.split('_')[0] ?? '-'})`).join(' • ')}`);
  }

  const watch = r.watch ?? [];
  if (watch.length > 0) {
    wl();
    wl('  👀 WATCH (Score 45-60):');
    wl(`  ${watch.slice(0, 10).map(s => s.symbol).join(' • ')}`);
  }

  wl();
  wl(`  🎯 Method: ${(r.methodology ?? '').slice(0, 110)}`);
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 4. Active Universe Filter
// ─────────────────────────────────────────────────────────────────────────

async function runUniverse() {
  h2('Active Universe Filter (Liquidity Screening)');
  wl('  💧 يحسب متوسط قيمة التداول اليومية لآخر 30 يوم (EGP)');
  wl('  📊 LIQUID > 2M EGP/يوم | ILLIQUID 500K-2M | THIN < 500K | DEAD < 100K');
  wl();

  const t0 = Date.now();
  const r  = await pythonActiveUniverse(500_000);

  if (!r.success) {
    wl(err(`فشل Active Universe: ${r.error}`));
    return;
  }

  const s = r.summary ?? {};
  wl(`  ${r.note}`);
  wl();
  wl(`  إجمالي: ${r.total_symbols} سهم مُحلَّل`);
  wl(`    ✅ LIQUID:   ${(s.LIQUID   ?? 0).toString().padStart(3)} سهم`);
  wl(`    ⚠️  ILLIQUID: ${(s.ILLIQUID ?? 0).toString().padStart(3)} سهم`);
  wl(`    🔶 THIN:     ${(s.THIN     ?? 0).toString().padStart(3)} سهم`);
  wl(`    ❌ DEAD:     ${(s.DEAD     ?? 0).toString().padStart(3)} سهم`);
  wl();

  const liquid = r.liquid_universe ?? [];
  if (liquid.length > 0) {
    wl('  ✅ أعلى 20 سهم سيولة (EGP/يوم):');
    wl('  Symbol  AvgValue30d(K)  Price   Days  Amihud   Class');
    wl('  ' + '─'.repeat(57));
    for (const s of liquid.slice(0, 20)) {
      wl(
        `  ${String(s.symbol).padEnd(7)}` +
        ` ${String(s.avg_value_30d_k).padEnd(16)}` +
        ` ${fmt(s.price_latest, 2).padEnd(7)}` +
        ` ${String(s.days_traded).padEnd(5)}` +
        ` ${fmt(s.amihud_30d, 4).padEnd(8)}` +
        ` ${s.liquidity_class}`
      );
    }
  }

  const dead = r.dead_symbols ?? [];
  if (dead.length > 0) {
    wl();
    wl(`  ❌ أسهم DEAD/غير نشطة (${dead.length} سهم):`);
    wl(`  ${dead.slice(0, 30).join(' • ')}`);
  }

  const core = r.recommended_core ?? [];
  if (core.length > 0) {
    wl();
    wl('  ⭐ الـ Universe الموصى به (أعلى 30 سيولة):');
    wl(`  ${core.join(', ')}`);
  }

  const filters = r.filters_used ?? {};
  wl();
  wl(`  الفلاتر: MinValue=${(filters.min_avg_daily_value / 1000).toFixed(0)}K EGP/يوم | Window=${filters.window_days}d`);
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 5. Sector Rotation
// ─────────────────────────────────────────────────────────────────────────

async function runSectorRotation() {
  h2('Sector Rotation Alpha (Relative Strength + Momentum Acceleration)');

  const t0 = Date.now();
  const r  = await pythonSectorRotation();

  if (!r.success) {
    wl(err(`فشل Sector Rotation: ${r.error}`));
    return;
  }

  wl(`  📊 متوسط السوق: Mom5=${fmt(r.market_avg_mom5, 2)}% | Mom10=${fmt(r.market_avg_mom10, 2)}%`);
  wl(`  ${r.rotation_insight}`);
  wl();

  const ICONS = {
    LEADING: '🔥', IMPROVING: '⬆️ ', WEAKENING: '⚠️ ', RECOVERING: '🔄', LAGGING: '❌',
  };

  wl('  Sector               Class        Score   RS5    Mom5   Mom10  RSI    Stocks  Pct+');
  wl('  ' + '─'.repeat(80));
  for (const s of (r.sector_ranking ?? [])) {
    const icon = ICONS[s.classification] ?? '◾';
    wl(
      `  ${icon} ${s.sector.padEnd(18)}` +
      ` ${s.classification.padEnd(11)}` +
      ` ${fmt(s.rotation_score, 2).padEnd(7)}` +
      ` ${fmt(s.rs_vs_market, 2).padEnd(6)}` +
      ` ${fmt(s.avg_mom5, 2).padEnd(6)}` +
      ` ${fmt(s.avg_mom10, 2).padEnd(6)}` +
      ` ${fmt(s.avg_rsi, 1).padEnd(6)}` +
      ` ${String(s.n_stocks).padEnd(7)}` +
      ` ${fmt(s.pct_positive, 0)}%`
    );
  }

  // Best stocks in leading sectors
  const best = r.best_in_leading ?? {};
  if (Object.keys(best).length > 0) {
    wl();
    wl('  ⭐ أفضل أسهم في القطاعات الرائدة:');
    for (const [sector, stocks] of Object.entries(best)) {
      const syms = stocks.map(s => `${s.symbol}(Mom5=${fmt(s.momentum_5d, 1)}%,RSI=${fmt(s.rsi14, 0)})`);
      wl(`    ${sector}: ${syms.join(' | ')}`);
    }
  }

  wl();
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 6. Pairs Trading
// ─────────────────────────────────────────────────────────────────────────

async function runPairsTrading() {
  h2('Pairs Trading (Engle-Granger Cointegration)');
  wl('  🔗 يكتشف أزواج الأسهم المترابطة إحصائياً — استراتيجية مستقلة عن اتجاه السوق');
  wl('  📌 z < -2 = Long Spread (شراء S1 + بيع S2) | z > +2 = Short Spread');
  wl();

  const t0 = Date.now();
  const r  = await pythonPairsTrading(120, 0.65, 0.10);

  if (!r.success) {
    wl(err(`فشل Pairs Trading: ${r.error}`));
    return;
  }

  wl(`  مرشحين بـ corr≥${r.params?.min_corr}: ${r.candidates_screened} زوج`);
  wl(`  متكاملة (coint p≤${r.params?.coint_pval_threshold}): ${r.cointegrated_count} زوج`);
  wl(`  ${r.summary}`);
  wl();

  const actionable = r.actionable_pairs ?? [];
  const neutral    = r.neutral_pairs    ?? [];

  if (actionable.length > 0) {
    wl('  🎯 أزواج بإشارة نشطة:');
    wl('  Pair          Signal            Z-Score  Corr   P-val  Hedge  S1-Sector    S2-Sector');
    wl('  ' + '─'.repeat(90));
    for (const p of actionable.slice(0, 15)) {
      const signalIcon =
        p.signal === 'LONG_SPREAD'       ? '🟢 LONG ' :
        p.signal === 'SHORT_SPREAD'      ? '🔴 SHORT' :
        p.signal === 'APPROACHING_BUY'   ? '👀 APPR+' :
        p.signal === 'APPROACHING_SELL'  ? '👀 APPR-' : '◾ ' + p.signal;
      wl(
        `  ${p.pair.padEnd(13)} ` +
        ` ${signalIcon.padEnd(17)}` +
        ` ${fmt(p.z_score, 2).padEnd(8)}` +
        ` ${fmt(p.correlation, 3).padEnd(6)}` +
        ` ${fmt(p.coint_pval, 4).padEnd(6)}` +
        ` ${fmt(p.hedge_ratio, 3).padEnd(6)}` +
        ` ${(p.sector1 ?? '?').padEnd(12)}` +
        ` ${p.sector2 ?? '?'}`
      );
    }
  } else {
    wl(warn('لا أزواج بإشارة نشطة الآن (z-scores قريبة من 0 = spread طبيعي)'));
  }

  if (neutral.length > 0) {
    wl();
    wl('  ⏳ أزواج متكاملة (جاهزة عند انتهاء Spread):');
    for (const p of neutral.slice(0, 5)) {
      wl(`    ${p.pair.padEnd(12)} z=${fmt(p.z_score, 2).padEnd(6)} corr=${fmt(p.correlation, 3)} p=${fmt(p.coint_pval, 4)}`);
    }
  }

  wl();
  wl(`  📖 ${r.strategy_note}`);
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 7. Portfolio Filter
// ─────────────────────────────────────────────────────────────────────────

async function runPortfolioFilter() {
  h2('Portfolio Filter (أفضل 5 صفقات اليوم مع تنويع القطاعات)');

  const t0  = Date.now();
  const ens = await pythonEnsembleSignal();

  if (!ens.success) {
    wl(err(`فشل Ensemble: ${ens.error}`));
    return;
  }

  // جمع كل الإشارات (STRONG_BUY + BUY + WATCH)
  const allSignals = [
    ...(ens.strong_buy ?? []),
    ...(ens.buy        ?? []),
    ...(ens.watch      ?? []),
  ];

  if (allSignals.length === 0) {
    wl(warn('لا إشارات اليوم — السوق في momentum صاعد (RSI مرتفع)'));
    wl('  عند ظهور إشارات، سيظهر هنا المحفظة المقترحة مع تنويع القطاعات.');
    return;
  }

  const portfolio = filterPortfolio(allSignals, 5, 45);

  wl(`  🎯 من ${allSignals.length} إشارة — المختار بعد التنويع: ${portfolio.length} صفقة`);
  wl();

  if (portfolio.length > 0) {
    wl('  #  Symbol  Score  Sector        Alloc%  Risk(EGP)  Signal');
    wl('  ' + '─'.repeat(60));
    for (const p of portfolio) {
      const signalIcon =
        (p.composite_score ?? 0) >= 75 ? '🔥' :
        (p.composite_score ?? 0) >= 60 ? '🟢' : '👀';
      wl(
        `  ${String(p.rank).padEnd(3)}` +
        ` ${p.symbol.padEnd(7)}` +
        ` ${fmt(p.composite_score, 0).padEnd(6)}` +
        ` ${(p.sector ?? 'Unknown').padEnd(13)}` +
        ` ${String(p.allocationPct + '%').padEnd(7)}` +
        ` ${(p.riskEGP?.toLocaleString() ?? '—').padEnd(10)}` +
        ` ${signalIcon}`
      );
    }
    wl();
    const totalAlloc = portfolio.reduce((s, p) => s + p.allocationPct, 0);
    const totalRisk  = portfolio.reduce((s, p) => s + (p.riskEGP ?? 0), 0);
    wl(`  إجمالي التخصيص: ${totalAlloc}% | إجمالي المخاطرة: ${totalRisk.toLocaleString()} جنيه`);
  }

  wl();
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 6. Event-Based Engine (Standalone)
// ─────────────────────────────────────────────────────────────────────────

async function runEventSignals() {
  h2('Event-Based Engine v1 — Path-Dependent Signal Detection');
  wl('  يكشف التسلسل الزمني للأحداث (لا snapshot ثابت) — Path-Awareness');
  wl('');
  wl('  ┌── التسلسل الذهبي ──────────────────────────────────────────────────┐');
  wl('  │  UPTREND → SHARP_DROP → PANIC → EXHAUSTION = HIGH_PROB_REVERSAL   │');
  wl('  │  WR5 ~62% | أقوى إشارة في النظام                                  │');
  wl('  └────────────────────────────────────────────────────────────────────┘');
  wl();

  const t0 = Date.now();
  const r  = await eventSignals({ minScore: 45, topN: 25 });

  if (!r.success) {
    wl(`  ❌ فشل: ${r.error}`);
    return;
  }

  // Hybrid Regime v2
  const r2 = r.regime_v2 ?? {};
  const confIcon = r2.confidence === 'HIGH' ? '🟢' : r2.confidence === 'MEDIUM' ? '🟡' : '🔴';
  wl(`  Hybrid Regime v2: ${r2.regime_v2 ?? '?'} | ${confIcon} ${r2.confidence ?? '?'} | Breadth=${r2.breadth_pct ?? '?'}% | Mult=×${r2.regime_mult ?? '?'}`);
  wl(`  ${r2.leading_note ?? ''}`);
  wl(`  ${r2.breadth_note ?? ''} | ${r2.vol_note || 'تقلب طبيعي'}`);
  wl();

  const s = r.summary ?? {};
  wl(`  📊 فحص: ${s.total_screened} سهم → ${s.total_signals} إشارة`);
  wl(`  ⭐ HIGH_PROB_REVERSAL: ${s.high_prob_reversal ?? 0} (التسلسل الذهبي)`);
  wl(`  🟡 LIKELY_REVERSAL:   ${s.likely_reversal ?? 0}`);
  wl(`  🟠 POSSIBLE_REVERSAL: ${s.possible_reversal ?? 0}`);
  wl(`  ⚪ OVERSOLD_WEAK:     ${s.oversold_weak ?? 0}`);
  wl();
  wl(`  ${r.note ?? ''}`);

  const gold = r.gold_signals ?? [];
  if (gold.length > 0) {
    wl();
    wl('  ⭐ التسلسل الذهبي — HIGH_PROB_REVERSAL (WR5~62%):');
    wl('  Symbol  Adj.Score  Events                      RSI   Mom5   ATR_z');
    wl('  ' + '─'.repeat(70));
    for (const g of gold) {
      wl(
        `  ${String(g.symbol).padEnd(7)}` +
        ` ${String(g.adj_score).padEnd(10)}` +
        ` ${(g.events ?? []).join('→').padEnd(28)}` +
        ` ${String(g.rsi14 ?? '').padEnd(5)}` +
        ` ${String((g.mom5 ?? 0).toFixed(1)).padEnd(6)}` +
        ` ${(g.atr_z ?? 0).toFixed(2)}`
      );
    }
  }

  const sigs = (r.signals ?? []).filter(s2 => s2.state !== 'HIGH_PROB_REVERSAL').slice(0, 15);
  if (sigs.length > 0) {
    wl();
    wl('  📋 باقي الإشارات (score ≥ 45 بعد تعديل Regime):');
    wl('  Symbol  State                   Score  Events                   RSI');
    wl('  ' + '─'.repeat(68));
    for (const sig of sigs) {
      wl(
        `  ${String(sig.symbol).padEnd(7)}` +
        ` ${String(sig.state).padEnd(24)}` +
        ` ${String(sig.adj_score).padEnd(6)}` +
        ` ${(sig.events ?? []).join('+').padEnd(25)}` +
        ` ${sig.rsi14 ?? ''}`
      );
    }
  }

  wl();
  wl(`  🎯 Method: ${r.methodology ?? ''}`);
  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 9. Market State Transition Engine — Markov Chain
// ─────────────────────────────────────────────────────────────────────────

async function runStateTransitions() {
  h2('Market State Transition Engine — نموذج ماركوف الاحتمالي');
  wl('  يُصنِّف كل شمعة إلى 10 حالات ويبني P(next_state | current_state)');
  wl('  + P(TRUE_REVERSAL | state + regime) + Discriminant conditions');
  wl();

  const t0 = Date.now();
  const r  = await pythonStateTransitions(5);   // fwd_bars = 5

  if (!r.success) {
    wl(`  ❌ فشل: ${r.error}`);
    return;
  }

  // ── ملخص السوق ─────────────────────────────────────────────────────────
  wl(`  🌍 Regime الآن: ${r.market_regime_now ?? '?'} | ${r.dataset?.n_rows?.toLocaleString()} شمعة | ${r.dataset?.n_symbols} سهم`);
  wl();

  // ── توزيع الحالات الحالي ───────────────────────────────────────────────
  const dist = r.state_distribution ?? {};
  const total= Object.values(dist).reduce((a, b) => a + b, 0) || 1;
  const STATE_EMOJI = {
    PANIC:              '🔴',
    VELOCITY_EXHAUSTION:'🟠',
    EXHAUSTION:         '🟡',
    STABILIZATION:      '🟢',
    POTENTIAL_BOUNCE:   '🔵',
    ACCELERATING_UP:    '🚀',
    TRENDING_UP:        '📈',
    SHARP_DROP:         '⬇️',
    CONTINUATION_DOWN:  '⬇️',
    DISTRIBUTION:       '⚠️',
    NEUTRAL:            '⚪',
  };
  wl('  📊 توزيع حالات السوق (آخر شريط لكل سهم):');
  const distOrder = [
    'PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION', 'STABILIZATION',
    'POTENTIAL_BOUNCE', 'ACCELERATING_UP', 'TRENDING_UP',
    'SHARP_DROP', 'CONTINUATION_DOWN', 'DISTRIBUTION', 'NEUTRAL',
  ];
  for (const st of distOrder) {
    const n   = dist[st] ?? 0;
    if (!n) continue;
    const pct = (n / total * 100).toFixed(1);
    const ico = STATE_EMOJI[st] ?? '  ';
    const bar = '█'.repeat(Math.round(n / total * 30));
    wl(`  ${ico} ${st.padEnd(22)} ${String(n).padStart(4)} (${pct.padStart(5)}%)  ${bar}`);
  }
  wl();

  // ── Golden Signals الآن ────────────────────────────────────────────────
  const golden = r.golden_signals_now ?? [];
  const hiPri  = r.high_priority_now ?? [];
  if (golden.length > 0) {
    wl(`  ⭐ الإشارات الذهبية الآن (PANIC→VEL_EXHAUS + CRASH/DOWN) — ${golden.length} سهم:`);
    wl('  Symbol  State                 Regime  P_TR%  Prior  RSI    Mom5');
    wl('  ' + '─'.repeat(70));
    for (const g of golden) {
      wl(
        `  ${String(g.symbol).padEnd(8)}` +
        ` ${String(g.state).padEnd(22)}` +
        ` ${String(g.regime).padEnd(8)}` +
        ` ${g.p_true_rev != null ? String(g.p_true_rev).padEnd(6) : '—     '}` +
        ` ${g.prior_panic ? '✓PANIC' : '      '}` +
        ` ${g.rsi != null ? String(g.rsi).padEnd(6) : '      '}` +
        ` ${g.mom5 != null ? g.mom5.toFixed(1) : '—'}`
      );
    }
    wl();
  } else if (hiPri.length > 0) {
    wl(`  🟠 إشارات عالية الأولوية الآن (CRASH/DOWN regime) — ${hiPri.length} سهم:`);
    wl('  Symbol  State                 Regime  P_TR%  RSI    Mom5');
    wl('  ' + '─'.repeat(65));
    for (const g of hiPri.slice(0, 12)) {
      wl(
        `  ${String(g.symbol).padEnd(8)}` +
        ` ${String(g.state).padEnd(22)}` +
        ` ${String(g.regime).padEnd(8)}` +
        ` ${g.p_true_rev != null ? String(g.p_true_rev).padEnd(6) : '—     '}` +
        ` ${g.rsi != null ? String(g.rsi).padEnd(6) : '      '}` +
        ` ${g.mom5 != null ? g.mom5.toFixed(1) : '—'}`
      );
    }
    wl();
  } else {
    wl('  ℹ️  لا توجد إشارات ذهبية اليوم (يحتاج CRASH/DOWN regime + prior PANIC)');
    wl();
  }

  // ── Discriminant Matrix ────────────────────────────────────────────────
  const disc = r.discriminant ?? {};
  if (Object.keys(disc).length > 0) {
    wl('  🔬 Discriminant — ما الذي يُفرِّق TRUE_REVERSAL من DEAD_CAT?');
    wl(`  ${'Condition'.padEnd(38)} ${'N'.padStart(5)}  ${'P_TR%'.padStart(7)}  ${'Avg5%'.padStart(7)}`);
    wl('  ' + '─'.repeat(65));
    const discOrder = [
      'all_exhaustion_baseline',
      'panic_vel_exh_crash',
      'rsi_lt25_atr_crash',
      'vel_exh_crash',
      'rsi_still_falling',
      'rsi_flat_trap',
      'surge_regime_trap',
    ];
    for (const key of discOrder) {
      const d = disc[key];
      if (!d) continue;
      const warn_flag = (key === 'rsi_flat_trap' || key === 'surge_regime_trap') ? ' ⚠️' : '';
      const gold_flag = key === 'panic_vel_exh_crash' ? ' 🌟' : '';
      wl(
        `  ${String(d.label ?? key).padEnd(38)}` +
        ` ${String(d.n ?? '').padStart(5)}` +
        `  ${String(d.p_true_rev ?? '—').padStart(6)}%` +
        `  ${d.avg_fwd_ret != null ? (d.avg_fwd_ret > 0 ? '+' : '') + d.avg_fwd_ret.toFixed(2) + '%' : '—'}` +
        `${gold_flag}${warn_flag}`
      );
    }
    wl();
  }

  // ── Regime Conditional (أهم حالتين) ───────────────────────────────────
  const rc = r.regime_conditional ?? {};
  const KEY_STATES = ['VELOCITY_EXHAUSTION', 'EXHAUSTION'];
  for (const st of KEY_STATES) {
    const byReg = rc[st];
    if (!byReg || !Object.keys(byReg).length) continue;
    wl(`  📐 P(TRUE_REVERSAL | ${st} + regime):`);
    wl(`  ${'Regime'.padEnd(10)} ${'N'.padStart(5)}  ${'P_TR%'.padStart(7)}  ${'P_CD%'.padStart(7)}  ${'Avg5%'.padStart(7)}`);
    wl('  ' + '─'.repeat(50));
    for (const reg of ['CRASH', 'DOWN', 'NEUTRAL', 'UP', 'SURGE']) {
      const d = byReg[reg];
      if (!d) continue;
      const icon = reg === 'CRASH' ? '🔴' : reg === 'DOWN' ? '🟠' : reg === 'SURGE' ? '🚨' : '⚪';
      wl(
        `  ${icon} ${String(reg).padEnd(8)}` +
        ` ${String(d.n).padStart(5)}` +
        `  ${String(d.p_true_rev).padStart(6)}%` +
        `  ${String(d.p_cont_down).padStart(6)}%` +
        `  ${d.avg_fwd_ret > 0 ? '+' : ''}${d.avg_fwd_ret.toFixed(2)}%`
      );
    }
    wl();
  }

  // ── مصفوفة الانتقال (أهم الحالات) ────────────────────────────────────
  const tm = r.transition_matrix ?? {};
  const KEY_TM = ['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION', 'ACCELERATING_UP'];
  for (const st of KEY_TM) {
    const d = tm[st];
    if (!d) continue;
    const top = d.top3 ?? [];
    const topStr = top.map(t => `${t.state}(${t.prob}%)`).join(' → ');
    wl(`  🔀 ${st.padEnd(22)}: ${topStr}  | WR=${d.wr}% | Avg=${d.avg_fwd_ret > 0 ? '+' : ''}${d.avg_fwd_ret}%`);
  }
  wl();

  // ── State Duration ─────────────────────────────────────────────────────
  const dur = r.state_duration ?? {};
  const KEY_DUR = ['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION', 'TRENDING_UP', 'NEUTRAL'];
  if (Object.keys(dur).length > 0) {
    wl('  ⏱️  مدة الحالات (بالأشرطة):');
    wl(`  ${'State'.padEnd(22)} ${'Median'.padStart(6)}  ${'Mean'.padStart(6)}  ${'P90'.padStart(6)}`);
    wl('  ' + '─'.repeat(48));
    for (const st of KEY_DUR) {
      const d = dur[st];
      if (!d) continue;
      wl(
        `  ${String(st).padEnd(22)}` +
        ` ${String(d.median).padStart(6)}` +
        `  ${String(d.mean).padStart(6)}` +
        `  ${String(d.p90).padStart(6)}`
      );
    }
    wl();
  }

  // ── Golden Path Probabilities ──────────────────────────────────────────
  const gseq = r.golden_sequences ?? [];
  if (gseq.length > 0) {
    wl('  🌟 احتماليات المسار الذهبي:');
    for (const g of gseq) {
      if (g.prob > 0) {
        wl(`    ${g.path.padEnd(40)} ${String(g.prob).padStart(5)}%  ← ${g.meaning}`);
      }
    }
    wl(`\n  P(المسار الكامل) = ${r.golden_full_prob_pct?.toFixed(4) ?? '?'}%`);
    wl();
  }

  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// 10. Conditional Market State Evolution
// ─────────────────────────────────────────────────────────────────────────

async function runConditionalTransitions() {
  h2('Conditional State Evolution — نموذج الانتقال الشرطي متعدد الأبعاد');
  wl('  P(REVERSAL | state, regime, sector, ATR, liquidity, duration, breadth)');
  wl('  يُجيب: متى تنعكس؟ ومتى تفشل؟ وأي متغير خفي يتحكم؟');
  wl();

  const t0 = Date.now();
  const r  = await pythonConditionalTransitions(5, 0.03);

  if (!r.success) { wl(`  ❌ فشل: ${r.error}`); return; }

  const ds = r.dataset ?? {};
  wl(`  📦 ${ds.n_rows?.toLocaleString()} شمعة | ${ds.n_symbols} سهم | fwd=${ds.fwd_bars}d | TR>${ds.true_rev_thr}`);
  wl();

  // ── أفضل 5 شروط (5D surface) ──────────────────────────────────────────
  const best = r.best_conditions ?? [];
  if (best.length > 0) {
    wl('  ┌── أفضل الشروط المُركَّبة (5D Surface) ────────────────────────────────┐');
    wl('  │  State                 Regime  Sector       ATR    N     P_TR%  Avg%  │');
    wl('  │  ' + '─'.repeat(68) + '│');
    for (const c of best.slice(0, 6)) {
      const line =
        `  │  ${String(c.state).padEnd(22)}` +
        ` ${String(c.regime).padEnd(8)}` +
        ` ${String(c.sector).padEnd(13)}` +
        ` ${String(c.atr).padEnd(7)}` +
        ` ${String(c.n).padStart(4)}` +
        `  ${String(c.p_tr).padStart(5)}%` +
        `  ${c.avg_fwd > 0 ? '+' : ''}${c.avg_fwd.toFixed(2)}%  │`;
      wl(line);
    }
    wl('  └' + '─'.repeat(70) + '┘');
    wl();
  }

  // ── أخطر الفخاخ ───────────────────────────────────────────────────────
  const traps = r.worst_traps ?? [];
  if (traps.length > 0) {
    wl('  🚫 أخطر الفخاخ — AVOID (hard_fail > 30%):');
    wl(`  ${'State+Regime'.padEnd(34)} ${'N'.padStart(4)}  ${'HardFail%'.padStart(9)}  ${'Avg5%'.padStart(7)}`);
    wl('  ' + '─'.repeat(62));
    for (const t of traps) {
      wl(
        `  ⛔ ${String(`${t.state}+${t.regime}`).padEnd(32)}` +
        ` ${String(t.n).padStart(4)}` +
        `  ${String(t.p_hard_fail).padStart(8)}%` +
        `  ${t.avg_fwd > 0 ? '+' : ''}${t.avg_fwd.toFixed(2)}%`
      );
    }
    wl();
  }

  // ── Duration Surface ───────────────────────────────────────────────────
  const durSurf = r.duration_surface ?? {};
  const DUR_STATES = ['VELOCITY_EXHAUSTION', 'EXHAUSTION', 'PANIC'];
  let dur_header_shown = false;
  for (const st of DUR_STATES) {
    const d = durSurf[st];
    if (!d || !d['_meta']) continue;
    if (!dur_header_shown) {
      wl('  ⏱️  Duration Surface — P(TR | state, duration_in_state):');
      wl(`  ${'State'.padEnd(24)} ${'1bar'.padStart(6)} ${'2bar'.padStart(6)} ${'3bar'.padStart(6)} ${'4-5'.padStart(6)} ${'6+'.padStart(6)}  ExhaustAt`);
      wl('  ' + '─'.repeat(72));
      dur_header_shown = true;
    }
    const vals = ['1','2','3','4-5','6+'].map(bkt => {
      const v = d[bkt];
      return v ? `${v.p_tr}%` : '—';
    });
    const meta = d['_meta'];
    wl(
      `  ${String(st).padEnd(24)}` +
      vals.map(v => String(v).padStart(6)).join('') +
      `  → ${meta.exhaustion_at} bars (${meta.exhaustion_p_tr}%)` +
      (meta.duration_matters ? ' ✓' : '')
    );
  }
  wl();

  // ── Sector Conditionality ──────────────────────────────────────────────
  const secSurf = r.sector_surface ?? {};
  const SEC_STATES = ['PANIC', 'VELOCITY_EXHAUSTION', 'EXHAUSTION'];
  wl('  🏭 Sector Conditionality — هل الانتقال ثابت عبر القطاعات؟');
  wl(`  ${'State'.padEnd(24)} ${'BANKS'.padStart(6)} ${'RE'.padStart(6)} ${'TELE'.padStart(6)} ${'IND'.padStart(6)} ${'CONS'.padStart(6)}  Stable?`);
  wl('  ' + '─'.repeat(72));
  for (const st of SEC_STATES) {
    const d = secSurf[st];
    if (!d) continue;
    const secs = ['BANKS', 'REAL_ESTATE', 'TELECOM_TECH', 'INDUSTRIALS', 'CONSUMER'];
    const vals = secs.map(s => d[s] ? `${d[s].p_tr}%` : '—');
    const meta = d['_meta'] ?? {};
    wl(
      `  ${String(st).padEnd(24)}` +
      vals.map(v => String(v).padStart(6)).join('') +
      `  ${meta.sector_stable ? '✅' : '⚠️ '} best=${meta.best_sector ?? '?'}`
    );
  }
  wl();

  // ── Regime Stability ──────────────────────────────────────────────────
  const regStab = r.regime_stability ?? {};
  wl('  🌊 Regime Stability — هل يصمد الانتقال عبر البيئات؟');
  wl(`  ${'State'.padEnd(24)} ${'CRASH'.padStart(6)} ${'DOWN'.padStart(6)} ${'SIDE'.padStart(6)} ${'NEUT'.padStart(6)} ${'UP'.padStart(6)} ${'SURGE'.padStart(6)}  Verdict`);
  wl('  ' + '─'.repeat(78));
  for (const st of ['PANIC','VELOCITY_EXHAUSTION','EXHAUSTION','STABILIZATION']) {
    const d = regStab[st];
    if (!d) continue;
    const regs = ['CRASH','DOWN','SIDEWAYS','NEUTRAL','UP','SURGE'];
    const vals = regs.map(reg => d[reg] ? `${d[reg].p_tr}%` : '—');
    const meta = d['_meta'] ?? {};
    const vIcon = meta.verdict === 'STABLE' ? '✅' : meta.verdict === 'MILD' ? '⚠️' : '❌';
    wl(
      `  ${String(st).padEnd(24)}` +
      vals.map(v => String(v).padStart(6)).join('') +
      `  ${vIcon} ${meta.verdict ?? '?'}`
    );
  }
  wl();

  // ── Failure Autopsy (أهم حالتين) ──────────────────────────────────────
  const fe = r.failure_engine ?? {};
  for (const st of ['VELOCITY_EXHAUSTION', 'PANIC']) {
    const byReg = fe[st]?.by_regime ?? {};
    const byAtr = fe[st]?.by_atr ?? {};
    if (!Object.keys(byReg).length) continue;

    wl(`  🔬 Failure Autopsy — ${st}:`);
    wl(`  ${'Regime'.padEnd(10)} ${'N'.padStart(4)}  ${'TrueRev'.padStart(8)}  ${'Modest'.padStart(7)}  ${'DeadCat'.padStart(8)}  ${'HardFail'.padStart(9)}  ${'Avg5%'.padStart(7)}`);
    wl('  ' + '─'.repeat(68));
    for (const reg of ['CRASH','DOWN','SIDEWAYS','NEUTRAL','UP','SURGE']) {
      const v = byReg[reg];
      if (!v) continue;
      const danger = v.p_hard_fail > 25 ? ' ⚠️' : '';
      wl(
        `  ${String(reg).padEnd(10)}` +
        ` ${String(v.n).padStart(4)}` +
        `  ${String(v.p_true_rev).padStart(7)}%` +
        `  ${String(v.p_modest_win).padStart(6)}%` +
        `  ${String(v.p_dead_cat).padStart(7)}%` +
        `  ${String(v.p_hard_fail).padStart(8)}%` +
        `  ${v.avg_fwd > 0 ? '+' : ''}${v.avg_fwd.toFixed(2)}%${danger}`
      );
    }

    // ATR effect
    const atrVals = ['LOW','NORMAL','HIGH','EXTREME'].map(t =>
      byAtr[t] ? `${t}:${byAtr[t].p_true_rev}%` : null
    ).filter(Boolean);
    if (atrVals.length) wl(`  ↳ ATR: ${atrVals.join(' | ')}`);
    wl();
  }

  // ── Breadth Surface ────────────────────────────────────────────────────
  const breadthSurf = r.breadth_surface ?? {};
  const bVEL = breadthSurf['VELOCITY_EXHAUSTION'] ?? {};
  if (Object.keys(bVEL).length > 0) {
    wl('  📡 Breadth Conditionality (VELOCITY_EXHAUSTION):');
    wl(`  ${'Breadth_Zone'.padEnd(18)} ${'N'.padStart(4)}  ${'P_TR%'.padStart(7)}  ${'Avg5%'.padStart(7)}`);
    wl('  ' + '─'.repeat(44));
    for (const [label, v] of Object.entries(bVEL)) {
      if (!v?.n) continue;
      const icon = v.p_tr >= 60 ? '🟢' : v.p_tr >= 40 ? '🟡' : '🔴';
      wl(`  ${icon} ${String(label).padEnd(16)} ${String(v.n).padStart(4)}  ${String(v.p_tr).padStart(6)}%  ${v.avg_fwd > 0 ? '+' : ''}${v.avg_fwd.toFixed(2)}%`);
    }
    wl();
  }

  // ── Hidden Variables & Self-Learning ──────────────────────────────────
  const sl = r.self_learning ?? {};
  const hv = sl.hidden_variables ?? {};
  if (Object.keys(hv).length > 0) {
    wl('  🧠 Self-Learning — المتغير الخفي الأكثر تأثيراً:');
    wl(`  ${'State'.padEnd(24)} ${'Best Dim'.padEnd(12)} ${'Var↓%'.padStart(6)}  ${'dur'.padStart(5)} ${'atr'.padStart(5)} ${'sector'.padStart(7)} ${'regime'.padStart(7)} ${'liq'.padStart(5)}`);
    wl('  ' + '─'.repeat(74));
    for (const [st, v] of Object.entries(hv)) {
      const vr = v.variance_reduced ?? {};
      wl(
        `  ${String(st).padEnd(24)}` +
        ` ${String(v.most_powerful_dim).padEnd(12)}` +
        ` ${String(v.improvement_pct).padStart(5)}%` +
        ` ${String(vr.duration ?? 0).padStart(5)}%` +
        ` ${String(vr.atr ?? 0).padStart(5)}%` +
        ` ${String(vr.sector ?? 0).padStart(7)}%` +
        ` ${String(vr.regime ?? 0).padStart(7)}%` +
        ` ${String(vr.liquidity ?? 0).padStart(5)}%`
      );
    }
    wl();

    // Split/Merge Recommendations
    const instab = sl.state_instability ?? {};
    const splitList = Object.entries(instab)
      .filter(([, v]) => v.split_candidate)
      .map(([st, v]) => `${st}(by ${v.most_important_dim})`);
    if (splitList.length > 0) {
      wl(`  ✂️  مرشَّحو التقسيم: ${splitList.join(' | ')}`);
    }
    const merges = sl.merge_candidates ?? [];
    if (merges.length > 0) {
      const ml = merges.slice(0,3).map(m => `${m.states.join('+')}(Δ${m.diff}%)`).join(' | ');
      wl(`  🔗 مرشَّحو الدمج (P_TR متشابه): ${ml}`);
    }
    wl();
  }

  // ── Physics ───────────────────────────────────────────────────────────
  const ph = r.physics ?? {};
  if (ph.pressure_high && ph.pressure_low) {
    const hi = ph.pressure_high; const lo = ph.pressure_low;
    wl('  ⚡ Market Physics — ضغط البائعين (drop_accel × ATR_z):');
    wl(`  Pressure HIGH (n=${hi.n}): P(TR)=${hi.p_tr}%  Sharpe=${hi.sharpe}  Avg=${hi.avg_fwd > 0 ? '+' : ''}${hi.avg_fwd.toFixed(2)}%`);
    wl(`  Pressure LOW  (n=${lo.n}): P(TR)=${lo.p_tr}%  Sharpe=${lo.sharpe}  Avg=${lo.avg_fwd > 0 ? '+' : ''}${lo.avg_fwd.toFixed(2)}%`);
    const better = hi.p_tr > lo.p_tr ? 'HIGH pressure' : 'LOW pressure';
    wl(`  → ${better} → انعكاس أكثر موثوقية`);
    wl();
  }

  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// Evolving Market Structure  (Multi-Horizon Alpha Decay + Cognitive Map)
// ─────────────────────────────────────────────────────────────────────────

async function runEvolvingStructure() {
  const t0 = Date.now();
  h2('🧬 Evolving Market Structure — خريطة إدراكية حيّة');

  const r = await pythonEvolvingStructure(5, 30, 180, 0.7);
  if (!r?.success) {
    wl(err(`فشل: ${r?.error ?? 'unknown'}`));
    return;
  }

  if (JSON_MODE) { wl(JSON.stringify(r, null, 2)); return; }

  // ── Header ────────────────────────────────────────────────────────────
  const stab  = r.structure_stability ?? '—';
  const stabIcon = stab === 'UNSTABLE' ? '🔴' : stab === 'SHIFTING' ? '🟡' : '🟢';
  const h     = r.horizons ?? {};
  wl(`  ${stabIcon} Structure: ${stab}  |  short=${h.short_days}d  medium=${h.medium_days}d  total=${h.total_bars?.toLocaleString()} bars`);
  wl(`  🎯 Dominant Failure Mode: ${r.dominant_failure_mode ?? '—'}`);
  if (r.summary) wl(`  📝 ${r.summary}`);
  wl();

  // ── Structure Alerts ──────────────────────────────────────────────────
  const alerts = r.structure_alerts ?? [];
  if (alerts.length > 0) {
    const highAlerts = alerts.filter(a => a.severity === 'HIGH');
    const modAlerts  = alerts.filter(a => a.severity !== 'HIGH');
    const ATYPE = { REGIME_SHIFT: '🌊', TRANSITION_DRIFT: '🔀', VOL_PERSISTENCE_CHANGE: '📡', FAILURE_CLUSTER: '⚠️ ' };

    if (highAlerts.length > 0) {
      sep('!', 65);
      wl(`  🚨 ${highAlerts.length} HIGH-SEVERITY STRUCTURE ALERTS:`);
      for (const a of highAlerts) {
        const icon = ATYPE[a.type] ?? '⚠️ ';
        if (a.type === 'REGIME_SHIFT') {
          const arrow = (a.delta ?? 0) > 0 ? '▲' : '▼';
          wl(`  ${icon} REGIME_SHIFT  ${arrow} ${String(a.regime).padEnd(10)} ${a.long_pct}% → ${a.short_pct}%  (Δ${a.delta > 0 ? '+' : ''}${a.delta}%)`);
        } else if (a.type === 'TRANSITION_DRIFT') {
          const mv = a.top_mover ?? {};
          wl(`  ${icon} TRANS_DRIFT   ${a.state}  L1=${a.l1_norm}  biggest: →${mv.to} Δ${mv.delta > 0 ? '+' : ''}${mv.delta}%`);
        } else if (a.type === 'VOL_PERSISTENCE_CHANGE') {
          wl(`  ${icon} VOL_PERSIST   ${a.direction}  hist=${a.long} → recent=${a.short}  Δ${a.delta > 0 ? '+' : ''}${a.delta}`);
        } else if (a.type === 'FAILURE_CLUSTER') {
          wl(`  ${icon} FAIL_CLUSTER  ${a.state}  recent_FR=${a.recent_fr}% vs hist=${a.hist_fr}%  mode=${a.worst_type}`);
        }
      }
      sep('!', 65);
      wl();
    }
    if (modAlerts.length > 0) {
      wl(`  ℹ️  ${modAlerts.length} moderate alerts (REGIME/DRIFT):  ` +
        modAlerts.slice(0,4).map(a => a.regime ?? a.state ?? a.type).join(', '));
      wl();
    }
  }

  // ── Alpha Decay Table ─────────────────────────────────────────────────
  // sorted: COLLAPSING→WEAKENING→STABLE→STRENGTHENING→UNKNOWN
  const STATUS_ORDER = { COLLAPSING:0, WEAKENING:1, STABLE:2, STRENGTHENING:3, UNKNOWN:4 };
  const ad = r.alpha_decay ?? {};
  const adEntries = Object.entries(ad)
    .filter(([,v]) => v.long?.p != null)
    .sort((a, b) => (STATUS_ORDER[a[1].alpha_status]??5) - (STATUS_ORDER[b[1].alpha_status]??5));

  if (adEntries.length > 0) {
    h2('📉 Alpha Decay — تدهور الحافة عبر الأفق');
    const S_ICON = { COLLAPSING:'💀', WEAKENING:'🔴', STABLE:'⚪', STRENGTHENING:'🟢', UNKNOWN:'❓' };
    const S_W = 22; const R_W = 8;
    const hdr = `${'State'.padEnd(S_W)} ${'Regime'.padEnd(R_W)} ${'Long%'.padStart(6)} ${'Med%'.padStart(6)} ${'Short%'.padStart(7)} ${'Δ(S-L)'.padStart(8)} ${'Accel'.padStart(7)} ${'Cluster'.padStart(8)} Status`;
    wl('  ' + hdr);
    wl('  ' + '─'.repeat(hdr.length));
    for (const [key, v] of adEntries) {
      const sI   = S_ICON[v.alpha_status] ?? '?';
      const pL   = v.long?.p    != null ? String(v.long.p    + '%').padStart(6)  : '  —'.padStart(6);
      const pM   = v.medium?.p  != null ? String(v.medium.p  + '%').padStart(6)  : '  —'.padStart(6);
      const pS   = v.short?.p   != null ? String(v.short.p   + '%').padStart(7)  : '   —'.padStart(7);
      const dv   = v.decay_velocity != null ? (v.decay_velocity > 0 ? '+' : '') + v.decay_velocity + '%' : '—';
      const da   = v.decay_acceleration != null ? (v.decay_acceleration > 0 ? '+' : '') + v.decay_acceleration : '—';
      const clu  = v.failure_clustering ? '🔴YES' : '⚪no';
      const [state, regime] = key.split('|');
      wl(`  ${String(state).padEnd(S_W)} ${String(regime).padEnd(R_W)} ${pL} ${pM} ${pS} ${String(dv).padStart(8)} ${String(da).padStart(7)} ${clu.padStart(8)} ${sI}${v.alpha_status}`);
    }
    wl();
  }

  // ── Cognitive Map — adaptive combined P(TR) ───────────────────────────
  const cm = r.cognitive_map ?? {};
  const cmEntries = Object.entries(cm)
    .filter(([,v]) => v.combined_p_tr != null)
    .sort((a, b) => (b[1].combined_p_tr ?? 0) - (a[1].combined_p_tr ?? 0));

  if (cmEntries.length > 0) {
    h2('🗺️  Cognitive Map — الخريطة الإدراكية (P_TR مُجمَّع تكيُّفياً)');
    const S_W = 22; const R_W = 8;
    const hdr = `${'State'.padEnd(S_W)} ${'Regime'.padEnd(R_W)} ${'P_comb%'.padStart(8)} ${'P_Bayes%'.padStart(9)} ${'CI-80'.padStart(13)} ${'Weights(S/M/L)'.padStart(15)} ${'Conf'.padStart(7)} Status`;
    wl('  ' + hdr);
    wl('  ' + '─'.repeat(hdr.length));

    const STATUS_COLOR = { COLLAPSING:'💀', WEAKENING:'🔴', STABLE:'⚪', STRENGTHENING:'🟢', UNKNOWN:'❓' };
    for (const [key, v] of cmEntries) {
      const sI  = STATUS_COLOR[v.alpha_status] ?? '?';
      const ci  = Array.isArray(v.ci_80) && v.ci_80[0] != null
        ? `[${v.ci_80[0]}–${v.ci_80[1]}]` : '—';
      const aw  = v.adaptive_weights;
      const wStr = aw ? `${aw.short}/${aw.medium}/${aw.long}` : '—';
      const [state, regime] = key.split('|');
      wl(`  ${String(state).padEnd(S_W)} ${String(regime).padEnd(R_W)} ${String(v.combined_p_tr + '%').padStart(8)} ${String((v.bayesian_p_tr ?? '—') + '%').padStart(9)} ${ci.padStart(13)} ${wStr.padStart(15)} ${String(v.confidence ?? '—').padStart(7)} ${sI}${v.alpha_status}`);
    }
    wl();

    // Promoted / suppressed
    const prom = r.promoted_edges ?? [];
    const supp = r.suppressed_edges ?? [];
    if (prom.length > 0) {
      wl(`  🟢 PROMOTED (${prom.length}): ` + prom.map(e => e.key + `→${e.combined_p}%`).join('  '));
      wl();
    }
    if (supp.length > 0) {
      wl(`  🔴 SUPPRESSED (${supp.length}): ` + supp.map(e => `${e.key}(${e.status},Δ${e.decay_v}%)`).join('  '));
      wl();
    }
  }

  // ── Structural Drift: Regime Timeline ─────────────────────────────────
  const rd = r.structural_drift?.regime_drift ?? {};
  const rdEntries = Object.entries(rd).filter(([,v]) => v.status !== 'STABLE');
  if (rdEntries.length > 0) {
    h2('🌊 Regime Drift — تطور تردد الأنظمة عبر الزمن');
    const R_W = 10;
    wl(`  ${'Regime'.padEnd(R_W)} ${'Long%'.padStart(7)} ${'Med%'.padStart(7)} ${'Short%'.padStart(8)} ${'Δ(S-L)'.padStart(9)} Status`);
    wl('  ' + '─'.repeat(55));
    for (const [reg, v] of rdEntries.sort((a,b)=>Math.abs(b[1].delta_l_s)-Math.abs(a[1].delta_l_s))) {
      const arrow = (v.delta_l_s ?? 0) > 0 ? '▲' : '▼';
      const sIcon = v.status === 'SHIFTING' ? '🔴' : '🟡';
      wl(`  ${String(reg).padEnd(R_W)} ${String(v.long_pct + '%').padStart(7)} ${String(v.medium_pct + '%').padStart(7)} ${String(v.short_pct + '%').padStart(8)} ${(arrow + (v.delta_l_s > 0 ? '+' : '') + v.delta_l_s + '%').padStart(9)} ${sIcon}${v.status}`);
    }
    wl();
  }

  // ── Structural Drift: Transition Matrix ───────────────────────────────
  const td = r.structural_drift?.transition_drift ?? {};
  const tdDrifting = Object.entries(td).filter(([,v]) => v.drifting);
  if (tdDrifting.length > 0) {
    h2('🔀 Transition Drift — تحوّل مصفوفة الانتقال (long vs short)');
    for (const [state, v] of tdDrifting.sort((a,b)=>b[1].l1_norm-a[1].l1_norm)) {
      wl(`  ${state}  L1=${v.l1_norm}`);
      for (const mv of (v.top_movers ?? []).slice(0,3)) {
        const arrow = mv.delta > 0 ? '▲' : '▼';
        wl(`    ${arrow} →${String(mv.to).padEnd(22)} ${mv.long_pct}% → ${mv.short_pct}%  (Δ${mv.delta > 0 ? '+' : ''}${mv.delta}%)`);
      }
    }
    wl();
  }

  // ── Vol Persistence + Breadth Correlation ────────────────────────────
  const vp = r.structural_drift?.vol_persistence ?? {};
  const brc = r.structural_drift?.breadth_rev_corr ?? {};
  const vpd = r.structural_drift?.vol_persistence_delta;
  if (vp.long != null || brc.long != null) {
    h2('📡 Volatility Persistence & Breadth Correlation');
    if (vp.long != null) {
      const vpArrow = vpd != null ? (vpd > 0 ? '▲ INCREASING' : '▼ DECREASING') : '';
      wl(`  ATR_z Autocorrelation (lag-1):  long=${vp.long}  medium=${vp.medium ?? '—'}  short=${vp.short ?? '—'}  ${vpArrow}`);
    }
    if (brc.long != null) {
      wl(`  Breadth ↔ Reversal Corr:        long=${brc.long}  medium=${brc.medium ?? '—'}  short=${brc.short ?? '—'}`);
    }
    const rs = r.structural_drift?.reversal_strength ?? {};
    if (rs.long) {
      wl(`  Reversal Strength (fwd ret):    long: n=${rs.long.n}  med=${rs.long.median}%  P25-75=[${rs.long.p25}%–${rs.long.p75}%]`);
      if (rs.short) wl(`                                  short: n=${rs.short.n}  med=${rs.short.median}%`);
    }
    wl();
  }

  // ── Failure Typing ────────────────────────────────────────────────────
  const ft = r.failure_types ?? {};
  const ftKeys = Object.keys(ft);
  if (ftKeys.length > 0) {
    h2('💀 Failure Typing — تشريح الإخفاقات');
    const S_W = 22;
    wl(`  ${'State'.padEnd(S_W)} ${'DeadCat%'.padStart(9)} ${'ContTrap%'.padStart(10)} ${'Drift%'.padStart(7)} ${'FakeRev%'.padStart(9)} ${'WorstType'.padStart(16)} ${'Cluster'.padStart(8)}`);
    wl('  ' + '─'.repeat(85));
    for (const [state, fv] of Object.entries(ft)) {
      const clu = fv.clustering ? `🔴${fv.cluster_severity}` : '⚪NONE';
      wl(`  ${String(state).padEnd(S_W)} ${String(fv.dead_cat_pct + '%').padStart(9)} ${String(fv.continuation_trap_pct + '%').padStart(10)} ${String(fv.drift_failure_pct + '%').padStart(7)} ${String(fv.fake_reversal_pct + '%').padStart(9)} ${String(fv.worst_type).padStart(16)} ${clu.padStart(8)}`);
    }
    wl();
    wl(`  💡 Dominant: ${r.dominant_failure_mode} — السقوط الحر المستمر هو أبرز سبب إخفاق`);
    wl();
  }

  // ── Market Physics (Long Horizon) ─────────────────────────────────────
  const phLong = r.market_physics?.long ?? {};
  const phShort = r.market_physics?.short ?? {};
  if (Object.keys(phLong).length > 0) {
    h2('⚡ Market Physics — ميكانيكا السوق (long vs short)');

    if (phLong.pressure_high) {
      const phH = phLong.pressure_high;  const phsH = phShort.pressure_high ?? {};
      wl(`  ⚡ Pressure HIGH:  long P(TR)=${phH.p_tr}%  short P(TR)=${phsH.p_tr ?? '—'}%  (n_long=${phH.n}, n_short=${phsH.n ?? '—'})`);
    }
    if (phLong.exhaustion_timing) {
      const ex = phLong.exhaustion_timing;  const exs = phShort.exhaustion_timing ?? {};
      wl(`  🔻 Exhaustion Timing:`);
      wl(`     long:  n=${ex.n}  median_gain=${ex.median_gain}%  P25-75=[${ex.p25_gain}%–${ex.p75_gain}%]`);
      if (exs.n) wl(`     short: n=${exs.n}  median_gain=${exs.median_gain}%`);
    }
    if (phLong.behavioral_persistence != null) {
      const bpL = phLong.behavioral_persistence;  const bpS = phShort.behavioral_persistence;
      const bpDelta = bpS != null ? round2(bpS - bpL) : null;
      wl(`  🔗 Behavioral Persistence:  long=${bpL}  short=${bpS ?? '—'}  ${bpDelta != null ? (bpDelta > 0 ? '▲' : '▼') + bpDelta : ''}`);
    }
    if (phLong.liquidity_absorption) {
      const la = phLong.liquidity_absorption;  const las = phShort.liquidity_absorption ?? {};
      wl(`  💧 Liquidity Absorption:   long P(TR)=${la.p_tr ?? '—'}%  short P(TR)=${las.p_tr ?? '—'}%`);
    }
    wl();
  }

  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

const round2 = (v, d = 3) => v != null ? +v.toFixed(d) : null;

// ─────────────────────────────────────────────────────────────────────────
// Adaptive Market Memory  (Bayesian Living Beliefs)
// ─────────────────────────────────────────────────────────────────────────

async function runAdaptiveMemory() {
  const t0 = Date.now();
  h2('🧠 Adaptive Market Memory — ذاكرة السوق الحية');

  const r = await pythonAdaptiveMemory(5, 0.7, 90);
  if (!r?.success) {
    wl(err(`فشل: ${r?.error ?? 'unknown'}`));
    return;
  }

  if (JSON_MODE) { wl(JSON.stringify(r, null, 2)); return; }

  // ── Current Regime + Summary ──────────────────────────────────────────
  const cr = r.current_regime ?? '—';
  const sc = r.structure_changed ?? false;
  wl(`  🌐 Regime الحالي: ${cr}  |  Structure Changed: ${sc ? '⚠️  YES' : '✅ NO'}`);
  if (r.summary) wl(`  📝 ${r.summary}`);
  wl();

  // ── Structural Change Alert ───────────────────────────────────────────
  const shifts = r.regime_shifts ?? [];
  if (sc && shifts.length > 0) {
    sep('!', 65);
    wl('  ⚠️  تحذير: بنية السوق تغيّرت بشكل جوهري — Regime Distribution Shift!');
    for (const s of shifts) {
      const arrow = s.direction === 'MORE' ? '▲' : '▼';
      const delta = s.shift;
      wl(`  ${arrow} ${String(s.regime).padEnd(12)} ${String(s.historical_pct ?? '—').padStart(5)}% → ${String(s.recent_pct ?? '—').padStart(5)}%  (Δ${delta > 0 ? '+' : ''}${delta}%)`);
    }
    sep('!', 65);
    wl();
  }

  // ── Living Beliefs Table — top beliefs sorted by posterior ─────────────
  // living_beliefs is a list of objects: { state, regime, posterior_p_tr, ci_80, drift, drift_magnitude, edge_quality, failure_memory, confidence, n_recent }
  const lb = r.living_beliefs ?? [];
  if (lb.length > 0) {
    h2('📊 Living Beliefs — المعتقدات البايزية الحية');

    const DRIFT_ICON = {
      STRENGTHENING: '🟢▲', MILD_STRENGTH: '🟡↗',
      STABLE: '⚪→',
      MILD_DECAY: '🟡↘', WEAKENING: '🔴▼',
    };
    const EDGE_ICON  = { STRONG: '💎', MODERATE: '✅', WEAK: '⚠️ ', NONE: '❌' };
    const FAIL_ICON  = { IMPROVING: '📈', NORMAL: '✅', CAUTION: '⚠️ ', DANGER: '🚨' };

    const S_W = 22; const R_W = 9;
    const hdr = `${'State'.padEnd(S_W)} ${'Regime'.padEnd(R_W)} ${'Post%'.padStart(6)} ${'CI-80'.padStart(13)} ${'Drift'.padStart(14)} ${'Edge'.padStart(4)} ${'Fail'.padStart(4)} ${'Conf'.padStart(7)}`;
    wl('  ' + hdr);
    wl('  ' + '─'.repeat(hdr.length));

    for (const b of lb) {
      const ci = Array.isArray(b.ci_80) ? `[${b.ci_80[0]}–${b.ci_80[1]}]` : '—';
      const dI  = DRIFT_ICON[b.drift] ?? '⚪';
      const eI  = EDGE_ICON[b.edge_quality] ?? '—';
      const fI  = FAIL_ICON[b.failure_memory] ?? '—';
      const dmag = b.drift_magnitude;
      const dStr = `${dI} ${dmag != null ? (dmag > 0 ? '+' : '') + dmag + '%' : ''}`;
      wl(`  ${String(b.state).padEnd(S_W)} ${String(b.regime ?? '').padEnd(R_W)} ${String(b.posterior_p_tr + '%').padStart(6)} ${ci.padStart(13)} ${dStr.padEnd(14)} ${eI.padStart(4)} ${fI.padStart(4)} ${String(b.confidence ?? '').padStart(7)}`);
    }
    wl();

    // Best and weakening edges
    const best = lb.filter(b => ['STRONG','MODERATE'].includes(b.edge_quality));
    const weakening = lb.filter(b => b.drift === 'WEAKENING');

    if (best.length > 0) {
      h2('💎 Best Edges — أقوى الحواف الموثوقة');
      for (const b of best) {
        const ci = Array.isArray(b.ci_80) ? `[${b.ci_80[0]}–${b.ci_80[1]}]` : '—';
        const dI = DRIFT_ICON[b.drift] ?? '⚪→';
        const eI = EDGE_ICON[b.edge_quality] ?? '';
        wl(`  ${eI}  ${b.state} + ${b.regime}`);
        wl(`     Posterior=${b.posterior_p_tr}%  CI=${ci}  n_recent=${b.n_recent}  conf=${b.confidence}`);
        wl(`     Drift: ${dI} ${b.drift} (${b.drift_magnitude > 0 ? '+' : ''}${b.drift_magnitude}%)  FailMem: ${FAIL_ICON[b.failure_memory] ?? ''} ${b.failure_memory}`);
        wl();
      }
    }

    if (weakening.length > 0) {
      h2('🔴 Weakening Edges — تراجع الموثوقية (احذر!)');
      for (const b of weakening) {
        const bb = r.bayesian_beliefs?.[b.state]?.[b.regime] ?? {};
        const hist = bb.p_all_tw;
        wl(`  🔴▼  ${b.state} + ${b.regime}`);
        wl(`     P_hist=${hist != null ? hist + '%' : '—'}  →  Posterior=${b.posterior_p_tr}%  Drift=${b.drift_magnitude}%  n_recent=${b.n_recent}`);
        wl(`     ⚠️  لا تستخدم P_hist الخام — الحالة تضعف بشكل ملحوظ`);
        wl();
      }
    }
  }

  // ── Hierarchical Multipliers ──────────────────────────────────────────
  // hier[state] = { base_p_tr, regime:{CRASH:1.38,...}, duration:{...}, atr:{...}, sector:{...}, breadth:{...} }
  const hier = r.hierarchical ?? {};
  const keyStates = ['VELOCITY_EXHAUSTION', 'EXHAUSTION', 'PANIC', 'POTENTIAL_BOUNCE'];
  const hierFound = keyStates.filter(s => hier[s]);
  if (hierFound.length > 0) {
    h2('📐 Hierarchical Multipliers — مضاعفات الأبعاد');
    for (const state of hierFound) {
      const h = hier[state];
      wl(`  ${state}  (base P(TR)=${h.base_p_tr ?? '—'}%)`);
      wl('  ' + '─'.repeat(62));

      const sections = [
        ['Regime',   h.regime],
        ['Breadth',  h.breadth],
        ['Duration', h.duration],
        ['ATR',      h.atr],
        ['Sector',   h.sector],
      ];

      for (const [label, mults] of sections) {
        if (!mults || !Object.keys(mults).length) continue;
        const sorted = Object.entries(mults).sort((a, b) => b[1] - a[1]);
        const items = sorted.map(([k, v]) => {
          const icon = v > 1.25 ? '🟢' : v > 0.85 ? '⚪' : '🔴';
          return `${icon}${k}×${(+v).toFixed(2)}`;
        }).join('  ');
        wl(`    ${label.padEnd(10)}: ${items}`);
      }
      wl();
    }
  }

  // ── Failure Memory per State ──────────────────────────────────────────
  // failure_memory[state] = { historical_fail_rate, recent_fail_rate, mid_fail_rate, fail_drift, memory_status, n_recent }
  const fm = r.failure_memory ?? {};
  const fmKeys = Object.keys(fm);
  if (fmKeys.length > 0) {
    h2('🚨 Failure Memory — ذاكرة الإخفاقات');
    const STATUS_ICON = { IMPROVING: '📈 IMPROVING', NORMAL: '✅ NORMAL', CAUTION: '⚠️  CAUTION', DANGER: '🚨 DANGER' };
    const sorted = Object.entries(fm).sort((a, b) => (b[1].fail_drift ?? 0) - (a[1].fail_drift ?? 0));
    for (const [state, fv] of sorted) {
      const sLabel = STATUS_ICON[fv.memory_status] ?? fv.memory_status ?? '—';
      const hist = fv.historical_fail_rate != null ? `${fv.historical_fail_rate}%` : '—';
      const rec  = fv.recent_fail_rate     != null ? `${fv.recent_fail_rate}%`     : '—';
      const drift = fv.fail_drift != null ? `Δ${fv.fail_drift > 0 ? '+' : ''}${fv.fail_drift}%` : '';
      wl(`  ${sLabel.padEnd(20)} ${state}`);
      wl(`    Hist=${hist}  Recent=${rec}  ${drift}  n_recent=${fv.n_recent ?? '—'}`);
    }
    wl();
  }

  // ── Temporal Decay Curve ──────────────────────────────────────────────
  // decay_curve[year_str] = { n_bars, raw_pct, decay_pct }
  const dc = r.decay_curve ?? {};
  const dcEntries = Object.entries(dc);
  if (dcEntries.length > 0) {
    h2('⏳ Temporal Decay Curve — وزن السنوات في الذاكرة (λ=0.7)');
    const maxDecay = Math.max(...dcEntries.map(([, v]) => v.decay_pct));
    for (const [year, v] of dcEntries) {
      const barLen = maxDecay > 0 ? Math.round((v.decay_pct / maxDecay) * 28) : 0;
      const bar = '█'.repeat(barLen) + '░'.repeat(28 - barLen);
      wl(`  ${year}  ${bar}  ${String(v.decay_pct.toFixed(1) + '%').padStart(6)}  (raw=${v.raw_pct}%  n=${v.n_bars})`);
    }
    wl();
  }

  // ── Market Physics ────────────────────────────────────────────────────
  // physics = { exhaustion_signature:{n_exhausting,p_tr_exhausting,n_normal,p_tr_normal,interpretation},
  //             volatility_release:{n,p_tr,interpretation},
  //             liquidity_absorption:{n,p_tr,interpretation} }
  const ph = r.physics ?? {};
  const phKeys = Object.keys(ph);
  if (phKeys.length > 0) {
    h2('⚡ Market Physics — ميكانيكا السوق');

    if (ph.exhaustion_signature) {
      const ex = ph.exhaustion_signature;
      wl('  🔻 Exhaustion Signature (تباطؤ الزخم + تسطح RSI):');
      wl(`     Exhausting: n=${ex.n_exhausting}  P(TR)=${ex.p_tr_exhausting}%`);
      wl(`     Normal:     n=${ex.n_normal}  P(TR)=${ex.p_tr_normal}%`);
      if (ex.interpretation) wl(`     → ${ex.interpretation}`);
      wl();
    }
    if (ph.volatility_release) {
      const vr = ph.volatility_release;
      wl('  📉 Volatility Release (ATR_z هابط بعد ذروة):');
      wl(`     n=${vr.n}  P(TR)=${vr.p_tr}%`);
      if (vr.interpretation) wl(`     → ${vr.interpretation}`);
      wl();
    }
    if (ph.liquidity_absorption) {
      const la = ph.liquidity_absorption;
      wl('  💧 Liquidity Absorption (حجم منخفض + تذبذب عالٍ):');
      wl(`     n=${la.n}  P(TR)=${la.p_tr}%`);
      if (la.interpretation) wl(`     → ${la.interpretation}`);
      wl();
    }
  }

  wl(`  ⏱️  ${Date.now() - t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// Market Evolution Engine  (Meta-Learning Cognitive Map)
// ─────────────────────────────────────────────────────────────────────────

async function runMarketEvolution() {
  const t0 = Date.now();
  h2('🌀 Market Evolution Engine — محرك تطور السوق');

  const r = await pythonMarketEvolution(5, 30, 180);
  if (!r?.success) {
    wl(err(`فشل: ${r?.error ?? 'unknown'}`));
    return;
  }

  if (JSON_MODE) { wl(JSON.stringify(r, null, 2)); return; }

  const hz   = r.horizons ?? {};
  const tc   = r.tier_counts ?? {};
  const cmap = r.cognitive_map ?? {};
  const ba   = r.behavioral_attractors ?? {};
  const se   = r.structural_evolution ?? {};

  // ── Header ───────────────────────────────────────────────────────────────
  const TIER_ICON = { DURABLE:'💎', ADAPTIVE:'🔄', CYCLICAL:'🌊', FRAGILE:'⚠️', COLLAPSING:'💥', UNKNOWN:'❓' };
  const tierBar = Object.entries(tc)
    .sort((a,b) => ['DURABLE','ADAPTIVE','CYCLICAL','FRAGILE','COLLAPSING'].indexOf(a[0]) -
                   ['DURABLE','ADAPTIVE','CYCLICAL','FRAGILE','COLLAPSING'].indexOf(b[0]))
    .map(([t,n]) => `${TIER_ICON[t]??'?'}${t}=${n}`).join(' ');

  const totalEdges   = Object.values(tc).reduce((s,n) => s+n, 0);
  const collapseN    = tc.COLLAPSING ?? 0;
  const fragileN     = tc.FRAGILE    ?? 0;
  const durableN     = tc.DURABLE    ?? 0;
  const stateIcon    = collapseN > 0 ? '🔴' : fragileN > 2 ? '🟡' : durableN >= totalEdges * 0.6 ? '🟢' : '🟡';
  const regimeNow    = cmap.current_regime ?? '—';
  const domTier      = cmap.dominant_tier  ?? '—';
  const invariantN   = (cmap.invariant_behaviors ?? []).length;
  const topStates    = (cmap.top_states_now ?? []).slice(0,3).map(s=>`${s.state}(${s.pct}%)`).join(' ');

  sep('═');
  wl(`  ${stateIcon}  بنية السوق: ${domTier} | ${tierBar}`);
  wl(`  📍 الريجيم الحالي: ${regimeNow} | الحالات: ${topStates}`);
  wl(`  🔒 سلوكيات ثابتة (invariant): ${invariantN} | إجمالي الـedges: ${totalEdges} | بيانات: ${hz.total_bars?.toLocaleString() ?? '?'} شمعة`);
  sep('═');

  // ── Section 1: Edge Meta-Table sorted by tier ──────────────────────────
  const TIER_ORDER = { COLLAPSING:0, FRAGILE:1, CYCLICAL:2, ADAPTIVE:3, DURABLE:4, UNKNOWN:5 };
  const edges = Object.entries(r.edge_meta ?? {})
    .sort((a,b) => (TIER_ORDER[a[1].tier]??9) - (TIER_ORDER[b[1].tier]??9) || (b[1].p_long??0) - (a[1].p_long??0));

  if (edges.length) {
    h2('🗺️  خريطة الـ Edges — التصنيف الخماسي');
    const hdr = ['Edge (حالة|ريجيم)', 'Tier', 'P(TR)L', 'P(TR)M', 'P(TR)S', 'Bayes', 'Decay_v', 'Consist', 'FC', 'FP'].map(s=>s.padEnd(16));
    wl('  ' + hdr.join(''));
    wl('  ' + '─'.repeat(160));
    for (const [key, e] of edges) {
      const t = e.tier ?? '—';
      const icon = TIER_ICON[t] ?? '?';
      const pL   = e.p_long    != null ? `${e.p_long}%`   : '—';
      const pM   = e.p_medium  != null ? `${e.p_medium}%` : '—';
      const pS   = e.p_short   != null ? `${e.p_short}%`  : '—';
      const bp   = e.bayes_p   != null ? `${e.bayes_p}%`  : '—';
      const dv   = e.decay_velocity != null
        ? `${e.decay_velocity > 0 ? '↑' : '↓'}${Math.abs(e.decay_velocity)}%` : '—';
      const cs   = e.horizon_consistency != null ? `${e.horizon_consistency}%` : '—';
      const fc   = e.failure_clustering  ? '🔴' : '🟢';
      const fp   = e.failure_persistence != null ? round2(e.failure_persistence,2) : '—';
      const row  = [
        key.replace('|','  ').padEnd(32),
        `${icon}${t}`.padEnd(16),
        pL.padEnd(10),pM.padEnd(10),pS.padEnd(10),bp.padEnd(10),
        dv.padEnd(10),cs.padEnd(10),fc.padEnd(6),String(fp).padEnd(8),
      ];
      wl('  ' + row.join(''));
    }
  }

  // ── Section 2: Invariant Behaviors ───────────────────────────────────────
  const invariants = cmap.invariant_behaviors ?? [];
  if (invariants.length) {
    h2('🔒 سلوكيات ثابتة عبر كل الريجيمات (Invariant Market Laws)');
    for (const inv of invariants) {
      wl(`  ✅  ${inv.state.padEnd(28)} P(TR)=${inv.mean_p_tr}%  std=${inv.std}%  (${inv.n_regimes} ريجيمات)`);
    }
  } else {
    h2('🔒 سلوكيات ثابتة');
    wl('  ⚪  لا توجد invariant behaviors بعد (يحتاج std<5% وmean>35% عبر ≥4 ريجيمات)');
  }

  // ── Section 3: Reversal Half-Life ─────────────────────────────────────
  h2('⏱️  نصف عمر الارتداد — Half-Life Profile (بالشمعة)');
  const hl = r.reversal_halflife ?? {};
  for (const [state, hld] of Object.entries(hl)) {
    const peak = hld.peak_p_tr ?? 0;
    const dur  = hld.peak_duration ?? '?';
    const hl_v = hld.half_life ?? '?';
    const opt  = hld.optimal_entry_dur ?? dur;
    // ASCII sparkline long
    const prof = (hld.profile_long ?? []).filter(p => p.p_tr != null);
    const spark = prof.map(p => {
      const filled = Math.round((p.p_tr/100)*10);
      return `${p.dur}d:${'█'.repeat(filled)}${'░'.repeat(10-filled)}${p.p_tr}%`;
    }).join('  ');
    wl(`  ${state.padEnd(28)} peak=${peak}%@${dur}شمعة  HL=${hl_v}  دخول_مثالي=${opt}شمعة`);
    if (spark) wl(`    ${spark}`);
  }

  // ── Section 4: Structural Evolution Timeline ──────────────────────────
  h2('📈 Timeline تطور الـ Edge (شهري)');
  const etl = r.evolution_timeline ?? {};
  for (const [key, points] of Object.entries(etl)) {
    if (!points.length) continue;
    const sparkVals = points.map(p => {
      const b = Math.round((p.p_tr/100)*8);
      return `${p.period.slice(0,7)}:${'▓'.repeat(b)}${'░'.repeat(8-b)}${p.p_tr}%`;
    }).join('  ');
    wl(`  ${key.padEnd(32)}  ${sparkVals}`);
  }

  // ── Section 5: Causal Physics ─────────────────────────────────────────
  h2('⚛️  الفيزياء السببية — ما يميّز الارتداد عن الفشل');
  const cp = r.causal_physics ?? {};
  wl(`  ${'الحالة'.padEnd(28)} ${'ATR_z(R)'.padEnd(10)} ${'ATR_z(F)'.padEnd(10)} ${'RSI_slp(R)'.padEnd(12)} ${'RSI_slp(F)'.padEnd(12)} ${'Vol(R)'.padEnd(8)} ${'Vol(F)'.padEnd(8)} ${'P_hi_press'.padEnd(12)} ${'Discriminants'}`);
  wl('  ' + '─'.repeat(170));
  for (const [state, phys] of Object.entries(cp)) {
    const azR  = phys.atr_z_at_reversal   != null ? round2(phys.atr_z_at_reversal,   3) : '—';
    const azF  = phys.atr_z_at_failure     != null ? round2(phys.atr_z_at_failure,     3) : '—';
    const rsR  = phys.rsi_slope_at_rev     != null ? round2(phys.rsi_slope_at_rev,     2) : '—';
    const rsF  = phys.rsi_slope_at_fail    != null ? round2(phys.rsi_slope_at_fail,    2) : '—';
    const vrR  = phys.vol_r_at_rev         != null ? round2(phys.vol_r_at_rev,         2) : '—';
    const vrF  = phys.vol_r_at_fail        != null ? round2(phys.vol_r_at_fail,         2) : '—';
    const php  = phys.p_tr_high_pressure   != null ? `${phys.p_tr_high_pressure}%`  : '—';
    const disc = (phys.key_discriminants ?? []).join(' | ');
    wl(`  ${state.padEnd(28)} ${String(azR).padEnd(10)} ${String(azF).padEnd(10)} ${String(rsR).padEnd(12)} ${String(rsF).padEnd(12)} ${String(vrR).padEnd(8)} ${String(vrF).padEnd(8)} ${php.padEnd(12)} ${disc}`);
  }

  // ── Section 6: Failure Topology ───────────────────────────────────────
  h2('💀 طبولوجيا الفشل — 5 أنواع');
  const ft = r.failure_topology ?? {};
  wl(`  ${'الحالة'.padEnd(28)} ${'fail%'.padEnd(8)} ${'DeadCat'.padEnd(10)} ${'ContTrap'.padEnd(10)} ${'Drift'.padEnd(8)} ${'VolComp'.padEnd(10)} ${'RgmTrap'.padEnd(10)} ${'Dominant'.padEnd(16)} ${'Avg_ret'.padEnd(10)} ${'W10%'.padEnd(8)} Recent`);
  wl('  ' + '─'.repeat(180));
  for (const [state, ftp] of Object.entries(ft)) {
    const dom = ftp.dominant_failure ?? '—';
    const domI= dom === 'CONTINUATION_TRAP' ? '🔴' : dom === 'VOL_COMPRESSION' ? '🟡' : dom === 'DEAD_CAT' ? '⚪' : '🟠';
    const rec  = ftp.recent_fail_rate != null && ftp.hist_fail_rate != null
      ? (ftp.recent_fail_rate > ftp.hist_fail_rate * 1.1 ? `↑${ftp.recent_fail_rate}%` : `→${ftp.recent_fail_rate}%`)
      : '—';
    wl(`  ${state.padEnd(28)} ${String(ftp.overall_fail_rate??'—').padEnd(8)} ${String(ftp.dead_cat_pct??'—').padEnd(10)} ${String(ftp.continuation_trap_pct??'—').padEnd(10)} ${String(ftp.drift_failure_pct??'—').padEnd(8)} ${String(ftp.vol_compression_pct??'—').padEnd(10)} ${String(ftp.regime_trap_pct??'—').padEnd(10)} ${(domI+dom).padEnd(22)} ${String(ftp.avg_fail_ret??'—').padEnd(10)} ${String(ftp.worst_10pct_ret??'—').padEnd(8)} ${rec}`);
  }

  // ── Section 7: Behavioral Attractors ─────────────────────────────────
  h2('🧲 المستقطبات السلوكية — Behavioral Attractors');
  const pers  = ba.persistence       ?? {};
  const persS = ba.persistence_short ?? {};
  const attr  = ba.attractor_score   ?? {};
  const escV  = ba.escape_velocity   ?? {};
  const revPers = ba.reversal_state_persistence ?? {};

  wl(`  ${'الحالة'.padEnd(28)} ${'Persist%L'.padEnd(12)} ${'Persist%S'.padEnd(12)} ${'Attract%'.padEnd(12)} ${'Escape%'.padEnd(10)} باركود`);
  wl('  ' + '─'.repeat(120));
  const allStates = [...new Set([...Object.keys(pers), ...Object.keys(persS)])].sort();
  for (const st of allStates) {
    const pL  = pers[st]  ?? null;
    const pS  = persS[st] ?? null;
    const atS = attr[st]  ?? null;
    const esc = escV[st]  ?? null;
    const bar = pL != null ? '█'.repeat(Math.round(pL/10)) + '░'.repeat(10-Math.round(pL/10)) : '──────────';
    const dlt = pL != null && pS != null ? (pS > pL+5 ? ' ↑' : pS < pL-5 ? ' ↓' : '  ') : '  ';
    wl(`  ${st.padEnd(28)} ${String(pL??'—').padEnd(12)} ${String(pS??'—').padEnd(12)} ${String(atS??'—').padEnd(12)} ${String(esc??'—').padEnd(10)} ${bar}${dlt}`);
  }

  const topA = ba.top_attractors ?? [];
  if (topA.length) {
    wl('');
    wl(`  🏆 أعلى المستقطبات: ${topA.slice(0,4).map(a=>`${a.state}(${a.score}%)`).join(' → ')}`);
  }
  const tStick  = ba.trend_stickiness        ?? null;
  const tStickS = ba.trend_stickiness_short  ?? null;
  if (tStick != null) {
    const delta = tStickS != null ? (tStickS > tStick ? `+${round2(tStickS-tStick,1)}% ↑ قوة متزايدة` : `${round2(tStickS-tStick,1)}% ↓ تراجع`) : '';
    wl(`  📌 التشابك الاتجاهي (TRENDING_UP→TRENDING_UP): ${tStick}% (long)  ${tStickS??'—'}% (short)  ${delta}`);
  }
  // Reversal state persistence
  if (Object.keys(revPers).length) {
    wl('');
    wl('  ⏳ ثبات حالات الارتداد (persistence):');
    for (const [st, p] of Object.entries(revPers)) {
      const bar = '█'.repeat(Math.round(p/10)) + '░'.repeat(10-Math.round(p/10));
      wl(`     ${st.padEnd(26)} ${bar} ${p}%`);
    }
  }

  // ── Section 8: Instability Zones ──────────────────────────────────────
  const iz = (r.instability_zones ?? []).filter(z => z.score >= 2);
  if (iz.length) {
    h2('⚠️  مناطق عدم الاستقرار — Instability Zones');
    for (const z of iz.slice(0, 8)) {
      const severity = z.score >= 4 ? '🔴' : z.score >= 3 ? '🟡' : '🟠';
      wl(`  ${severity}  ${z.key.padEnd(36)} tier=${z.tier.padEnd(10)} score=${z.score}  سبب: ${(z.reasons??[]).join(', ')}`);
    }
  }

  // ── Section 9: Structural Evolution (Vol ACF + Trend Quarterly) ───────
  h2('📊 التطور الهيكلي — Vol Clustering + Trend Stickiness بالربع');
  const volAcf = se.vol_clustering_acf ?? {};
  if (volAcf.long != null) {
    const lvl = volAcf.short != null && volAcf.long != null
      ? (volAcf.short < volAcf.long - 0.1 ? '🟡 تراجع التجمع القصير' : '🟢 تجمع مستقر')
      : '';
    wl(`  📈 تجمع التقلب (ACF لاج-1): long=${volAcf.long}  medium=${volAcf.medium??'—'}  short=${volAcf.short??'—'}  ${lvl}`);
  }
  const tsq = se.trend_stickiness_quarterly ?? {};
  const tsqSorted = Object.entries(tsq)
    .filter(([,v]) => v != null)
    .sort(([a],[b]) => a.localeCompare(b));
  if (tsqSorted.length) {
    wl('');
    wl('  📅 ثبات الاتجاه فصلياً:');
    for (const [q, v] of tsqSorted) {
      const bar = '█'.repeat(Math.round(v/10)) + '░'.repeat(10-Math.round(v/10));
      const flag = v > 60 ? ' ← قوي' : v < 30 ? ' ← ضعيف' : '';
      wl(`     ${q.padEnd(8)}  ${bar}  ${v}%${flag}`);
    }
  }

  wl('');
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// Macro Regime Dashboard
// ─────────────────────────────────────────────────────────────────────────

async function runMacroRegime() {
  const t0 = Date.now();
  h2('🏛️  Macro Regime Dashboard — الاقتصاد الكلي المصري');

  const r = await pythonMacroRegime(168);
  if (!r?.success) {
    wl(err(`فشل: ${r?.error ?? 'unknown'}`));
    wl('  💡 شغّل: node scripts/fetch_economics.mjs --notify');
    return;
  }

  if (JSON_MODE) { wl(JSON.stringify(r, null, 2)); return; }

  const REGIME_ICON = {
    DISINFLATION_EASING: '🟢', DISINFLATION_HOLD: '🟩',
    STABLE_GROWTH: '🟢',       REFLATION: '🟡',
    TIGHT_GROWING: '🟠',       HIGH_INFLATION_RISING: '🔴',
    STAGFLATION_TIGHT: '🔴',   MONETARY_SHOCK: '💥',
    EGP_CRISIS: '🆘',          NEUTRAL: '⚪', UNKNOWN: '❓',
  };

  const regime  = r.macro_regime ?? 'UNKNOWN';
  const ri      = REGIME_ICON[regime] ?? '⚪';
  const score   = r.regime_score ?? 0;
  const mult    = r.equity_multiplier ?? 1.0;
  const scoreBar = '█'.repeat(Math.round(score / 10)) + '░'.repeat(10 - Math.round(score / 10));
  const multStr  = mult >= 1.05 ? `+${((mult-1)*100).toFixed(1)}% دعم` : mult <= 0.95 ? `${((mult-1)*100).toFixed(1)}% ضغط` : 'محايد';

  sep('═');
  wl(`  ${ri}  Macro Regime: ${regime}`);
  wl(`  📊  Score: ${scoreBar} ${score}/100  |  Equity Mult: ${mult}× (${multStr})`);
  wl(`  ⚖️   Bias: ${r.strategic_bias ?? '—'}`);
  sep('═');

  const c = r.core ?? {};
  const tr = r.trends ?? {};
  const x  = r.external ?? {};
  const f  = r.fiscal ?? {};

  // ── Core Indicators ────────────────────────────────────────────────────
  h2('💰 المؤشرات الأساسية');
  const f2 = (v, d=2) => v != null ? (+v).toFixed(d) : '—';
  const fmt_rate = (v) => v != null ? `${f2(v)}%` : '—';

  wl(`  💵  USD/EGP:               ${f2(c.usd_egp, 4)}  (${tr.fx_trend ?? '—'})`);
  const inflMomI = tr.inflation_momentum === 'falling' ? '↘️' : tr.inflation_momentum === 'rising' ? '↗️' : '→';
  wl(`  📈  تضخم YoY:              ${fmt_rate(c.inflation_yoy)}  ${inflMomI} ${tr.inflation_momentum ?? '—'}`);
  wl(`  🎯  تضخم أساسي:            ${fmt_rate(c.core_inflation)}`);
  const rateMomI = tr.rate_cycle === 'falling' ? '↘️' : tr.rate_cycle === 'rising' ? '↗️' : '→';
  wl(`  🏦  فائدة CBE:             ${fmt_rate(c.cbe_rate)}  ${rateMomI} ${tr.rate_cycle ?? '—'}`);
  if (c.real_interest_rate != null) {
    const rr   = c.real_interest_rate;
    const rrI  = rr < 0 ? '🟢' : rr < 4 ? '🟡' : '🔴';
    const rrNt = rr < 0 ? 'أسهم تتفوق على ودائع' : rr > 5 ? 'ودائع جذابة — تنافس قوي' : 'تنافس معتدل';
    wl(`  ${rrI}  فائدة حقيقية:         ${f2(rr)}%  (${rrNt})`);
  }
  wl(`  📊  GDP YoY:               ${fmt_rate(c.gdp_yoy)}  (${tr.growth_trend ?? '—'})`);
  wl(`  👥  بطالة:                 ${fmt_rate(c.unemployment)}`);

  // ── External & Reserves ───────────────────────────────────────────────
  h2('🌍 الخارجي والاحتياطيات');
  wl(`  🏦  احتياطيات أجنبية:      $${f2(x.fx_reserves_b, 1)}B`);
  wl(`  📦  ميزان تجاري:           $${f2(x.trade_balance_m, 2)}B/شهر  (صادرات $${f2(x.exports_m,2)}B  /  واردات $${f2(x.imports_m,2)}B)`);
  wl(`  💸  تحويلات:               $${f2(x.remittances_q, 2)}B`);
  wl(`  🔄  حساب جاري:             $${f2(x.current_account_b, 2)}B`);
  wl(`  💳  دين خارجي:             $${f2(x.external_debt_b, 1)}B`);
  wl(`  🏗️   FDI:                  $${f2(x.fdi_q_b, 2)}B/ربع`);

  // ── Fiscal ────────────────────────────────────────────────────────────
  h2('🏛️  المالية العامة');
  wl(`  📊  دين حكومي/GDP:         ${fmt_rate(f.govt_debt_gdp)}`);
  if (f.budget_balance_egp_t != null) wl(`  💰  رصيد الموازنة:          EGP ${f2(f.budget_balance_egp_t, 2)}T`);
  if (f.govt_revenue_egp_t   != null) wl(`  📥  إيرادات:                EGP ${f2(f.govt_revenue_egp_t, 2)}T`);
  if (f.fiscal_exp_egp_t     != null) wl(`  📤  إنفاق:                  EGP ${f2(f.fiscal_exp_egp_t, 2)}T`);

  // ── History sparklines ─────────────────────────────────────────────────
  const hist = r.history ?? {};
  if (hist.inflation_yoy?.length) {
    h2('📈 تاريخ التضخم (24 شهر)');
    for (const b of hist.inflation_yoy.slice(-18)) {
      const bar = '█'.repeat(Math.round(b.value / 3));
      wl(`     ${b.date}: ${String(b.value?.toFixed(1)).padStart(5)}%  ${bar}`);
    }
  }
  if (hist.cbe_rate?.length) {
    h2('🏦 تاريخ فائدة CBE (24 شهر)');
    for (const b of hist.cbe_rate.slice(-18)) {
      const bar = '█'.repeat(Math.round(b.value / 3));
      wl(`     ${b.date}: ${String(b.value?.toFixed(2)).padStart(6)}%  ${bar}`);
    }
  }
  if (hist.fx_reserves_b?.length) {
    h2('💰 تاريخ الاحتياطيات ($B)');
    const vals = hist.fx_reserves_b.map(b => b.value);
    const mx   = Math.max(...vals.filter(v => v != null));
    for (const b of hist.fx_reserves_b.slice(-12)) {
      if (b.value == null) continue;
      const bar = '█'.repeat(Math.round(b.value / mx * 10));
      wl(`     ${b.date}: $${String(b.value?.toFixed(1)).padStart(5)}B  ${bar}`);
    }
  }

  // ── Interpretation ────────────────────────────────────────────────────
  if (r.interpretation?.length) {
    h2('🔍 التفسير الاستراتيجي');
    for (const line of r.interpretation) wl(`  ${line}`);
  }

  wl('');
  wl(`  ⏱  ${Date.now()-t0}ms  |  مصدر: ${r._source ?? '—'}  |  تحديث: ${r._fetched_at?.slice(0,16) ?? '—'}`);
}

// ═══════════════════════════════════════════════════════════════════════════
// LATENT ENGINE DISPLAY FUNCTIONS
// ═══════════════════════════════════════════════════════════════════════════

async function runBehavioralForces() {
  const t0 = Date.now();
  h2('🧬 Behavioral Forces — قوى السوق السلوكية الكامنة');

  const r = await pythonBehavioralForces();
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  const mf = r.market_forces || {};
  const ep = mf.exhaustion_pressure || {};
  const ve = mf.volatility_energy   || {};
  const de = mf.directional_energy  || {};
  const pf = mf.participation_flow  || {};
  const tc = mf.trend_coherence     || {};
  const rp = mf.reversal_potential  || {};

  // Market-level force dashboard
  sep();
  wl(`  🎯  Dominant Archetype: ${r.dominant_archetype}`);
  wl(`  📊  Stocks: ${r.n_stocks} | ⬆ Reversal Candidates: ${r.reversal_candidates} | ⬇ Exhaustion: ${r.exhaustion_candidates}`);
  sep();

  h2('⚡ القوى السلوكية للسوق كله');
  const bar6 = (v, n=10) => {
    const positive = v >= 0;
    const filled = Math.round(Math.abs(v) * n);
    const b = '█'.repeat(filled) + '░'.repeat(n - filled);
    return positive ? `+${b}` : `-${b}`;
  };
  const fmtForce = (name, f, desc) =>
    `  ${name.padEnd(22)} ${bar6(f.mean||0)} ${(f.mean||0).toFixed(3).padStart(6)}  (high:${f.pct_high||0}% | low:${f.pct_low||0}%)  ${desc}`;

  wl(fmtForce('exhaustion_pressure', ep, 'RSI position intensity'));
  wl(fmtForce('volatility_energy',   ve, 'ATR / volume expansion'));
  wl(fmtForce('directional_energy',  de, 'momentum direction'));
  wl(fmtForce('participation_flow',  pf, 'liquidity absorption'));
  wl(fmtForce('trend_coherence',     tc, 'ADX alignment'));
  wl(fmtForce('reversal_potential',  rp, 'composite oversold score'));

  // Archetype distribution
  h2('🗺️  توزيع الـ Archetypes');
  const dist = r.archetype_distribution || {};
  const totalArch = Object.values(dist).reduce((a,b)=>a+b, 0) || 1;
  for (const [arch, cnt] of Object.entries(dist).sort((a,b)=>b[1]-a[1])) {
    const pct = (cnt/totalArch*100).toFixed(0);
    const bar = '█'.repeat(Math.round(cnt/totalArch*20));
    wl(`  ${arch.padEnd(28)} ${bar.padEnd(22)} ${cnt} (${pct}%)`);
  }

  // Sector forces heatmap
  const secF = r.sector_forces || {};
  if (Object.keys(secF).length > 0) {
    h2('🏭 قوى القطاعات');
    wl(`  ${'القطاع'.padEnd(26)} إرهاق    طاقة   اتجاه   ارتداد`);
    wl(`  ${'─'.repeat(65)}`);
    for (const [sec, forces] of Object.entries(secF)
        .sort((a,b) => (b[1].reversal_potential||0)-(a[1].reversal_potential||0))
        .slice(0, 10)) {
      const ep2 = (forces.exhaustion_pressure||0).toFixed(2).padStart(7);
      const ve2 = (forces.volatility_energy||0).toFixed(2).padStart(7);
      const de2 = ((forces.directional_energy||0)>=0?'+':'')+((forces.directional_energy||0).toFixed(2)).padStart(6);
      const rp2 = (forces.reversal_potential||0).toFixed(2).padStart(7);
      wl(`  ${sec.slice(0,25).padEnd(26)} ${ep2}  ${ve2}  ${de2}  ${rp2}`);
    }
  }

  // Top reversal candidates
  const archStocks = r.archetype_stocks || {};
  for (const arch of ['DEEPLY_OVERSOLD', 'HIGH_REVERSAL_POTENTIAL']) {
    const stocks = archStocks[arch] || [];
    if (stocks.length) {
      h2(`🔥 ${arch} (${stocks.length} أسهم)`);
      for (const s of stocks.slice(0, 8)) {
        wl(`  ${s.symbol.padEnd(8)} RSI=${s.rsi}  rev_pot=${s.rev_pot}  dir=${s.dir_energy}`);
      }
    }
  }

  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runDurationAnalysis() {
  const t0 = Date.now();
  h2('⏳ Duration Analysis — P(TR) بدلالة الزمن في الحالة');

  const r = await pythonDurationAnalysis(3);
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  wl(`  افق: ${r.horizon_bars} بار | حد الارتداد: ${r.tr_threshold_pct}%`);
  wl('');

  // Key insights
  const insights = r.key_insight || [];
  if (insights.length) {
    h2('💡 الاكتشافات الرئيسية');
    for (const ins of insights) wl(`  ✅ ${ins}`);
    wl('');
  }

  // Duration table per state
  h2('📊 جدول P(TR) × مدة الحالة');
  const COHORTS = ['1','2','3','4-5','6-10','11+'];
  wl(`  ${'الحالة'.padEnd(28)} ${'@1بار'.padStart(6)} ${'@2'.padStart(6)} ${'@3'.padStart(6)} ${'@4-5'.padStart(6)} ${'@6-10'.padStart(7)} ${'@11+'.padStart(6)}  peak   trend`);
  wl(`  ${'─'.repeat(95)}`);

  const analysis = r.analysis || {};
  const ranked   = r.ranked_states || [];

  for (const {state} of ranked.slice(0, 12)) {
    const an = analysis[state];
    if (!an) continue;
    const cohorts = an.cohorts || {};
    const vals = COHORTS.map(c => {
      const p = cohorts[c]?.p_tr;
      if (p == null) return '  —   ';
      const flag = p >= 50 ? '🟢' : p >= 40 ? '🟡' : '🔴';
      return `${flag}${p.toFixed(0).padStart(3)}%`;
    });
    const bestC = an.best_cohort || '—';
    const peak  = an.peak_p_tr || 0;
    const trend = an.trend === 'RISING' ? '↗RISING' : an.trend === 'FALLING' ? '↘FALLING' : '→STABLE';
    wl(`  ${state.padEnd(28)} ${vals.join(' ')}  @${bestC}=${peak}%  ${trend}`);
  }

  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runSectorMarkov() {
  const t0 = Date.now();
  h2('🏭 Sector Markov — مصفوفات الانتقال حسب القطاع');

  const r = await pythonSectorMarkov();
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  wl(`  ${r.n_sectors} قطاعات | ${r.n_invariants} invariant edges عابرة للقطاعات`);

  // Cross-sector invariants
  const invs = r.cross_sector_invariants || [];
  if (invs.length) {
    h2('🔒 Behavioral Invariants — عابرة لجميع القطاعات');
    wl(`  ${'من'.padEnd(28)} ${'إلى'.padEnd(28)} Overall  Mean%  Std%  CV    قطاعات`);
    wl(`  ${'─'.repeat(100)}`);
    for (const inv of invs.slice(0, 10)) {
      const cv = (inv.cv * 100).toFixed(0);
      const stab = inv.cv < 0.15 ? '🟢' : inv.cv < 0.25 ? '🟡' : '🔴';
      wl(`  ${stab} ${inv.from.padEnd(26)} ${inv.to.padEnd(26)} ${String(inv.overall_pct).padStart(6)}%  ${String(inv.mean_sector_pct).padStart(5)}%  ${String(inv.std_sector_pct).padStart(4)}%  ${cv}%  ${inv.n_sectors}`);
    }
  }

  // Sector-specific edges
  const specific = r.sector_specific_edges || [];
  if (specific.length) {
    h2('⚡ Sector-Specific Edges — تختلف عن المتوسط ≥15%');
    wl(`  ${'القطاع'.padEnd(22)} ${'من'.padEnd(24)} ${'إلى'.padEnd(24)} قطاع  سوق   Δ`);
    wl(`  ${'─'.repeat(95)}`);
    for (const e of specific.slice(0, 12)) {
      const flag = e.diff > 0 ? '🟢+' : '🔴';
      wl(`  ${e.sector.slice(0,21).padEnd(22)} ${e.from.padEnd(24)} ${e.to.padEnd(24)} ${e.sector_pct}%  ${e.overall_pct}%  ${flag}${Math.abs(e.diff)}%`);
    }
  }

  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runLatentCompress() {
  const t0 = Date.now();
  h2('🔭 Latent Compress — فضاء السلوك الكامن (PCA)');

  const r = await pythonLatentCompress();
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  const expVar = r.explained_variance_pct || [];
  sep();
  wl(`  🗜️  ${r.n_stocks} سهم × ${r.n_features} مؤشر → 3 أبعاد كامنة`);
  wl(`  📊  تفسير التباين: PC1=${expVar[0]}%  PC2=${expVar[1]}%  PC3=${expVar[2]}%  مجموع=${r.total_explained_3pc}%`);
  sep();

  h2('🧭 الأبعاد الكامنة');
  const loadings = r.top_loadings || [];
  for (let i = 0; i < loadings.length; i++) {
    const {dim, loadings: loads} = loadings[i];
    wl(`\n  PC${i+1}: ${dim} (${expVar[i]}%)`);
    for (const [feat, v] of loads) {
      const bar = '█'.repeat(Math.round(Math.abs(v) * 15));
      const sign = v >= 0 ? '+' : '-';
      wl(`    ${sign}${bar.padEnd(16)} ${feat} (${v >= 0 ? '+' : ''}${v})`);
    }
  }

  h2('🗃️  Clusters السلوكية');
  const clusters = r.clusters || {};
  for (const [cid, cl] of Object.entries(clusters).sort()) {
    const archIcon = cl.archetype.includes('OVERBOUGHT') ? '🔴' :
                     cl.archetype.includes('OVERSOLD')   ? '🟢' :
                     cl.archetype.includes('HIGH_PRESS')  ? '🟡' : '⚪';
    wl(`  ${archIcon} ${cid}: ${cl.archetype.padEnd(28)} RSI=${cl.avg_rsi}  mom5d=${cl.avg_mom5d}%  n=${cl.n_stocks}`);
    wl(`     أسهم: ${cl.top_symbols.join(', ')}`);
  }

  h2('🔗 Feature Correlations (vs PC1)');
  for (const [feat, corr] of r.feature_correlations_d1 || []) {
    const bar = '█'.repeat(Math.round(Math.abs(corr) * 15));
    const sign = corr >= 0 ? '+' : '-';
    wl(`  ${sign}${bar.padEnd(17)} ${feat} (r=${corr})`);
  }

  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runInvariantDiscovery() {
  const t0 = Date.now();
  h2('🔒 Invariant Discovery — قوانين فيزياء السوق الدائمة');

  const r = await pythonInvariantDiscovery(3);
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  wl(`  اختُبرت ${r.n_tested} حالات | ${r.n_invariants} invariants (ثابتة عبر الزمن)`);
  wl(`  المنهجية: ${r.methodology}`);

  const invs = r.invariants || [];
  if (invs.length) {
    h2('✅ BEHAVIORAL INVARIANTS — السلوكيات الثابتة');
    wl(`  ${'الحالة'.padEnd(28)} Mean%  Std%  Min%  Max%  سنوات  اتجاه`);
    wl(`  ${'─'.repeat(80)}`);
    for (const inv of invs) {
      const statusIcon = inv.status === 'DEGRADING' ? '⚠️' : inv.status === 'IMPROVING' ? '📈' : '🟢';
      wl(`  ${statusIcon} ${inv.state.padEnd(26)} ${String(inv.mean_p_tr).padStart(5)}%  ${String(inv.std_p_tr).padStart(4)}%  ${String(inv.min_p_tr).padStart(4)}%  ${String(inv.max_p_tr).padStart(4)}%  ${inv.n_years}     ${inv.status}`);
      // Show yearly breakdown
      const yrs = Object.entries(inv.yearly || {}).sort();
      wl(`    ${yrs.map(([y,p]) => `${y}:${p}%`).join('  ')}`);
    }
  } else {
    wl(`  ⚪ لم يُعثر على invariants قوية — السوق في حالة تحوّل`);
  }

  const unstable = r.unstable_edges || [];
  if (unstable.length) {
    h2('⚠️  Unstable Edges — غير موثوقة عبر الزمن');
    for (const ue of unstable.slice(0, 6)) {
      wl(`  🔴 ${ue.state.padEnd(28)} std=${ue.std_p_tr}% range=[${ue.min_p_tr}%–${ue.max_p_tr}%]`);
    }
  }

  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runTemporalStability() {
  const t0 = Date.now();
  h2('📅 Temporal Stability — ثبات الـ Edges عبر الزمن');

  const r = await pythonTemporalStability(180);
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  wl(`  ${r.n_states_tested} حالات × ${r.n_windows} نافذة (${r.window_size_days} يوم)`);

  const res = r.stability_results || {};

  // Group by stability
  const groups = {INVARIANT:[], STABLE:[], MODERATE:[], DEGRADING:[], IMPROVING:[], UNSTABLE:[]};
  for (const [state, sr] of Object.entries(res)) {
    (groups[sr.stability] || groups.UNSTABLE).push([state, sr]);
  }

  const icons = {INVARIANT:'✅ INVARIANT',STABLE:'🟢 STABLE',MODERATE:'🟡 MODERATE',
                 DEGRADING:'🔴 DEGRADING',IMPROVING:'📈 IMPROVING',UNSTABLE:'⚠️ UNSTABLE'};

  for (const [stability, entries] of Object.entries(groups)) {
    if (!entries.length) continue;
    h2(icons[stability] + ` (${entries.length})`);
    for (const [state, sr] of entries) {
      const trend = sr.slope_per_window > 1 ? '📈' : sr.slope_per_window < -1 ? '📉' : '→';
      wl(`  ${state.padEnd(28)} mean=${sr.mean_p_tr}%  std=${sr.std_p_tr}%  CV=${sr.cv_pct}%  slope=${sr.slope_per_window >= 0 ? '+' : ''}${sr.slope_per_window} ${trend}`);
      if (sr.warning) wl(`    ↳ ${sr.warning}`);
    }
  }

  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// FORCE FIELD ENGINE — Phase 2
// ─────────────────────────────────────────────────────────────────────────

async function runForceFieldNow() {
  const t0 = Date.now();
  h2('⚡ Force Field NOW — 9-Force Market Snapshot');
  wl('  محرك مجال القوة — لقطة فورية من 9 قوى خفية لكل سهم (~2s)');
  wl();

  const r = await pythonForceFieldNow();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  // Header stats
  const nStocks = r.n_stocks ?? '—';
  wl(`  📊 إجمالي الأسهم: ${nStocks}   |   حالة السوق: ${r.field_state ?? '—'}`);
  wl();

  // Active couplings
  const couplings = r.active_couplings || [];
  if (couplings.length) {
    wl(`  ⚡ اقترانات نشطة: ${couplings.join(' | ')}`);
    wl();
  }

  // Instability zone
  if (r.instability_zone) {
    wl(`  ⚠️  منطقة عدم الاستقرار: ${r.instability_zone}`);
    wl();
  }

  // Market forces averages
  const fa = r.market_forces || {};
  if (Object.keys(fa).length) {
    wl('  ─ متوسط قوى السوق ─');
    wl('  ' + 'القوة'.padEnd(32) + 'متوسط   % عالي   % منخفض  شريط');
    wl('  ' + '─'.repeat(75));
    const forces = [
      ['BUYING_PRESSURE',       '🟢 ضغط الشراء          '],
      ['SELLING_PRESSURE',      '🔴 ضغط البيع           '],
      ['EXHAUSTION_FORCE',      '😮 قوة الإرهاق         '],
      ['PANIC_FORCE',           '😱 قوة الذعر           '],
      ['VOLATILITY_EXPANSION',  '🌪️  توسع التقلب         '],
      ['LIQUIDITY_ABSORPTION',  '💧 امتصاص السيولة      '],
      ['MOMENTUM_INERTIA',      '🚀 قصور الزخم          '],
      ['INSTABILITY_INDEX',     '⚠️  مؤشر عدم الاستقرار  '],
      ['MEAN_REVERSION_TENSION','🔄 توتر الارتداد        '],
    ];
    for (const [key, label] of forces) {
      const fd = fa[key];
      if (!fd) continue;
      const v    = fd.mean ?? 0;
      const hi   = fd.pct_high ?? 0;
      const lo   = fd.pct_low  ?? 0;
      const bars = Math.round(Math.abs(v) * 15);
      const bar  = (v < 0 ? '◀' : '') + '█'.repeat(bars) + (v >= 0 ? '▶' : '');
      wl(`  ${label} ${fmt(v,3).padEnd(8)} ${fmt(hi,1).padEnd(8)} ${fmt(lo,1).padEnd(9)} ${bar}`);
    }
    wl();
  }

  // Archetype distribution
  const ad = r.archetype_distribution || {};
  if (Object.keys(ad).length) {
    wl('  ─ توزيع النماذج الأولية ─');
    const total = typeof nStocks === 'number' ? nStocks : 0;
    const sorted = Object.entries(ad).sort((a, b) => b[1] - a[1]);
    for (const [arch, count] of sorted) {
      const pct = total ? ((count / total) * 100).toFixed(1) : '—';
      wl(`  ${arch.padEnd(32)} ${String(count).padStart(4)} سهم  (${pct}%)`);
    }
    wl();
  }

  // Reversal candidates
  const candidates = r.reversal_candidates || [];
  if (candidates.length) {
    wl('  ─ مرشحات الانعكاس ─');
    for (const s of candidates.slice(0, 8)) {
      const mrt = s.forces?.MEAN_REVERSION_TENSION;
      wl(`  • ${(s.symbol ?? '—').padEnd(12)} ${s.archetype?.padEnd(26) ?? ''} RSI=${fmt(s.rsi,1).padEnd(7)} MRT=${fmt(mrt,3)}  ${s.sector || ''}`);
    }
    wl();
  }

  // Top stocks by archetype from stock_forces
  const stockForces = r.stock_forces || [];
  if (stockForces.length) {
    // Group by archetype
    const byArch = {};
    for (const s of stockForces) {
      if (!byArch[s.archetype]) byArch[s.archetype] = [];
      byArch[s.archetype].push(s);
    }
    // Show top 4 archetypes by count
    const topArchs = Object.entries(byArch)
      .sort((a,b) => b[1].length - a[1].length)
      .slice(0, 4);
    wl('  ─ أبرز الأسهم لكل نموذج ─');
    for (const [arch, stocks] of topArchs) {
      wl(`  ${arch}:`);
      for (const s of stocks.slice(0, 5)) {
        const dom = (s.dominant || []).slice(0,2).join('+');
        wl(`    • ${(s.symbol ?? '—').padEnd(10)} RSI=${fmt(s.rsi,1).padEnd(7)} [${dom}]  ${s.sector || ''}`);
      }
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runForceInteractions() {
  const t0 = Date.now();
  h2('🔗 Force Interactions — Coupling Matrix');
  wl('  تحليل التفاعلات بين القوى — هل يُعزز الإرهاق + الذعر بعضهما؟ (~15s)');
  wl();

  const r = await pythonForceInteractions();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  // Baseline
  wl(`  السعر الأساسي P(TR): ${fmt(r.baseline_p_tr, 1)}%   |   n_bars: ${r.n_bars ?? '—'}   |   أفق: ${r.horizon ?? '—'} شمعة`);
  wl();

  const pairs = r.coupling_matrix || [];
  if (pairs.length) {
    wl('  ─ مصفوفة الاقتران (P(TR|f1+f2) vs individual) ─');
    wl('  ' + 'الزوج'.padEnd(50) + ' P(كلاهما)  P(f1)    P(f2)    تأثير    النوع');
    wl('  ' + '─'.repeat(100));
    for (const p of pairs) {
      const label = `${p.force_1} × ${p.force_2}`;
      const eff   = p.interaction_effect != null
        ? (p.interaction_effect > 0 ? '+' : '') + fmt(p.interaction_effect, 1) + '%'
        : '—';
      wl(`  ${label.padEnd(50)} ${fmt(p.p_both_high,1).padEnd(11)} ${fmt(p.p_f1_only,1).padEnd(9)} ${fmt(p.p_f2_only,1).padEnd(9)} ${eff.padEnd(9)} ${p.coupling_type ?? '—'}`);
    }
    wl();
  }

  // Strongest coupling
  if (r.strongest_coupling) {
    const sc = r.strongest_coupling;
    if (typeof sc === 'string') {
      wl(`  ⭐ أقوى اقتران: ${sc}`);
    } else {
      const scLabel = `${sc.force_1 ?? '—'} × ${sc.force_2 ?? '—'}`;
      wl(`  ⭐ أقوى اقتران: ${scLabel}  P(TR)=${fmt(sc.p_both_high,1)}%  (${(sc.interaction_effect>0?'+':'')}${fmt(sc.interaction_effect,1)}%)`);
    }
  }

  // Super-additive pairs
  const superAdd = r.super_additive_pairs || [];
  if (superAdd.length) {
    wl('  🔥 أزواج سوبر-مضافة (تضخيم قوي):');
    for (const p of superAdd) wl(`  • ${p.force_1} + ${p.force_2} → P(TR)=${fmt(p.p_both_high,1)}% (+${fmt(p.interaction_effect,1)}%)`);
    wl();
  }

  // Cancellation pairs
  const cancel = r.cancellation_pairs || [];
  if (cancel.length) {
    wl('  ❌ أزواج تُعادل بعضها (تجنَّب):');
    for (const p of cancel) wl(`  • ${p.force_1} + ${p.force_2} → P(TR)=${fmt(p.p_both_high,1)}% (${fmt(p.interaction_effect,1)}%)`);
    wl();
  }

  // Insights
  const insights = pairs.filter(p => p.insight).map(p => p.insight);
  if (insights.length) {
    wl('  ─ الرؤى ─');
    for (const ins of insights) wl(`  ${ins}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runForceEvolution() {
  const t0 = Date.now();
  h2('📈 Force Evolution — Decay, Acceleration & Half-Life');
  wl('  ديناميكيات القوى عبر الزمن — هل تتسارع أم تتلاشى؟ (~10s)');
  wl();

  const r = await pythonForceEvolution();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  عدد الأسهم: ${r.n_stocks ?? '—'}   |   عتبة TR: ${r.tr_threshold_pct ?? '—'}%`);
  wl();

  const evo = r.evolution || {};
  if (Object.keys(evo).length) {
    wl('  ─ خصائص كل قوة ─');
    wl('  ' + 'القوة'.padEnd(28) + 'نصف العمر  وقت الصعود  P(TR سريع)  P(TR بطيء)  P(TR ذروة)');
    wl('  ' + '─'.repeat(100));
    for (const [force, data] of Object.entries(evo)) {
      wl(`  ${force.padEnd(28)} ${fmt(data.half_life_bars,1).padEnd(11)} ${fmt(data.rise_time_bars,1).padEnd(12)} ${fmt(data.p_tr_fast_decay,1).padEnd(12)} ${fmt(data.p_tr_slow_decay,1).padEnd(12)} ${fmt(data.p_tr_extreme_peak,1)}`);
    }
    wl();
  }

  // Fastest decay forces
  const fastDecay = r.fastest_decay || [];
  if (fastDecay.length) {
    wl('  ─ أسرع القوى تلاشياً (نصف العمر < 2 شمعة) ─');
    for (const [force, data] of fastDecay) {
      wl(`  • ${force.padEnd(28)} نصف العمر: ${fmt(data.half_life_bars,1)} شمعة  |  ${data.insight ?? ''}`);
    }
    wl();
  }

  // Key insights
  const keyInsight = r.key_insight || [];
  if (keyInsight.length) {
    wl('  ─ الدروس المستفادة ─');
    for (const ins of keyInsight) wl(`  💡 ${ins}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runMarketMemoryForce() {
  const t0 = Date.now();
  h2('🧠 Market Memory — Alpha Decay Detection');
  wl('  هل تفقد القوى قدرتها التنبؤية بعد التكرار؟ (~10s)');
  wl();

  const r = await pythonMarketMemory();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  نافذة الذاكرة: ${r.memory_window_days ?? '—'} يوم`);
  wl();

  const memory = r.force_memory || {};
  if (Object.keys(memory).length) {
    wl('  ─ P(TR) حسب التكرار (ظهور أول / ثاني / ثالث+) ─');
    wl('  ' + 'القوة'.padEnd(28) + 'ظهور 1     ظهور 2     ظهور 3+    الاتجاه        نوع الذاكرة');
    wl('  ' + '─'.repeat(100));
    for (const [force, data] of Object.entries(memory)) {
      const occ = data.occurrences || {};
      const p1  = occ['1']?.p_tr;
      const p2  = occ['2']?.p_tr;
      const p3  = occ['3']?.p_tr;
      const trend = p1 != null && p3 != null
        ? (p3 > p1 ? '📈 تعزيز' : p3 < p1 ? '📉 تلاشٍ' : '➡️  ثابت')
        : '—';
      wl(`  ${force.padEnd(28)} ${fmt(p1,1).padEnd(11)} ${fmt(p2,1).padEnd(11)} ${fmt(p3,1).padEnd(11)} ${trend.padEnd(15)} ${data.memory_type ?? '—'}`);
    }
    wl();
  }

  // Meta insights
  const metaInsights = r.meta_insight || [];
  if (metaInsights.length) {
    wl('  ─ رؤى الذاكرة ─');
    for (const ins of metaInsights) wl(`  ${ins}`);
    wl();
  }

  // Individual force insights
  const forceInsights = Object.values(memory).map(d => d.insight).filter(Boolean);
  if (forceInsights.length) {
    wl('  ─ تفاصيل التكيّف ─');
    for (const ins of forceInsights) wl(`  • ${ins}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runFailurePhysicsForce() {
  const t0 = Date.now();
  h2('💥 Failure Physics — Why Do Reversals Fail?');
  wl('  فيزياء الفشل — Cohen\'s d تشريح الانعكاسات الفاشلة (~8s)');
  wl();

  const r = await pythonFailurePhysics();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_true_reversal=${r.n_true_reversal ?? '—'}   n_failed=${r.n_failed ?? '—'}`);
  wl();

  // Discriminant forces (Cohen's d)
  const disc = r.discriminants || [];
  if (disc.length) {
    wl('  ─ المُمايِزات الرئيسية (Cohen\'s d) ─');
    wl('  ' + 'القوة'.padEnd(28) + 'TR mean   Fail mean  Δ          Cohen\'s d');
    wl('  ' + '─'.repeat(80));
    for (const d of disc) {
      wl(`  ${d.force.padEnd(28)} ${fmt(d.tr_mean,3).padEnd(10)} ${fmt(d.fail_mean,3).padEnd(11)} ${fmt(d.difference,3).padEnd(11)} ${fmt(d.effect_d,3)}`);
    }
    wl();
    // Show top interpretation
    if (disc[0]?.interpretation) {
      wl(`  🔍 أبرز: ${disc[0].interpretation}`);
      wl();
    }
  }

  // Failure topology
  const topo = r.failure_topology || {};
  if (Object.keys(topo).length) {
    wl('  ─ طبولوجيا الفشل ─');
    const sorted = Object.entries(topo).sort((a,b) => (b[1].pct||0) - (a[1].pct||0));
    for (const [ftype, data] of sorted) {
      wl(`  ${ftype.padEnd(30)} ${fmt(data.pct,1)}%  (n=${data.n ?? '—'})`);
    }
    wl();
  }

  // Blocking profile
  const blocking = r.blocking_profile || {};
  if (Object.keys(blocking).length) {
    wl('  ─ ملف القوة المُعيقة ─');
    const sorted = Object.entries(blocking).sort((a,b) => (b[1].pct||0) - (a[1].pct||0));
    for (const [force, data] of sorted) {
      wl(`  ${force.padEnd(30)} ${fmt(data.pct,1)}%  (n=${data.n ?? '—'})`);
    }
    wl();
  }

  // Composite insight
  if (r.composite_insight) {
    wl(`  💡 ${r.composite_insight}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runForceAttractorsDisplay() {
  const t0 = Date.now();
  h2('🌀 Force Attractors — Market Stability Basins');
  wl('  K-Means(k=6) على الفضاء 9D — أين تتجمع القوى؟ (~5s)');
  wl();

  const r = await pythonForceAttractors();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_observations=${r.n_observations ?? '—'}   n_stocks=${r.n_stocks ?? '—'}`);
  const evPct = (r.explained_variance_3d || []).map(v => fmt(v,1) + '%').join(' / ');
  if (evPct) wl(`  Explained variance 3D: ${evPct}   (total=${fmt(r.total_explained,1)}%)`);
  wl();

  const attractors = r.attractors || {};
  const attEntries = Object.entries(attractors);
  if (attEntries.length) {
    wl('  ─ مراكز الجذب ─');
    const sorted = attEntries.sort((a,b) => (b[1].pct_time||0) - (a[1].pct_time||0));
    for (const [id, att] of sorted) {
      const domForces = (att.dominant_forces || []).slice(0,3).map(f => `${f[0]}(${fmt(f[1],2)})`).join(', ');
      wl(`  [${id}] ${(att.type ?? '—').padEnd(28)}  ${fmt(att.pct_time,1)}% وقت  مكوث=${fmt(att.avg_stay_bars,1)} شمعة  n=${att.n_observations ?? '—'}`);
      if (domForces) wl(`       القوى المهيمنة: ${domForces}`);
    }
    wl();
  }

  // Instability zones
  const unstable = attEntries.filter(([,a]) => a.type?.includes('INSTAB') || a.type?.includes('PANIC'));
  if (unstable.length) {
    wl('  ─ مناطق عدم الاستقرار ─');
    for (const [id, u] of unstable) {
      wl(`  ⚠️  [${id}] ${u.type}  (${fmt(u.pct_time,1)}% وقت، مكوث ${fmt(u.avg_stay_bars,1)} شمعة)`);
    }
    wl();
  }

  if (r.instability_zone_pct != null) {
    wl(`  إجمالي وقت في مناطق عدم الاستقرار: ${fmt(r.instability_zone_pct,1)}%`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runForceFieldFullReport() {
  const t0 = Date.now();
  h2('🧬 Force Field FULL — Complete Market Force Analysis');
  wl('  التحليل الشامل لمجال القوة السوقي — جميع الأبعاد (~60s)');
  wl();

  const r = await pythonForceFieldFull();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  // Delegate to individual display functions if sub-results are available
  if (r.force_field_now)    { wl('  ✅ Force Field Now        — تم'); }
  if (r.force_interactions) { wl('  ✅ Force Interactions     — تم'); }
  if (r.force_evolution)    { wl('  ✅ Force Evolution        — تم'); }
  if (r.market_memory)      { wl('  ✅ Market Memory          — تم'); }
  if (r.failure_physics)    { wl('  ✅ Failure Physics        — تم'); }
  if (r.force_attractors)   { wl('  ✅ Force Attractors       — تم'); }

  wl();

  // Synthesis
  const synthesis = r.synthesis || {};
  if (Object.keys(synthesis).length) {
    h2('🎯 التوليف الاستراتيجي');
    if (synthesis.market_phase)    wl(`  مرحلة السوق الحالية: ${synthesis.market_phase}`);
    if (synthesis.dominant_force)  wl(`  القوة المهيمنة: ${synthesis.dominant_force}`);
    if (synthesis.regime)          wl(`  النظام العام: ${synthesis.regime}`);
    if (synthesis.action_bias)     wl(`  التحيز الاستراتيجي: ${synthesis.action_bias}`);
    if (synthesis.key_risks?.length) {
      wl('  المخاطر الرئيسية:');
      for (const risk of synthesis.key_risks) wl(`  • ${risk}`);
    }
    if (synthesis.opportunities?.length) {
      wl('  الفرص:');
      for (const opp of synthesis.opportunities) wl(`  • ${opp}`);
    }
    wl();
  }

  sep();
  wl(`  ⏱  إجمالي وقت التحليل: ${r.elapsed_sec ?? '—'}s (تشغيل: ${Date.now()-t0}ms)`);
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// PROPAGATION ENGINE (Phase 3) — display functions
// ─────────────────────────────────────────────────────────────────────────

async function runPropagationNow() {
  const t0 = Date.now();
  h2('🌊 Propagation NOW — Live Force Transmission Snapshot');
  wl('  لقطة آنية لانتشار القوى — مصادر الإشارة والقطاعات المعرضة للعدوى (~2s)');
  wl();

  const r = await pythonPropagationNow();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  // Market summary
  const stateEmoji = { HIGH_CONTAGION_RISK: '🔴', MODERATE_RISK: '🟡', LOW_RISK: '✅' };
  wl(`  📊 الأسهم: ${r.n_stocks ?? '—'}   |   القطاعات: ${r.n_sectors ?? '—'}`);
  wl(`  ضغط السوق: ${fmt(r.market_stress,3)}   |   تماسك السوق: ${fmt(r.market_coherence,3)}`);
  wl(`  ${stateEmoji[r.propagation_state] ?? '⚪'} حالة الانتشار: ${r.propagation_state ?? '—'}  (استعداد=${fmt(r.propagation_readiness,3)})`);
  wl();

  // Sector profiles
  const profiles = r.sector_profiles || {};
  if (Object.keys(profiles).length) {
    wl('  ─ مؤشرات القطاعات ─');
    wl('  ' + 'القطاع'.padEnd(30) + 'الحالة'.padEnd(16) + 'ضغط    OB     تماسك  RSI');
    wl('  ' + '─'.repeat(82));
    const sorted = Object.entries(profiles).sort((a,b) => b[1].stress_fraction - a[1].stress_fraction);
    for (const [sec, p] of sorted) {
      const emoji = p.state === 'STRESSED' ? '🔴' : p.state === 'OVERBOUGHT' ? '🟠' :
                    p.state === 'TRENDING'  ? '🟢' : p.state === 'NEUTRAL' ? '⚪' : '🟡';
      wl(`  ${sec.padEnd(30)} ${emoji} ${p.state.padEnd(14)} ${fmt(p.stress_fraction,2).padEnd(7)} ${fmt(p.ob_fraction,2).padEnd(7)} ${fmt(p.coherence,2).padEnd(7)} ${fmt(p.avg_rsi,1)}`);
    }
    wl();
  }

  // Transmission alerts
  const alerts = r.transmission_alerts || [];
  if (alerts.length) {
    wl('  ─ تحذيرات الانتقال العدواني ─');
    for (const a of alerts) {
      wl(`  ⚠️  ${a.from.padEnd(28)} ──▶ ${a.to.padEnd(28)} خطر=${fmt(a.risk,3)}`);
      wl(`       ${a.mechanism}`);
    }
    wl();
  }

  // Force sources
  const sources = r.force_sources || [];
  if (sources.length) {
    wl('  ─ مصادر القوى الحالية ─');
    wl('  ' + 'الرمز'.padEnd(12) + 'الدور'.padEnd(22) + 'RSI    Momentum  قطاع');
    wl('  ' + '─'.repeat(72));
    for (const s of sources.slice(0, 12)) {
      wl(`  ${s.symbol.padEnd(12)} ${s.role.padEnd(22)} ${fmt(s.rsi,1).padEnd(7)} ${fmt(s.momentum,3).padEnd(10)} ${s.sector}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runContagionChains() {
  const t0 = Date.now();
  h2('🔗 Contagion Chains — P(sector B follows A | lag)');
  wl('  سلاسل العدوى التاريخية — متى وكيف تنتقل الضغوط بين القطاعات؟ (~25s)');
  wl();

  const r = await pythonContagionChains();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_stocks=${r.n_stocks ?? '—'}  n_dates=${r.n_dates ?? '—'}  عتبة الانتشار=${fmt(r.onset_threshold,2)}  Baseline=${fmt(r.baseline_increase,3)}`);
  wl();

  // Top contagion pairs
  const pairs = r.top_contagion || [];
  if (pairs.length) {
    wl('  ─ أقوى أزواج العدوى ─');
    wl('  ' + 'الزوج'.padEnd(42) + 'P(عدوى)  أقصى تأخر  Lift   القوة');
    wl('  ' + '─'.repeat(80));
    for (const p of pairs.slice(0, 15)) {
      const lag1 = p.lags?.['1'] || {};
      wl(`  ${p.pair.padEnd(42)} ${fmt(p.peak_p*100,1).padEnd(9)} ${String(p.peak_lag).padEnd(11)} ${fmt(lag1.lift,2).padEnd(7)} ${p.strength}`);
    }
    wl();
  }

  // Sector power
  const power = r.sector_power || {};
  if (Object.keys(power).length) {
    wl('  ─ قوة الانتشار لكل قطاع ─');
    const sorted = Object.entries(power).sort((a,b) => b[1].spread_power - a[1].spread_power);
    for (const [sec, p] of sorted) {
      const role = p.net_role === 'SPREADER' ? '📡 ناشر' : p.net_role === 'RECEIVER' ? '📥 مستقبل' : '⚖️  متوازن';
      wl(`  ${sec.padEnd(30)} ${role.padEnd(14)} نشر=${fmt(p.spread_power,3)}  استقبال=${fmt(p.receive_power,3)}`);
    }
    wl();
  }

  // Contagion chains
  const chains = r.contagion_chains || [];
  if (chains.length) {
    wl('  ─ سلاسل العدوى المكتشفة ─');
    for (const c of chains) {
      wl(`  ${c.length} قطاع: ${c.chain}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runSectorTransmission() {
  const t0 = Date.now();
  h2('🏭 Sector Transmission — Leadership & Lag Matrix');
  wl('  مصفوفة نقل القوى بين القطاعات — من يقود ومن يتبع؟ (~15s)');
  wl();

  const r = await pythonSectorTransmission();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_sectors=${r.n_sectors ?? '—'}  n_dates=${r.n_dates ?? '—'}`);
  if (r.insight) wl(`  💡 ${r.insight}`);
  wl();

  // Sector lead ranking
  const ranking = r.sector_lead_ranking || [];
  if (ranking.length) {
    wl('  ─ ترتيب القيادة (أكثر تقدماً في الوقت → أكثر قيادة) ─');
    wl('  ' + 'القطاع'.padEnd(30) + 'الدور'.padEnd(22) + 'تأخر vs سوق  ارتباط  ضغط متوسط  تضخيم');
    wl('  ' + '─'.repeat(90));
    for (const s of ranking) {
      const roleEmoji = s.role === 'LEAD_SECTOR'   ? '📡 قائد'
                      : s.role === 'FOLLOW_SECTOR'  ? '📥 تابع'
                      : s.role === 'ABSORBER'        ? '🛡️  ماص'
                      : s.role === 'AMPLIFIER'       ? '🔊 مضخم'
                      : '⚖️  محايد';
      const lagStr = s.lag_vs_market != null ? String(s.lag_vs_market) : '—';
      wl(`  ${s.sector.padEnd(30)} ${roleEmoji.padEnd(22)} ${lagStr.padEnd(13)} ${fmt(s.corr_vs_market,3).padEnd(9)} ${fmt(s.avg_stress,3).padEnd(11)} ×${fmt(s.amplification,2)}`);
    }
    wl();
  }

  // Top transmission pairs
  const pairs = r.transmission_pairs || [];
  if (pairs.length) {
    wl('  ─ أقوى أزواج النقل ─');
    wl(`  ${'من → إلى'.padEnd(48)} ${'اتجاه'.padEnd(16)} ارتباط  تأخر`);
    wl('  ' + '─'.repeat(82));
    for (const p of pairs.slice(0, 10)) {
      const pairStr = `${p.s1} → ${p.s2}`;
      const dirIcon = p.direction === 'S1_LEADS' ? '⟶' : p.direction === 'S2_LEADS' ? '⟵' : '↔';
      wl(`  ${pairStr.padEnd(48)} ${dirIcon} ${p.direction.padEnd(14)} ${fmt(p.peak_corr,3).padEnd(8)} ${p.peak_lag}يوم`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runInstabilityCascades() {
  const t0 = Date.now();
  h2('⚡ Instability Cascades — Panic Propagation Physics');
  wl('  فيزياء انهيارات الاستقرار — متى تتحول الضغوط إلى عاصفة سوقية؟ (~15s)');
  wl();

  const r = await pythonInstabilityCascades();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  if (!r.n_cascades) {
    wl(warn('لم يتم اكتشاف أي تتابعات هامة'));
    return;
  }

  wl(`  📊 إجمالي التتابعات: ${r.n_cascades}  |  منهجية: ${r.n_systemic} | قطاعية: ${r.n_sector_wide} | محدودة: ${r.n_localized}`);
  wl(`  متوسط السعة: ${fmt(r.avg_amplitude*100,1)}%  |  متوسط المدة: ${fmt(r.avg_duration_bars,1)} شمعة  |  نسبة السريعة: ${fmt(r.fast_cascades_pct*100,0)}%`);
  if (r.insight) wl(`  💡 ${r.insight}`);
  wl();

  // Trigger analysis
  const triggers = r.trigger_analysis || [];
  if (triggers.length) {
    wl('  ─ تحليل محفزات التتابع ─');
    wl('  ' + 'القطاع المحفز'.padEnd(30) + 'عدد المرات  P(شديد)  متوسط السعة');
    wl('  ' + '─'.repeat(62));
    for (const t of triggers) {
      wl(`  ${t.sector.padEnd(30)} ${String(t.n_triggers).padEnd(12)} ${fmt(t.p_severe*100,0).padEnd(9)}% ${fmt(t.avg_amplitude*100,1)}%`);
    }
    wl();
  }

  // Cascade breakers
  const breakers = r.cascade_breakers || [];
  if (breakers.length) {
    wl('  ─ كاسرات التتابع (قطاعات تمتص الصدمة) ─');
    for (const b of breakers) {
      wl(`  🛡️  ${b.sector.padEnd(30)} ضغط متوسط أثناء الأزمات: ${fmt(b.avg_cascade_stress*100,1)}%`);
    }
    wl();
  }

  // Top cascades
  const cascades = r.cascades || [];
  if (cascades.length) {
    wl('  ─ أشد التتابعات تاريخياً ─');
    wl('  ' + 'التصنيف'.padEnd(14) + 'المحفز'.padEnd(28) + 'السعة    قمة السوق   مدة');
    wl('  ' + '─'.repeat(70));
    for (const c of cascades.slice(0, 8)) {
      const emoji = c.category === 'SYSTEMIC' ? '🔴' : c.category === 'SECTOR_WIDE' ? '🟠' : '🟡';
      wl(`  ${emoji} ${c.category.padEnd(12)} ${(c.trigger_sector||'—').padEnd(28)} ${fmt(c.amplitude*100,1).padEnd(9)}% ${fmt(c.peak_breadth*100,0).padEnd(12)}% ${c.duration_bars} شمعة`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runRoleClassification() {
  const t0 = Date.now();
  h2('🎭 Role Classification — SOURCE / AMPLIFIER / ABSORBER');
  wl('  تصنيف كل سهم كـ مصدر قوة / مضخم / ماص / مرساة / متأخر (~12s)');
  wl();

  const r = await pythonRoleClassification();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  📊 مُصنَّف: ${r.n_classified ?? '—'} سهم`);
  if (r.insight) wl(`  💡 ${r.insight}`);
  wl();

  // Role distribution
  const dist = r.role_distribution || {};
  if (Object.keys(dist).length) {
    wl('  ─ توزيع الأدوار ─');
    const total = Object.values(dist).reduce((a,b) => a+b, 0);
    const roleEmojis = {
      FORCE_SOURCE:          '📡',
      FORCE_AMPLIFIER:       '🔊',
      FORCE_ABSORBER:        '🛡️',
      STABILITY_ANCHOR:      '⚓',
      DELAYED_REACTOR:       '⏰',
      INSTABILITY_GENERATOR: '💥',
      NEUTRAL_PARTICIPANT:   '⚪',
    };
    for (const [role, cnt] of Object.entries(dist)) {
      const pct = total ? fmt(cnt/total*100, 1) : '—';
      const bar = '█'.repeat(Math.round(cnt/total*20)).padEnd(20);
      wl(`  ${roleEmojis[role] ?? '•'} ${role.padEnd(26)} ${String(cnt).padStart(4)} سهم  (${pct}%)  ${bar}`);
    }
    wl();
  }

  // Top stocks per role
  const tops = r.top_per_role || {};
  const showRoles = ['FORCE_SOURCE', 'FORCE_AMPLIFIER', 'FORCE_ABSORBER', 'STABILITY_ANCHOR'];
  for (const role of showRoles) {
    const stocks = tops[role] || [];
    if (!stocks.length) continue;
    const emoji = { FORCE_SOURCE:'📡', FORCE_AMPLIFIER:'🔊', FORCE_ABSORBER:'🛡️', STABILITY_ANCHOR:'⚓' }[role];
    wl(`  ─ أبرز ${emoji} ${role} ─`);
    for (const s of stocks.slice(0, 6)) {
      wl(`  • ${s.symbol.padEnd(10)} ${s.sector.padEnd(24)} lead=${fmt(s.lead_score,2)}  absorb=${fmt(s.absorb_score,2)}  anchor=${fmt(s.anchor_score,2)}`);
    }
    wl();
  }

  // Sector risk composition
  wl(`  ─ أخطر القطاعات / أأمنها ─`);
  wl(`  🔴 أكثر خطورة: ${r.riskiest_sector ?? '—'}`);
  wl(`  ✅ أكثر أماناً: ${r.safest_sector  ?? '—'}`);
  wl();

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runDiffusionAnalysis() {
  const t0 = Date.now();
  h2('🌊 Diffusion Analysis — Force Spread Speed & Radius');
  wl('  سرعة وامتداد انتشار القوى — من أين تبدأ وإلى أين تصل؟ (~15s)');
  wl();

  const r = await pythonDiffusionAnalysis();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_dates=${r.n_dates ?? '—'}  أحداث الضغط: ${r.market_stress_events ?? '—'}`);
  wl(`  نصف عمر السوق الكلي: ${r.market_half_life_bars ?? '—'} شمعة`);

  // Market diffusion curve
  const curve = r.market_diffusion_curve || [];
  if (curve.length) {
    const bars = curve.slice(0, 8).map((v, i) =>
      `t+${i}: ${'█'.repeat(Math.round(v*10)).padEnd(10)} ${fmt(v,2)}`
    ).join('   ');
    wl(`  منحنى الانتشار الكلي: ${bars.slice(0, 80)}`);
    wl();
  }

  // Speed ranking
  const speed = r.speed_ranking || [];
  if (speed.length) {
    wl('  ─ أسرع القطاعات انتشاراً للضغط ─');
    wl('  ' + 'القطاع'.padEnd(30) + 'نصف العمر  قمة التأخر  نوع الانتشار  n أحداث');
    wl('  ' + '─'.repeat(78));
    for (const s of speed.slice(0, 8)) {
      const hlStr = s.half_life_bars != null ? String(s.half_life_bars) : '—';
      wl(`  ${s.sector.padEnd(30)} ${hlStr.padEnd(11)} ${String(s.peak_lag_bars ?? '—').padEnd(12)} ${(s.type ?? '—').padEnd(14)} ${s.n_events ?? '—'}`);
    }
    wl();
  }

  // Top amplifiers and absorbers
  const amps = r.top_amplifiers || [];
  const abs  = r.top_absorbers  || [];
  if (amps.length) {
    wl('  ─ أقوى مُضخِّمات الانتشار ─');
    for (const a of amps) wl(`  🔊 ${a.sector.padEnd(30)} معامل=${fmt(a.coefficient,2)}  ذروة=×${fmt(a.max_ratio,1)}`);
    wl();
  }
  if (abs.length) {
    wl('  ─ أقوى ماصات الانتشار ─');
    for (const a of abs) wl(`  🛡️  ${a.sector.padEnd(30)} معامل=${fmt(a.coefficient,2)}`);
    wl();
  }

  // Propagation radius
  const radius = r.propagation_radius || {};
  if (Object.keys(radius).length) {
    wl('  ─ نصف قطر الانتشار (عدد القطاعات المتأثرة) ─');
    const sorted = Object.entries(radius).sort((a,b) => b[1].avg_radius - a[1].avg_radius);
    for (const [sec, d] of sorted.slice(0, 6)) {
      const coverPct = d.coverage != null ? fmt(d.coverage*100,0)+'%' : '—';
      wl(`  ${sec.padEnd(30)} متوسط=${fmt(d.avg_radius,1)}  ذروة=${d.max_radius}  تغطية=${coverPct}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runRegimeNetworks() {
  const t0 = Date.now();
  h2('🌍 Regime Networks — Topology Across CRISIS / CALM');
  wl('  شبكة الانتقال حسب النظام — كيف تتغير بنية السوق في الأزمات؟ (~20s)');
  wl();

  const r = await pythonRegimeNetworks();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_dates=${r.n_dates ?? '—'}`);

  // Regime distribution
  const dist = r.regime_distribution || {};
  if (Object.keys(dist).length) {
    wl('  ─ توزيع الأنظمة التاريخية ─');
    const emojis = { CRISIS:'🔴', STRESS:'🟠', MODERATE:'🟡', CALM:'🟢', RECOVERY:'🔵' };
    for (const [reg, cnt] of Object.entries(dist)) {
      wl(`  ${emojis[reg] ?? '⚪'} ${reg.padEnd(12)} ${cnt} يوم`);
    }
    wl();
  }

  // Per-regime profiles
  const profiles = r.regime_profiles || {};
  const regimeOrder = ['CRISIS', 'STRESS', 'MODERATE', 'CALM', 'RECOVERY'];
  for (const regime of regimeOrder) {
    const prof = profiles[regime];
    if (!prof) continue;
    const emoji = { CRISIS:'🔴', STRESS:'🟠', MODERATE:'🟡', CALM:'🟢', RECOVERY:'🔵' }[regime] ?? '⚪';
    wl(`  ${emoji} ${regime} (${prof.n_dates} يوم | ${fmt(prof.pct_of_history*100,0)}% من التاريخ)  كثافة الشبكة: ${fmt(prof.network_density,3)}`);

    if (prof.top_stressed?.length) {
      const topSecs = prof.top_stressed.map(s => `${s.sector}(${fmt(s.avg*100,0)}%)`).join(', ');
      wl(`    أكثر القطاعات إجهاداً: ${topSecs}`);
    }
    if (prof.top_correlations?.length) {
      const tc = prof.top_correlations[0];
      wl(`    أقوى ارتباط: ${tc.s1} ↔ ${tc.s2} (r=${fmt(tc.corr,2)})`);
    }
    wl();
  }

  // Topology changes
  const changes = r.topology_changes || [];
  if (changes.length) {
    wl('  ─ تغيرات الطوبولوجيا ─');
    for (const c of changes) wl(`  • ${c}`);
    wl();
  }

  // Network invariants
  const invariants = r.network_invariants || [];
  if (invariants.length) {
    wl('  ─ ثوابت الشبكة ─');
    for (const inv of invariants) wl(`  🔒 ${inv}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runPropagationFullReport() {
  const t0 = Date.now();
  h2('🌊 PROPAGATION FULL — Complete Transmission Physics Report');
  wl('  التحليل الشامل لانتشار القوى في السوق (~90s)');
  wl();

  const r = await pythonPropagationFull();
  if (!r.success && !r.propagation_now) { wl(warn('خطأ: ' + r.error)); return; }

  const sections = [
    ['propagation_now',      '⚡ Propagation NOW'],
    ['contagion_chains',     '🔗 Contagion Chains'],
    ['sector_transmission',  '🏭 Sector Transmission'],
    ['instability_cascades', '💥 Instability Cascades'],
    ['role_classification',  '🎭 Role Classification'],
    ['diffusion_analysis',   '🌊 Diffusion Analysis'],
    ['regime_networks',      '🌍 Regime Networks'],
  ];

  for (const [key, label] of sections) {
    const sub = r[key];
    wl(sub && !sub.error ? `  ✅ ${label}` : `  ❌ ${label}: ${sub?.error ?? 'خطأ'}`);
  }

  // Summary synthesis
  wl();
  h2('🎯 التوليف الاستراتيجي للانتشار');

  const now     = r.propagation_now     || {};
  const roles   = r.role_classification || {};
  const cascades= r.instability_cascades|| {};
  const trans   = r.sector_transmission || {};

  if (now.propagation_state)    wl(`  حالة الانتشار الآنية: ${now.propagation_state} (استعداد=${fmt(now.propagation_readiness,3)})`);
  if (trans.insight)             wl(`  قيادة القطاعات: ${trans.insight}`);
  if (cascades.insight)          wl(`  التتابعات: ${cascades.insight}`);
  if (roles.riskiest_sector)     wl(`  أخطر قطاع: ${roles.riskiest_sector}  |  أأمن: ${roles.safest_sector}`);
  if (roles.insight)             wl(`  الأدوار: ${roles.insight}`);

  const alerts = now.transmission_alerts || [];
  if (alerts.length) {
    wl();
    wl('  🚨 تحذيرات الانتشار الحالية:');
    for (const a of alerts.slice(0, 3)) {
      wl(`  • ${a.from} ──▶ ${a.to}  (خطر=${fmt(a.risk,3)})`);
    }
  }

  wl();
  sep();
  wl(`  ⏱  إجمالي الوقت: ${r.elapsed_sec ?? '—'}s (تشغيل: ${Date.now()-t0}ms)`);
  sep();
}

// ═════════════════════════════════════════════════════════════════════════
// ⚡  PHASE 4 — ENERGY FLOW ENGINE
// ═════════════════════════════════════════════════════════════════════════

const ENERGY_ICONS = {
  MOMENTUM_ENERGY:          '🚀',
  PANIC_ENERGY:             '😱',
  EXHAUSTION_ENERGY:        '😮‍💨',
  VOLATILITY_ENERGY:        '🌀',
  LIQUIDITY_STRESS:         '🚰',
  MEAN_REVERSION_PRESSURE:  '🎯',
  INSTABILITY_ENERGY:       '⚡',
  TREND_PERSISTENCE_ENERGY: '📈',
};
const ENERGY_NAMES_AR = {
  MOMENTUM_ENERGY:          'طاقة الزخم',
  PANIC_ENERGY:             'طاقة الذعر',
  EXHAUSTION_ENERGY:        'طاقة الإنهاك',
  VOLATILITY_ENERGY:        'طاقة التقلب',
  LIQUIDITY_STRESS:         'ضغط السيولة',
  MEAN_REVERSION_PRESSURE:  'ضغط الارتداد',
  INSTABILITY_ENERGY:       'طاقة الاضطراب',
  TREND_PERSISTENCE_ENERGY: 'طاقة استمرارية الترند',
};

function energyBar(v) {
  const filled = Math.round((v || 0) * 12);
  const color  = v >= 0.35 ? '🔴' : v >= 0.20 ? '🟡' : '🟢';
  return color + '█'.repeat(filled).padEnd(12) + ` ${fmt(v,3)}`;
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyNow() {
  const t0 = Date.now();
  h2('⚡ Energy NOW — Behavioral Thermodynamics Snapshot');
  wl('  لقطة آنية لطاقة السلوكيات السوقية — من أين تتدفق القوة؟ (~2s)');
  wl();

  const r = await pythonEnergyNow();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  // Market energy bar chart
  const mkt = r.market_energy || {};
  wl(`  📊 الأسهم: ${r.n_stocks ?? '—'}  |  القطاعات: ${r.n_sectors ?? '—'}`);
  wl(`  🌡️  حالة السوق: ${r.market_state ?? '—'}   الطاقة الإجمالية: ${fmt(r.total_system_energy, 3)}`);
  wl(`  ⚡ الطاقة المسيطرة: ${ENERGY_NAMES_AR[r.dominant_energy] ?? r.dominant_energy}`);
  wl();

  wl('  ─ مستويات الطاقة الكلية للسوق ─');
  for (const dim of Object.keys(ENERGY_NAMES_AR)) {
    const v = mkt[dim] ?? 0;
    const icon = ENERGY_ICONS[dim] ?? '•';
    wl(`  ${icon} ${ENERGY_NAMES_AR[dim].padEnd(28)} ${energyBar(v)}`);
  }
  wl();

  // Sector energy table
  const sectors = r.sector_profiles || {};
  const secRows = Object.entries(sectors).sort((a,b) => b[1].total_energy - a[1].total_energy);
  if (secRows.length) {
    wl('  ─ الطاقة لكل قطاع ─');
    wl('  ' + 'القطاع'.padEnd(28) + 'الحالة'.padEnd(22) + 'إجمالي  🚀زخم  😱ذعر  😮‍💨إنهاك  🌀تقلب  ⚡اضطراب');
    wl('  ' + '─'.repeat(100));
    for (const [sec, d] of secRows) {
      const stateIcon = d.energy_state === 'PANIC' ? '🔴' : d.energy_state === 'EXHAUSTION' ? '🟠' :
                        d.energy_state === 'HIGH_MOMENTUM' ? '🚀' : d.energy_state === 'INSTABILITY' ? '⚡' :
                        d.energy_state === 'COMPRESSED_VOLATILITY' ? '🌀' : '🟢';
      wl(`  ${sec.padEnd(28)} ${stateIcon} ${d.energy_state.padEnd(20)} ` +
         `${fmt(d.total_energy,3).padEnd(9)}${fmt(d.MOMENTUM_ENERGY,3).padEnd(7)}` +
         `${fmt(d.PANIC_ENERGY,3).padEnd(7)}${fmt(d.EXHAUSTION_ENERGY,3).padEnd(9)}` +
         `${fmt(d.VOLATILITY_ENERGY,3).padEnd(8)}${fmt(d.INSTABILITY_ENERGY,3)}`);
    }
    wl();
  }

  if (r.hottest_sectors?.length) {
    wl(`  🔥 أعلى طاقة اضطراب: ${r.hottest_sectors.join(', ')}`);
  }
  if (r.compressed_sectors?.length) {
    wl(`  🌀 أكثر ضغطاً (نوابض): ${r.compressed_sectors.join(', ')}`);
  }
  if (r.exhausted_sectors?.length) {
    wl(`  😮‍💨 أكثر إنهاكاً: ${r.exhausted_sectors.join(', ')}`);
  }
  if (r.trending_sectors?.length) {
    wl(`  📈 أقوى استمرارية: ${r.trending_sectors.join(', ')}`);
  }

  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyFlow() {
  const t0 = Date.now();
  h2('🌊 Energy Flow — Sector-to-Sector Transmission');
  wl('  كيف تتدفق الطاقة السلوكية بين القطاعات؟ من يُصدّر ومن يَستقبل؟ (~15s)');
  wl();

  const r = await pythonEnergyFlow();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_sectors=${r.n_sectors ?? '—'}  n_dates=${r.n_dates ?? '—'}`);
  wl();

  // Sector roles
  const roles = r.sector_roles || {};
  if (Object.keys(roles).length) {
    const roleIcons = {
      ENERGY_SOURCE:     '📡',
      ENERGY_SINK:       '📥',
      ENERGY_STORAGE:    '🔋',
      ENERGY_AMPLIFIER:  '🔊',
      ENERGY_CONVERTER:  '🔄',
      ENERGY_DISSIPATOR: '🌫️',
    };
    wl('  ─ أدوار الطاقة لكل قطاع ─');
    wl('  ' + 'القطاع'.padEnd(30) + 'الدور'.padEnd(22) + 'صادر   وارد   صافي');
    wl('  ' + '─'.repeat(72));
    const sorted = Object.entries(roles).sort((a,b) => b[1].net - a[1].net);
    for (const [sec, d] of sorted) {
      const icon = roleIcons[d.role] ?? '•';
      wl(`  ${sec.padEnd(30)} ${icon} ${d.role.padEnd(20)} ` +
         `${fmt(d.outflow,3).padEnd(8)}${fmt(d.inflow,3).padEnd(8)}${fmt(d.net,3)}`);
    }
    wl();
  }

  // Top flow pairs
  const pairs = r.top_flow_pairs || [];
  if (pairs.length) {
    wl('  ─ أقوى قنوات التدفق ─');
    wl('  ' + 'الاتجاه'.padEnd(52) + 'تأخر   ارتباط');
    wl('  ' + '─'.repeat(68));
    for (const p of pairs.slice(0, 10)) {
      wl(`  ${p.direction.padEnd(52)} ${String(p.lag).padEnd(7)}${fmt(p.corr,3)}`);
    }
    wl();
  }

  // Per-dimension leaders
  const dimL = r.dim_leaders || {};
  if (Object.keys(dimL).length) {
    wl('  ─ القيادة لكل نوع طاقة ─');
    for (const [state, d] of Object.entries(dimL)) {
      wl(`  ${state.padEnd(26)} ${d.source} ──▶ ${d.target}  (تأخر=${d.lag_bars}، r=${fmt(d.corr,2)})`);
    }
    wl();
  }

  // Bottlenecks
  const bnecks = r.bottlenecks || [];
  if (bnecks.length) {
    wl('  ─ نقاط الاختناق (طاقة تدخل ولا تخرج) ─');
    for (const b of bnecks) {
      wl(`  ⚠️  ${b.sector.padEnd(30)} وارد=${fmt(b.inflow,3)}  صادر=${fmt(b.outflow,3)}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyAccumulation() {
  const t0 = Date.now();
  h2('🔋 Energy Accumulation — Buildup Zones & Coiled Springs');
  wl('  أين تتراكم الطاقة؟ نوابض مضغوطة، خزانات اضطراب، مناطق إفراط (~15s)');
  wl();

  const r = await pythonEnergyAccumulation();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_sectors=${r.n_sectors ?? '—'}  n_dates=${r.n_dates ?? '—'}`);

  // Market velocity
  const mv = r.market_velocity || {};
  if (Object.keys(mv).length) {
    wl();
    wl('  ─ سرعة تراكم الطاقة في السوق (آخر 20 يوم vs قبلها) ─');
    const dimsSorted = Object.entries(mv).sort((a,b) => Math.abs(b[1]) - Math.abs(a[1]));
    for (const [dim, delta] of dimsSorted.slice(0, 5)) {
      const arrow = delta > 0 ? '⬆️' : delta < 0 ? '⬇️' : '➡️';
      const sign  = delta > 0 ? '+' : '';
      wl(`  ${arrow} ${ENERGY_NAMES_AR[dim]?.padEnd(28) ?? dim.padEnd(28)} ${sign}${fmt(delta,4)}`);
    }
    wl();
  }

  // Coiled springs
  const coiled = r.coiled_springs || [];
  if (coiled.length) {
    wl('  ─ 🌀 نوابض مضغوطة (تقلب مكبوت، جاهز للانفجار) ─');
    for (const c of coiled) {
      wl(`  🌀 ${c.sector.padEnd(32)} ضغط=${fmt(c.vol_energy,3)}  تراكم=${fmt(c.buildup,3)}`);
    }
    wl();
  }

  // Instability reservoirs
  const reservoirs = r.instability_reservoirs || [];
  if (reservoirs.length) {
    wl('  ─ ⚡ خزانات الاضطراب (طاقة عالية ومتزايدة) ─');
    for (const res of reservoirs) {
      wl(`  ⚡ ${res.sector.padEnd(32)} اضطراب=${fmt(res.instability,3)}  تراكم=${fmt(res.buildup_score,3)}`);
    }
    wl();
  }

  // Overextended
  const overex = r.overextended || [];
  if (overex.length) {
    wl('  ─ 😮‍💨 مناطق الإفراط (إنهاك + زخم = خطر التحول) ─');
    for (const o of overex) {
      wl(`  😮‍💨 ${o.sector.padEnd(32)} إنهاك=${fmt(o.exhaustion,3)}  زخم=${fmt(o.momentum,3)}`);
    }
    wl();
  }

  // Fastest buildup ranking
  const buildup = r.fastest_buildup || [];
  if (buildup.length) {
    wl('  ─ أسرع القطاعات تراكماً للطاقة ─');
    for (const b of buildup) {
      const dimAr = ENERGY_NAMES_AR[b.dim] ?? b.dim;
      wl(`  📈 ${b.sector.padEnd(32)} نقطة=${fmt(b.score,3)}  نوع: ${dimAr}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyTransformation() {
  const t0 = Date.now();
  h2('🔄 Energy Transformation — Behavioral Markov Chain');
  wl('  كيف تتحول الطاقة السلوكية؟ الزخم → إنهاك → ارتداد → زخم جديد (~20s)');
  wl();

  const r = await pythonEnergyTransformation();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  الأشرطة الكلية: ${r.n_total_bars?.toLocaleString() ?? '—'}  |  الحالات: ${r.n_states ?? '—'}`);
  wl();

  // Key pathways
  const paths = r.key_pathways || [];
  if (paths.length) {
    wl('  ─ مسارات التحول الرئيسية ─');
    wl('  ' + 'المسار'.padEnd(52) + 'P(تحول)  عدد   متوسط المدة');
    wl('  ' + '─'.repeat(82));
    for (const p of paths) {
      const bar = '█'.repeat(Math.round((p.probability || 0) * 20)).padEnd(20);
      wl(`  ${p.pathway.padEnd(52)} ${fmt(p.probability,3).padEnd(9)}` +
         `${String(p.n_observed).padEnd(7)}${p.avg_duration_before ?? '—'} شمعة  ${bar}`);
    }
    wl();
  }

  // Transition matrix (top states only)
  const matrix = r.transition_matrix || {};
  const topStates = Object.entries(matrix)
    .filter(([, v]) => v.total_events > 50)
    .sort((a,b) => b[1].total_events - a[1].total_events)
    .slice(0, 6);
  if (topStates.length) {
    wl('  ─ مصفوفة الانتقال (أكثر الحالات شيوعاً) ─');
    for (const [state, info] of topStates) {
      const nextStr = Object.entries(info.next_states || {})
        .map(([s, p]) => `${s}(${fmt(p,2)})`).join(' | ');
      wl(`  ${state.padEnd(26)} n=${String(info.total_events).padEnd(6)} تالياً: ${nextStr}`);
    }
    wl();
  }

  // Top 3-step cycles
  const cycles = r.top_3step_cycles || [];
  if (cycles.length) {
    wl('  ─ أكثر الدورات الثلاثية تكراراً ─');
    for (const c of cycles.slice(0, 5)) {
      wl(`  🔄 ${c.cycle.padEnd(70)} n=${c.n}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyPersistence() {
  const t0 = Date.now();
  h2('⏳ Energy Persistence — Half-Life & Decay Curves');
  wl('  كم تعيش كل طاقة؟ نصف العمر، منحنى الانحلال، احتمال الانطلاق (~15s)');
  wl();

  const r = await pythonEnergyPersistence();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  if (r.most_persistent) {
    wl(`  📌 أكثر ثباتاً: ${ENERGY_NAMES_AR[r.most_persistent] ?? r.most_persistent}`);
    wl(`  ⚡ أسرع انطلاقاً: ${ENERGY_NAMES_AR[r.fastest_release] ?? r.fastest_release}`);
    wl(`  🔥 أعلى ذروة: ${ENERGY_NAMES_AR[r.highest_intensity] ?? r.highest_intensity}`);
    wl();
  }

  // Duration ranking table
  const ranking = r.duration_ranking || [];
  if (ranking.length) {
    wl('  ─ جدول الثبات لكل نوع طاقة ─');
    wl('  ' + 'الطاقة'.padEnd(30) + 'نصف العمر  متوسط الأشرطة  P(انطلاق سريع)  عدد الأحداث');
    wl('  ' + '─'.repeat(84));
    for (const e of ranking) {
      const nameAr = ENERGY_NAMES_AR[e.energy] ?? e.energy;
      wl(`  ${nameAr.padEnd(30)} ${String(e.half_life).padEnd(12)}${fmt(e.avg_bars,1).padEnd(16)}` +
         `${fmt(e.p_release,3).padEnd(18)}${e.episodes}`);
    }
    wl();
  }

  // Survival curves for top 3 most persistent
  const persist = r.persistence || {};
  const top3 = ranking.slice(0, 3).map(e => e.energy);
  for (const dim of top3) {
    const p = persist[dim];
    if (!p?.survival_curve) continue;
    const bars = Object.entries(p.survival_curve)
      .map(([t, v]) => `t+${t}: ${'█'.repeat(Math.round(v*10)).padEnd(10)} ${fmt(v,2)}`)
      .join('   ');
    wl(`  📊 ${ENERGY_NAMES_AR[dim] ?? dim}: ${bars.slice(0, 80)}`);
  }
  if (top3.length) wl();

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runRegimeEnergy() {
  const t0 = Date.now();
  h2('🌍 Regime Energy — Thermodynamics by Market State');
  wl('  الطاقة السلوكية حسب النظام — كيف تختلف الديناميكيات في الأزمات؟ (~20s)');
  wl();

  const r = await pythonRegimeEnergy();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  n_dates=${r.n_dates ?? '—'}`);

  // Regime distribution
  const dist = r.regime_distribution || {};
  if (Object.keys(dist).length) {
    const emojis = { CRISIS:'🔴', STRESS:'🟠', MODERATE:'🟡', BULL:'🚀', CALM:'🟢' };
    wl('  ─ توزيع الأنظمة ─');
    for (const [reg, cnt] of Object.entries(dist)) {
      wl(`  ${emojis[reg] ?? '⚪'} ${reg.padEnd(12)} ${cnt} يوم`);
    }
    wl();
  }

  // Per-regime energy profiles
  const profiles = r.profiles || {};
  const regOrder = ['CRISIS', 'STRESS', 'MODERATE', 'BULL', 'CALM'];
  const emojis = { CRISIS:'🔴', STRESS:'🟠', MODERATE:'🟡', BULL:'🚀', CALM:'🟢' };
  for (const reg of regOrder) {
    const p = profiles[reg];
    if (!p) continue;
    wl(`  ${emojis[reg] ?? '⚪'} ${reg} (${p.n_dates} يوم | ${fmt(p.pct_history*100,0)}%)  طاقة إجمالية=${fmt(p.total_energy,3)}  سائدة: ${p.dominant}`);
    const dims = p.avg_energy || {};
    const top3 = Object.entries(dims).sort((a,b) => b[1]-a[1]).slice(0, 3);
    for (const [dim, val] of top3) {
      wl(`    ${ENERGY_ICONS[dim]??'•'} ${ENERGY_NAMES_AR[dim]?.padEnd(26)??dim.padEnd(26)} ${fmt(val,4)}`);
    }
    wl();
  }

  // Transition signatures
  const sigs = r.transition_signatures || {};
  const topSigs = Object.entries(sigs).filter(([,v]) => v.n_events >= 3).slice(0, 5);
  if (topSigs.length) {
    wl('  ─ إشارات الطاقة قبل تغيير النظام ─');
    for (const [key, sig] of topSigs) {
      wl(`  ⚠️  ${key.padEnd(20)} تحذير: ${ENERGY_NAMES_AR[sig.warning_signal] ?? sig.warning_signal}  (n=${sig.n_events})`);
    }
    wl();
  }

  // Crisis vs Calm amplification
  const amp = r.crisis_vs_calm_amplification || {};
  if (Object.keys(amp).length) {
    wl(`  ─ تضخيم الأزمة مقارنة بالهدوء ─`);
    const sorted = Object.entries(amp).sort((a,b) => b[1]-a[1]).slice(0, 4);
    for (const [dim, ratio] of sorted) {
      wl(`  ×${fmt(ratio,1).padEnd(7)} ${ENERGY_NAMES_AR[dim] ?? dim}`);
    }
    if (r.most_amplified_in_crisis) {
      wl(`  ⚡ أكثر تضخماً في الأزمات: ${ENERGY_NAMES_AR[r.most_amplified_in_crisis] ?? r.most_amplified_in_crisis}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runFailurePhysicsEnergy() {
  const t0 = Date.now();
  h2('🔬 Failure Physics — Why Did Energy Fail to Release?');
  wl('  لماذا فشلت الطاقة في الانطلاق؟ كابحات هيكلية وممتصات خفية (~15s)');
  wl();

  const r = await pythonFailurePhysicsEnergy();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  if (r.insight) wl(`  💡 ${r.insight}`);
  wl();

  const fs = r.failure_summary || {};
  wl('  ─ ملخص حالات الفشل عبر السوق ─');
  wl(`  🔒 اضطراب ممتص (لم يتحول لذعر):     ${fs.absorbed_instability ?? 0} حدثة`);
  wl(`  🌀 تقلب مكبوت (لم ينفجر):            ${fs.suppressed_volatility ?? 0} حدثة`);
  wl(`  😮‍💨 إنهاك مستمر (لم يُعطِ ارتداد):   ${fs.exhaustion_persistence ?? 0} حدثة`);
  wl(`  💪 ذعر ممتص (لم يُسبب كارثة):        ${fs.absorbed_panic ?? 0} حدثة`);
  wl();

  // Structural dampeners
  const dampeners = r.structural_dampeners || [];
  if (dampeners.length) {
    wl('  ─ الكابحات الهيكلية (قطاعات تمتص الطاقة) ─');
    wl('  ' + 'القطاع'.padEnd(30) + 'إجمالي الكبح  اضطراب  تقلب  إنهاك  ذعر');
    wl('  ' + '─'.repeat(76));
    for (const d of dampeners) {
      const t = d.types || {};
      wl(`  🛡️  ${d.sector.padEnd(28)} ${String(d.total_failures).padEnd(14)}` +
         `${String(t.n_absorbed_instability??0).padEnd(9)}` +
         `${String(t.n_suppressed_volatility??0).padEnd(7)}` +
         `${String(t.n_exhaustion_persistence??0).padEnd(7)}` +
         `${t.n_absorbed_panic ?? 0}`);
    }
    wl();
  }

  if (r.top_vol_suppressors?.length) {
    wl(`  🌀 أقوى كابحات التقلب: ${r.top_vol_suppressors.join(', ')}`);
  }
  if (r.top_panic_absorbers?.length) {
    wl(`  💪 أقوى ممتصات الذعر: ${r.top_panic_absorbers.join(', ')}`);
  }
  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyInvariants() {
  const t0 = Date.now();
  h2('🔒 Energy Invariants — Universal Laws of Market Thermodynamics');
  wl('  قوانين الطاقة الثابتة — ما الذي يحدث دائماً؟ (~20s)');
  wl();

  const r = await pythonEnergyInvariants();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  عدد الفرضيات المختبرة: ${r.n_invariants ?? '—'}`);
  if (r.best_predictor) wl(`  🏆 أقوى قانون: ${r.best_predictor}`);
  wl();

  const all = r.invariants || [];
  const strong = all.filter(i => i.strength === 'STRONG');
  const moderate = all.filter(i => i.strength === 'MODERATE');
  const weak = all.filter(i => i.strength === 'WEAK');

  if (strong.length) {
    wl('  ─ 🔴 قوانين قوية (P > 65%) ─');
    for (const inv of strong) {
      const bar = '█'.repeat(Math.round(inv.p_confirmed * 20)).padEnd(20);
      wl(`  🔒 ${inv.invariant}`);
      wl(`     P=${fmt(inv.p_confirmed,3)} | n=${inv.n_triggered} مشاهدة | ${bar}`);
    }
    wl();
  }

  if (moderate.length) {
    wl('  ─ 🟡 قوانين معتدلة (45–65%) ─');
    for (const inv of moderate) {
      wl(`  ⚠️  ${inv.invariant.padEnd(56)} P=${fmt(inv.p_confirmed,3)}  n=${inv.n_triggered}`);
    }
    wl();
  }

  if (weak.length) {
    wl('  ─ 🟢 علاقات ضعيفة (< 45%) ─');
    for (const inv of weak) {
      wl(`  •  ${inv.invariant.padEnd(56)} P=${fmt(inv.p_confirmed,3)}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runEnergyFullReport() {
  const t0 = Date.now();
  h2('⚡ ENERGY FULL — Complete Behavioral Thermodynamics Report');
  wl('  التقرير الشامل لتدفق الطاقة السلوكية في سوق EGX (~2min)');
  wl();

  const r = await pythonEnergyFull();
  if (!r.success && !r.energy_now) { wl(warn('خطأ: ' + r.error)); return; }

  const sections = [
    ['energy_now',           '⚡ Energy NOW'],
    ['energy_flow',          '🌊 Energy Flow'],
    ['energy_accumulation',  '🔋 Accumulation'],
    ['energy_transformation','🔄 Transformation'],
    ['energy_persistence',   '⏳ Persistence'],
    ['regime_energy',        '🌍 Regime Energy'],
    ['failure_physics',      '🔬 Failure Physics'],
    ['energy_invariants',    '🔒 Invariants'],
  ];

  for (const [key, label] of sections) {
    const sub = r[key];
    wl(sub && !sub.error ? `  ✅ ${label}` : `  ❌ ${label}: ${sub?.error ?? 'خطأ'}`);
  }
  wl();

  // Synthesis
  h2('🎯 التوليف الاستراتيجي للطاقة');

  const now    = r.energy_now    || {};
  const flow   = r.energy_flow   || {};
  const accum  = r.energy_accumulation || {};
  const inv    = r.energy_invariants   || {};
  const fail   = r.failure_physics     || {};
  const trans  = r.energy_transformation || {};

  if (now.market_state)         wl(`  🌡️  حالة السوق الآنية: ${now.market_state}  (طاقة=${fmt(now.total_system_energy,3)})`);
  if (now.dominant_energy)      wl(`  ⚡ الطاقة المسيطرة: ${ENERGY_NAMES_AR[now.dominant_energy] ?? now.dominant_energy}`);
  if (flow.sources?.length)     wl(`  📡 مصادر الطاقة: ${flow.sources.join(', ')}`);
  if (flow.sinks?.length)       wl(`  📥 مصارف الطاقة: ${flow.sinks.join(', ')}`);
  if (accum.coiled_springs?.length)  wl(`  🌀 نوابض جاهزة: ${accum.coiled_springs.map(c=>c.sector).join(', ')}`);
  if (inv.best_predictor)       wl(`  🔒 أقوى قانون: ${inv.best_predictor}`);
  if (fail.insight)             wl(`  🔬 ${fail.insight}`);

  const topPath = (trans.key_pathways || [])[0];
  if (topPath)                  wl(`  🔄 أشيع تحول: ${topPath.pathway} (P=${fmt(topPath.probability,2)})`);

  wl();
  sep();
  wl(`  ⏱  إجمالي: ${r.elapsed_sec ?? '—'}s | تشغيل: ${Date.now()-t0}ms`);
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// ── Phase 5: Temporal Causality Engine ───────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────

const CAUSAL_ROLE_ICONS = {
  CAUSAL_TRIGGER:      '💥',
  EARLY_PROPAGATOR:    '📡',
  DELAYED_REACTOR:     '⏳',
  FEEDBACK_AMPLIFIER:  '🔊',
  STABILIZATION_NODE:  '⚓',
  TERMINAL_ABSORBER:   '📥',
  NEUTRAL_TRANSMITTER: '↔️',
};

const CAUSAL_ROLE_AR = {
  CAUSAL_TRIGGER:      'زناد سببي',
  EARLY_PROPAGATOR:    'ناقل مبكر',
  DELAYED_REACTOR:     'متفاعل متأخر',
  FEEDBACK_AMPLIFIER:  'مضخم تغذية راجعة',
  STABILIZATION_NODE:  'عقدة استقرار',
  TERMINAL_ABSORBER:   'ماص نهائي',
  NEUTRAL_TRANSMITTER: 'ناقل محايد',
};

const EVENT_ICONS = {
  PANIC_ONSET:       '😱',
  MOMENTUM_SURGE:    '🚀',
  EXHAUSTION_ONSET:  '😮‍💨',
  VOL_COMPRESSION:   '🌀',
  VOL_EXPLOSION:     '💣',
  INSTABILITY_SPIKE: '⚡',
  REVERSAL_ONSET:    '🔄',
  LIQUIDITY_DRAIN:   '🚰',
  TREND_BREAKOUT:    '📈',
  RECOVERY_ONSET:    '🌱',
};

const EVENT_AR = {
  PANIC_ONSET:       'بداية ذعر',
  MOMENTUM_SURGE:    'انطلاق زخم',
  EXHAUSTION_ONSET:  'بداية إنهاك',
  VOL_COMPRESSION:   'ضغط تقلب',
  VOL_EXPLOSION:     'انفجار تقلب',
  INSTABILITY_SPIKE: 'ارتفاع اضطراب',
  REVERSAL_ONSET:    'بداية ارتداد',
  LIQUIDITY_DRAIN:   'نضوب سيولة',
  TREND_BREAKOUT:    'اختراق اتجاه',
  RECOVERY_ONSET:    'بداية انتعاش',
};

function liftBar(v) {
  const filled = Math.min(12, Math.round(((v || 0) - 1.0) * 4));
  const color  = v >= 3.0 ? '🔴' : v >= 2.0 ? '🟠' : v >= 1.5 ? '🟡' : '⚪';
  return color + '█'.repeat(Math.max(0, filled)).padEnd(12) + ` ${fmt(v, 2)}`;
}

// ─────────────────────────────────────────────────────────────────────────

async function runCausalNow() {
  const t0 = Date.now();
  h2('⏱  Causal NOW — Temporal Causality Snapshot');
  wl('  لقطة آنية: ما الأحداث السببية النشطة؟ وماذا ستُسبّب بعد ذلك؟ (~2s)');
  wl();

  const r = await pythonCausalNow();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const maeDict = r.market_active_events || {};
  const nActive = Object.keys(maeDict).length;
  wl(`  📊 الأسهم: ${r.n_stocks ?? '—'}  |  القطاعات: ${r.n_sectors ?? '—'}  |  الأحداث النشطة: ${nActive}`);
  wl(`  🌡️  حالة السوق: ${r.market_state ?? '—'}`);
  wl();

  // Market-wide active events (dict event→count)
  if (nActive) {
    wl('  ─ الأحداث السببية النشطة في السوق ─');
    wl('  ' + 'الحدث'.padEnd(28) + 'عدد الأسهم');
    wl('  ' + '─'.repeat(44));
    const sorted = Object.entries(maeDict).sort((a, b) => b[1] - a[1]);
    for (const [ev, cnt] of sorted) {
      const icon = EVENT_ICONS[ev] ?? '•';
      const name = EVENT_AR[ev] ?? ev;
      wl(`  ${icon} ${name.padEnd(26)} ${cnt}`);
    }
    wl();
  }

  // Market predictions
  const preds = r.market_predictions || [];
  if (preds.length) {
    wl('  ─ التنبؤات السببية ─');
    wl('  ' + 'الحدث المتوقع'.padEnd(28) + 'بعد  احتمال  السبب');
    wl('  ' + '─'.repeat(80));
    for (const p of preds) {
      const icon = EVENT_ICONS[p.event] ?? '•';
      const name = EVENT_AR[p.event] ?? p.event;
      wl(`  ${icon} ${name.padEnd(26)} ${String(p.lag ?? '?').padEnd(5)}بار  ${fmt(p.p ?? p.probability ?? 0, 2).padEnd(8)}${p.reason ?? ''}`);
    }
    wl();
  }

  // Sector overview: top 5 by event count
  const sectorProfiles = r.sector_profiles || {};
  const topSectors = Object.entries(sectorProfiles)
    .sort((a, b) => Object.values(b[1].active_events || {}).reduce((s,v)=>s+v,0) -
                    Object.values(a[1].active_events || {}).reduce((s,v)=>s+v,0))
    .slice(0, 5);
  if (topSectors.length) {
    wl('  ─ أكثر القطاعات نشاطاً سببياً ─');
    for (const [sec, d] of topSectors) {
      const evList = Object.entries(d.active_events || {}).sort((a,b)=>b[1]-a[1]).slice(0,3)
        .map(([e, pct]) => `${EVENT_AR[e] ?? e}(${fmt(pct*100,0)}%)`).join(', ');
      wl(`  • ${sec.padEnd(30)} ${d.state ?? '—'}  |  ${evList}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runCausalChains() {
  const t0 = Date.now();
  h2('🔗 Causal Chains — Multi-Step Behavioral Cascades');
  wl('  اكتشاف السلاسل السببية: A → B → C مع احتمالية شرطية (~20s)');
  wl();

  const r = await pythonCausalChains();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  إجمالي الأزواج السببية: ${r.n_causal_pairs ?? 0}`);
  wl();

  // Top pairwise causal links
  const pairs = r.top_causal_pairs || [];
  if (pairs.length) {
    wl('  ─ أقوى الروابط السببية الثنائية ─');
    wl('  ' + 'من'.padEnd(22) + '→  إلى'.padEnd(22) + 'تأخر  رافعة  P(ب|أ)  عينات');
    wl('  ' + '─'.repeat(84));
    for (const p of pairs.slice(0, 12)) {
      const fromIcon = EVENT_ICONS[p.from] ?? '•';
      const toIcon   = EVENT_ICONS[p.to]   ?? '•';
      const fromName = EVENT_AR[p.from] ?? p.from;
      const toName   = EVENT_AR[p.to]   ?? p.to;
      const strength = p.strength === 'STRONG' ? '🔴' : p.strength === 'MODERATE' ? '🟠' : '🟡';
      wl(`  ${strength}${fromIcon}${fromName.padEnd(18)}→  ${toIcon}${toName.padEnd(20)}` +
         `${String(p.peak_lag ?? '?').padEnd(6)}${fmt(p.lift ?? 0, 2).padEnd(8)}${fmt(p.p_cond ?? 0, 3).padEnd(7)}${p.n_events ?? 0}`);
    }
    wl();
  }

  // Confirmed loops (A→B→C cycles)
  const cloops = r.confirmed_loops || [];
  if (cloops.length) {
    wl('  ─ الدورات الحلقية المؤكدة (A→B→C) ─');
    for (const c of cloops.slice(0, 5)) {
      wl(`  🔁 ${c.loop}  (P_chain=${fmt(c.p_chain ?? 0, 3)})`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runFeedbackLoops() {
  const t0 = Date.now();
  h2('🔁 Feedback Loops — Amplifying vs Dampening Cycles');
  wl('  كشف حلقات التغذية الراجعة: A → B → A — مُضخّمة أم مُخمِّدة؟ (~15s)');
  wl();

  const r = await pythonFeedbackLoops();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  إجمالي الحلقات: ${r.n_loops ?? 0}  (مُضخّمة: ${r.n_amplifying ?? 0}, مُخمِّدة: ${r.n_dampening ?? 0})`);
  wl();

  const loops = r.loops || [];
  if (loops.length) {
    wl('  ─ أقوى حلقات التغذية الراجعة ─');
    wl('  ' + 'الحلقة'.padEnd(44) + 'النوع'.padEnd(14) + 'كسب   تأخر الدورة');
    wl('  ' + '─'.repeat(78));
    for (const lp of loops.slice(0, 10)) {
      const aName = EVENT_AR[lp.ev_A] ?? lp.ev_A;
      const bName = EVENT_AR[lp.ev_B] ?? lp.ev_B;
      const loopStr = `${aName} ⇄ ${bName}`;
      const typeIcon = lp.loop_type === 'EXPLOSIVE'   ? '💥' :
                       lp.loop_type === 'AMPLIFYING'  ? '🔊' :
                       lp.loop_type === 'REINFORCING' ? '🔁' :
                       lp.loop_type === 'DAMPENING'   ? '🔇' : '↔️';
      wl(`  ${loopStr.padEnd(44)} ${typeIcon} ${(lp.loop_type ?? '').padEnd(12)} ${fmt(lp.loop_gain ?? 0, 2).padEnd(7)}${lp.total_cycle_lag ?? '?'}`);
    }
    wl();
  }

  // Self-reinforcing loops
  const selfLoops = r.self_loops || [];
  if (selfLoops.length) {
    wl('  ─ الأحداث ذاتية التعزيز ─');
    for (const sl of selfLoops.slice(0, 5)) {
      const name = EVENT_AR[sl.event] ?? sl.event;
      const icon = EVENT_ICONS[sl.event] ?? '•';
      wl(`  ${icon} ${name.padEnd(28)} (رافعة=${fmt(sl.lift ?? 0, 2)}, تأخر=${sl.lag ?? '?'})`);
    }
    wl();
  }

  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runTemporalMemory() {
  const t0 = Date.now();
  h2('⏳ Temporal Memory — How Long Do Causal Effects Persist?');
  wl('  كم تدوم الآثار السببية؟ متى تضمحل؟ نصف عمر التأثير السببي (~15s)');
  wl();

  const r = await pythonTemporalMemory();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  أزواج سببية مختبرة: ${r.n_causal_pairs ?? 0}`);
  wl();

  const memory = r.event_memory || {};
  if (Object.keys(memory).length) {
    wl('  ─ نصف عمر التأثير السببي لكل حدث ─');
    wl('  ' + 'الحدث'.padEnd(26) + 'نصف العمر  ذروة التأثير');
    wl('  ' + '─'.repeat(60));
    const sorted = Object.entries(memory).sort((a, b) => (b[1].half_life ?? 0) - (a[1].half_life ?? 0));
    for (const [ev, d] of sorted) {
      const icon = EVENT_ICONS[ev] ?? '•';
      const name = EVENT_AR[ev] ?? ev;
      wl(`  ${icon} ${name.padEnd(24)} ${String(fmt(d.half_life ?? 0, 1)).padEnd(12)}${d.peak_lag ?? '?'}`);
    }
    wl();
  } else {
    wl('  ⚠️  لا توجد أزواج سببية كافية لتقدير الذاكرة الزمنية');
    wl();
  }

  // Top decay curves from Python
  const decay = r.top_decay_curves || {};
  if (Object.keys(decay).length) {
    wl('  ─ منحنيات الاضمحلال ─');
    for (const [pair, d] of Object.entries(decay).slice(0, 5)) {
      wl(`  📉 ${pair}: نصف عمر=${fmt(d.half_life ?? 0, 1)} | ذروة تأخر=${d.peak_lag ?? '?'}`);
    }
    wl();
  }

  if (r.longest_memory  && r.longest_memory  !== '—') wl(`  📅 أطول ذاكرة: ${r.longest_memory}`);
  if (r.shortest_memory && r.shortest_memory !== '—') wl(`  ⚡ أقصر ذاكرة: ${r.shortest_memory}`);

  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runSectorCausalRoles() {
  const t0 = Date.now();
  h2('🏭 Sector Causal Roles — Who Triggers, Who Reacts?');
  wl('  تصنيف القطاعات: من يُحرّك السوق ومن يتبع؟ (~15s)');
  wl();

  const r = await pythonSectorCausalRoles();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const roles = r.sector_roles || {};
  if (Object.keys(roles).length) {
    wl('  ─ الأدوار السببية للقطاعات ─');
    wl('  ' + 'القطاع'.padEnd(30) + 'الدور'.padEnd(24) + 'متوسط التأخر  كثافة الأحداث');
    wl('  ' + '─'.repeat(80));
    const sorted = Object.entries(roles).sort((a, b) => (a[1].avg_lag_vs_market ?? 0) - (b[1].avg_lag_vs_market ?? 0));
    for (const [sec, d] of sorted) {
      const icon = CAUSAL_ROLE_ICONS[d.role] ?? '•';
      const roleAr = CAUSAL_ROLE_AR[d.role] ?? d.role;
      wl(`  ${sec.padEnd(30)} ${icon} ${roleAr.padEnd(22)} ${fmt(d.avg_lag_vs_market ?? 0, 1).padEnd(14)}${fmt(d.event_density ?? 0, 1)}`);
    }
    wl();
  }

  // Derive summaries from sector_roles
  const triggers   = Object.entries(roles).filter(([,d]) => d.role === 'CAUSAL_TRIGGER').map(([s]) => s);
  const absorbers  = Object.entries(roles).filter(([,d]) => d.role === 'TERMINAL_ABSORBER').map(([s]) => s);
  const amplifiers = Object.entries(roles).filter(([,d]) => d.role === 'FEEDBACK_AMPLIFIER').map(([s]) => s);
  if (triggers.length)   wl(`  💥 الزوانيد السببية: ${triggers.join(', ')}`);
  if (absorbers.length)  wl(`  📥 الماصّات النهائية: ${absorbers.join(', ')}`);
  if (amplifiers.length) wl(`  🔊 المضخّمات: ${amplifiers.join(', ')}`);

  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runCausalFailure() {
  const t0 = Date.now();
  h2('🔬 Causal Failure — Why Did Chains Break?');
  wl('  لماذا فشلت التسلسلات السببية؟ ما الذي قاطعها؟ (~15s)');
  wl();

  const r = await pythonCausalFailure();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const avgSuccessRate = 1 - (r.avg_failure_rate ?? 0);
  wl(`  السلاسل المختبرة: ${r.n_chains_tested ?? 0}  |  متوسط معدل النجاح: ${fmt(avgSuccessRate * 100, 1)}%`);
  wl();

  // chain_analysis is a dict keyed by "FROM→TO"
  const chainAnalysis = r.chain_analysis || {};
  const chainEntries = Object.entries(chainAnalysis);
  if (chainEntries.length) {
    wl('  ─ تحليل فشل السلاسل السببية ─');
    // Sort by p_failure descending
    chainEntries.sort((a, b) => (b[1].p_failure ?? 0) - (a[1].p_failure ?? 0));
    for (const [key, f] of chainEntries.slice(0, 8)) {
      const label = f.label ?? key;
      const pSuccess = (f.p_success ?? 0) * 100;
      const pFail    = (f.p_failure ?? 0) * 100;
      wl(`  ${pFail < 50 ? '✅' : pFail < 80 ? '⚠️ ' : '❌'} ${label}`);
      wl(`      نجاح: ${fmt(pSuccess, 1)}%  |  فشل: ${fmt(pFail, 1)}%  |  عينات: ${f.n_triggered ?? 0}`);
      if (f.failure_mechanism) wl(`      🔍 آلية الفشل: ${f.failure_mechanism}`);
      // Top differentiators
      const diffs = Object.entries(f.differentiators || {}).sort((a,b) => Math.abs(b[1].delta) - Math.abs(a[1].delta)).slice(0, 2);
      for (const [feat, d] of diffs) {
        const direction = d.delta > 0 ? 'مرتفع' : 'منخفض';
        wl(`      📊 ${feat} ${direction} عند النجاح (Δ=${fmt(d.delta, 3)})`);
      }
      wl();
    }
  }

  // Rejected chains
  const rejected = r.rejected_chains || [];
  if (rejected.length) {
    wl('  ─ السلاسل التي لم تتأكد ─');
    for (const rc of rejected.slice(0, 5)) {
      wl(`  ✗ ${rc.label ?? rc.chain}  (رافعة=${fmt(rc.lift ?? 0, 2)}, P=${fmt(rc.p_confirmed ?? 0, 3)})`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runRegimeCausality() {
  const t0 = Date.now();
  h2('🌍 Regime Causality — How Causal Structure Changes per Regime');
  wl('  كيف تتغير الهياكل السببية في BULL / STRESS / CRISIS؟ (~20s)');
  wl();

  const r = await pythonRegimeCausality();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  ريجيمات مكتشفة: ${r.n_regimes ?? 0}`);
  wl();

  const regimes = r.regime_matrices || {};
  const REGIME_ICONS = { BULL: '🟢', STRESS: '🟠', CRISIS: '🔴', CALM: '⚪', NEUTRAL: '⚪' };
  const regimeDist = r.regime_distribution || {};

  for (const [regime, data] of Object.entries(regimes)) {
    const icon = REGIME_ICONS[regime] ?? '•';
    const nDays = regimeDist[regime] ?? 0;
    wl(`  ${icon} ═══ ريجيم ${regime} (${nDays} يوم) ═══`);
    const topPairs = (data.top_pairs || []).slice(0, 5);
    for (const p of topPairs) {
      wl(`     🔗 ${p.chain}  (رافعة=${fmt(p.lift ?? 0, 2)}, تأخر=${p.lag ?? '?'}, P=${fmt(p.p_cond ?? 0, 3)})`);
    }
    wl();
  }

  // Universal chains (present in all regimes)
  const universal = r.universal_chains || [];
  if (universal.length) {
    wl('  ─ الروابط الشاملة (موجودة في كل الريجيمات) ─');
    for (const uc of universal.slice(0, 6)) {
      wl(`  🌍 ${uc.chain}  (متوسط رافعة=${fmt(uc.avg_lift ?? 0, 2)}, عدد ريجيمات=${uc.n_regimes ?? 0})`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runCausalInvariants() {
  const t0 = Date.now();
  h2('🔒 Causal Invariants — Universal Laws That Always Hold');
  wl('  القوانين السببية الثابتة عبر الزمن والريجيمات (~15s)');
  wl();

  const r = await pythonCausalInvariants();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  قوانين مختبرة: ${r.n_tested ?? 0}  |  مؤكدة: ${r.n_confirmed ?? 0}`);
  wl();

  const invariants = r.invariants || [];
  if (invariants.length) {
    wl('  ─ القوانين الثابتة المؤكدة ─');
    wl('  ' + 'القانون'.padEnd(50) + 'P(ب|أ)  رافعة  عينات');
    wl('  ' + '─'.repeat(78));
    for (const inv of invariants) {
      const fromName = EVENT_AR[inv.from] ?? inv.from;
      const toName   = EVENT_AR[inv.to]   ?? inv.to;
      const law = `${fromName} → ${toName} (تأخر ${inv.max_lag ?? '?'})`;
      wl(`  🔒 ${law.padEnd(48)} ${fmt(inv.p_confirmed ?? 0, 3).padEnd(8)}${fmt(inv.lift ?? 0, 2).padEnd(7)}${inv.n_triggered ?? 0}`);
    }
    wl();
  }

  // Confirmed loops
  const cloops = r.confirmed_loops || [];
  if (cloops.length) {
    wl('  ─ الدورات الحلقية الثابتة ─');
    for (const c of cloops.slice(0, 5)) {
      wl(`  🔁 ${c.loop}  (P=${fmt(c.p_chain ?? 0, 3)})`);
    }
    wl();
  }

  // Rejected (informative)
  const rejected = r.rejected_chains || [];
  if (rejected.length) {
    wl('  ─ فرضيات لم تتأكد من البيانات ─');
    for (const rc of rejected.slice(0, 4)) {
      wl(`  ✗ ${rc.label ?? rc.chain}  (رافعة=${fmt(rc.lift ?? 0, 2)} — تحت العتبة)`);
    }
    wl();
  }

  if (r.strongest_law) wl(`  🏆 أقوى قانون ثابت: ${r.strongest_law}`);

  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runCausalFullReport() {
  const t0 = Date.now();
  h2('⏱  CAUSAL FULL — Complete Temporal Causality Report');
  wl('  التقرير الشامل لهيكل السببية الزمنية في سوق EGX (~2min)');
  wl();

  const r = await pythonCausalFull();
  if (!r.success && !r.causal_now) { wl(warn('خطأ: ' + r.error)); return; }

  const sections = [
    ['causal_now',          '⏱  Causal NOW'],
    ['causal_chains',       '🔗 Causal Chains'],
    ['feedback_loops',      '🔁 Feedback Loops'],
    ['temporal_memory',     '⏳ Temporal Memory'],
    ['sector_causal_roles', '🏭 Sector Roles'],
    ['causal_failure',      '🔬 Causal Failure'],
    ['regime_causality',    '🌍 Regime Causality'],
    ['causal_invariants',   '🔒 Invariants'],
  ];

  for (const [key, label] of sections) {
    const sub = r[key];
    wl(sub && !sub.error ? `  ✅ ${label}` : `  ❌ ${label}: ${sub?.error ?? 'خطأ'}`);
  }
  wl();

  // Synthesis
  h2('🎯 التوليف الاستراتيجي للسببية');

  const now    = r.causal_now    || {};
  const chains = r.causal_chains || {};
  const loops  = r.feedback_loops || {};
  const mem    = r.temporal_memory || {};
  const roles  = r.sector_causal_roles || {};
  const inv    = r.causal_invariants || {};
  const fail   = r.causal_failure || {};

  const maeNow = now.market_active_events || {};
  if (Object.keys(maeNow).length) {
    const topEvents = Object.entries(maeNow).sort((a,b)=>b[1]-a[1]).slice(0,3)
      .map(([e]) => EVENT_AR[e] ?? e).join(', ');
    wl(`  ⚡ الأحداث النشطة الآن: ${topEvents}`);
  }
  const predsNow = now.market_predictions || [];
  if (predsNow.length) {
    const top = predsNow[0];
    wl(`  🎯 أقوى تنبؤ: ${EVENT_AR[top.event] ?? top.event} بعد ${top.lag ?? '?'} بار (P=${fmt(top.p ?? top.probability ?? 0, 2)})`);
  }
  const topPairs = chains.top_causal_pairs || [];
  if (topPairs.length) {
    const tp = topPairs[0];
    wl(`  🔗 أقوى رابط سببي: ${EVENT_AR[tp.from] ?? tp.from} → ${EVENT_AR[tp.to] ?? tp.to} (رافعة=${fmt(tp.lift ?? 0, 2)})`);
  }
  if (loops.n_amplifying) wl(`  🔊 حلقات مضخّمة: ${loops.n_amplifying}`);
  if (mem.longest_memory && mem.longest_memory !== '—') wl(`  📅 أطول ذاكرة: ${mem.longest_memory}`);
  const triggerSectors = Object.entries(roles.sector_roles || {})
    .filter(([,d]) => d.role === 'CAUSAL_TRIGGER').map(([s]) => s);
  if (triggerSectors.length) wl(`  💥 أكثر القطاعات تأثيراً: ${triggerSectors.join(', ')}`);
  if (inv.strongest_law) wl(`  🔒 القانون الثابت الأقوى: ${inv.strongest_law}`);
  const failRate = fail.avg_failure_rate;
  if (failRate !== undefined) wl(`  🔬 متوسط معدل نجاح السلاسل: ${fmt((1-failRate)*100, 1)}%`);

  wl();
  sep();
  wl(`  ⏱  إجمالي: ${r.elapsed_sec ?? '—'}s | تشغيل: ${Date.now()-t0}ms`);
  sep();
}

// ─────────────────────────────────────────────────────────────────────────

// ═════════════════════════════════════════════════════════════════════════
// ── Phase 6: Adaptive Market Decision Engine ──────────────────────────────
// ═════════════════════════════════════════════════════════════════════════

const DECISION_ICONS = {
  HIGH_CONVICTION: '🟢',
  CONDITIONAL:     '🔵',
  FRAGILE:         '🟡',
  TRANSITIONAL:    '🟠',
  UNSTABLE:        '🔴',
  AVOID:           '⛔',
};

const DECISION_AR = {
  HIGH_CONVICTION: 'قناعة عالية',
  CONDITIONAL:     'مشروط',
  FRAGILE:         'هش',
  TRANSITIONAL:    'انتقالي',
  UNSTABLE:        'غير مستقر',
  AVOID:           'تجنّب',
};

const REGIME_ICON = { BULL:'🟢', STRESS:'🟠', CRISIS:'🔴', CALM:'⚪', NEUTRAL:'⚫' };

function ebpBar(v) {
  const filled = Math.min(12, Math.max(0, Math.round((v + 0.2) * 20)));
  const color  = v >= 0.35 ? '🟢' : v >= 0.20 ? '🔵' : v >= 0.10 ? '🟡' : '🔴';
  return color + '█'.repeat(filled).padEnd(12) + ` ${fmt(v, 3)}`;
}

function uncBar(v) {
  const filled = Math.min(12, Math.round((v || 0) * 12));
  const color  = v >= 0.65 ? '🔴' : v >= 0.45 ? '🟠' : '🟢';
  return color + '█'.repeat(filled).padEnd(12) + ` ${fmt(v, 3)}`;
}

// ─────────────────────────────────────────────────────────────────────────

async function runDecisionNow() {
  const t0 = Date.now();
  h2('🧠 Decision NOW — Adaptive Decision State');
  wl('  لقطة آنية لقرارات السوق — أين تتركّز الفرص؟ (~2s)');
  wl();

  const r = await pythonDecisionNow();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const regIcon = REGIME_ICON[r.market_regime] ?? '•';
  const decIcon = r.market_decision === 'PROCEED' ? '✅' : r.market_decision === 'CAUTIOUS' ? '⚠️ ' : '🛑';
  wl(`  ${regIcon} الريجيم: ${r.market_regime ?? '—'}   ${decIcon} قرار السوق: ${r.market_decision ?? '—'}`);
  wl(`  📊 متوسط EBP للسوق: ${ebpBar(r.avg_market_ebp ?? 0)}   الأسهم: ${r.n_stocks ?? 0}`);
  wl();

  // Decision distribution
  const dist = r.decision_distribution || {};
  const distOrder = ['HIGH_CONVICTION','CONDITIONAL','FRAGILE','TRANSITIONAL','UNSTABLE','AVOID'];
  wl('  ─ توزيع حالات القرار ─');
  for (const state of distOrder) {
    const n = dist[state] ?? 0;
    if (!n) continue;
    const icon = DECISION_ICONS[state] ?? '•';
    const bar  = '█'.repeat(Math.round(n / (r.n_stocks || 1) * 40)).padEnd(40);
    wl(`  ${icon} ${DECISION_AR[state].padEnd(16)} ${String(n).padEnd(6)} ${bar}`);
  }
  wl();

  // Top opportunities
  const opps = r.top_opportunities || [];
  if (opps.length) {
    wl('  ─ أفضل الفرص الحالية ─');
    wl('  ' + 'رمز'.padEnd(10) + 'القطاع'.padEnd(28) + 'الحالة'.padEnd(18) + 'EBP    P(نجاح) عدم يقين  تنبؤ');
    wl('  ' + '─'.repeat(100));
    for (const o of opps.slice(0, 15)) {
      const icon = DECISION_ICONS[o.decision] ?? '•';
      const pred = o.top_prediction ? `← ${EVENT_AR?.[o.top_prediction] ?? o.top_prediction}` : '';
      wl(`  ${icon} ${o.symbol.padEnd(8)} ${(o.sector ?? '').padEnd(26)} ${DECISION_AR[o.decision].padEnd(16)} ` +
         `${fmt(o.ebp, 3).padEnd(7)}${fmt(o.p_success, 2).padEnd(9)}${fmt(o.uncertainty, 3).padEnd(12)}${pred}`);
    }
    wl();
  }

  if ((r.avoid_list || []).length) {
    wl(`  ⛔ تجنّب الآن: ${r.avoid_list.join(', ')}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runOpportunityScan() {
  const t0 = Date.now();
  h2('🔍 Opportunity Scan — Full EBP Decomposition');
  wl('  مسح شامل للفرص مع تفكيك EBP الكامل (~20s)');
  wl();

  const r = await pythonOpportunityScan();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const regIcon = REGIME_ICON[r.market_regime] ?? '•';
  wl(`  ${regIcon} الريجيم: ${r.market_regime ?? '—'}   تم مسح: ${r.n_scanned ?? 0} سهم`);
  wl(`  🟢 عالي القناعة: ${r.n_high_conviction ?? 0}   🔵 مشروط: ${r.n_conditional ?? 0}   🟡 هش: ${r.n_fragile ?? 0}   ⛔ تجنّب: ${r.n_avoid ?? 0}`);
  wl();

  // High conviction + Conditional table
  const allBest = [...(r.high_conviction || []).slice(0,8), ...(r.conditional || []).slice(0,7)];
  if (allBest.length) {
    wl('  ─ أفضل الفرص بالتفصيل ─');
    wl('  ' + 'رمز'.padEnd(8) + 'حالة'.padEnd(10) + 'Score  EBP    P(+)  ثقة   استقرار  مخاطر  قلق   كيلي%  تنبؤ');
    wl('  ' + '─'.repeat(108));
    for (const o of allBest) {
      const icon = DECISION_ICONS[o.decision] ?? '•';
      const pred = o.predicted_next ? `${EVENT_AR?.[o.predicted_next] ?? o.predicted_next}+${o.pred_lag}` : '—';
      wl(`  ${icon}${o.symbol.padEnd(7)} ${DECISION_AR[o.decision].padEnd(9)} ` +
         `${fmt(o.opp_score,3).padEnd(7)}${fmt(o.ebp,3).padEnd(7)}` +
         `${fmt(o.p_success,2).padEnd(6)}${fmt(o.causal_confidence,2).padEnd(7)}` +
         `${fmt(o.structural_stability,2).padEnd(10)}${fmt(o.instability_risk,2).padEnd(7)}` +
         `${fmt(o.uncertainty,3).padEnd(6)}${String(o.kelly_pct ?? 0).padEnd(7)}${pred}`);
    }
    wl();
  }

  // Sector concentration of opportunities
  const secConc = r.sector_concentration || {};
  if (Object.keys(secConc).length) {
    wl('  ─ تركّز الفرص بالقطاع ─');
    const sorted = Object.entries(secConc).sort((a,b)=>b[1]-a[1]);
    for (const [sec, n] of sorted.slice(0, 8)) {
      wl(`  • ${sec.padEnd(32)} ${n} فرصة`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runPortfolioOptimize() {
  const t0 = Date.now();
  h2('💼 Portfolio Optimize — Kelly Sizing + Diversification');
  wl('  تحسين المحفظة: حجم كيلي + قيود التنويع + وزن الريجيم (~20s)');
  wl();

  const r = await pythonPortfolioOptimize();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const regIcon = REGIME_ICON[r.market_regime] ?? '•';
  wl(`  ${regIcon} الريجيم: ${r.market_regime ?? '—'}`);
  wl(`  💡 ${r.regime_advice ?? '—'}`);
  wl();
  wl(`  📊 مراكز: ${r.n_positions ?? 0}   مُخصَّص: ${fmt(r.total_allocated_pct ?? 0, 1)}%   نقدية: ${fmt(r.cash_reserve_pct ?? 0, 1)}%`);
  wl(`  📈 متوسط EBP المحفظة: ${fmt(r.avg_portfolio_ebp ?? 0, 4)}   متوسط P(نجاح): ${fmt(r.avg_p_success ?? 0, 2)}`);
  wl(`  🔀 درجة التنويع: ${fmt(r.diversification_score ?? 0, 3)}`);
  wl();

  const portfolio = r.portfolio || [];
  if (portfolio.length) {
    wl('  ─ تركيبة المحفظة الموصى بها ─');
    wl('  ' + 'رمز'.padEnd(10) + 'قطاع'.padEnd(28) + 'حالة'.padEnd(16) + 'تخصيص%  EBP    P(+)  كيلي%  قلق');
    wl('  ' + '─'.repeat(96));
    for (const p of portfolio) {
      const icon = DECISION_ICONS[p.decision] ?? '•';
      wl(`  ${icon} ${p.symbol.padEnd(8)} ${(p.sector ?? '').padEnd(26)} ${DECISION_AR[p.decision].padEnd(14)} ` +
         `${fmt(p.allocation_pct ?? 0, 1).padEnd(9)}${fmt(p.ebp, 3).padEnd(7)}` +
         `${fmt(p.p_success ?? 0, 2).padEnd(6)}${fmt(p.kelly_raw_pct ?? 0, 1).padEnd(7)}${fmt(p.uncertainty ?? 0, 3)}`);
    }
    wl();
  }

  const secAlloc = r.sector_allocation || {};
  if (Object.keys(secAlloc).length) {
    wl('  ─ التخصيص بالقطاع ─');
    const sorted = Object.entries(secAlloc).sort((a,b)=>b[1]-a[1]);
    for (const [sec, pct] of sorted) {
      const bar = '█'.repeat(Math.round(pct / 5)).padEnd(14);
      wl(`  ${sec.padEnd(32)} ${bar} ${fmt(pct, 1)}%`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runUncertaintyMap() {
  const t0 = Date.now();
  h2('🌫️  Uncertainty Map — Confidence Decay & Structural Instability');
  wl('  خريطة عدم اليقين: أين الأسهم والقطاعات الأكثر وضوحاً؟ (~10s)');
  wl();

  const r = await pythonUncertaintyMap();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const lvlIcon = r.market_unc_level === 'HIGH' ? '🔴' : r.market_unc_level === 'MEDIUM' ? '🟠' : '🟢';
  wl(`  ${lvlIcon} عدم يقين السوق الكلي: ${uncBar(r.market_uncertainty ?? 0)}  (${r.market_unc_level ?? '—'})`);
  wl(`  📊 أسهم محللة: ${r.n_stocks ?? 0}`);
  wl();

  // Sector uncertainty table
  const secUnc = r.sector_uncertainty || {};
  if (Object.keys(secUnc).length) {
    wl('  ─ عدم اليقين بالقطاع ─');
    wl('  ' + 'القطاع'.padEnd(32) + 'المستوى'.padEnd(10) + 'متوسط   انحراف  أسهم');
    wl('  ' + '─'.repeat(70));
    const sorted = Object.entries(secUnc).sort((a,b)=>a[1].avg_uncertainty - b[1].avg_uncertainty);
    for (const [sec, d] of sorted) {
      const lvl = d.level === 'HIGH' ? '🔴' : d.level === 'MEDIUM' ? '🟠' : '🟢';
      wl(`  ${sec.padEnd(32)} ${lvl} ${d.level.padEnd(8)} ${fmt(d.avg_uncertainty,3).padEnd(8)}${fmt(d.std,3).padEnd(8)}${d.n}`);
    }
    wl();
  }

  if ((r.most_certain_sectors || []).length)   wl(`  🟢 أوضح القطاعات (أقل قلقاً):  ${r.most_certain_sectors.join(', ')}`);
  if ((r.most_uncertain_sectors || []).length) wl(`  🔴 أكثر القطاعات ضبابية: ${r.most_uncertain_sectors.join(', ')}`);
  wl();

  // Top 5 clearest stocks
  const clear = r.lowest_uncertainty_stocks || [];
  if (clear.length) {
    wl('  ─ أوضح 5 أسهم (أقل عدم يقين) ─');
    for (const s of clear.slice(0, 5)) {
      wl(`  🟢 ${s.symbol?.padEnd(10) ?? '—'} ${(s.sector ?? '').padEnd(28)} عدم يقين=${fmt(s.uncertainty, 3)}`);
    }
    wl();
  }

  // Top 5 most uncertain
  const murky = r.highest_uncertainty_stocks || [];
  if (murky.length) {
    wl('  ─ أكثر 5 أسهم ضبابية (تجنّب) ─');
    for (const s of murky.slice(0, 5)) {
      wl(`  🔴 ${s.symbol?.padEnd(10) ?? '—'} ${(s.sector ?? '').padEnd(28)} عدم يقين=${fmt(s.uncertainty, 3)}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runRegimeDecisions() {
  const t0 = Date.now();
  h2('🌍 Regime Decisions — Policy Under Current Market Regime');
  wl('  السياسة القرارية المناسبة للريجيم الحالي (~15s)');
  wl();

  const r = await pythonRegimeDecisions();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const pol   = r.current_policy || {};
  const regime = r.current_regime ?? '—';
  const icon   = REGIME_ICON[regime] ?? '•';

  wl(`  ${icon} الريجيم الحالي: ${regime}`);
  wl(`  📋 ${pol.description ?? '—'}`);
  wl(`  📐 التحجيم: ${pol.sizing_guideline ?? '—'}`);
  wl();
  wl(`  ┌─────────────────────────────────────────────┐`);
  wl(`  │  حد EBP الأدنى:     ${String(fmt(pol.min_ebp ?? 0, 2)).padEnd(28)}│`);
  wl(`  │  أقصى تعرض:         ${String(fmt((pol.max_exposure ?? 0) * 100, 0) + '%').padEnd(28)}│`);
  wl(`  │  حد عدم اليقين:     ${String(fmt(pol.max_uncertainty ?? 0, 2)).padEnd(28)}│`);
  wl(`  │  مضاعف الحجم:       ${String(fmt(pol.position_mult ?? 0, 2)).padEnd(28)}│`);
  wl(`  └─────────────────────────────────────────────┘`);
  wl();

  if ((pol.preferred || []).length)     wl(`  ✅ الأحداث المفضّلة: ${pol.preferred.map(e => EVENT_AR?.[e] ?? e).join(', ')}`);
  if ((pol.avoid_states || []).length)  wl(`  ⛔ الحالات المحظورة: ${pol.avoid_states.map(s => DECISION_AR[s] ?? s).join(', ')}`);
  wl();

  const qualifying = r.qualifying_stocks || [];
  if (qualifying.length) {
    wl(`  ─ أسهم مؤهلة تحت السياسة الحالية (${r.n_qualifying ?? 0}) ─`);
    wl('  ' + 'رمز'.padEnd(10) + 'قطاع'.padEnd(28) + 'حالة'.padEnd(16) + 'EBP    قلق');
    wl('  ' + '─'.repeat(72));
    for (const s of qualifying.slice(0, 12)) {
      const dIcon = DECISION_ICONS[s.decision] ?? '•';
      wl(`  ${dIcon} ${s.symbol.padEnd(8)} ${(s.sector ?? '').padEnd(26)} ${DECISION_AR[s.decision].padEnd(14)} ` +
         `${fmt(s.ebp, 3).padEnd(7)}${fmt(s.uncertainty, 3)}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runInactionAnalysis() {
  const t0 = Date.now();
  h2('🛑 Inaction Intelligence — When NOT to Act');
  wl('  هل يجب التصرف الآن؟ — كشف الضجيج والتعارض والاضطراب (~5s)');
  wl();

  const r = await pythonInactionAnalysis();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  const recIcons = {
    WAIT:                 '🛑',
    CAUTIOUS:             '⚠️ ',
    PROCEED:              '✅',
    PROCEED_WITH_CAUTION: '🟡',
  };
  const icon = recIcons[r.recommendation] ?? '•';
  wl(`  ${icon} التوصية: ${r.recommendation ?? '—'}   (ثقة: ${fmt(r.confidence ?? 0, 0)*100}%)`);
  wl(`  📊 نسبة اللاتصرف: ${fmt((r.inaction_score ?? 0) * 100, 0)}%`);
  wl(`  ${REGIME_ICON[r.market_regime] ?? '•'} الريجيم: ${r.market_regime ?? '—'}`);
  wl();

  // Market energy snapshot
  const mkt = r.market_energy || {};
  const energyOrder = ['MOMENTUM_ENERGY','PANIC_ENERGY','EXHAUSTION_ENERGY','VOLATILITY_ENERGY','LIQUIDITY_STRESS'];
  wl('  ─ طاقة السوق الكلية ─');
  for (const k of energyOrder) {
    if (mkt[k] === undefined) continue;
    wl(`  ${ENERGY_ICONS?.[k] ?? '•'} ${(ENERGY_NAMES_AR?.[k] ?? k).padEnd(28)} ${energyBar(mkt[k])}`);
  }
  wl();

  const wait = r.reasons_to_wait || [];
  if (wait.length) {
    wl('  ─ أسباب التوقف ─');
    for (const w of wait) {
      wl(`  🛑 ${w.signal}`);
      wl(`     ${w.message}`);
    }
    wl();
  }

  const proceed = r.reasons_to_proceed || [];
  if (proceed.length) {
    wl('  ─ أسباب المضي قُدُماً ─');
    for (const p of proceed) {
      wl(`  ✅ ${p.signal}`);
      wl(`     ${p.message}`);
    }
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runDecisionFailureAnalysis() {
  const t0 = Date.now();
  h2('🔬 Decision Failure — Why Did Good Setups Fail?');
  wl('  لماذا فشلت الإشارات الجيدة؟ تشريح الفشل القراري (~20s)');
  wl();

  const r = await pythonDecisionFailure();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  إجمالي النقاط: ${r.n_analyzed ?? 0}   نجاح: ${r.n_successes ?? 0}   فشل: ${r.n_failures ?? 0}`);
  wl(`  📊 معدل النجاح الفعلي: ${fmt((r.success_rate ?? 0) * 100, 1)}%`);
  wl();

  // Side-by-side profiles
  const fp = r.failure_profile || {};
  const sp = r.success_profile || {};
  wl('  ─ مقارنة بصمة الفشل vs النجاح ─');
  wl('  ' + 'المتغير'.padEnd(24) + 'عند الفشل'.padEnd(14) + 'عند النجاح');
  wl('  ' + '─'.repeat(52));
  const profileKeys = [
    ['panic',     'PANIC_ENERGY'],
    ['liq',       'LIQUIDITY_STRESS'],
    ['exhaustion','EXHAUSTION_ENERGY'],
    ['instability','INSTABILITY_RISK'],
    ['uncertainty','UNCERTAINTY'],
    ['ebp',       'EBP المتوقع'],
    ['actual_ret','العائد الفعلي'],
  ];
  for (const [k, label] of profileKeys) {
    const fv = fp[k] ?? 0;
    const sv = sp[k] ?? 0;
    const diff = fv - sv;
    const arrow = diff > 0.02 ? '↑ مرتفع عند الفشل' : diff < -0.02 ? '↓ منخفض عند الفشل' : '≈';
    wl(`  ${label.padEnd(22)} ${fmt(fv, 4).padEnd(14)}${fmt(sv, 4).padEnd(12)}${arrow}`);
  }
  wl();

  // Discriminants
  const discs = r.discriminants || [];
  if (discs.length) {
    wl('  ─ أقوى المحددات التمييزية ─');
    for (const d of discs.slice(0, 4)) {
      wl(`  🔍 ${d.feature.padEnd(22)} Δ=${fmt(d.delta, 3)}  ${d.direction}`);
    }
    wl();
  }

  // Failure types
  const types = r.failure_types || [];
  if (types.length) {
    wl('  ─ أنواع الفشل ─');
    for (const t of types.filter(x => x.n > 0)) {
      wl(`  ⚠️  ${t.type.padEnd(28)} (${t.n} حالة)  ${t.desc}`);
    }
    wl();
  }

  // Recommendations
  const recs = r.recommendations || [];
  if (recs.length) {
    wl('  ─ توصيات التحسين ─');
    for (const rec of recs) wl(`  💡 ${rec}`);
    wl();
  }

  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runAdaptiveThresholds() {
  const t0 = Date.now();
  h2('⚙️  Adaptive Thresholds — Self-Calibrated Decision Cutoffs');
  wl('  معايير القرار المُعايَرة تلقائياً من بيانات السوق (~20s)');
  wl();

  const r = await pythonAdaptiveThresholds();
  if (!r.success) { wl(warn('خطأ: ' + r.error)); return; }

  wl(`  أفضل عتبة EBP مكتشفة: ${fmt(r.best_ebp_threshold ?? 0, 2)}`);
  wl(`  أفضل حد عدم يقين: ${fmt(r.best_unc_threshold ?? 0, 2)}`);
  wl();

  // EBP calibration table
  const ebpCal = r.ebp_calibration || {};
  if (Object.keys(ebpCal).length) {
    wl('  ─ معايرة عتبة EBP (Win Rate عند كل حد) ─');
    wl('  ' + 'عتبة EBP'.padEnd(12) + 'Win Rate  إشارات  Score');
    wl('  ' + '─'.repeat(44));
    for (const [thresh, d] of Object.entries(ebpCal).sort((a,b)=>parseFloat(a[0])-parseFloat(b[0]))) {
      const best = parseFloat(thresh) === (r.best_ebp_threshold ?? 0);
      const star = best ? ' ◄ أفضل' : '';
      wl(`  ${String(thresh).padEnd(12)}${fmt(d.win_rate * 100, 1).padEnd(10)}%  ${String(d.n_signals).padEnd(9)}${fmt(d.score ?? 0, 2)}${star}`);
    }
    wl();
  }

  // Recommended thresholds
  const rec = r.recommended || {};
  if (Object.keys(rec).length) {
    wl('  ─ الإعدادات الموصى بها ─');
    wl(`  🟢 EBP عالي القناعة:  ${fmt(rec.ebp_high_conviction ?? 0, 2)}`);
    wl(`  🔵 EBP مشروط:         ${fmt(rec.ebp_conditional ?? 0, 2)}`);
    wl(`  🟡 EBP هش:            ${fmt(rec.ebp_fragile ?? 0, 2)}`);
    wl(`  🌫️  حد عدم اليقين:    ${fmt(rec.max_uncertainty ?? 0, 2)}`);
    wl(`  📐 كسر كيلي:          ${fmt(rec.kelly_fraction ?? 0, 2)}`);
    wl(`  📊 أقصى حجم مركز:     ${fmt(rec.max_position_pct ?? 0, 1)}%`);
    wl(`  🏭 أقصى تعرض قطاعي:  ${fmt(rec.max_sector_pct ?? 0, 1)}%`);
    wl();
  }

  // Compare current vs recommended
  const cur = r.current || {};
  wl('  ─ الحالي vs الموصى به ─');
  wl('  ' + 'المعيار'.padEnd(26) + 'الحالي'.padEnd(12) + 'الموصى به');
  wl('  ' + '─'.repeat(52));
  const compRows = [
    ['EBP_HIGH_CONVICTION', rec.ebp_high_conviction],
    ['EBP_CONDITIONAL',     rec.ebp_conditional],
    ['EBP_FRAGILE',         rec.ebp_fragile],
    ['UNCERT_MEDIUM',       rec.max_uncertainty],
  ];
  for (const [k, recVal] of compRows) {
    const curVal = cur[k] ?? 0;
    const delta  = (recVal ?? 0) - curVal;
    const arrow  = delta > 0.01 ? '▲ أعلى' : delta < -0.01 ? '▼ أخفض' : '≈ مماثل';
    wl(`  ${k.padEnd(24)} ${fmt(curVal, 2).padEnd(12)}${fmt(recVal ?? 0, 2).padEnd(10)} ${arrow}`);
  }

  wl();
  wl(`  ⏱  ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────

async function runDecisionFullReport() {
  const t0 = Date.now();
  h2('🧠 DECISION FULL — Complete Adaptive Decision Intelligence');
  wl('  التقرير القراري الشامل — كل أبعاد الذكاء القراري (~2min)');
  wl();

  const r = await pythonDecisionFull();
  if (!r.success && !r.decision_now) { wl(warn('خطأ: ' + r.error)); return; }

  const sections = [
    ['decision_now',     '🧠 Decision NOW'],
    ['opportunity_scan', '🔍 Opportunity Scan'],
    ['portfolio',        '💼 Portfolio'],
    ['uncertainty',      '🌫️  Uncertainty Map'],
    ['regime_decisions', '🌍 Regime Decisions'],
    ['inaction',         '🛑 Inaction Analysis'],
    ['failure_analysis', '🔬 Failure Analysis'],
    ['thresholds',       '⚙️  Adaptive Thresholds'],
  ];
  for (const [key, label] of sections) {
    const sub = r[key];
    wl(sub && !sub.error ? `  ✅ ${label}` : `  ❌ ${label}: ${sub?.error ?? 'خطأ'}`);
  }
  wl();

  // ── Master synthesis ──
  h2('🎯 التوليف القراري الاستراتيجي');

  const now   = r.decision_now    || {};
  const scan  = r.opportunity_scan|| {};
  const port  = r.portfolio       || {};
  const unc   = r.uncertainty     || {};
  const reg   = r.regime_decisions|| {};
  const inact = r.inaction        || {};
  const fail  = r.failure_analysis|| {};
  const thr   = r.thresholds      || {};

  const regIcon = REGIME_ICON[now.market_regime ?? reg.current_regime] ?? '•';
  const decIcon = now.market_decision === 'PROCEED' ? '✅' :
                  now.market_decision === 'CAUTIOUS' ? '⚠️ ' : '🛑';

  wl(`  ${regIcon} الريجيم: ${now.market_regime ?? reg.current_regime ?? '—'}`);
  wl(`  ${decIcon} القرار الكلي: ${now.market_decision ?? inact.recommendation ?? '—'}`);
  wl(`  📊 متوسط EBP: ${fmt(now.avg_market_ebp ?? 0, 4)}   عدم يقين السوق: ${fmt(unc.market_uncertainty ?? 0, 3)}`);
  wl();

  // Top 5 opportunities
  const topOpps = (scan.high_conviction || []).slice(0, 3).concat((scan.conditional || []).slice(0, 2));
  if (topOpps.length) {
    wl('  🏆 أفضل 5 فرص:');
    for (const o of topOpps) {
      wl(`    ${DECISION_ICONS[o.decision] ?? '•'} ${o.symbol}  EBP=${fmt(o.ebp,3)}  قلق=${fmt(o.uncertainty,3)}`);
    }
    wl();
  }

  if (port.n_positions)          wl(`  💼 حجم المحفظة المقترح: ${port.n_positions} مركز  (${fmt(port.total_allocated_pct ?? 0, 1)}% مُخصَّص)`);
  if (port.diversification_score)wl(`  🔀 درجة التنويع: ${fmt(port.diversification_score, 3)}`);

  // Inaction verdict
  const inactIcon = inact.recommendation === 'WAIT' ? '🛑' :
                    inact.recommendation === 'PROCEED' ? '✅' : '⚠️ ';
  if (inact.recommendation) wl(`  ${inactIcon} توصية اللاتصرف: ${inact.recommendation}  (أسباب التوقف: ${(inact.reasons_to_wait || []).length})`);

  // Failure insight
  if (fail.success_rate !== undefined) wl(`  🔬 معدل نجاح السيناريوهات التاريخية: ${fmt(fail.success_rate * 100, 1)}%`);

  // Best threshold
  if (thr.best_ebp_threshold) wl(`  ⚙️  أفضل عتبة EBP من البيانات: ${fmt(thr.best_ebp_threshold, 2)}`);

  wl();
  sep();
  wl(`  ⏱  إجمالي: ${r.elapsed_sec ?? '—'}s | تشغيل: ${Date.now()-t0}ms`);
  sep();
}

// ══════════════════════════════════════════════════════════════════════════
// Phase 7 — Self-Evolving Market Intelligence Engine
// ══════════════════════════════════════════════════════════════════════════

const HEALTH_ICONS  = { HEALTHY:'🟢', STABLE:'🔵', DEGRADING:'🟡', CRITICAL:'🔴' };
const TRUST_ICONS   = { TRUST:'✅', REDUCE_CONFIDENCE:'⚠️ ', REBUILD:'🔴', INVALIDATE:'☠️ ' };
const HYPO_ICONS    = { SECTOR_DIVERGENCE_FORCE:'🌐', LAG_DRIFT:'⏳', DISTRIBUTION_ANOMALY:'📊',
                        REGIME_SPLIT:'🔀', DEFAULT:'💡' };
const ARCH_ICONS    = { CURRENT_5STATE:'🏛️ ', RISK_ON_OFF_3:'⚖️ ', VOLATILITY_REGIME:'🌊',
                        MOMENTUM_REGIME:'🚀' };

function healthBar(s) {
  const v  = Math.max(0, Math.min(1, s));
  const n  = Math.round(v * 10);
  const ic = s >= 0.75 ? '🟢' : s >= 0.55 ? '🔵' : s >= 0.35 ? '🟡' : '🔴';
  return `${ic}${'█'.repeat(n)}${'░'.repeat(10-n)} ${(v*100).toFixed(0).padStart(3)}%`;
}
function driftBar(d) {
  const v = Math.max(0, Math.min(1, d));
  const n = Math.round(v * 10);
  const ic = d < 0.05 ? '🟢' : d < 0.12 ? '🟡' : '🔴';
  return `${ic}${'█'.repeat(n)}${'░'.repeat(10-n)} ${d.toFixed(3)}`;
}

// 1. meta_status
async function runMetaStatus() {
  const t0 = Date.now();
  wl(h2('🔬 Meta Status — System Health Dashboard'));
  wl('صحة كل مرحلة من المراحل الست، ومستوى الثقة الكلي (~7s)\n');
  const r = await pythonMetaStatus();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const TI = TRUST_ICONS[r.trust_level] || '❓';
  wl(`  ${TI} الثقة الكلية: ${r.trust_level}   🌍 الريجيم: ${r.market_regime}   📊 أسهم: ${r.n_symbols}`);
  wl(`  📈 الصحة العامة: ${healthBar(r.overall_health)}\n`);

  wl('  ─ صحة المراحل ─');
  const pLabels = {
    phase1_latent:'المرحلة 1 — القوى الكامنة',
    phase2_forces:'المرحلة 2 — حقول القوى  ',
    phase3_propagation:'المرحلة 3 — الانتشار   ',
    phase4_energy:'المرحلة 4 — طاقة السوق  ',
    phase5_causal:'المرحلة 5 — السببية     ',
    phase6_decision:'المرحلة 6 — القرارات    ',
  };
  for (const [k, info] of Object.entries(r.phase_health || {})) {
    const label = pLabels[k] || k;
    wl(`  ${HEALTH_ICONS[info.status]||'⚪'} ${label}  ${healthBar(info.score).padEnd(28)}  ${info.detail}`);
  }

  if (r.flags && r.flags[0] !== 'All phases within acceptable bounds') {
    wl('\n' + '  ⚠️  تنبيهات:');
    r.flags.forEach(f => wl(`    • ${f}`));
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 2. decay_scan
async function runDecayScan() {
  const t0 = Date.now();
  wl(h2('📉 Decay Scan — Model Decay Detection'));
  wl('كشف تآكل النماذج: انحلال الألفا، انهيار القوانين السببية، انجراف الريجيم (~8s)\n');
  const r = await pythonDecayScan();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl('  ─ انحلال الألفا (الارتباط مؤخراً vs تاريخياً) ─');
  const indLabels = { rsi_proxy:'RSI (مؤشر الزخم)  ', vol_ratio:'نسبة الحجم      ', mom5:'زخم 5 أيام     ' };
  for (const [k, v] of Object.entries(r.alpha_decay || {})) {
    const label = indLabels[k] || k;
    const ic = v.status === 'STABLE' ? '🟢' : v.status === 'DEGRADING' ? '🟡' : '🔴';
    wl(`  ${ic} ${label}  حديث=${v.recent_corr.toFixed(4)}  تاريخي=${v.hist_corr.toFixed(4)}  انجراف: ${driftBar(v.drift)}  ${v.status}`);
  }

  wl('\n' + '  ─ التحقق من القوانين السببية (Phase 5) ─');
  wl(`  ${'القانون'.padEnd(34)} الأصلي  مؤخراً   ${' '.repeat(6)}الحالة`);
  wl('  ' + '─'.repeat(80));
  for (const inv of (r.invariant_validation || [])) {
    const ic = inv.status === 'VALID' ? '🟢' : inv.status === 'DEGRADED' ? '🟡' : '🔴';
    const decay = inv.decay_pct > 0 ? ` (↓${inv.decay_pct.toFixed(0)}%)` : '';
    wl(`  ${ic} ${inv.invariant.padEnd(32)} ${String(inv.original_lift).padStart(6)}  ${String(inv.recent_lift).padStart(7)}${decay.padEnd(10)}  ${inv.status}  n=${inv.recent_n}`);
  }

  const rs = r.regime_stability || {};
  const rsIc = rs.status === 'STABLE' ? '🟢' : '🟡';
  wl(`\n  ${rsIc} انجراف الريجيم: ${rs.drift?.toFixed(3)} (${rs.status})  — حديث entropy=${rs.recent_entropy?.toFixed(3)}, تاريخي=${rs.hist_entropy?.toFixed(3)}`);

  const score = r.overall_decay_score || 0;
  const scoreIc = score < 0.15 ? '🟢' : score < 0.30 ? '🟡' : '🔴';
  wl(`\n  ${scoreIc} درجة التآكل الكلية: ${(score*100).toFixed(1)+'%'}`);
  if (r.decayed_components && r.decayed_components[0] !== 'No significant decay detected') {
    r.decayed_components.forEach(d => wl(`    ⚠️  ${d}`));
  } else {
    wl('    ✅ لا تآكل ملحوظ');
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 3. hypothesis_gen
async function runHypothesisGen() {
  const t0 = Date.now();
  wl(h2('🌊 Hypothesis Generation — New Market Hypotheses'));
  wl('توليد فرضيات جديدة تلقائياً وتحدي الافتراضات القائمة (~10s)\n');
  const r = await pythonHypothesisGen();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  💡 فرضيات جديدة: ${r.total_hypotheses}   ⚔️  تحديات للافتراضات: ${r.total_challenges}\n`);

  if (r.new_hypotheses?.length) {
    wl('  ─ الفرضيات الجديدة ─');
    for (const h of r.new_hypotheses) {
      const ic = HYPO_ICONS[h.type] || HYPO_ICONS.DEFAULT;
      wl(`\n  ${ic} ${h.id} — ${h.type}`);
      wl(`     📝 ${h.description}`);
      wl(`     📊 الدليل: ${h.evidence}`);
      wl(`     🎯 ثقة: ${(h.confidence*100).toFixed(0)}%   💊 توصية: ${h.recommendation}`);
    }
  }

  if (r.challenged_assumptions?.length) {
    wl('\n' + '  ─ افتراضات مطعون فيها ─');
    for (const c of r.challenged_assumptions) {
      const ic = c.severity === 'HIGH' ? '🔴' : '🟡';
      wl(`\n  ${ic} الافتراض: ${c.assumption}`);
      wl(`     التحدي: ${c.challenge}`);
      wl(`     الدليل: ${c.evidence}   شدة: ${c.severity}`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 4. arch_compete
async function runArchCompete() {
  const t0 = Date.now();
  wl(h2('🏭 Architecture Competition — Which Regime Classifier Wins?'));
  wl('تنافس معماريات تصنيف الريجيم: أيها أفضل تجانساً وفصلاً؟ (~9s)\n');
  const r = await pythonArchCompete();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const wi = r.winner === 'CURRENT_5STATE' ? '✅' : '⚠️ ';
  wl(`  ${wi} الفائز: ${r.winner}   المعمارية الحالية: رتبة #${r.incumbent_rank}`);
  wl(`  💡 ${r.recommendation}\n`);

  wl(`  ${'رتبة'.padEnd(5)} ${'المعمارية'.padEnd(24)} ${'تجانس'.padEnd(10)} ${'فصل'.padEnd(10)} ${'توازن'.padEnd(10)} ${'إجمالي'.padEnd(10)}`);
  wl('  ' + '─'.repeat(72));
  for (const a of (r.architectures || [])) {
    const ic = ARCH_ICONS[a.name] || '⚙️ ';
    const tag = a.name === 'CURRENT_5STATE' ? ' ← حالي' : '';
    wl(`  #${a.rank}  ${ic} ${a.name.padEnd(20)}  ${String(a.homogeneity?.toFixed(3)||'?').padEnd(9)} ${String(a.separation?.toFixed(3)||'?').padEnd(9)} ${String(a.balance?.toFixed(3)||'?').padEnd(9)} ${a.overall_score?.toFixed(3||'?')}${tag}`);
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 5. taxonomy_audit
async function runTaxonomyAudit() {
  const t0 = Date.now();
  wl(h2('🏷️  Taxonomy Audit — Are Our Categories Still Valid?'));
  wl('مراجعة التصنيفات: معدلات الأحداث، ترابط القوى، إمكانية الوصول لحالات القرار (~6s)\n');
  const r = await pythonTaxonomyAudit();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl('  ─ تدقيق الأحداث السببية ─');
  wl(`  ${'الحدث'.padEnd(24)} ${'عدد'.padEnd(8)} ${'معدل'.padEnd(10)} ${'حالة'.padEnd(12)} ${'توصية'}`);
  wl('  ' + '─'.repeat(70));
  for (const [ev, info] of Object.entries(r.event_audit || {})) {
    const ic = info.status === 'FREQUENT' ? '🔴' : info.status === 'ACTIVE' ? '🟢' : info.status === 'RARE' ? '🟡' : '⛔';
    wl(`  ${ic} ${ev.padEnd(22)} ${String(info.count).padEnd(7)} ${(info.fire_rate*100).toFixed(1).padStart(5)}%    ${info.status.padEnd(11)} ${info.recommendation}`);
  }

  if (r.force_correlations?.length) {
    wl('\n' + '  ─ ترابط القوى ─');
    for (const fc of r.force_correlations) {
      const ic = fc.recommendation === 'CONSIDER_MERGE' ? '🔴' : fc.recommendation === 'WATCH' ? '🟡' : '🟢';
      wl(`  ${ic} ${fc.pair.padEnd(36)} ارتباط=${fc.corr.toFixed(3)}   ${fc.recommendation}`);
    }
  }

  wl('\n' + '  ─ إمكانية الوصول لحالات القرار ─');
  for (const [st, info] of Object.entries(r.decision_state_audit || {})) {
    const ic = info.rate < 0.02 ? '⚠️ ' : info.rate > 0.5 ? '🔴' : '🟢';
    wl(`  ${ic} ${st.padEnd(20)} ${(info.rate*100).toFixed(1).padStart(6)}%   ${info.recommendation}`);
  }

  if (r.redesign_suggestions?.length && r.redesign_suggestions[0] !== 'Taxonomy healthy — no urgent redesign needed') {
    wl('\n' + '  ─ اقتراحات إعادة التصميم ─');
    r.redesign_suggestions.forEach(s => wl(`  💡 ${s}`));
  } else {
    wl('\n  ✅ التصنيفات سليمة — لا حاجة لإعادة تصميم عاجلة');
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 6. regime_intelligence
async function runRegimeIntelligencePh7() {
  const t0 = Date.now();
  wl(h2('🌍 Regime Intelligence — Phase Reliability by Regime'));
  wl('أي المراحل موثوقة في كل ريجيم؟ وأيها يحتاج تحفظاً؟ (~10s)\n');
  const r = await pythonRegimeIntelligence();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  🎯 الريجيم الحالي: ${r.current_regime}`);
  const curRel = r.current_regime_reliability || {};
  if (Object.keys(curRel).length) {
    wl(`\n  ─ موثوقية المراحل في الريجيم الحالي (${r.current_regime}) ─`);
    for (const [ph, score] of Object.entries(curRel)) {
      if (score === null) continue;
      wl(`    ${healthBar(score)}  ${ph}`);
    }
  }

  wl('\n' + '  ─ تغطية البيانات بالريجيم ─');
  for (const [regime, n] of Object.entries(r.regime_data_coverage || {})) {
    const ic = n >= 30 ? '🟢' : n >= 15 ? '🟡' : '🔴';
    wl(`  ${ic} ${regime.padEnd(10)}  ${n} نافذة تحليل`);
  }

  if (r.low_coverage_warnings && r.low_coverage_warnings[0] !== 'All regimes have adequate coverage') {
    wl('\n' + '  ⚠️  تحذيرات التغطية:');
    r.low_coverage_warnings.forEach(w => wl(`    • ${w}`));
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 7. evolution_memory
async function runEvolutionMemory() {
  const t0 = Date.now();
  wl(h2('📚 Evolution Memory — Architectural History Log'));
  wl('سجل تطور النظام: ما الذي تغيّر، ومتى، ولماذا (~0.1s)\n');
  const r = await pythonEvolutionMemory();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  📁 ملف السجل: ${r.log_path}`);
  wl(`  📋 إجمالي المدخلات: ${r.total_entries}   التحسن الصافي: ${r.net_improvement > 0 ? '🟢' : r.net_improvement < 0 ? '🔴' : '⚪'} ${r.net_improvement?.toFixed(4)}`);

  if (r.architecture_history?.length) {
    wl('\n' + '  ─ تاريخ المعماريات ─');
    for (const h of r.architecture_history) {
      wl(`  📌 ${h.date || '?'}: ${h.action || '?'}  ${h.note || ''}`);
    }
  } else {
    wl('\n  📝 لا تاريخ معماري مسجّل بعد — أول تشغيل');
  }

  if (r.recent_entries?.length) {
    wl('\n' + '  ─ آخر 6 مدخلات تلقائية ─');
    for (const e of r.recent_entries) {
      wl(`  📅 ${e.date}  vol_ratio=${e.metric_value}  RSI=${e.avg_rsi||'?'}  ${e.note||''}`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 8. meta_decision
async function runMetaDecision() {
  const t0 = Date.now();
  wl(h2('🧠 Meta-Decision — Trust, Rebuild, or Invalidate?'));
  wl('القرار المعرفي: هل نثق بالنماذج الحالية أم نعيد بناءها؟ (~5s)\n');
  const r = await pythonMetaDecision();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const TI = TRUST_ICONS[r.decision] || '❓';
  const conf = r.confidence || 0;
  wl(`  ${TI} القرار: ${r.decision}   ثقة: ${(conf*100).toFixed(1)}%   ريجيم: ${r.market_regime}`);
  wl(`  🔄 المراجعة القادمة: بعد ${r.next_review_bars} شمعة\n`);

  wl('  ─ المقاييس ─');
  const sc = r.scores || {};
  wl(`  📉 انجراف الألفا:    ${driftBar(sc.alpha_drift||0)}`);
  wl(`  🌍 ثبات الريجيم:    ${healthBar(sc.regime_stability||0)}`);
  wl(`  📊 صحة الأحداث:     ${healthBar(sc.event_health||0)}`);
  wl(`  🔗 صحة القوانين:    ${healthBar(sc.invariant_validity||0)}  (VC→VX lift=${sc.vc_vx_lift||'?'})`);

  wl('\n' + '  ─ المبررات ─');
  (r.rationale || []).forEach(rat => wl(`  • ${rat}`));
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 9. self_rewrite
async function runSelfRewrite() {
  const t0 = Date.now();
  wl(h2('✍️  Self-Rewrite — Architecture Redesign Proposals'));
  wl('مقترحات إعادة تصميم النظام: دمج / تقسيم / أبعاد جديدة / إعادة معايرة (~8s)\n');
  const r = await pythonSelfRewrite();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  🧬 الجيل الحالي: ${r.current_generation}\n`);

  if (r.redesign_proposals?.length) {
    wl('  ─ مقترحات التعديل ─');
    for (const p of r.redesign_proposals) {
      const ic = p.action === 'ADAPT' ? '⚙️ ' : p.action === 'RECALIBRATE' ? '🔧' : '📐';
      wl(`  ${ic} ${p.component}  [${p.action}]`);
      wl(`     الحالي: ${p.current}  →  المقترح: ${p.proposed}  (ثقة ${(p.confidence*100).toFixed(0)}%)`);
      wl(`     ${p.rationale}`);
    }
  }

  if (r.split_candidates?.length) {
    wl('\n' + '  ─ مرشحات التقسيم ─');
    for (const s of r.split_candidates) {
      wl(`  🔀 تقسيم ${s.state} → ${s.proposed.join(' | ')}   ثقة=${((s.confidence||0)*100).toFixed(0)}%`);
      wl(`     ${s.rationale}`);
    }
  }

  if (r.merge_candidates?.length) {
    wl('\n' + '  ─ مرشحات الدمج ─');
    for (const m of r.merge_candidates) {
      wl(`  🔗 دمج ${m.states.join(' + ')}   ثقة=${((m.confidence||0)*100).toFixed(0)}%`);
      wl(`     ${m.rationale}`);
    }
  }

  if (r.new_dimensions?.length) {
    wl('\n' + '  ─ أبعاد جديدة مقترحة ─');
    for (const d of r.new_dimensions) {
      wl(`  ➕ ${d.dimension}   ثقة=${((d.confidence||0)*100).toFixed(0)}%`);
      wl(`     ${d.description}`);
      wl(`     الدليل: ${d.evidence}`);
    }
  }

  wl('\n' + '  ─ أولويات العمل ─');
  (r.priority_actions || []).forEach((a,i) => wl(`  ${i+1}. ${a}`));
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// 10. evolution_full
async function runEvolutionFullReport() {
  const t0 = Date.now();
  wl(h2('🧬 EVOLUTION FULL — Complete Self-Evolving Intelligence Report'));
  wl('التقرير الشامل للتطور الذاتي للذكاء: كل أبعاد التقييم والتطور (~60s)\n');
  const r = await pythonEvolutionFull();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  // Show status checklist
  const components = r.components || {};
  const steps = [
    ['meta_status',         '🔬 Meta Status'],
    ['decay_scan',          '📉 Decay Scan'],
    ['hypothesis_gen',      '🌊 Hypothesis Gen'],
    ['arch_compete',        '🏭 Architecture Competition'],
    ['taxonomy_audit',      '🏷️  Taxonomy Audit'],
    ['regime_intelligence', '🌍 Regime Intelligence'],
    ['evolution_memory',    '📚 Evolution Memory'],
    ['meta_decision',       '🧠 Meta Decision'],
    ['self_rewrite',        '✍️  Self Rewrite'],
  ];
  for (const [k, label] of steps) {
    const ic = components[k]?.error ? '❌' : '✅';
    wl(`  ${ic} ${label}`);
  }

  const s = r.synthesis || {};
  wl(`\n─────────────────────────────────────────────────────────────────`);
  wl(`  ▶ 🎯 التوليف التطوري الاستراتيجي`);
  wl(`─────────────────────────────────────────────────────────────────`);
  wl(`  🌍 الريجيم: ${s.market_regime}   ${TRUST_ICONS[s.trust_level]||'?'} القرار: ${s.trust_level}`);
  wl(`  📈 الصحة الكلية: ${healthBar(s.overall_health||0)}   📉 تآكل: ${((s.decay_score||0)*100).toFixed(1)}%`);
  wl(`  💡 فرضيات جديدة: ${s.n_hypotheses}   ⚔️  تحديات: ${s.n_challenges}`);
  wl(`  🏆 المعمارية الفائزة: ${s.winning_arch}   (الحالية رتبة #${s.incumbent_rank})`);
  wl(`  🔧 مقترحات إعادة التصميم: ${s.n_proposals}`);
  wl('\n  🎯 أولويات العمل الفورية:');
  (s.priority_actions || []).forEach((a,i) => wl(`    ${i+1}. ${a}`));
  wl(`\n═══════════════════════════════════════════════════════════════`);
  wl(`  ⏱  إجمالي: ${s.total_elapsed_sec?.toFixed(1)||'?'}s | تشغيل: ${Date.now()-t0}ms`);
}

// ═══════════════════════════════════════════════════════════════════════════
//  PHASE 8 — WORLD–MARKET COUPLING ENGINE
// ═══════════════════════════════════════════════════════════════════════════

const COUPLING_ICONS = {
  STABLE:         '🟢', MILD:       '🟡', ELEVATED: '🟠', ACUTE:       '🔴',
  NORMAL:         '🟢', TIGHT:      '🟡', CRISIS:   '🔴', ABUNDANT:    '💧',
  INDEPENDENT:    '🟢', MODERATE:   '🟡', HIGH_CONTAGION: '🟠', CRISIS_SYNC: '🔴',
  STABLE_REGIME:  '🟢', SHIFTING:   '🟡', DECOUPLING: '🔴',
  EASY_MONEY:     '💰', STABLE_MAC: '🟢', INFLATION_PRESSURE: '🌡️ ',
  POLICY_TIGHTENING: '🏦', LIQUIDITY_CRISIS: '🌊', EXTERNAL_SHOCK: '⚡',
  BULL: '📈', BEAR: '📉', NEUTRAL: '⚖️ ',
};

function coupIcon(key)  { return COUPLING_ICONS[key] || '⚪'; }
function coupBar(score, width = 10) {
  const s = Math.max(0, Math.min(1, score || 0));
  const filled = Math.round(s * width);
  const ic = s > 0.65 ? '🟢' : s > 0.35 ? '🟡' : '🔴';
  return ic + '█'.repeat(filled) + '░'.repeat(width - filled) + ` ${(s * 100).toFixed(0)}%`;
}
function intensBar(score, width = 10) {
  const s = Math.max(0, Math.min(1, score || 0));
  const filled = Math.round(s * width);
  const ic = s > 0.6 ? '🔴' : s > 0.35 ? '🟡' : '🟢';
  return ic + '█'.repeat(filled) + '░'.repeat(width - filled) + ` ${(s * 100).toFixed(0)}%`;
}
function forceBar(score, width = 10) {
  const s = Math.max(0, Math.min(1, score || 0));
  const filled = Math.round(s * width);
  const ic = s > 0.6 ? '⚡' : s > 0.35 ? '🟡' : '🟢';
  return ic + '█'.repeat(filled) + '░'.repeat(width - filled) + ` ${(s * 100).toFixed(0)}%`;
}

// ── 1. coupling_now ──────────────────────────────────────────────────────
async function runWorldCouplingNow() {
  const t0 = Date.now();
  h2('🌍 World–Market Coupling — Live Snapshot');
  wl('ربط السوق بالعالم: حالة كل بُعد خارجي الآن (~5s)\n');
  const r = await pythonCouplingNow();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const mkt = r.regime || 'UNKNOWN';
  const fi  = r.forces || {};
  wl(`  ${coupIcon(mkt)} ريجيم السوق: ${mkt}   🔗 شدة الارتباط: ${intensBar(r.coupling_intensity)}   🏆 القوة المهيمنة: ${r.dominant_force || '—'}`);
  wl(`  📊 أسهم محللة: ${r.n_stocks}   📅 أيام: ${r.n_days}`);

  wl('\n  ─ أبعاد الربط الخارجي ─');
  const dims = [
    ['FX_STRESS',           'ضغط العملة (EGP)',       r.fx?.score,        r.fx?.state],
    ['LIQUIDITY_TIGHTNESS', 'شُح السيولة',             1-(r.liquidity?.score||0.5), r.liquidity?.state],
    ['CONTAGION',           'عدوى السوق',              r.contagion?.score, r.contagion?.state],
    ['INFLATION',           'ضغط التضخم',              fi.INFLATION,       r.inflation?.state],
    ['POLICY',              'حساسية السياسة النقدية',  fi.POLICY,          r.policy?.state],
  ];
  for (const [key, label, score, state] of dims) {
    wl(`  ${forceBar(score||0).padEnd(32)}  ${label.padEnd(26)}  ${state||'—'}`);
  }

  if (r.liquidity) {
    wl(`\n  💧 السيولة: vol_ratio=${(r.liquidity.vol_ratio||0).toFixed(3)}   مشاركة=${((r.liquidity.breadth||0)*100).toFixed(0)}%`);
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 2. fx_impact ─────────────────────────────────────────────────────────
async function runWorldFxImpact() {
  const t0 = Date.now();
  h2('💱 FX Impact — Currency Stress & Sector Sensitivity');
  wl('تأثير ضغط العملة على قطاعات السوق وسلوك الذعر والانتشار (~6s)\n');
  const r = await pythonFxImpact();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const ic = coupIcon(r.fx_state);
  wl(`  ${ic} حالة EGP: ${r.fx_state}   درجة الضغط: ${forceBar(r.fx_stress_score)}`);

  const m = r.behavioural_modifiers || {};
  wl('\n  ─ تأثيرات ضغط العملة على سلوك السوق ─');
  wl(`  ⚡ تضخيم الذعر:         ${(m.panic_amplification||1).toFixed(2)}×  ${m.panic_amplification>1.2?'(مرتفع)':'(طبيعي)'}`);
  wl(`  🌊 سرعة الانتشار:       ${(m.propagation_speed_modifier||1).toFixed(2)}×`);
  wl(`  💨 معدل تسرب الطاقة:    ${(m.energy_leak_rate||1).toFixed(2)}×`);
  wl(`  ⏱  إزاحة الفجوة السببية: ${m.causal_lag_shift_bars>=0?'+':''}${m.causal_lag_shift_bars||0} شمعة`);

  if (r.sector_impacts?.length) {
    wl('\n  ─ أداء القطاعات الحساسة للعملة (10 أيام vs السوق) ─');
    wl(`  ${'القطاع'.padEnd(38)}  ${'Δ vs سوق'.padStart(10)}  ${'الاتجاه'}`);
    wl(`  ${'─'.repeat(65)}`);
    for (const s of r.sector_impacts.slice(0, 8)) {
      const dir = s.direction === 'UNDERPERFORM' ? '🔴' : s.direction === 'OUTPERFORM' ? '🟢' : '⚪';
      wl(`  ${(s.sector||'—').padEnd(38)}  ${((s.vs_market||0)*100).toFixed(3).padStart(8)}%  ${dir} ${s.direction}`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 3. macro_regimes ─────────────────────────────────────────────────────
async function runWorldMacroRegimes() {
  const t0 = Date.now();
  h2('🏦 Macro Regimes — External Environment Classification');
  wl('تصنيف البيئة الاقتصادية الكلية وتأثيرها على كل أبعاد السوق (~6s)\n');
  const r = await pythonWorldMacroRegimes();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const regime = r.current_macro_regime || 'UNKNOWN';
  const regimeIcons = {
    EXTERNAL_SHOCK: '⚡', LIQUIDITY_CRISIS: '🌊', POLICY_TIGHTENING: '🏦',
    INFLATION_PRESSURE: '🌡️ ', EASY_MONEY: '💰', STABLE: '🟢',
  };
  wl(`  ${regimeIcons[regime]||'⚪'} الريجيم الكلي الحالي: ${regime}`);

  const e = r.behavioural_effects || {};
  wl('\n  ─ تأثيرات الريجيم على آليات السوق ─');
  wl(`  🎯 مضاعف احتمال الذعر:    ${(e.panic_prob_mult||1).toFixed(2)}×`);
  wl(`  🌊 سرعة الانتشار:         ${(e.prop_speed||1).toFixed(2)}×`);
  wl(`  ⏱  إزاحة الفجوة السببية:  ${(e.causal_lag_shift||0)>=0?'+':''}${e.causal_lag_shift||0} شمعة`);
  wl(`  🔋 ثبات الطاقة:           ${(e.energy_persist||1).toFixed(2)}×`);
  wl(`  ⚠️  عتبة عدم الاستقرار:   ${((e.instability_threshold||0.6)*100).toFixed(0)}%`);

  const sigs = r.supporting_signals || {};
  wl('\n  ─ الإشارات الداعمة ─');
  const sigList = [
    ['ضغط العملة',  sigs.fx_stress?.score,  sigs.fx_stress?.state],
    ['السيولة',     sigs.liquidity?.score,  sigs.liquidity?.state],
    ['التضخم',      sigs.inflation?.score,  sigs.inflation?.state],
    ['السياسة',     sigs.policy?.score,     sigs.policy?.state],
    ['العدوى',      sigs.contagion?.score,  sigs.contagion?.state],
  ];
  for (const [label, score, state] of sigList) {
    wl(`  ${(label||'').padEnd(18)}  ${coupBar(score||0).padEnd(30)}  ${state||'—'}`);
  }

  if (r.regime_history_60d) {
    wl('\n  ─ توزيع الريجيم الكلي (آخر 60 يوم) ─');
    for (const [k, v] of Object.entries(r.regime_history_60d).sort((a,b)=>b[1]-a[1])) {
      wl(`  ${(regimeIcons[k]||'⚪')} ${k.padEnd(22)}  ${v} يوم`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 4. liquidity_cycle ───────────────────────────────────────────────────
async function runWorldLiquidityCycle() {
  const t0 = Date.now();
  h2('💧 Liquidity Cycle — Market Plumbing & Flow Dynamics');
  wl('دورة السيولة: التأثير على سرعة الانتشار وتعافي الطاقة والفجوات السببية (~5s)\n');
  const r = await pythonLiquidityCycle();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const stIc = { ABUNDANT:'💧', NORMAL:'🟢', TIGHT:'🟡', CRISIS:'🔴' };
  const tIc  = { IMPROVING:'🟢', STABLE:'🔵', DETERIORATING:'🔴', INSUFFICIENT_DATA:'⚪' };
  wl(`  ${stIc[r.current_state]||'⚪'} حالة السيولة: ${r.current_state}   ${coupBar(r.liquidity_score)}`);
  wl(`  ${tIc[r.trend]||'⚪'} الاتجاه: ${r.trend}   vol_ratio=${(r.avg_vol_ratio||0).toFixed(3)}   مشاركة=${((r.avg_breadth||0)*100).toFixed(0)}%`);

  const be = r.behavioural_effects || {};
  wl('\n  ─ تأثير السيولة على ميكانيكية السوق ─');
  wl(`  🌊 مضاعف سرعة الانتشار:        ${(be.propagation_speed_modifier||1).toFixed(2)}×`);
  wl(`  ⏳ إزاحة تأخر التعافي:          ${be.recovery_lag_shift_days||0} أيام إضافية`);
  wl(`  💨 معدل استنزاف الطاقة:         ${(be.energy_drain_rate||1).toFixed(2)}×`);
  wl(`  🎯 مضاعف احتمال الذعر:          ${(be.panic_probability_modifier||1).toFixed(2)}×`);

  if (r.series_20d?.length) {
    wl('\n  ─ سلسلة السيولة (آخر 20 يوم) ─');
    wl(`  ${'vol_ratio'.padEnd(12)}  ${'مشاركة'.padEnd(10)}  حالة`);
    wl(`  ${'─'.repeat(40)}`);
    const stateIcons = { ABUNDANT:'💧', NORMAL:'🟢', TIGHT:'🟡', CRISIS:'🔴' };
    for (const d of r.series_20d.slice(-10)) {
      wl(`  ${(d.vol_ratio||0).toFixed(3).padEnd(12)}  ${((d.breadth||0)*100).toFixed(0).padEnd(8)}%  ${stateIcons[d.state]||'⚪'} ${d.state}`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 5. sector_coupling ───────────────────────────────────────────────────
async function runWorldSectorCoupling() {
  const t0 = Date.now();
  h2('🏭 Sector Coupling — Macro Sensitivity Maps');
  wl('خرائط ربط القطاعات بالماكرو: بيتا، حساسية، اتجاه أداء كل قطاع (~8s)\n');
  const r = await pythonSectorCoupling();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  📊 قطاعات محللة: ${r.n_sectors || 0}   عائد السوق 10 أيام: ${((r.market_10d_ret||0)*100).toFixed(3)}%`);

  if (r.group_coupling && Object.keys(r.group_coupling).length) {
    wl('\n  ─ بيتا مجموعات الماكرو ─');
    const sortedGroups = Object.entries(r.group_coupling).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1]));
    for (const [grp, beta] of sortedGroups) {
      const bar = coupBar(Math.abs(beta));
      const sign = beta >= 0 ? '+' : '';
      wl(`  ${grp.padEnd(22)}  β=${sign}${beta.toFixed(3).padStart(7)}  ${bar}`);
    }
  }

  if (r.sector_map?.length) {
    wl('\n  ─ أعلى القطاعات حساسية للماكرو ─');
    wl(`  ${'القطاع'.padEnd(38)}  ${'مجموعة'.padEnd(18)}  ${'β'.padStart(7)}  ${'حساسية'.padEnd(8)}  ${'Δ سوق'}`);
    wl(`  ${'─'.repeat(90)}`);
    const sensIc = { HIGH:'🔴', MEDIUM:'🟡', LOW:'🟢' };
    for (const s of r.sector_map.slice(0, 12)) {
      const sign = (s.vs_market||0) >= 0 ? '+' : '';
      wl(`  ${(s.sector||'—').padEnd(38)}  ${(s.macro_group||'—').padEnd(18)}  ${(s.market_beta||0).toFixed(3).padStart(7)}  ${sensIc[s.sensitivity]||'⚪'} ${(s.sensitivity||'—').padEnd(6)}  ${sign}${((s.vs_market||0)*100).toFixed(3)}%`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 6. shock_memory ──────────────────────────────────────────────────────
async function runWorldShockMemory() {
  const t0 = Date.now();
  h2('🧠 Shock Memory — External Shock Persistence & Decay');
  wl('كيف تبقى الصدمات الخارجية مؤثرةً في سلوك السوق؟ نصف عمر الذاكرة (~5s)\n');
  const r = await pythonShockMemory();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  if (r.n_shocks === 0) {
    wl(`  ✅ ${r.message||'لا صدمات كبيرة في النافذة الزمنية'}`);
    wl(`\n  ⏱  ${Date.now()-t0}ms`);
    return;
  }

  const trendIc = { INCREASING:'🔴', STABLE:'🟡', DECREASING:'🟢' };
  wl(`  📊 صدمات: ${r.n_shocks}   متوسط الصدمة: ${((r.avg_shock_ret||0)*100).toFixed(2)}%   الأسوأ: ${((r.worst_shock_ret||0)*100).toFixed(2)}%`);
  wl(`  🧠 نصف عمر الذاكرة: ${r.half_life_days != null ? r.half_life_days+' شمعة' : 'لم يُقَس'}`);
  wl(`  ${trendIc[r.shock_frequency_trend]||'⚪'} تكرار الصدمات: ${r.shock_frequency_trend}   (${r.recent_30d_shocks||0} صدمة في 30 يوم مقابل ${r.historical_shocks||0} سابقاً)`);

  if (r.post_shock_response && Object.keys(r.post_shock_response).length) {
    wl('\n  ─ متوسط استجابة السوق بعد الصدمة ─');
    wl(`  ${'فجوة (شمعات)'.padEnd(14)}  استجابة`);
    for (const [lag, val] of Object.entries(r.post_shock_response).slice(0, 10)) {
      const pct = (val * 100).toFixed(3);
      const ic  = val > 0 ? '🟢' : val < -0.001 ? '🔴' : '⚪';
      wl(`  +${lag.padEnd(13)}  ${ic} ${pct}%`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 7. contagion_scan ────────────────────────────────────────────────────
async function runWorldContagionScan() {
  const t0 = Date.now();
  h2('🦠 Contagion Scan — Cross-Sector Synchronisation');
  wl('فحص العدوى: هل السوق يتحرك كوحدة واحدة؟ هل الضغط مستورَد؟ (~5s)\n');
  const r = await pythonContagionScan();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const cnIc = { INDEPENDENT:'🟢', MODERATE:'🟡', HIGH_CONTAGION:'🟠', CRISIS_SYNC:'🔴' };
  const tIc  = { FALLING:'🟢', STABLE:'🔵', RISING:'🔴', INSUFFICIENT:'⚪' };
  wl(`  ${cnIc[r.contagion_state]||'⚪'} حالة العدوى: ${r.contagion_state}   درجة: ${forceBar(r.contagion_score)}`);
  wl(`  ${tIc[r.contagion_trend]||'⚪'} الاتجاه: ${r.contagion_trend}   حوادث ذعر متزامن (30 يوم): ${r.synchronized_panics_30d||0}`);

  if (r.top_correlations?.length) {
    wl('\n  ─ أعلى الارتباطات بين المجموعات ─');
    for (const c of r.top_correlations.slice(0, 5)) {
      const val = Math.abs(c.cor||0);
      const ic  = val > 0.6 ? '🔴' : val > 0.4 ? '🟡' : '🟢';
      wl(`  ${ic} ${(c.pair||'—').padEnd(48)}  ρ=${(c.cor||0).toFixed(3)}`);
    }
  }

  if (r.contagion_series?.length) {
    wl('\n  ─ تطور العدوى عبر الزمن ─');
    const max = Math.max(...r.contagion_series, 0.01);
    for (const v of r.contagion_series) {
      const bar = '█'.repeat(Math.round((v / max) * 15));
      wl(`  ${bar.padEnd(16)}  ${v.toFixed(4)}`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 8. coupling_stability ────────────────────────────────────────────────
async function runWorldCouplingStability() {
  const t0 = Date.now();
  h2('⚖️  Coupling Stability — Structural Breaks in Macro–Market Links');
  wl('ثبات العلاقات الاقتصادية: هل روابط الماكرو بالسوق مستقرة أم تتكسر؟ (~6s)\n');
  const r = await pythonCouplingStability();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const stIc = { STABLE:'🟢', SHIFTING:'🟡', DECOUPLING:'🔴' };
  wl(`  ${stIc[r.stability_state]||'⚪'} الحالة: ${r.stability_state}   درجة الثبات: ${coupBar(r.stability_score||0)}   انكسارات بنيوية: ${r.n_structural_breaks||0}`);

  if (r.couplings) {
    wl('\n  ─ حالة كل رابط ─');
    wl(`  ${'الرابط'.padEnd(26)}  ${'ارتباط حديث'.padStart(14)}  ${'Δ'.padStart(8)}  ${'كسر بنيوي?'}`);
    wl(`  ${'─'.repeat(72)}`);
    const labels = {
      fx_coupling:        'FX ← السوق (بنوك)',
      rate_coupling:      'أسعار فائدة ← السوق',
      inflation_coupling: 'تضخم ← السوق',
      consumer_coupling:  'استهلاك ← السوق',
    };
    for (const [key, info] of Object.entries(r.couplings||{})) {
      const brk = info.break_detected ? '🔴 نعم' : '🟢 لا';
      const cor  = info.recent_cor != null ? info.recent_cor.toFixed(3) : '—';
      const delta= info.delta != null ? (info.delta >= 0 ? '+' : '')+info.delta.toFixed(3) : '—';
      wl(`  ${(labels[key]||key).padEnd(26)}  ${cor.padStart(14)}  ${delta.padStart(8)}  ${brk}`);
    }
  }

  // Rolling series
  const anyCoupling = Object.values(r.couplings||{}).find(c => c.series?.length > 0);
  if (anyCoupling?.series?.length) {
    wl('\n  ─ سلسلة ارتباط FX (آخر 8 نوافذ) ─');
    const fxS = r.couplings?.fx_coupling?.series || [];
    for (const v of fxS) {
      const bar = '█'.repeat(Math.round(Math.abs(v) * 10));
      const ic  = v > 0 ? '🟢' : '🔴';
      wl(`  ${ic} ${(v >= 0 ? '+' : '')+v.toFixed(3).padEnd(8)}  ${bar}`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 9. adaptive_world ────────────────────────────────────────────────────
async function runWorldAdaptive() {
  const t0 = Date.now();
  h2('🔄 Adaptive World Model — Evolving Macro Dependencies');
  wl('تطور نموذج العالم: كيف تتغير قوى الربط الخارجي عبر الزمن؟ (~5s)\n');
  const r = await pythonAdaptiveWorldModel();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const adapIc = { ADAPTING:'🟢', STATIC:'🟡' };
  wl(`  ${adapIc[r.adaptation_status]||'⚪'} حالة التكيف: ${r.adaptation_status}   فترات محللة: ${r.n_periods||0}`);

  if (r.trends) {
    wl('\n  ─ اتجاهات قوى الربط ─');
    const tIcons = { RISING:'🔴', FALLING:'🟢', STABLE:'🔵', INSUFFICIENT:'⚪' };
    const tLabels = {
      fx_stress: 'ضغط العملة',   liquidity: 'السيولة',
      inflation: 'التضخم',       contagion: 'العدوى',
    };
    for (const [k, trend] of Object.entries(r.trends||{})) {
      wl(`  ${tIcons[trend]||'⚪'} ${(tLabels[k]||k).padEnd(20)}  ${trend}`);
    }
  }

  if (r.force_dominance_recent && Object.keys(r.force_dominance_recent).length) {
    wl('\n  ─ القوى المهيمنة مؤخراً ─');
    for (const [f, cnt] of Object.entries(r.force_dominance_recent).sort((a,b)=>b[1]-a[1])) {
      wl(`  ${f.padEnd(20)}  ${cnt} فترة`);
    }
  }

  if (r.evolution_series?.length) {
    wl('\n  ─ تطور شدة القوى (آخر 10 فترات) ─');
    wl(`  ${'FX'.padEnd(8)}  ${'سيولة'.padEnd(8)}  ${'تضخم'.padEnd(8)}  ${'عدوى'.padEnd(8)}  ${'عائد سوق'}`);
    wl(`  ${'─'.repeat(56)}`);
    for (const e of r.evolution_series) {
      const ret = ((e.market_ret||0)*100).toFixed(3);
      const retIc = e.market_ret > 0 ? '🟢' : '🔴';
      wl(`  ${(e.fx_stress||0).toFixed(3).padEnd(8)}  ${(e.liquidity||0).toFixed(3).padEnd(8)}  ${(e.inflation||0).toFixed(3).padEnd(8)}  ${(e.contagion||0).toFixed(3).padEnd(8)}  ${retIc} ${ret}%`);
    }
  }
  wl(`\n  ⏱  ${Date.now()-t0}ms`);
}

// ── 10. coupling_full ────────────────────────────────────────────────────
async function runWorldCouplingFull() {
  const t0 = Date.now();
  h2('🌐 WORLD–MARKET COUPLING FULL — Complete Intelligence Report');
  wl('التقرير الشامل لربط السوق بالعالم الخارجي: كل أبعاد الماكرو والاقتران (~55s)\n');

  const r = await pythonCouplingFull();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const steps = [
    ['coupling_now',       '🌍 Coupling Snapshot'],
    ['fx_impact',          '💱 FX Impact'],
    ['macro_regimes',      '🏦 Macro Regimes'],
    ['liquidity_cycle',    '💧 Liquidity Cycle'],
    ['contagion_scan',     '🦠 Contagion Scan'],
    ['coupling_stability', '⚖️  Coupling Stability'],
    ['adaptive_world',     '🔄 Adaptive World'],
    ['shock_memory',       '🧠 Shock Memory'],
    ['sector_coupling',    '🏭 Sector Coupling'],
  ];
  for (const [key, label] of steps) {
    const sub = r[key];
    wl(`  ${sub?.__ok__ !== false ? '✅' : '❌'} ${label}`);
  }

  const sy = r.synthesis || {};
  wl('\n' + '─'.repeat(65));
  wl('  ▶ 🎯 التوليف الاستراتيجي للربط العالمي');
  wl('─'.repeat(65));

  const mktIc = coupIcon(sy.market_regime);
  const macIc = {EXTERNAL_SHOCK:'⚡',LIQUIDITY_CRISIS:'🌊',POLICY_TIGHTENING:'🏦',
                  INFLATION_PRESSURE:'🌡️ ',EASY_MONEY:'💰',STABLE:'🟢'}[sy.macro_regime]||'⚪';
  wl(`  ${mktIc} ريجيم السوق: ${sy.market_regime}   ${macIc} ريجيم الماكرو: ${sy.macro_regime}`);
  wl(`  🔗 شدة الارتباط: ${intensBar(sy.coupling_intensity||0)}   🏆 القوة المهيمنة: ${sy.dominant_force||'—'}`);
  wl(`  🔄 حالة التكيف: ${sy.adaptation_status||'—'}`);

  if (sy.key_risks?.length) {
    wl('\n  ⚠️  مخاطر الماكرو الرئيسية:');
    sy.key_risks.forEach(risk => wl(`    • ${risk}`));
  }
  if (sy.opportunities?.length) {
    wl('\n  ✅ الفرص الناجمة عن البيئة الخارجية:');
    sy.opportunities.forEach(op => wl(`    • ${op}`));
  }

  const elapsed = ((Date.now()-t0)/1000).toFixed(1);
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  إجمالي: ${elapsed}s | تشغيل: ${Date.now()-t0}ms`);
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 9 — Cognitive Orchestrator
// ─────────────────────────────────────────────────────────────────────────

const ORCH_HEALTH_ICONS = { HEALTHY:'🟢', DEGRADED:'🟡', CRITICAL:'🔴', UNKNOWN:'⚪' };
function healthIcon(state) { return ORCH_HEALTH_ICONS[state] || '⚪'; }
function confLabel(c) {
  if (c >= 0.85) return '🟢 VERY HIGH';
  if (c >= 0.70) return '🟡 HIGH';
  if (c >= 0.55) return '🟠 MODERATE';
  return '🔴 LOW';
}
function postureIcon(p) {
  return {AGGRESSIVE_LONG:'🚀',MODERATE_LONG:'📈',NEUTRAL:'⚖️ ',DEFENSIVE:'🛡️ ',AVOID:'🚫'}[p]||'⚪';
}

async function runOrchHealth() {
  const t0 = Date.now();
  h2('🏥 COGNITIVE ORCHESTRATOR — Data Health Check');
  wl('فحص صحة البيانات عبر كل طبقات التحليل (~3s)\n');

  const r = await pythonOrchHealth();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const stIcon = r.overall_state === 'HEALTHY' ? '🟢' : r.overall_state === 'DEGRADED' ? '🟡' : '🔴';
  wl(`  ${stIcon} الحالة العامة: ${r.overall_state}   صحة: ${(r.overall_health*100).toFixed(1)}%`);
  wl(`  📊 الرموز: ${r.n_symbols}   مع مؤشرات: ${r.n_with_indicators}`);

  const checks = r.checks || {};
  wl('\n  فحوصات مفصلة:');
  for (const [k,v] of Object.entries(checks)) {
    const score = v.score ?? 1;
    const ic = score >= 0.8 ? '✅' : score >= 0.4 ? '⚠️ ' : '❌';
    const extras = Object.entries(v).filter(([kk]) => kk !== 'score').map(([kk,vv]) => `${kk}=${vv}`).join(', ');
    wl(`  ${ic} ${k}: سكور=${(score*100).toFixed(0)}%${extras ? '  '+extras : ''}`);
  }

  if (r.warnings?.length) {
    wl('\n  ⚠️  تحذيرات:');
    r.warnings.forEach(w => wl(`    • ${w}`));
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchNow() {
  const t0 = Date.now();
  h2('🧠 COGNITIVE ORCHESTRATOR — Real-Time Snapshot');
  wl('اللقطة الإدراكية الآنية: ملخص جميع طبقات الذكاء (~6s)\n');

  const r = await pythonOrchNow();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const pIcon = postureIcon(r.posture);
  wl(`  📅 ${r.date} ${r.time}`);
  wl(`  🎯 الريجيم: ${r.regime}   🌍 الطبقة المهيمنة: ${r.dominant_layer}`);
  wl(`  ${confLabel(r.global_confidence)}  (${(r.global_confidence*100).toFixed(1)}%)`);
  wl(`  ${pIcon} الموقف: ${r.posture}   التعرض: ${r.exposure_pct?.toFixed(1)}%`);
  wl(`  ⚔️  تعارضات: ${r.n_conflicts}   رموز: ${r.n_symbols}   ثقة تطور: ${r.trust}`);

  wl('\n  صحة الطبقات:');
  const lh = r.layer_health || {};
  for (const [layer, info] of Object.entries(lh)) {
    const ic = healthIcon(info.state);
    wl(`  ${ic} ${layer.padEnd(12)} ${healthBar(info.health)} ${(info.health*100).toFixed(1)}%  ${info.detail||''}`);
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchArbitrate() {
  const t0 = Date.now();
  h2('⚖️  COGNITIVE ORCHESTRATOR — Arbitration Engine');
  wl('محرك التحكيم المعرفي: من يقود القرار؟ (~6s)\n');

  const r = await pythonOrchArbitrate();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  🏆 الفائز: ${r.winner}`);
  wl(`  📝 السبب: ${r.reason}`);

  if (r.priority_stack?.length) {
    wl('\n  📋 ترتيب الأولويات:');
    r.priority_stack.forEach((s,i) => wl(`    ${i+1}. ${s}`));
  }

  if (r.active_conflicts?.length) {
    wl('\n  ⚔️  التعارضات النشطة:');
    r.active_conflicts.forEach(c => wl(`    • [${c.severity}] ${c.id}: ${c.desc}`));
  }

  if (r.layer_states) {
    wl('\n  طبقات حسب الحالة:');
    for (const [state, layers] of Object.entries(r.layer_states)) {
      const ic = state === 'HEALTHY' ? '🟢' : state === 'DEGRADED' ? '🟡' : '🔴';
      wl(`  ${ic} ${state}: ${layers.join(', ')}`);
    }
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchConfidence() {
  const t0 = Date.now();
  h2('📊 COGNITIVE ORCHESTRATOR — Global Confidence Map');
  wl('خريطة الثقة العالمية: وزن كل طبقة في القرار الكلي (~6s)\n');

  const r = await pythonOrchConfidence();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  🎯 الثقة الكلية: ${confLabel(r.global_confidence)}  ${(r.global_confidence*100).toFixed(1)}%`);
  wl(`  🔴 طبقات حرجة: ${r.n_critical_layers}   ⚔️  تعارضات: ${r.n_conflicts}`);

  wl('\n  تفصيل الطبقات:');
  const pl = r.per_layer || {};
  for (const [layer, info] of Object.entries(pl)) {
    const ic = healthIcon(info.state);
    const wt = (info.weight*100).toFixed(0);
    const contrib = (info.contribution*100).toFixed(2);
    wl(`  ${ic} ${layer.padEnd(12)} صحة: ${healthBar(info.health)} ${(info.health*100).toFixed(1)}%  وزن: ${wt}%  إسهام: ${contrib}%`);
  }

  if (r.penalties?.length) {
    wl('\n  💸 عقوبات الطبقات الحرجة:');
    r.penalties.forEach(p => wl(`    • ${p}`));
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchConflicts() {
  const t0 = Date.now();
  h2('⚔️  COGNITIVE ORCHESTRATOR — Conflict Scanner');
  wl('ماسح التعارضات بين الطبقات المعرفية (~6s)\n');

  const r = await pythonOrchConflicts();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const allClear = r.all_clear;
  wl(`  ${allClear ? '✅ لا تعارضات' : `⚔️  ${r.n_conflicts} تعارض مكتشف`}`);

  if (!allClear && r.conflicts?.length) {
    wl('\n  التعارضات المكتشفة:');
    for (const c of r.conflicts) {
      const ic = c.severity === 'CRITICAL' ? '🔴' : c.severity === 'HIGH' ? '🟠' : c.severity === 'MEDIUM' ? '🟡' : '🟢';
      wl(`  ${ic} [${c.severity}] ${c.id}`);
      wl(`     ${c.desc}`);
    }

    if (r.severity_dist) {
      wl('\n  توزيع الخطورة:');
      for (const [sev, cnt] of Object.entries(r.severity_dist)) {
        wl(`    ${sev}: ${cnt}`);
      }
    }
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchPosture() {
  const t0 = Date.now();
  h2('🎯 COGNITIVE ORCHESTRATOR — Trading Posture');
  wl('الموقف التداولي المُحسَّب من جميع الطبقات (~6s)\n');

  const r = await pythonOrchPosture();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const pIcon = postureIcon(r.posture);
  wl(`  ${pIcon} الموقف: ${r.posture}`);
  wl(`  💼 التعرض المقترح: ${r.exposure_pct?.toFixed(1)}%`);
  wl(`  📏 الحد الأقصى للمركز: ${r.max_position_pct?.toFixed(1)}%`);
  wl(`  🛑 أولوية الوقف: ${r.stop_priority}`);
  wl(`  🎯 الريجيم: ${r.regime}   🔑 الثقة: ${r.trust}`);

  if (r.rationale?.length) {
    wl('\n  المبررات:');
    r.rationale.forEach(line => wl(`    • ${line}`));
  }

  if (r.adjustments?.length) {
    wl('\n  التعديلات المطبقة:');
    r.adjustments.forEach(a => wl(`    ⚙️  ${a}`));
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchWatch() {
  const t0 = Date.now();
  h2('👁️  COGNITIVE ORCHESTRATOR — Instability Watch');
  wl('رقابة الاستقرار: كشف مبكر لإشارات الخطر (~6s)\n');

  const r = await pythonOrchWatch();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const escIcon = {CLEAR:'🟢',CAUTION:'🟡',WARNING:'🟠',CRITICAL:'🔴',EMERGENCY:'🚨'}[r.escalation_level]||'⚪';
  wl(`  ${escIcon} مستوى التصعيد: ${r.escalation_level}`);
  wl(`  🛡️  الإجراء الوقائي: ${r.safety_action}`);
  wl(`  ⚠️  التنبيهات: ${r.n_alerts}`);

  if (r.alerts?.length) {
    wl('\n  التنبيهات المفصلة:');
    r.alerts.forEach(a => {
      const ic = a.level === 'CRITICAL' ? '🔴' : a.level === 'HIGH' ? '🟠' : a.level === 'INFO' ? 'ℹ️ ' : '🟡';
      wl(`  ${ic} [${a.id||a.level}] ${a.msg||a.message||''}`);
    });
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchSync() {
  const t0 = Date.now();
  h2('🔄 COGNITIVE ORCHESTRATOR — Evolution Sync');
  wl('مزامنة الذاكرة التطورية مع حالة السوق الراهنة (~6s)\n');

  const r = await pythonOrchSync();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const syncIc = r.sync_status === 'SYNCED' || r.sync_status === 'OK' ? '✅' : r.sync_status === 'PARTIAL' ? '🟡' : '❌';
  wl(`  ${syncIc} حالة المزامنة: ${r.sync_status}`);
  wl(`  📅 التاريخ: ${r.today}`);
  wl(`  📚 سجلات التنسيق: ${r.orch_log_entries}   التطور: ${r.evo_log_entries}   الاقتران: ${r.coup_log_entries}`);

  const se = r.synced_entry || {};
  if (se.regime) {
    wl('\n  الإدخال المُزامَن:');
    wl(`    🎯 ريجيم: ${se.regime}   ثقة: ${(se.global_confidence*100||0).toFixed(1)}%`);
    wl(`    🎯 موقف: ${se.posture}   تعرض: ${se.exposure_pct?.toFixed(1)}%`);
    wl(`    🔑 ثقة التطور: ${se.evo_trust}   اقتران: ${se.dominant_coupling}`);
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOrchReport() {
  const t0 = Date.now();
  h2('📋 COGNITIVE ORCHESTRATOR — Daily Intelligence Report');
  wl('التقرير الاستخباراتي اليومي — 15 قسمًا من جميع الطبقات (~10s)\n');

  const r = await pythonOrchReport();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const sections = [
    ['s01_regime',        '🌍 الريجيم والسياق الماكروي'],
    ['s02_forces',        '⚡ القوى الكامنة'],
    ['s03_propagation',   '🔗 الانتشار والتموج'],
    ['s04_energy',        '🔋 الطاقة السوقية'],
    ['s05_causality',     '🔎 السببية الزمنية'],
    ['s06_world',         '🌐 الاقتران العالمي'],
    ['s07_confidence',    '📊 مستوى الثقة الكلية'],
    ['s08_warnings',      '⚠️  التحذيرات والمخاطر'],
    ['s09_posture',       '🎯 الموقف التداولي'],
    ['s10_opportunities', '🚀 الفرص المؤهلة'],
    ['s11_avoid',         '🚫 مناطق التجنب'],
    ['s12_delta',         '📈 التغيير مقارنة بالأمس'],
    ['s13_evolution',     '🧬 حالة التطور الذاتي'],
    ['s14_trust',         '🔑 مستوى الثقة المكتسبة'],
    ['s15_outlook',       '🔮 التوقعات والاستراتيجية'],
  ];

  for (const [key, title] of sections) {
    const sec = r[key];
    if (!sec) continue;
    wl(`\n${'─'.repeat(65)}`);
    wl(`  ▶ ${title}`);
    wl('─'.repeat(65));
    if (typeof sec === 'string') {
      wl(`  ${sec}`);
    } else if (sec.lines?.length) {
      sec.lines.forEach(ln => wl(`  ${ln}`));
    } else {
      const entries = Object.entries(sec).filter(([k]) => k !== 'title');
      for (const [k,v] of entries) {
        if (Array.isArray(v)) {
          if (v.length === 0) { wl(`  ${k}: (none)`); continue; }
          wl(`  ${k}:`);
          v.slice(0,6).forEach(item => {
            if (typeof item === 'object') wl(`    • ${JSON.stringify(item)}`);
            else wl(`    • ${item}`);
          });
        } else if (typeof v === 'number') {
          wl(`  ${k}: ${Number.isInteger(v) ? v : v.toFixed(3)}`);
        } else {
          wl(`  ${k}: ${v}`);
        }
      }
    }
  }

  const elapsed = ((Date.now()-t0)/1000).toFixed(1);
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  إجمالي: ${elapsed}s`);
}

async function runOrchFull() {
  const t0 = Date.now();
  h2('🧠 COGNITIVE ORCHESTRATOR — Full Intelligence Cycle');
  wl('الدورة الذكية الكاملة: كل الطبقات + التحكيم + الموقف + الفرص (~15s)\n');

  const r = await pythonOrchFull();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  // Orchestration steps
  const steps = r.orchestration_steps || {};
  wl('  خطوات التنسيق:');
  for (const [k,v] of Object.entries(steps)) {
    wl(`    ✅ ${k}: ${v}`);
  }

  // Synthesis
  const sy = r.synthesis || {};
  wl('\n' + '─'.repeat(65));
  wl('  ▶ 🎯 التوليف الاستراتيجي الكامل');
  wl('─'.repeat(65));
  const pIcon = postureIcon(sy.posture);
  wl(`  🎯 ريجيم: ${sy.regime}   🌍 ماكرو: ${sy.macro_regime}`);
  wl(`  ${confLabel(sy.global_confidence)}  (${(sy.global_confidence*100||0).toFixed(1)}%)`);
  wl(`  🏆 الطبقة المهيمنة: ${sy.dominant_layer}`);
  wl(`  ${pIcon} الموقف: ${sy.posture}   التعرض: ${sy.exposure_pct?.toFixed(1)}%`);
  wl(`  📊 تصعيد: ${sy.escalation}   ثقة: ${sy.trust}`);

  // Opportunities
  const opps = r.opportunities || [];
  if (opps.length) {
    wl(`\n  🚀 الفرص المؤهلة (${opps.length}):`);
    opps.slice(0,8).forEach(o => {
      wl(`    ${o.symbol.padEnd(8)} سكور: ${(o.score*100).toFixed(1)}%  RSI: ${o.rsi14?.toFixed(1)||'—'}  mom5: ${(o.mom5*100||0).toFixed(2)}%`);
    });
  }

  // Avoids
  const avoids = r.avoid_zones || [];
  if (avoids.length) {
    wl(`\n  🚫 مناطق التجنب (${avoids.length}): ${avoids.slice(0,6).map(a=>a.symbol).join(', ')}`);
  }

  // Layer health
  const layers = r.layers || {};
  wl('\n  صحة الطبقات:');
  for (const [layer, info] of Object.entries(layers)) {
    const ic = healthIcon(info.state);
    wl(`  ${ic} ${layer.padEnd(12)} ${healthBar(info.health)} ${(info.health*100).toFixed(1)}%  ${info.detail||''}`);
  }

  const elapsed = ((Date.now()-t0)/1000).toFixed(1);
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  إجمالي: ${elapsed}s`);
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 10 — Market Operating System
// ─────────────────────────────────────────────────────────────────────────

const STEP_ICONS = { OK:'✅', FAILED:'❌', PARTIAL:'⚠️ ' };
function stepIcon(s) { return STEP_ICONS[s] || '⚪'; }
function durStr(d) { return d != null ? `${Number(d).toFixed(1)}s` : '—'; }
function pipeBar(done, total) {
  const pct = Math.round(done/Math.max(total,1)*10);
  return '█'.repeat(pct) + '░'.repeat(10-pct);
}

async function runOsPipelineRun() {
  const t0 = Date.now();
  h2('⚙️  MARKET OS — Full Autonomous Daily Pipeline');
  wl('تشغيل الأنبوب التشغيلي الكامل: بيانات → ذكاء → تحكيم → تنبيهات → أرشيف (~15s)\n');

  const r = await pythonOsPipelineRun();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const statIc = r.status === 'OK' ? '✅' : r.status === 'PARTIAL' ? '⚠️ ' : '🔴';
  wl(`  ${statIc} الحالة: ${r.status}   الخطوات: ${r.steps_done}/${r.steps_total}   ⏱  ${r.duration_sec}s`);
  wl(`  📊 رموز: ${r.n_symbols}   ثقة: ${(r.global_confidence*100||r.confidence*100).toFixed(1)}%   موقف: ${r.posture}`);
  wl(`  ⚔️  تعارضات: ${r.n_conflicts}   🚨 تنبيهات جديدة: ${r.n_alerts}`);

  if (r.alert_types?.length) {
    wl(`\n  🔔 أنواع التنبيهات: ${r.alert_types.join(', ')}`);
  }

  wl('\n  تفاصيل الخطوات:');
  (r.steps || []).forEach(s => {
    const ic = stepIcon(s.status);
    wl(`  ${ic} ${s.step.padEnd(18)} ${durStr(s.duration_sec)}${s.error ? '  ERR: '+s.error.slice(0,50) : ''}`);
  });

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsPipelineStatus() {
  const t0 = Date.now();
  h2('📋 MARKET OS — Pipeline Status');
  wl('حالة آخر تشغيل للأنبوب التشغيلي (~1s)\n');

  const r = await pythonOsPipelineStatus();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const statIc = r.last_status === 'OK' ? '✅' : r.last_status === 'PARTIAL' ? '⚠️ ' : r.last_status === 'UNKNOWN' ? '⚪' : '🔴';
  wl(`  ${statIc} آخر تشغيل: ${r.last_run}   الحالة: ${r.last_status}`);
  wl(`  ⏱  المدة: ${durStr(r.last_duration_sec)}   رموز: ${r.n_symbols||'—'}`);
  wl(`  ✅ الخطوات: ${r.steps_done||'—'}/${r.steps_total||'—'}   📊 إجمالي التشغيلات: ${r.total_runs}`);

  if (r.recent_runs?.length) {
    wl('\n  آخر 7 تشغيلات:');
    r.recent_runs.forEach(run => {
      const ic = run.status === 'OK' ? '✅' : run.status === 'PARTIAL' ? '⚠️ ' : '❌';
      wl(`  ${ic} ${run.run_date}   ${run.status.padEnd(8)} ${run.steps_done}/${run.steps_total} خطوات   ${durStr(run.duration_sec)}`);
    });
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsDashboard() {
  const t0 = Date.now();
  h2('📊 MARKET OS — Real-Time Cognition Dashboard');
  wl('لوحة التحكم الإدراكية الحية: كل مؤشرات النظام دفعة واحدة (~7s)\n');

  const r = await pythonOsDashboard();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const mk = r.market || {};
  const pIcon = postureIcon(mk.posture);
  wl(`${'═'.repeat(65)}`);
  wl(`  📅 ${r.as_of}   📊 رموز: ${r.n_symbols}`);
  wl(`${'═'.repeat(65)}`);
  wl(`  🎯 ريجيم: ${mk.regime.padEnd(6)}  ${confLabel(mk.confidence)}  (${(mk.confidence*100).toFixed(1)}%)`);
  wl(`  ${pIcon} موقف: ${mk.posture}   تعرض: ${mk.exposure_pct?.toFixed(1)}%   🏆 ${mk.dominant_layer}`);
  wl(`  ⚔️  تعارضات: ${mk.n_conflicts}   📊 تصعيد: ${mk.escalation}`);

  // Layer bars
  wl(`\n  ─── صحة الطبقات ${'─'.repeat(40)}`);
  const layers = r.layers || {};
  for (const [k,v] of Object.entries(layers)) {
    const ic = healthIcon(v.state);
    wl(`  ${ic} ${k.padEnd(12)} ${healthBar(v.health)} ${v.detail?.slice(0,40)||''}`);
  }

  // Macro
  const mac = r.macro || {};
  wl(`\n  ─── ماكرو ${'─'.repeat(48)}`);
  wl(`  🌍 ${mac.regime}   💵 USD/EGP=${mac.usd_egp}   🏦 CBE=${mac.cbe_rate}%   📈 تضخم=${mac.inflation}%`);

  // Alerts
  if (r.n_alerts > 0) {
    wl(`\n  ─── تنبيهات نشطة (${r.n_alerts}) ${'─'.repeat(35)}`);
    (r.active_alerts || []).forEach(a => {
      const sic = a.severity === 'CRITICAL' ? '🔴' : a.severity === 'HIGH' ? '🟠' : '🟡';
      wl(`  ${sic} [${a.severity}] ${a.type}: ${a.message?.slice(0,55)||''}`);
    });
  } else {
    wl('\n  ✅ لا تنبيهات نشطة');
  }

  // Opportunities
  if (r.opportunities?.length) {
    wl(`\n  ─── فرص مؤهلة (${r.n_opportunities}) ${'─'.repeat(38)}`);
    r.opportunities.forEach(o => {
      wl(`  🚀 ${o.symbol?.padEnd(8)} سكور: ${(o.score*100).toFixed(1)}%  RSI: ${o.rsi14?.toFixed(0)||'—'}  mom5: ${(o.mom5*100||0).toFixed(2)}%`);
    });
  }

  // Pipeline
  const pl = r.pipeline || {};
  wl(`\n  ─── أنبوب التشغيل ${'─'.repeat(42)}`);
  wl(`  ⚙️  آخر تشغيل: ${pl.last_run}   الحالة: ${pl.status||'—'}   ⏱  ${durStr(pl.duration_sec)}`);

  // Confidence trend
  if (r.confidence_trend?.length) {
    const trend = r.confidence_trend.slice(-7);
    wl(`\n  ─── منحنى الثقة (آخر ${trend.length} أيام) ${'─'.repeat(28)}`);
    trend.forEach(t => wl(`  ${t.date} ${healthBar(t.conf)} ${(t.conf*100).toFixed(1)}%`));
  }

  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsAlertScan() {
  const t0 = Date.now();
  h2('🚨 MARKET OS — Intelligent Alert Scanner');
  wl('ماسح التنبيهات الذكي: 10 شروط مع منع الازدواجية (~7s)\n');

  const r = await pythonOsAlertScan();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  📅 ${r.date}   🎯 ريجيم: ${r.regime}   ثقة: ${(r.confidence*100).toFixed(1)}%   موقف: ${r.posture}`);
  wl(`  ${r.all_clear ? '✅ لا تنبيهات جديدة' : `🚨 ${r.n_new_alerts} تنبيه جديد`}   📤 أُرسل اليوم: ${r.n_sent_today}`);

  if (!r.all_clear && r.new_alerts?.length) {
    wl('\n  التنبيهات الجديدة:');
    r.new_alerts.forEach(a => {
      const sic = a.severity === 'CRITICAL' ? '🔴' : a.severity === 'HIGH' ? '🟠' : '🟡';
      wl(`\n  ${sic} ${a.type} [${a.severity}]`);
      wl(`     ${a.message}`);
    });

    if (r.severity_dist) {
      wl('\n  توزيع الخطورة:');
      for (const [sev, cnt] of Object.entries(r.severity_dist)) {
        wl(`    ${sev}: ${cnt}`);
      }
    }
  }

  if (r.sent_today?.length) {
    wl(`\n  سبق إرساله اليوم (${r.sent_today.length}):`);
    r.sent_today.forEach(a => wl(`    ✔️  ${a.alert_type} [${a.severity}]`));
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsArchive() {
  const t0 = Date.now();
  h2('🗄️  MARKET OS — Archive Cognition Snapshot');
  wl('حفظ لقطة الإدراك اليومية في قاعدة البيانات والأرشيف JSON (~7s)\n');

  const r = await pythonOsArchive();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  ${r.archived ? '✅' : '❌'} الأرشفة: ${r.archived ? 'ناجحة' : 'فشلت'}`);
  wl(`  📅 التاريخ: ${r.date}`);
  wl(`  📁 المسار: ${r.path}`);

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsHealth() {
  const t0 = Date.now();
  h2('🏥 MARKET OS — System Health Monitor');
  wl('مراقبة صحة النظام الشاملة: قاعدة البيانات، البيانات، الطبقات، الأنبوب (~7s)\n');

  const r = await pythonOsHealth();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const stIc = r.overall_state === 'HEALTHY' ? '🟢' : r.overall_state === 'DEGRADED' ? '🟡' : '🔴';
  wl(`  ${stIc} الحالة: ${r.overall_state}   صحة: ${(r.overall_health*100).toFixed(1)}%`);
  wl(`  📊 رموز: ${r.n_symbols}   مع مؤشرات: ${r.n_with_indicators}   🕐 ${r.last_checked}`);

  wl('\n  فحوصات مفصلة:');
  const checks = r.checks || {};
  for (const [k,v] of Object.entries(checks)) {
    const score = v.score ?? 1;
    const ic = score >= 0.8 ? '✅' : score >= 0.4 ? '⚠️ ' : '❌';
    const extras = Object.entries(v).filter(([kk]) => kk !== 'score')
      .map(([kk,vv]) => `${kk}=${vv}`).join('  ').slice(0, 60);
    wl(`  ${ic} ${k.padEnd(22)} سكور=${(score*100).toFixed(0)}%  ${extras}`);
  }

  if (r.warnings?.length) {
    wl('\n  ⚠️  تحذيرات:');
    r.warnings.forEach(w => wl(`    • ${w}`));
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsResilience() {
  const t0 = Date.now();
  h2('🔧 MARKET OS — Layer Resilience Check');
  wl('اختبار مرونة كل طبقة بشكل مستقل مع قياس زمن الاستجابة (~7s)\n');

  const r = await pythonOsResilience();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const stIc = r.overall_state === 'RESILIENT' ? '🟢' : r.overall_state === 'DEGRADED' ? '🟡' : '🔴';
  wl(`  ${stIc} الحالة: ${r.overall_state}   مرونة: ${(r.resilience_score*100).toFixed(0)}%`);
  wl(`  ✅ تعمل: ${r.n_operational}/${r.n_layers}   ⏱  متوسط: ${r.avg_latency_ms}ms   p95: ${r.p95_latency_ms}ms`);

  wl('\n  اختبار كل طبقة:');
  const layers = r.layers || {};
  for (const [k,v] of Object.entries(layers)) {
    if (v.status === 'OPERATIONAL') {
      wl(`  ✅ ${k.padEnd(12)} ${healthBar(v.health)} ${(v.health*100).toFixed(1)}%   ⏱  ${v.latency_ms}ms   ${v.detail?.slice(0,40)||''}`);
    } else {
      wl(`  ❌ ${k.padEnd(12)} FAILED — ${v.error?.slice(0,60)||''}`);
    }
  }

  if (r.recovery_notes?.length) {
    wl('\n  📝 ملاحظات الاسترداد:');
    r.recovery_notes.forEach(n => wl(`    • ${n}`));
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsObservability() {
  const t0 = Date.now();
  h2('📈 MARKET OS — System Observability');
  wl('مقاييس التنفيذ، زمن الاستجابة، منحنى الثقة، مؤشرات قاعدة البيانات (~2s)\n');

  const r = await pythonOsObservability();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  const pm = r.pipeline_metrics || {};
  wl(`  ─── مقاييس الأنبوب ${'─'.repeat(42)}`);
  wl(`  ✅ إجمالي التشغيلات: ${pm.total_runs}   معدل النجاح: ${(pm.success_rate*100).toFixed(1)}%`);
  wl(`  ⏱  متوسط: ${durStr(pm.avg_duration_sec)}   p95: ${durStr(pm.p95_duration_sec)}`);
  wl(`  📅 آخر تشغيل: ${pm.last_run||'—'}   الحالة: ${pm.last_status||'—'}`);

  if (Object.keys(r.step_timings||{}).length) {
    wl(`\n  ─── زمن استجابة الخطوات ${'─'.repeat(37)}`);
    for (const [step, t] of Object.entries(r.step_timings||{})) {
      const failStr = t.n_failed > 0 ? `  ❌ ${t.n_failed} فشل` : '';
      wl(`  ${step.padEnd(18)} p50: ${durStr(t.p50_sec)}  p95: ${durStr(t.p95_sec)}  n=${t.n_runs}${failStr}`);
    }
  }

  const db = r.db_metrics || {};
  wl(`\n  ─── مؤشرات قاعدة البيانات ${'─'.repeat(34)}`);
  wl(`  📊 OHLCV صفوف: ${(db.ohlcv_rows||0).toLocaleString()}   رموز: ${db.n_symbols||'—'}`);
  wl(`  📋 مؤشرات: ${db.indicator_rows||'—'}   لقطات إدراك: ${db.cognition_snapshots||'—'}   تنبيهات: ${db.alerts_sent||'—'}`);

  // Confidence trend last 7 days
  const trend = (r.confidence_trend||[]).slice(-7);
  if (trend.length) {
    wl(`\n  ─── منحنى الثقة (آخر ${trend.length} أيام) ${'─'.repeat(28)}`);
    trend.forEach(t => {
      wl(`  ${t.date}  ${healthBar(t.confidence||0)}  ${((t.confidence||0)*100).toFixed(1)}%  ${t.regime||''} → ${t.posture||''}`);
    });
  }

  const ls = r.log_sizes || {};
  wl(`\n  ─── أحجام السجلات ${'─'.repeat(42)}`);
  wl(`  pipeline: ${ls.pipeline_log}   orchestrator: ${ls.orchestrator_log}   evolution: ${ls.evolution_log}   coupling: ${ls.coupling_log}`);

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsReplay() {
  const t0 = Date.now();
  h2('⏪ MARKET OS — Historical Cognition Replay');
  wl('الجدول الزمني للإدراك التاريخي: تطور الريجيمات، الثقة، المواقف (~2s)\n');

  const r = await pythonOsReplay();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  wl(`  📚 لقطات محفوظة: ${r.n_snapshots}`);
  if (r.date_range?.earliest) {
    wl(`  📅 النطاق: ${r.date_range.earliest} → ${r.date_range.latest}`);
  }

  const ev = r.evolution || {};
  wl(`\n  ─── تحليل التطور ${'─'.repeat(43)}`);
  wl(`  🎯 الريجيم الحالي: ${ev.current_regime}   انتقالات: ${ev.regime_transitions}`);
  wl(`  📊 متوسط الثقة: ${(ev.avg_confidence*100).toFixed(1)}%   std: ${(ev.confidence_std*100).toFixed(1)}%`);
  wl(`  📉 أدنى: ${(ev.min_confidence*100).toFixed(1)}%   أعلى: ${(ev.max_confidence*100).toFixed(1)}%`);

  if (ev.regime_distribution) {
    wl('\n  توزيع الريجيمات:');
    for (const [reg, cnt] of Object.entries(ev.regime_distribution)) {
      wl(`    ${reg}: ${cnt} يوم`);
    }
  }
  if (ev.posture_distribution) {
    wl('\n  توزيع المواقف:');
    for (const [pos, cnt] of Object.entries(ev.posture_distribution)) {
      wl(`    ${pos}: ${cnt} يوم`);
    }
  }

  const timeline = r.timeline || [];
  if (timeline.length) {
    wl(`\n  ─── الجدول الزمني (آخر ${Math.min(timeline.length,14)} أيام) ${'─'.repeat(20)}`);
    timeline.slice(0,14).forEach(s => {
      const pIc = postureIcon(s.posture);
      const cBar = healthBar(s.confidence||0);
      wl(`  ${s.date}  ${s.regime.padEnd(5)} ${cBar} ${((s.confidence||0)*100).toFixed(1)}%  ${pIc} ${s.posture.padEnd(15)} cnf:${s.n_conflicts}`);
    });
  }

  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runOsFull() {
  const t0 = Date.now();
  h2('🚀 MARKET OS — Full System Status Report');
  wl('التقرير الشامل لنظام التشغيل: كل مؤشرات الحالة والصحة والتاريخ (~10s)\n');

  const r = await pythonOsFull();
  if (r.error) { wl(err(`خطأ: ${r.error}`)); return; }

  // Market Summary
  const ms = r.market_summary || {};
  const pIc = postureIcon(ms.posture);
  wl(`${'═'.repeat(65)}`);
  wl(`  📅 ${r.as_of}`);
  wl(`${'═'.repeat(65)}`);
  wl(`  🎯 ريجيم: ${ms.regime}   ماكرو: ${ms.macro_regime}`);
  wl(`  ${confLabel(ms.confidence)}  (${(ms.confidence*100).toFixed(1)}%)`);
  wl(`  ${pIc} موقف: ${ms.posture}   تعرض: ${ms.exposure_pct?.toFixed(1)}%`);
  wl(`  🏆 طبقة مهيمنة: ${ms.dominant_layer}   ⚔️  تعارضات: ${ms.n_conflicts}   📊 تصعيد: ${ms.escalation}`);

  // Layer health
  wl(`\n  ─── صحة الطبقات ${'─'.repeat(43)}`);
  for (const [k,v] of Object.entries(r.layers||{})) {
    wl(`  ${healthIcon(v.state)} ${k.padEnd(12)} ${healthBar(v.health)}`);
  }

  // Alerts
  if (r.n_alerts > 0) {
    wl(`\n  ─── تنبيهات نشطة (${r.n_alerts}) ${'─'.repeat(35)}`);
    (r.active_alerts||[]).forEach(a => {
      const sIc = a.severity==='CRITICAL'?'🔴':a.severity==='HIGH'?'🟠':'🟡';
      wl(`  ${sIc} ${a.type}: ${a.message?.slice(0,60)||''}`);
    });
  } else {
    wl('\n  ✅ لا تنبيهات نشطة');
  }

  // Opportunities
  if (r.opportunities?.length) {
    wl(`\n  ─── فرص مؤهلة (${r.opportunities.length}) ${'─'.repeat(37)}`);
    r.opportunities.forEach(o => {
      wl(`  🚀 ${o.symbol?.padEnd(8)} ${(o.score*100).toFixed(1)}%`);
    });
  }

  // Health summary
  const health = r.health || {};
  wl(`\n  ─── صحة النظام ${'─'.repeat(44)}`);
  const hIc = health.overall_state==='HEALTHY'?'🟢':health.overall_state==='DEGRADED'?'🟡':'🔴';
  wl(`  ${hIc} ${health.overall_state}   ${(health.overall_health*100).toFixed(1)}%`);

  // Pipeline
  const pp = r.pipeline || {};
  wl(`\n  ─── أنبوب التشغيل ${'─'.repeat(42)}`);
  wl(`  ⚙️  آخر تشغيل: ${pp.last_run||'—'}   ${pp.last_status||'—'}   ${durStr(pp.last_duration_sec)}`);

  // Observability
  const ob = r.observability || {};
  const pm = ob.pipeline_metrics || {};
  wl(`\n  ─── مراقبة الأداء ${'─'.repeat(42)}`);
  wl(`  📊 إجمالي التشغيلات: ${pm.total_runs||0}   نجاح: ${((pm.success_rate||0)*100).toFixed(1)}%   p95: ${durStr(pm.p95_duration_sec)}`);

  // History
  const hist = r.history || {};
  wl(`\n  ─── التاريخ ${'─'.repeat(48)}`);
  wl(`  📚 لقطات محفوظة: ${hist.n_snapshots}   انتقالات: ${hist.regime_transitions}   متوسط ثقة: ${(hist.avg_confidence*100||0).toFixed(1)}%`);
  if (hist.recent?.length) {
    hist.recent.slice(0,5).forEach(s => {
      wl(`    ${s.date}  ${s.regime.padEnd(5)} ${((s.confidence||0)*100).toFixed(1)}%  ${s.posture}`);
    });
  }

  const elapsed = ((Date.now()-t0)/1000).toFixed(1);
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  إجمالي: ${elapsed}s`);
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 11 — Telegram Report Formatter
// ─────────────────────────────────────────────────────────────────────────

async function runTgTestFormat() {
  const t0 = Date.now();
  sep(); wl('  📲 TELEGRAM REPORT FORMATTER — Preview Mode'); sep();
  const r = await pythonTgTestFormat();
  if (r.error) { wl(`  ❌ ${r.error}`); if (r.stderr) wl(r.stderr.slice(0,400)); return; }
  wl(`  📅 Date: ${r.date}  ${r.time}`);
  wl(`  📨 Messages: ${r.n_messages}   Total chars: ${r.total_chars}`);
  wl(`  📄 Msg1: ${r.preview?.msg1_chars||0} chars   Msg2: ${r.preview?.msg2_chars||0} chars`);
  wl('');
  wl('  ── Message 1 Preview ──────────────────────────────────────────');
  (r.messages?.[0] || '').split('\n').slice(0, 30).forEach(l => wl('  ' + l));
  if ((r.messages?.[0]||'').split('\n').length > 30) wl('  [... truncated ...]');
  wl('');
  wl('  ── Message 2 Preview ──────────────────────────────────────────');
  (r.messages?.[1] || '').split('\n').slice(0, 30).forEach(l => wl('  ' + l));
  if ((r.messages?.[1]||'').split('\n').length > 30) wl('  [... truncated ...]');
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runTgFormatDaily() {
  const t0 = Date.now();
  sep(); wl('  📲 TELEGRAM DAILY BRIEFING — Format'); sep();
  const r = await pythonTgFormatDaily();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📨 ${r.n_messages} messages | ${r.total_chars} chars total`);
  (r.messages || []).forEach((msg, i) => {
    wl(`\n  ── Message ${i+1} (${msg.length} chars) ──`);
    msg.split('\n').forEach(l => wl('  ' + l));
  });
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runTgFormatAlert() {
  const t0 = Date.now();
  sep(); wl('  🚨 TELEGRAM ALERT FORMAT'); sep();
  const r = await pythonTgFormatAlert();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📨 ${r.n_messages || 1} message(s)`);
  (r.messages || (r.message ? [r.message] : [])).forEach((msg, i) => {
    wl(`\n  ── Alert ${i+1} ──`);
    msg.split('\n').forEach(l => wl('  ' + l));
  });
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runTgFormatDelta() {
  const t0 = Date.now();
  sep(); wl('  📊 TELEGRAM DELTA REPORT'); sep();
  const r = await pythonTgFormatDelta();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📨 ${r.n_messages || 1} message(s) | ${r.total_chars || 0} chars`);
  (r.messages || (r.message ? [r.message] : [])).forEach((msg, i) => {
    wl(`\n  ── Delta ${i+1} ──`);
    msg.split('\n').forEach(l => wl('  ' + l));
  });
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 12 — DMIDS Market Intelligence Discovery
// ─────────────────────────────────────────────────────────────────────────

async function runDmidsStatus() {
  const t0 = Date.now();
  sep(); wl('  🔬 DMIDS — Market Intelligence System Status'); sep();
  const r = await pythonDmidsStatus();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📊 Stock profiles:     ${r.stock_profiles}`);
  wl(`  🚀 Explosive moves:    ${r.explosive_moves}`);
  wl(`  🧬 Precursor patterns: ${r.precursor_patterns}`);
  wl(`  ⚖️  Structural laws:    ${r.structural_laws}`);
  wl(`  🏭 Sector cycles:      ${r.sector_cycles}`);
  wl(`  🧠 Market memory:      ${r.market_memory}`);
  if (r.latest_report) wl(`  📄 Latest report:      ${r.latest_report}`);
  if (r.latest_memory) {
    const m = r.latest_memory;
    wl(`  💾 Memory snapshot:    ${m.date} | dominant=${m.dominant} | H=${m.hurst} | vol=${(m.vol*100).toFixed(2)}%`);
  }
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDmidsProfiles() {
  const t0 = Date.now();
  sep(); wl('  📊 DMIDS — STOCK BEHAVIORAL ARCHETYPES'); sep();
  wl('  ⏳ Profiling 252 stocks (computing Hurst, vol, momentum)...');
  const r = await pythonDmidsProfiles();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`\n  Profiled: ${r.n_profiled}/${r.n_total} stocks`);
  const ICONS = { MOMENTUM:'🚀', MEAN_REVERTER:'🔄', ACCUMULATOR:'📦', VOLATILE:'⚡',
                  STRUCTURAL_BREAK:'💥', THIN:'🔇', NEUTRAL:'⚖️ ' };
  wl('\n  ─── Archetype Distribution ───────────────────────────────────');
  const stats = r.archetype_stats || {};
  const sorted = Object.entries(r.archetypes || {}).sort((a,b) => b[1]-a[1]);
  sorted.forEach(([arch, cnt]) => {
    const ic = ICONS[arch] || '⚫';
    const st = stats[arch] || {};
    wl(`  ${ic} ${arch.padEnd(18)}  ${cnt.toString().padStart(3)} stocks | `+
       `vol=${(st.avg_vol_pct||0).toFixed(1)}% | H=${(st.avg_hurst||0).toFixed(3)} | `+
       `exp=${((st.avg_exp_freq||0)*100).toFixed(1)}%/day`);
  });
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDmidsExplosions() {
  const t0 = Date.now();
  sep(); wl('  🚀 DMIDS — EXPLOSIVE MOVE DISCOVERY'); sep();
  wl('  ⏳ Scanning all stocks for explosive moves + precursors...');
  const r = await pythonDmidsExplode();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`\n  Total: ${r.total_explosions} explosions | ${r.n_stocks_with_explosions} stocks`);
  wl(`  Median: ${r.median_return_pct}%  Avg: ${r.avg_return_pct}%`);
  wl('\n  ─── By Class ─────────────────────────────────────────────────');
  const cls = r.by_class || {};
  const dir = r.by_direction || {};
  ['EXTREME','LARGE','MEDIUM','SMALL'].forEach(c => {
    if (cls[c]) wl(`  ${c.padEnd(8)} ${cls[c].toString().padStart(5)} events`);
  });
  wl('\n  ─── By Direction ─────────────────────────────────────────────');
  if (dir.UP)   wl(`  🚀 UP:    ${dir.UP}`);
  if (dir.DOWN) wl(`  💥 DOWN:  ${dir.DOWN}`);
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDmidsPrecursors() {
  const t0 = Date.now();
  sep(); wl('  🧬 DMIDS — PRECURSOR PATTERN DISCOVERY'); sep();
  const r = await pythonDmidsPrecursors();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`\n  Analyzed: ${r.n_up_large} UP large | ${r.n_down_large} DOWN large | ${r.n_control} control`);
  wl(`  Patterns found: ${r.patterns_found}`);
  const confIcon = c => c==='HIGH'?'🟢':c==='MEDIUM'?'🟡':'🔴';
  if (r.up_patterns?.length) {
    wl('\n  ─── UPSIDE EXPLOSION PRECURSORS (Large+Extreme >8%) ─────────');
    r.up_patterns.forEach(p => {
      wl(`  ${confIcon(p.confidence_level)} [${(p.support_rate*100).toFixed(0)}% | d=${p.effect_size>0?'+':''}${p.effect_size.toFixed(2)}]`);
      wl(`     ${p.description}`);
    });
  }
  if (r.down_patterns?.length) {
    wl('\n  ─── DOWNSIDE EXPLOSION PRECURSORS ───────────────────────────');
    r.down_patterns.forEach(p => {
      wl(`  ${confIcon(p.confidence_level)} [${(p.support_rate*100).toFixed(0)}% | d=${p.effect_size>0?'+':''}${p.effect_size.toFixed(2)}]`);
      wl(`     ${p.description}`);
    });
  }
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDmidsSectors() {
  const t0 = Date.now();
  sep(); wl('  🏭 DMIDS — SECTOR BEHAVIORAL CYCLES'); sep();
  const r = await pythonDmidsSectors();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`\n  Sectors analyzed: ${r.n_sectors}`);
  wl(`  Most synchronized: ${r.most_synchronized || '—'}`);
  wl(`  Least synchronized: ${r.least_synchronized || '—'}`);
  wl('\n  ─── Sector Detail ────────────────────────────────────────────');
  const sectors = r.sectors || {};
  Object.entries(sectors).forEach(([sec, d]) => {
    const syncIcon = d.synchronization_pct > 50 ? '🔗' : d.synchronization_pct > 30 ? '📊' : '🔀';
    wl(`  ${syncIcon} ${sec.slice(0,28).padEnd(28)} ${d.n_active||d.n_stocks}st | `+
       `sync=${d.synchronization_pct.toFixed(1)}% | H=${d.avg_hurst.toFixed(2)} | `+
       `${d.volatility_level} vol | lead=${d.leadership_stock||'?'}`);
  });
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDmidsReport() {
  const t0 = Date.now();
  sep(); wl('  📄 DMIDS — RESEARCH REPORT'); sep();
  const r = await pythonDmidsReport();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl('');
  // Print the full report
  (r.report_preview || '').split('\n').forEach(l => wl(l));
  wl(`\n  📁 Report saved: ${r.report_file}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDmidsFull() {
  const t0 = Date.now();
  sep(); wl('  🔬 DMIDS — FULL DISCOVERY PIPELINE'); sep();
  wl('  Running: stock profiles → explosions → precursors → sectors → laws → report');
  wl('  (This may take 30-120 seconds for 252 stocks)');
  wl('');
  const r = await pythonDmidsFull();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const sp = r.stock_profiles || {}; const es = r.explosion_scan || {};
  const pd = r.precursor_discovery || {}; const sc = r.sector_cycles || {};
  const ku = r.knowledge_update || {}; const rr = r.research_report || {};
  wl(`  [1/5] Stock Profiles:     ${sp.n_profiled||0} stocks | archetypes: ${Object.keys(sp.archetypes||{}).length}`);
  wl(`  [2/5] Explosion Scan:     ${es.total_explosions||0} moves | ${es.elapsed||0}s`);
  wl(`  [3/5] Precursor Discovery: ${pd.patterns_found||0} patterns | ${pd.elapsed||0}s`);
  wl(`  [4/5] Sector Cycles:      ${sc.n_sectors||0} sectors | most_sync: ${sc.most_synchronized||'?'}`);
  wl(`  [5/5] Laws + Report:      ${ku.laws_generated||0} laws | report saved`);
  wl('');
  // Print the research report
  if (rr.report_preview) {
    rr.report_preview.split('\n').forEach(l => wl(l));
  }
  wl(`\n  📁 Report: ${rr.report_file||'?'}`);
  wl(`  ⏱  Total: ${r.total_elapsed}s`);
}

// ══════════════════════════════════════════════════════════════════════════
// Phase 13 — DHVD Display Functions
// ══════════════════════════════════════════════════════════════════════════

const STATUS_ICONS13 = { CONFIRMED:'✅', STRONG:'💪', VALIDATED:'🟢', DEGRADING:'🔶', WEAK:'🟡', REJECTED:'❌', DISCOVERED:'🔍' };

async function runDhvdStatus() {
  sep(); wl('  🔬 DHVD — Historical Validation System Status'); sep();
  const r = await pythonDhvdStatus();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  🗂  Validation results:   ${r.validation_results   ?? 0}`);
  wl(`  🧬 Precursor families:   ${r.precursor_families    ?? 0}`);
  wl(`  🌐 Regime history days:  ${r.regime_history_days   ?? 0}`);
  wl(`  ⚗️  Hypotheses tracked:   ${r.hypothesis_lifecycle  ?? 0}`);
  wl(`  💀 False breakouts:      ${r.false_breakouts        ?? 0}`);
  wl(`  📐 Precursor patterns:   ${r.precursor_patterns     ?? 0}`);
  wl(`  💥 Explosive moves:      ${r.explosive_moves        ?? 0}`);
  wl(`\n  Run npm run egx:dhvd:full to start validation.`);
}

async function runDhvdValidateLaws() {
  const t0 = Date.now();
  sep(); wl('  ⚗️  DHVD — Walk-Forward Law Validation'); sep();
  wl('  Testing each pattern across calendar years + OOS 40% holdout...');
  const r = await pythonDhvdValidateLaws();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  ✅ ${r.n_patterns} patterns | ${r.n_target_explosions} target | ${r.n_control_explosions} control`);
  wl(`  📅 OOS cutoff: ${r.oos_date_cutoff} | OOS events: ${r.oos_n_target}`);
  wl(`  📊 FDR-significant: ${r.n_significant_fdr ?? '?'}\n`);
  for (const res of (r.results || [])) {
    const icon = STATUS_ICONS13[res.status] ?? '❓';
    wl(`  ${icon} ${res.pattern} (${res.direction})`);
    wl(`      orig: support=${(res.original_support*100).toFixed(1)}% | effect=${res.original_effect.toFixed(3)}`);
    wl(`      val:  ${res.n_periods_passed}/${res.n_periods_tested} years | OOS=${(res.oos_support*100).toFixed(1)}% (n=${res.oos_n}) | conf=${res.confidence.toFixed(2)}`);
    wl('');
  }
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDhvdFamilies() {
  const t0 = Date.now();
  sep(); wl('  🧬 DHVD — Precursor Family Taxonomy'); sep();
  const r = await pythonDhvdFamilies();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  ${r.n_families} families | silhouette=${r.silhouette_score} | ${r.n_clustered} explosions clustered\n`);
  for (const f of (r.families || [])) {
    const c = f.centroid || {};
    wl(`  ${f.icon ?? '🔬'} ${f.name}  (${f.n} events | UP:${f.n_up} DOWN:${f.n_down} | avg=${(f.avg_magnitude*100).toFixed(1)}%)`);
    wl(`      ${f.description}`);
    wl(`      BBW=${c.pre5_bb_width?.toFixed(4)} VolR=${c.pre5_vol_ratio?.toFixed(2)} RSI=${c.pre5_rsi?.toFixed(1)} Mom=${c.pre5_momentum_5d?.toFixed(4)}`);
    wl('');
  }
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDhvdRegimes() {
  const t0 = Date.now();
  sep(); wl('  🌐 DHVD — Market Regime Reconstruction'); sep();
  const r = await pythonDhvdRegimes();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Date range: ${r.date_range} | ${r.n_days} trading days\n`);
  const dist  = r.regime_distribution || {};
  const icons = { BULL:'🐂', BEAR:'🐻', CHOPPY:'〰️', UNKNOWN:'❓' };
  const total = Object.values(dist).reduce((a,b)=>a+b,0) || 1;
  for (const [reg, cnt] of Object.entries(dist).sort((a,b)=>b[1]-a[1])) {
    const pct = (cnt/total*100).toFixed(0);
    const bar = '█'.repeat(Math.max(0, Math.round(cnt/total*25)));
    wl(`  ${icons[reg]??''} ${reg.padEnd(8)} ${String(cnt).padStart(4)}d  ${String(pct).padStart(3)}%  ${bar}`);
  }
  if (r.regime_law_performance && Object.keys(r.regime_law_performance).length) {
    wl('\n  Pattern support by regime:');
    for (const [pat, regStats] of Object.entries(r.regime_law_performance)) {
      const cells = Object.entries(regStats).map(([rr,s])=>`${rr}:${(s.support*100).toFixed(0)}%`).join('  ');
      wl(`    ${pat.padEnd(32)} ${cells}`);
    }
  }
  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDhvdFalseBreakouts() {
  const t0 = Date.now();
  sep(); wl('  💀 DHVD — False Breakout Anatomy'); sep();
  wl('  Scanning all symbols for 3%+ moves that reverse 60%+ within 3 bars...');
  const r = await pythonDhvdFalseBreakouts();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`\n  False breakouts: ${r.n_false_breakouts} | True explosions: ${r.n_true_explosions} | False rate: ${((r.false_rate??0)*100).toFixed(1)}%`);
  const fc = r.feature_comparison || {};
  if (Object.keys(fc).length) {
    wl('\n  Feature comparison (True Explosion vs False Breakout):');
    wl(`  ${'Feature'.padEnd(14)} ${'True'.padStart(9)}  ${'False'.padStart(9)}  ${'Cohen d'.padStart(8)}  Signal`);
    wl(`  ${'─'.repeat(60)}`);
    for (const [feat, v] of Object.entries(fc)) {
      const sig = v.distinguishing ? '⭐ DISTINGUISHING' : '';
      wl(`  ${feat.padEnd(14)} ${v.true_mean.toFixed(4).padStart(9)}  ${v.false_mean.toFixed(4).padStart(9)}  ${(v.cohen_d>=0?'+':'')}${v.cohen_d.toFixed(3).padStart(7)}  ${sig}`);
    }
  }
  const top = r.top_false_breakout_symbols || [];
  if (top.length) wl(`\n  Most false-breakout prone: ${top.slice(0,6).map(([s,n])=>`${s}(${n})`).join(' ')}`);
  wl(`\n  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s`);
}

async function runDhvdHypotheses() {
  sep(); wl('  ⚗️  DHVD — Hypothesis Lifecycle Status'); sep();
  const r = await pythonDhvdHypotheses();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.note) { wl(`  ℹ  ${r.note}`); return; }
  wl(`  ${r.n_hypotheses} hypotheses tracked\n`);
  const by = r.by_status || {};
  for (const [status, patterns] of Object.entries(by)) {
    wl(`  ${STATUS_ICONS13[status]??'?'} ${status}: ${(Array.isArray(patterns)?patterns:[]).join(', ')}`);
  }
}

async function runDhvdReport() {
  sep(); wl('  📄 DHVD — Generating Validation Report'); sep();
  const r = await pythonDhvdReport();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  ✅ Report saved: ${r.report_file}`);
}

async function runDhvdFull() {
  const t0 = Date.now();
  sep(); wl('  🔬 DHVD — FULL HISTORICAL VALIDATION PIPELINE'); sep();
  wl('  [1/5] Walk-forward law validation  (all years + OOS 40%)');
  wl('  [2/5] Precursor family clustering  (K-means, auto-k selection)');
  wl('  [3/5] Market regime reconstruction (BULL / BEAR / CHOPPY timeline)');
  wl('  [4/5] False breakout anatomy       (every symbol, every bar)');
  wl('  [5/5] Institutional research report');
  wl('  (Estimated runtime: 60–120 seconds)\n');
  const r = await pythonDhvdFull();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const lv = r.law_validation    || {};
  const pf = r.precursor_families|| {};
  const rh = r.regime_history    || {};
  const fb = r.false_breakouts   || {};
  const rd = rh.regime_distribution || {};
  wl(`  [1/5] ✅ Law validation:     ${lv.n_patterns??'?'} laws | OOS n=${lv.oos_n_target??0} | sig=${lv.n_significant_fdr??'?'}`);
  wl(`  [2/5] ✅ Precursor families: ${pf.n_families??'?'} clusters | silhouette=${pf.silhouette_score??'?'}`);
  wl(`  [3/5] ✅ Regime history:     ${rh.n_days??'?'} days | BULL:${rd.BULL??0} BEAR:${rd.BEAR??0} CHOPPY:${rd.CHOPPY??0}`);
  wl(`  [4/5] ✅ False breakouts:    ${fb.n_false_breakouts??'?'} | false rate ${((fb.false_rate??0)*100).toFixed(1)}%`);
  wl(`  [5/5] ✅ Report:             ${r.report_file ?? '?'}`);
  wl('');
  for (const h of (lv.results||[])) {
    const icon = STATUS_ICONS13[h.status] ?? '?';
    wl(`    ${icon} ${(h.pattern||'').padEnd(34)} conf=${h.confidence?.toFixed(2)}  ${h.status}`);
  }
  wl(`\n  ⏱  Total: ${r.total_elapsed}s`);
}

// ── Phase 14 — Law Synthesis Engine Display Functions ────────────────────────

const STATUS_ICONS14 = {
  STABLE_INVARIANT:    '🟢',
  SLOWLY_DEGRADING:    '🟡',
  REGIME_DEPENDENT:    '🔵',
  STRUCTURALLY_MUTATING:'🔶',
  TEMPORARY_ALPHA:     '🟠',
  DEAD_STRUCTURE:      '❌',
  UNKNOWN:             '❓',
};

async function runLsStatus() {
  sep(); wl('  🧬 Law Synthesis — System Status'); sep();
  const r = await pythonLsStatus();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Laws available:   ${r.precursor_patterns ?? '?'}`);
  wl(`  Explosive moves:  ${r.explosive_moves ?? '?'}`);
  wl(`  Stability curves: ${r.stability_curve_rows ?? 0} rows computed`);
  wl(`  Counterfactuals:  ${r.counterfactual_events ?? 0} events`);
  wl(`  Mutations:        ${r.mutation_events ?? 0} detected`);
  wl(`  Interactions:     ${r.interaction_pairs ?? 0} pairs`);
  wl(`  Network nodes:    ${r.network_nodes ?? 0} | edges=${r.network_edges ?? 0}`);
  wl(`  Physics events:   ${r.market_physics_rows ?? 0}`);
  wl('');
  wl(`  Run npm run egx:ls:full to start full synthesis.`);
}

async function runLsStability() {
  sep(); wl('  📈 Law Synthesis — Stability Curves'); sep();
  const r = await pythonLsStability();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  // API returns {patternName: {stability_class, n_quarters, curve, direction}}
  const entries = Object.entries(r).filter(([k]) => k !== 'error');
  wl(`  Laws analyzed: ${entries.length} | Quarters: ${entries[0]?.[1]?.n_quarters ?? '?'}`);
  wl('');
  for (const [name, v] of entries) {
    const icon = STATUS_ICONS14[v.stability_class] ?? '❓';
    const curve = v.curve ?? [];
    const recent = curve.slice(-3).map(c => `${(c.sr*100).toFixed(0)}%`).join(' → ');
    wl(`  ${icon} ${(name + ' (' + (v.direction||'') + ')').padEnd(36)} ${(v.stability_class||'').padEnd(24)} ${recent}`);
  }
}

async function runLsCounterfactuals() {
  sep(); wl('  🔬 Law Synthesis — Counterfactual Intelligence'); sep();
  const r = await pythonLsCounterfactuals();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  // API returns {pattern_id: {pattern_name, direction, hits, total, precision, false_alarm_rate}}
  const entries = Object.values(r).filter(v => typeof v === 'object' && v.total);
  const totalActs = entries.reduce((s, v) => s + (v.total ?? 0), 0);
  wl(`  Activations found: ${totalActs}`);
  wl('');
  for (const p of entries) {
    const label = `${p.pattern_name} (${p.direction})`;
    const hitPct   = ((p.precision ?? 0)*100).toFixed(1);
    const falsePct = ((p.false_alarm_rate ?? 0)*100).toFixed(1);
    wl(`  🎯 ${label.padEnd(36)}  hit=${hitPct}%  false_alarm=${falsePct}%  n=${p.total??0}`);
  }
}

async function runLsMutations() {
  sep(); wl('  🔀 Law Synthesis — Mutation Detection'); sep();
  const r = await pythonLsMutations();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  // API returns array directly or {mutations:[...]}
  const mutations = Array.isArray(r) ? r : (r.mutations ?? []);
  wl(`  Mutations detected: ${mutations.length}`);
  wl('');
  if (!mutations.length) {
    wl('  ✅ No structural mutations detected — all laws appear stable.');
    return;
  }
  const ICONS = { STRENGTHENING:'📈', WEAKENING:'📉', REGIME_SHIFT:'🔀', GRADUAL_DRIFT:'〰️' };
  for (const m of mutations) {
    const icon = ICONS[m.type] ?? '🔶';
    wl(`  ${icon} ${(m.pattern||'').padEnd(30)} (${m.direction ?? '?'})  changepoint: ${m.mutation_period ?? m.changepoint_quarter}`);
    wl(`       before=${((m.pre_support??m.sr_before??0)*100).toFixed(1)}%  →  after=${((m.post_support??m.sr_after??0)*100).toFixed(1)}%  Δ=+${((m.delta??0)*100).toFixed(1)}pp  conf=${m.confidence?.toFixed(2)}  ${m.type}`);
  }
}

async function runLsInteractions() {
  sep(); wl('  🔗 Law Synthesis — Multi-Law Interaction Matrix'); sep();
  const r = await pythonLsInteractions();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  // API returns array directly
  const ixs = Array.isArray(r) ? r : (r.interactions ?? []);
  wl(`  Pairs analyzed: ${ixs.length}`);
  wl('');
  const TYPE_ICONS = { AMPLIFY:'⚡', SUPPRESS:'🛑', NEUTRAL:'〰️', CONDITIONAL:'🔀' };
  for (const ix of ixs) {
    const icon = TYPE_ICONS[ix.type ?? ix.interaction_type] ?? '?';
    const uplift = ((ix.uplift ?? 1) - 1) * 100;
    const sign = uplift >= 0 ? '+' : '';
    const la = `${ix.a ?? ix.law_a} (${ix.a_dir ?? ''})`;
    const lb = `${ix.b ?? ix.law_b} (${ix.b_dir ?? ''})`;
    wl(`  ${icon} ${la.padEnd(28)} × ${lb.padEnd(28)}  uplift=${sign}${uplift.toFixed(1)}%  ${ix.type ?? ix.interaction_type}`);
  }
}

async function runLsNetwork() {
  sep(); wl('  🕸️  Law Synthesis — Law Network Graph'); sep();
  const r = await pythonLsNetwork();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Nodes: ${r.n_nodes ?? '?'} | Edges: ${r.n_edges ?? '?'}`);
  wl('');
  wl('  Top nodes by centrality:');
  for (const n of (r.nodes ?? []).slice(0, 8)) {
    const bar = '█'.repeat(Math.round((n.centrality ?? 0) * 20));
    const icon = STATUS_ICONS14[n.stability_class] ?? '❓';
    const label = `${n.pattern_name} (${n.direction})`;
    wl(`    ${icon} ${label.padEnd(36)}  centrality=${(n.centrality??0).toFixed(3)}  ${bar}`);
  }
  if (r.edges?.length) {
    wl('\n  Strongest edges:');
    // Look up node names by pattern_id
    const nodeMap = Object.fromEntries((r.nodes ?? []).map(n => [n.pattern_id, `${n.pattern_name} (${n.direction})`]));
    for (const e of (r.edges ?? []).slice(0, 6)) {
      const a = nodeMap[e.source_id] ?? e.source_id;
      const b = nodeMap[e.target_id] ?? e.target_id;
      const itype = e.interaction_type;
      wl(`    ${a.padEnd(32)} ─── ${b.padEnd(32)}  w=${e.weight?.toFixed(3)}  ${itype}`);
    }
  }
}

async function runLsPhysics() {
  sep(); wl('  ⚛️  Law Synthesis — Market Physics Reconstruction'); sep();
  const r = await pythonLsPhysics();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const dist   = r.physics_distribution ?? {};
  const total  = r.n_explosions_analyzed ?? Object.values(dist).reduce((s,v)=>s+v, 0);
  const domKey = Object.entries(dist).sort(([,a],[,b])=>b-a)[0]?.[0] ?? '?';
  wl(`  Events analyzed: ${total}`);
  wl('');
  const TYPE_ICONS = { COMPRESSION_RELEASE:'🗜️', MOMENTUM_BURST:'🚀', REVERSAL_SPIKE:'↩️', STRUCTURAL_EXPANSION:'🏗️' };
  for (const [ptype, n] of Object.entries(dist).sort(([,a],[,b])=>b-a)) {
    const icon = TYPE_ICONS[ptype] ?? '⚛️';
    const pct  = total > 0 ? (n/total*100).toFixed(0) : '?';
    const bar  = '█'.repeat(Math.round(n/total*20));
    wl(`  ${icon} ${ptype.padEnd(28)}  n=${String(n).padStart(5)}  ${pct}%  ${bar}`);
  }
  wl(`\n  Avg compression depth:  ${(r.avg_compression_depth??0).toFixed(4)}`);
  wl(`  Dominant physics type:  ${domKey}`);
}

async function runLsRegimeSystems() {
  sep(); wl('  🌐 Law Synthesis — Regime-Specific Law Systems'); sep();
  const r = await pythonLsRegimeSystems();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  // API returns {BULL:[{pattern,direction,support,original,role,n},...], BEAR:[...], CHOPPY:[...]}
  const systems = r.systems ?? r;   // handle both full-synthesis wrapper and direct call
  const REGIME_ICONS = { BULL:'🐂', BEAR:'🐻', CHOPPY:'〰️' };
  const ROLE_ICONS   = { DOMINANT:'★', ACTIVE:'✓', WEAK:'~', SUPPRESSED:'↓', INVERTED:'↕' };
  for (const [regime, laws] of Object.entries(systems)) {
    if (!Array.isArray(laws)) continue;
    const icon = REGIME_ICONS[regime] ?? '?';
    wl(`  ${icon} ${regime} REGIME:`);
    const STATUS_ORDER = ['DOMINANT','ACTIVE','WEAK','SUPPRESSED','INVERTED'];
    for (const status of STATUS_ORDER) {
      const matches = laws.filter(l => (l.role ?? l.regime_status) === status);
      if (!matches.length) continue;
      for (const l of matches) {
        const ri = ROLE_ICONS[status] ?? ' ';
        const label = `${l.pattern} (${l.direction})`;
        wl(`      ${ri} ${status.padEnd(12)} ${label.padEnd(32)} SR=${((l.support??0)*100).toFixed(1)}%  n=${l.n??'?'}`);
      }
    }
    wl('');
  }
}

async function runLsReport() {
  sep(); wl('  📄 Law Synthesis — Generating Synthesis Report'); sep();
  const r = await pythonLsReport();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  ✅ Report saved: ${r.report_file}`);
}

async function runLsFull() {
  const t0 = Date.now();
  sep(); wl('  🧬 LAW SYNTHESIS ENGINE — FULL PIPELINE (Phase 14)'); sep();
  wl('  [1/7] Stability curves        (rolling quarterly trajectories)');
  wl('  [2/7] Counterfactuals         (full OHLCV precursor scan)');
  wl('  [3/7] Mutation detection      (binary segmentation changepoints)');
  wl('  [4/7] Interaction matrix      (co-activation uplift)');
  wl('  [5/7] Law network graph       (centrality + edges)');
  wl('  [6/7] Market physics          (compression-release mechanics)');
  wl('  [7/7] Regime law systems      (BULL/BEAR/CHOPPY stratification)');
  wl('  (Estimated runtime: 90–180 seconds)\n');
  const r = await pythonLsFull();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const sc  = r.stability_curves  || {};
  const cf  = r.counterfactuals   || {};
  const md  = r.mutations         || {};
  const ix  = r.interactions      || {};
  const net = r.network           || {};
  const ph  = r.physics           || {};
  const rs  = r.regime_systems    || {};
  wl(`  [1/7] ✅ Stability:      ${sc.n_laws??'?'} laws classified`);
  wl(`  [2/7] ✅ Counterfactuals: ${cf.n_activations??'?'} activations | avg hit=${((cf.avg_hit_rate??0)*100).toFixed(1)}%`);
  wl(`  [3/7] ✅ Mutations:       ${md.n_mutations??0} detected`);
  wl(`  [4/7] ✅ Interactions:    ${ix.n_pairs??'?'} pairs | amplifiers=${ix.n_amplify??0}`);
  wl(`  [5/7] ✅ Network:         ${net.n_nodes??'?'} nodes | ${net.n_edges??'?'} edges`);
  wl(`  [6/7] ✅ Physics:         ${ph.n_events??'?'} events | dominant=${ph.dominant_type??'?'}`);
  wl(`  [7/7] ✅ Regime systems:  ${Object.keys(rs.systems??{}).length} regimes analyzed`);
  wl('');
  if (sc.curves?.length) {
    wl('  STABILITY CLASSIFICATION:');
    for (const c of (sc.curves ?? [])) {
      const icon = STATUS_ICONS14[c.stability_class] ?? '❓';
      wl(`    ${icon} ${(c.pattern||'').padEnd(34)} ${c.stability_class}`);
    }
  }
  wl(`\n  📄 Report: ${r.report_file ?? '?'}`);
  wl(`  ⏱  Total: ${r.total_elapsed ?? ((Date.now()-t0)/1000).toFixed(1)}s`);
}

// ── Phase 15 — Self-Learning Market Evolution Display Functions ───────────────

const REINFORCE_ICONS = {
  REINFORCED: '🟢', ACTIVE: '✅', DEGRADING: '🟡', ARCHIVED: '❌',
};
const BEHAV_ICONS = {
  EXPLOSIVE: '💥', STEADY: '✅', VOLATILE: '⚡', DORMANT: '😴',
};

async function runEvoStatus() {
  sep(); wl('  🧠 Evolution Engine — System Status'); sep();
  const r = await pythonEvoStatus();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Experience events:    ${(r.total_experience_events??0).toLocaleString()}`);
  wl(`  Confidence snapshots: ${r.confidence_snapshots ?? 0}`);
  wl(`  Laws scored:          ${r.laws_scored ?? 0}`);
  wl(`  Failure records:      ${(r.failure_records??0).toLocaleString()}`);
  wl(`  Stocks profiled:      ${r.stocks_profiled ?? 0}`);
  wl(`  Hypothesis candidates:${r.hypothesis_candidates ?? 0}`);
  wl(`  Regime models:        ${r.regime_models ?? 0}`);
  wl(`  Evolution runs:       ${r.runs ?? 0}`);
  if (r.last_run) {
    wl(`  Last run:             ${r.last_run.run_timestamp?.slice(0,19)}  (${r.last_run.run_type}, ${r.last_run.elapsed_s}s)`);
  }
  wl('');
  wl(`  Run npm run egx:evo:full to start full learning cycle.`);
}

async function runEvoExperience() {
  sep(); wl('  📚 Evolution — Market Experience Ingestion'); sep();
  const r = await pythonEvoExperience();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  New events ingested:  ${(r.new_events_ingested??0).toLocaleString()}`);
  wl(`  Total events:         ${(r.total_experience_events??0).toLocaleString()}`);
  wl(`  Date range:           ${r.date_range?.[0]} → ${r.date_range?.[1]}`);
}

async function runEvoConfidence() {
  sep(); wl('  📈 Evolution — Law Confidence Evolution'); sep();
  const r = await pythonEvoConfidence();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Laws updated: ${r.laws_updated ?? '?'} | Gaining: ${r.gaining ?? 0} | Losing: ${r.losing ?? 0}`);
  wl('');
  const updates = r.updates ?? [];
  for (const u of [...updates].sort((a, b) => Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))) {
    const arrow = (u.delta ?? 0) >= 0 ? '▲' : '▼';
    const rp = u.rolling_precision ?? u.rolling_sr ?? 0;
    const ap = u.alltime_precision ?? 0;
    wl(`  ${arrow} ${(u.pattern + ' (' + u.direction + ')').padEnd(34)}  ${u.old_conf?.toFixed(3)}→${u.new_conf?.toFixed(3)}  Δ=${u.delta?.toFixed(4)}  precision=${(rp*100).toFixed(1)}% vs baseline=${(ap*100).toFixed(1)}%`);
  }
}

async function runEvoReinforce() {
  sep(); wl('  ⚡ Evolution — Structural Reinforcement'); sep();
  const r = await pythonEvoReinforce();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const by_s = r.by_status ?? {};
  for (const [status, icon] of Object.entries(REINFORCE_ICONS)) {
    const n = by_s[status] ?? 0;
    if (n > 0) wl(`  ${icon} ${status.padEnd(14)}: ${n}`);
  }
  wl('');
  for (const s of (r.structures ?? []).sort((a, b) => b.score - a.score)) {
    const icon = REINFORCE_ICONS[s.status] ?? '?';
    wl(`  ${icon} ${(s.pattern + ' (' + s.direction + ')').padEnd(38)} score=${s.score.toFixed(3)}  rolling=${(s.rolling_sr*100).toFixed(1)}%  baseline=${(s.baseline_sr*100).toFixed(1)}%`);
  }
}

async function runEvoFailures() {
  sep(); wl('  ⚠️  Evolution — Failure Reconstruction & Root Cause'); sep();
  const r = await pythonEvoFailures();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Failures analyzed: ${(r.total_failures_analyzed??0).toLocaleString()}`);
  wl('');
  wl('  Global cause distribution:');
  for (const [cause, stats] of Object.entries(r.global_cause_distribution ?? {})) {
    const bar = '█'.repeat(Math.round((stats.pct ?? 0) / 5));
    wl(`    ${cause.padEnd(28)}  ${String(stats.pct ?? 0).padStart(5)}%  ${bar}`);
  }
  wl('');
  wl('  Per-law dominant failure cause:');
  for (const [name, ps] of Object.entries(r.per_pattern ?? {})) {
    wl(`    ${(name + ' (' + ps.direction + ')').padEnd(40)} → ${ps.dominant_cause}`);
  }
}

async function runEvoStocks() {
  sep(); wl('  🏭 Evolution — Stock Behavioral Memory'); sep();
  const r = await pythonEvoStocks();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Stocks profiled: ${r.stocks_profiled ?? '?'}`);
  wl('');
  for (const [cls, n] of Object.entries(r.behavioral_distribution ?? {})) {
    const icon = BEHAV_ICONS[cls] ?? '?';
    const bar  = '█'.repeat(Math.min(30, Math.round(n / 5)));
    wl(`  ${icon} ${cls.padEnd(12)}  ${String(n).padStart(4)} stocks  ${bar}`);
  }
}

async function runEvoHypotheses() {
  sep(); wl('  🔬 Evolution — Hypothesis Evolution'); sep();
  const r = await pythonEvoHypotheses();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  New candidates: ${r.new_candidates ?? 0} | Promoted: ${r.promoted ?? 0} | Rejected: ${r.rejected ?? 0}`);
  wl('');
  for (const [status, n] of Object.entries(r.by_status ?? {})) {
    wl(`    ${status.padEnd(14)}: ${n}`);
  }
  if (r.top_improvements?.length) {
    wl('\n  Top validated hypotheses:');
    for (const t of r.top_improvements) {
      wl(`    ★ ${t.law_name.padEnd(28)} (${t.direction})  thresh=${t.threshold?.toFixed(4)}  SR=${(t.support_rate*100).toFixed(1)}%  Δ=${((t.sr_improvement??0)*100).toFixed(1)}pp`);
    }
  }
}

async function runEvoRegimes() {
  sep(); wl('  🌐 Evolution — Regime Model Calibration'); sep();
  const r = await pythonEvoRegimes();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Models calibrated: ${r.models_calibrated ?? '?'} | Avg |error|: ${((r.avg_abs_error??0)*100).toFixed(1)}%`);
  wl('');
  // Fetch from DB via full command or use worst_calibrations
  if (r.worst_calibrations?.length) {
    wl('  Largest calibration errors:');
    for (const m of r.worst_calibrations) {
      const sign = m.error >= 0 ? '+' : '';
      wl(`    ${m.regime.padEnd(8)} ${(m.pattern + ' (' + m.direction + ')').padEnd(34)}  exp=${(m.expected*100).toFixed(1)}%  obs=${(m.observed*100).toFixed(1)}%  err=${sign}${(m.error*100).toFixed(1)}%  n=${m.n}`);
    }
  }
}

async function runEvoFull() {
  const t0 = Date.now();
  sep(); wl('  🧠 SELF-LEARNING EVOLUTION ENGINE — FULL PIPELINE (Phase 15)'); sep();
  wl('  [1/7] Market experience ingestion  (classify all activations)');
  wl('  [2/7] Confidence evolution         (EMA update per law)');
  wl('  [3/7] Structural reinforcement     (rolling precision scoring)');
  wl('  [4/7] Failure reconstruction       (root cause analysis)');
  wl('  [5/7] Stock behavioral memory      (per-symbol profiling)');
  wl('  [6/7] Hypothesis evolution         (threshold search & promotion)');
  wl('  [7/7] Regime model calibration     (expected vs observed)');
  wl('  (Estimated runtime: 10–30 seconds)\n');
  const r = await pythonEvoFull();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const exp  = r.experience     || {};
  const conf = r.confidence     || {};
  const rf   = r.reinforcement  || {};
  const fr   = r.failures       || {};
  const st   = r.stocks         || {};
  const hyp  = r.hypotheses     || {};
  const rc   = r.regime_models  || {};
  const by_s = rf.by_status     || {};
  wl(`  [1/7] ✅ Experience:     ${(exp.total_experience_events??0).toLocaleString()} events (${exp.new_events_ingested??0} new)`);
  wl(`  [2/7] ✅ Confidence:     ${conf.laws_updated??0} laws updated | ▲${conf.gaining??0} gaining ▼${conf.losing??0} losing`);
  wl(`  [3/7] ✅ Reinforcement:  REINFORCED=${by_s.REINFORCED??0} ACTIVE=${by_s.ACTIVE??0} DEGRADING=${by_s.DEGRADING??0} ARCHIVED=${by_s.ARCHIVED??0}`);
  wl(`  [4/7] ✅ Failures:       ${(fr.total_failures_analyzed??0).toLocaleString()} analyzed | dominant=${Object.entries(fr.global_cause_distribution??{}).sort(([,a],[,b])=>b.n-a.n)[0]?.[0]??'?'}`);
  wl(`  [5/7] ✅ Stocks:         ${st.stocks_profiled??0} profiled | EXPLOSIVE=${(st.behavioral_distribution??{}).EXPLOSIVE??0}`);
  wl(`  [6/7] ✅ Hypotheses:     ${hyp.new_candidates??0} new | ${hyp.promoted??0} validated | ${hyp.rejected??0} rejected`);
  wl(`  [7/7] ✅ Regime models:  ${rc.models_calibrated??0} calibrated | avg|error|=${((rc.avg_abs_error??0)*100).toFixed(1)}%`);
  wl('');
  if (r.key_findings?.length) {
    wl('  🧪 KEY LEARNINGS:');
    for (const f of r.key_findings) wl(`    • ${f}`);
  }
  wl(`\n  📄 Report: ${r.report_file ?? '?'}`);
  wl(`  ⏱  Total: ${r.total_elapsed ?? ((Date.now()-t0)/1000).toFixed(1)}s`);
}

// ── Phase 16 — Autonomous Market Cognition Engine ─────────────────────────
const STATUS_ICONS_COG = { DOMINANT:'🟢', ACTIVE:'✅', DEGRADING:'🟡', ARCHIVED:'❌' };
const DRIFT_ICONS      = { ACCELERATING:'🚀', INCREASING:'📈', STABLE:'➡️', DECREASING:'📉', FADING:'⬇️' };
const ARCH_ICONS_COG   = { EXPLOSIVE_FAST:'💥', EXPLOSIVE_STEADY:'⚡', VOLATILE_REVERSAL:'🔄', STEADY_GROWER:'📊', DORMANT:'💤' };

async function runCogStatus() {
  const t0 = Date.now();
  sep();
  wl('  🧠 Cognition Engine — System Status (Phase 16)');
  sep();
  const r = await pythonCogStatus();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const src = r.source_tables || {};
  wl(`  Stock DNA profiles:      ${r.stock_dna_profiles}`);
  wl(`  Sector DNA profiles:     ${r.sector_dna_profiles}`);
  wl(`  Explosion archetypes:    ${r.explosion_archetypes}`);
  wl(`  Universal signatures:    ${r.explosion_signatures}`);
  wl(`  Universal laws (P16):    ${r.universal_laws_p16}`);
  wl(`  Knowledge graph nodes:   ${r.knowledge_graph_nodes}`);
  wl(`  Knowledge graph edges:   ${r.knowledge_graph_edges}`);
  wl(`  Law competition runs:    ${r.law_competition_runs}`);
  wl(`  Cognition pipeline runs: ${r.cognition_runs}`);
  wl('');
  wl('  Source data:');
  wl(`    Explosive moves:   ${(src.explosive_moves||0).toLocaleString()}`);
  wl(`    Market physics:    ${(src.market_physics||0).toLocaleString()}`);
  wl(`    False breakouts:   ${(src.false_breakouts||0).toLocaleString()}`);
  wl(`    Counterfactuals:   ${(src.counterfactuals||0).toLocaleString()}`);
  if (r.last_run) {
    wl(`\n  Last run: ${r.last_run.run_timestamp?.slice(0,16)}  stage=${r.last_run.stage}  elapsed=${r.last_run.duration_sec}s`);
  }
  wl(`\n  Run npm run egx:cog:run to start full cognition cycle.`);
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogStockDNA() {
  const t0 = Date.now();
  sep();
  wl('  🧬 Cognition — Stock DNA Profiles');
  sep();
  const r = await pythonCogStockDNA();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Profiles built: ${r.profiles_built} | Elapsed: ${r.elapsed_sec}s`);
  wl('');
  wl('  Archetype distribution:');
  for (const [arch, n] of Object.entries(r.archetype_dist || {}))
    wl(`    ${ARCH_ICONS_COG[arch] ?? '•'} ${arch.padEnd(25)} ${n}`);
  wl('');
  wl(`  Behavioral drift: 🚀 Accelerating=${r.drift_accelerating} ⬇️ Fading=${r.drift_fading}`);
  wl('');
  wl('  Top explosive stocks:');
  for (const s of (r.top_explosive||[]).slice(0,10))
    wl(`    ${s.symbol?.padEnd(10)} ${ARCH_ICONS_COG[s.archetype]??''} ${s.archetype?.padEnd(20)} rate=${s.explosion_rate_pct?.toFixed(2)}%/100d  expl=${s.explosion_count}`);
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogSectorDNA() {
  const t0 = Date.now();
  sep();
  wl('  🏭 Cognition — Sector DNA Profiles');
  sep();
  const r = await pythonCogSectorDNA();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Sectors built: ${r.sectors_built} | Contagion edges: ${r.contagion_edges} | Elapsed: ${r.elapsed_sec}s`);
  wl('');
  wl('  Sector archetype distribution:');
  for (const [arch, n] of Object.entries(r.archetype_dist || {}))
    wl(`    ${arch.padEnd(20)} ${n}`);
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogExplosions() {
  const t0 = Date.now();
  sep();
  wl('  💥 Cognition — Explosion Anatomy & Archetypes');
  sep();
  const r = await pythonCogExplosions();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Total explosions: ${(r.total_explosions||0).toLocaleString()} | Signatures: ${r.n_signatures} (${r.n_universal} universal)`);
  wl('');
  wl('  Explosion archetypes:');
  for (const a of (r.archetypes||[]))
    wl(`    [${a.archetype_name?.padEnd(25)}] ${a.n_members?.toLocaleString()?.padStart(5)} (${a.pct_of_total?.toFixed(1)}%)  r1=${(a.avg_return_1d*100).toFixed(1)}%  r5=${(a.avg_return_5d*100).toFixed(1)}%  fbr=${a.false_breakout_rate?.toFixed(0)}%  phys=${a.dominant_physics_type??'—'}`);
  wl('');
  wl('  Universal signatures:');
  for (const s of (r.signatures||[]).filter(x=>x.scope==='UNIVERSAL'))
    wl(`    ✓ ${s.signature_name?.padEnd(35)} ${s.prevalence_pct?.toFixed(1)}% prevalence  uplift=${s.avg_return_uplift>0?'+':''}${s.avg_return_uplift?.toFixed(1)}%`);
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogLaws() {
  const t0 = Date.now();
  sep();
  wl('  ⚖️  Cognition — Universal Law Analysis (Phase 16)');
  sep();
  const r = await pythonCogLaws();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Laws: ${r.n_laws} | Dominant: ${r.dominant} | Active: ${r.active}`);
  wl(`  Directional random baseline — UP: ${((r.laws?.find(l=>l.direction==='UP')?.random_baseline_precision||r.random_baseline)*100).toFixed(1)}%  DOWN: ${((r.laws?.find(l=>l.direction==='DOWN')?.random_baseline_precision||0)*100).toFixed(1)}%`);
  wl('');
  for (const l of (r.laws||[])) {
    const icon = STATUS_ICONS_COG[l.law_status] ?? '•';
    const dir  = l.direction === 'UP' ? '▲' : '▼';
    const oos  = l.oos_gap >= 0 ? `+${(l.oos_gap*100).toFixed(1)}pp` : `${(l.oos_gap*100).toFixed(1)}pp`;
    wl(`  ${icon} ${dir} ${(l.pattern_name+' ('+l.direction+')')?.padEnd(38)} P=${l.precision?.toFixed(3)}  F1=${l.f1_score?.toFixed(3)}  ×${l.precision_vs_random?.toFixed(1)}rand  OOS=${oos}  ${l.is_regime_dependent?'regime-dep':'uniform'}`);
  }
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogMemory() {
  const t0 = Date.now();
  sep();
  wl('  🕸️  Cognition — Knowledge Graph & Memory');
  sep();
  const r = await pythonCogMemory();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Knowledge graph: ${r.nodes} nodes | ${r.edges} edges`);
  wl('');
  wl('  Node types:');
  for (const [t, n] of Object.entries(r.node_types||{})) wl(`    ${t?.padEnd(20)} ${n}`);
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogEvolve() {
  const t0 = Date.now();
  sep();
  wl('  🔬 Cognition — Self-Evolution Competition');
  sep();
  const r = await pythonCogEvolve();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Variants tested: ${r.variants_tested} | Improvements: ${(r.improved_patterns||[]).length} | Below random: ${(r.below_random||[]).length}`);
  if ((r.best_variants||[]).length) {
    wl('');
    wl('  Top threshold improvements:');
    for (const v of r.best_variants)
      wl(`    ${v.pattern_name} (${v.direction})  thresh=${v.variant_threshold?.toFixed(5)}  +${v.improvement_pp?.toFixed(1)}pp`);
  }
  if ((r.improved_patterns||[]).length) {
    wl('');
    wl('  Patterns with improved threshold candidates:');
    for (const p of r.improved_patterns)
      wl(`    ✓ ${p.pattern} (${p.direction})  new_prec=${p.new_prec?.toFixed(3)}  best_thresh=${p.best_thresh}`);
  }
  sep(); wl(`  ⏱️  ${((Date.now()-t0)/1000).toFixed(1)}s`); sep();
}

async function runCogFull() {
  const t0 = Date.now();
  sep();
  wl('  🧠 PHASE 16 — FULL COGNITION PIPELINE');
  wl('  7-stage autonomous market intelligence cycle');
  wl('  ────────────────────────────────────────');
  wl('  [1/7] Stock DNA         (247 symbol behavioral profiles)');
  wl('  [2/7] Sector DNA        (contagion network, leadership)');
  wl('  [3/7] Explosion Anatomy (K-means clustering, signatures)');
  wl('  [4/7] Universal Laws    (precision, OOS validation)');
  wl('  [5/7] Memory            (knowledge graph construction)');
  wl('  [6/7] Self-Evolution    (threshold competition)');
  wl('  [7/7] Report            (10-section intelligence report)');
  wl('  (Estimated runtime: 30–120 seconds)\n');
  sep();
  const r = await pythonCogFull();
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  ✅ ${r.stages_completed}/${r.stages_total} stages complete | ${r.total_elapsed}s`);
  wl('');
  const sd = r.stock_dna || {};
  const sc = r.sector_dna || {};
  const ea = r.explosion_anatomy || {};
  const ul = r.universal_laws || {};
  const mg = r.knowledge_graph || {};
  const ev = r.self_evolution || {};
  wl(`  [1/7] Stock DNA:       ${sd.profiles_built??0} profiles | ${JSON.stringify(sd.archetype_dist??{})}`);
  wl(`  [2/7] Sector DNA:      ${sc.sectors_built??0} sectors | ${sc.contagion_edges??0} contagion edges`);
  wl(`  [3/7] Explosions:      ${ea.n_signatures??0} signatures (${ea.n_universal??0} universal)`);
  wl(`  [4/7] Laws:            ${ul.n_laws??0} analyzed | ${ul.dominant??0} DOMINANT | ${ul.active??0} ACTIVE`);
  wl(`  [5/7] Memory:          ${mg.nodes??0} nodes | ${mg.edges??0} edges`);
  wl(`  [6/7] Evolution:       ${ev.variants_tested??0} variants | ${(ev.best_variants||[]).length} improvements`);
  wl(`  [7/7] Report:          ${r.report?.report_file ?? r.report_file ?? '?'}`);
  wl('');
  if (r.key_findings?.length) {
    wl('  🔑 KEY DISCOVERIES:');
    for (const f of r.key_findings) wl(`    • ${f}`);
  }
  sep(); wl(`  ⏱️  Total: ${r.total_elapsed}s`); sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 17 — Graph Contagion Engine
// ─────────────────────────────────────────────────────────────────────────

async function runGraphFull() {
  sep();
  wl('  🕸️  PHASE 17 — GRAPH CONTAGION ENGINE');
  wl('  Building network, PageRank, communities, spillover...');
  sep();
  const r = await pythonGraphFull({ lookback_days: 252, min_correlation: 0.3 });
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const net  = r.network   ?? {};
  const comms = r.communities ?? [];
  const hubs  = r.pagerank?.top_hubs ?? [];
  const spill = r.spillover?.top_spillover ?? [];
  wl(`  ✅ Network: ${net.n_nodes??'?'} nodes | ${net.n_edges??'?'} edges`);
  wl(`  🏘️  Communities: ${comms.length} | Largest: ${comms[0]?.size??'?'} stocks (hub: ${comms[0]?.hub_ticker??'?'})`);
  if (hubs.length) wl(`  🏆 Top hubs: ${hubs.slice(0,5).map(h=>h.ticker).join(', ')}`);
  if (spill.length) wl(`  🌊 Strongest spillover: ${spill[0]?.leader}→${spill[0]?.follower} (r=${spill[0]?.lag_correlation?.toFixed(3)})`);
  sep();
}

async function runGraphPagerank() {
  sep(); wl('  📊 PHASE 17 — PAGERANK INFLUENCE SCORES'); sep();
  const r = await pythonGraphPagerank({ top_n: 20 });
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const hubs = r.top_hubs ?? [];
  wl(`  🏆 Top ${hubs.length} influential stocks:`);
  for (const h of hubs.slice(0, 15))
    wl(`    ${h.rank?.toString().padStart(2)}. ${(h.ticker??'?').padEnd(10)} PR=${h.pagerank?.toFixed(5)}  deg=${h.degree}`);
  sep();
}

async function runGraphCommunities() {
  sep(); wl('  🏘️  PHASE 17 — COMMUNITY DETECTION'); sep();
  const r = await pythonGraphCommunity({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const comms = r.communities ?? [];
  wl(`  ${comms.length} communities detected:`);
  for (const c of comms.slice(0, 10))
    wl(`    Community ${c.community_id}: ${c.size} stocks | hub=${c.hub_ticker??'?'}`);
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 18 — RL Environment & Walk-Forward
// ─────────────────────────────────────────────────────────────────────────

async function runRLWalkForward() {
  sep();
  wl('  🤖 PHASE 18 — WALK-FORWARD BACKTESTING');
  wl('  Windows: 2021→2022, 2022→2023, 2023→2025');
  sep();
  const r = await pythonRLWalkForward({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const windows = r.windows ?? [];
  wl(`  🪟 ${windows.length} validation windows:`);
  for (const w of windows) {
    const p = w.performance ?? w;
    wl(`  ┌─ ${w.train_start??'?'} → ${w.test_end??'?'}`);
    wl(`  │  Return: ${p.total_return!=null?(p.total_return*100).toFixed(2)+'%':'?'}  Sharpe: ${p.sharpe_ratio?.toFixed(3)??'?'}  DD: ${p.max_drawdown!=null?(p.max_drawdown*100).toFixed(2)+'%':'?'}`);
    wl(`  └─ Trades: ${p.total_trades??'?'}  Win%: ${p.win_rate!=null?(p.win_rate*100).toFixed(1)+'%':'?'}`);
  }
  const agg = r.aggregate ?? {};
  if (agg.avg_sharpe != null) {
    wl('');
    wl(`  📊 Aggregate: return=${agg.avg_return!=null?(agg.avg_return*100).toFixed(2)+'%':'?'}  sharpe=${agg.avg_sharpe?.toFixed(3)}  consistency=${agg.consistency!=null?(agg.consistency*100).toFixed(1)+'%':'?'}`);
  }
  sep();
}

async function runRLReport() {
  sep(); wl('  📋 PHASE 18 — RL PERFORMANCE REPORT'); sep();
  const r = await pythonRLReport({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📄 Report: ${r.report_file??'?'}`);
  const s = r.summary ?? {};
  if (s.total_trades != null) {
    wl(`  Total trades: ${s.total_trades} | Avg return: ${s.avg_return!=null?(s.avg_return*100).toFixed(2)+'%':'?'}`);
    wl(`  Best window: ${s.best_window??'?'}`);
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 19 — SHAP Explainability Engine
// ─────────────────────────────────────────────────────────────────────────

async function runExplainDaily() {
  sep();
  wl('  🔬 PHASE 19 — DAILY SHAP EXPLANATIONS');
  sep();
  const r = await pythonExplainDaily({ date: new Date().toISOString().slice(0,10) });
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const stocks = r.explained_stocks ?? [];
  wl(`  📅 Date: ${r.date??'?'} | ${stocks.length} stocks explained`);
  wl('  🏆 Top candidates:');
  const sorted = [...stocks].sort((a,b)=>(b.explosion_prob??0)-(a.explosion_prob??0));
  for (const s of sorted.slice(0, 15)) {
    const prob = s.explosion_prob!=null ? (s.explosion_prob*100).toFixed(1)+'%' : '?';
    const top2 = (s.shap_factors??[]).slice(0,2).map(f=>f.feature).join(', ');
    wl(`    ${(s.ticker??'?').padEnd(10)} prob=${prob}  drivers=${top2}`);
  }
  sep();
}

async function runExplainImportance() {
  sep(); wl('  📊 PHASE 19 — GLOBAL FEATURE IMPORTANCE'); sep();
  const r = await pythonExplainImportance({ top_n: 25 });
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  const feats = r.features ?? [];
  wl(`  ${feats.length} features ranked by mean |SHAP|:`);
  for (const [i, f] of feats.slice(0, 20).entries())
    wl(`    ${(i+1).toString().padStart(2)}. ${(f.feature??'?').padEnd(35)} SHAP=${f.mean_abs_shap?.toFixed(5)}`);
  sep();
}

async function runExplainReport() {
  sep(); wl('  📋 PHASE 19 — EXPLAINABILITY MODEL REPORT'); sep();
  const r = await pythonExplainReport({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📄 Report: ${r.report_file??'?'}`);
  const perf = r.model_performance ?? {};
  if (perf.auc_roc != null) {
    wl(`  AUC-ROC: ${perf.auc_roc?.toFixed(4)}  F1: ${perf.f1_score?.toFixed(4)}  Samples: ${perf.n_samples??'?'}`);
    wl(`  Trained: ${perf.trained_at??'?'} | Backend: ${perf.backend??'?'}`);
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 5 Extension — PCMCI Causal Discovery
// ─────────────────────────────────────────────────────────────────────────

async function runCausalPCMCI() {
  sep();
  wl('  🔭 PHASE 5 — PCMCI CAUSAL SECTOR DISCOVERY');
  wl('  tigramite PCMCI: finding true causal links between EGX sectors');
  sep();
  const r = await pythonCausalPCMCI({ tau_max: 5, pc_alpha: 0.1 });
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  📊 Sectors analyzed:  ${r.n_sectors_analyzed??'?'}`);
  wl(`  🔗 Causal links found: ${r.n_causal_links??'?'} (p<0.05, |strength|>0.05)`);
  const links = r.top_links ?? [];
  if (links.length) {
    wl('');
    wl('  Top causal relationships:');
    for (const l of links.slice(0, 15))
      wl(`    ${(l.cause_sector??'?').padEnd(20)} → ${(l.effect_sector??'?').padEnd(20)} lag=${l.lag}d  strength=${l.strength?.toFixed(3)}  p=${l.pvalue?.toFixed(4)}`);
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 20 — Historical Integrity Engine
// ─────────────────────────────────────────────────────────────────────────

async function runIntegrityReport() {
  sep(); wl('  🔍 PHASE 20 — HISTORICAL INTEGRITY ENGINE'); sep();
  const r = await pythonIntegrityReport({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.tier_distribution) {
    wl('  📊 Data Quality Tiers:');
    Object.entries(r.tier_distribution).forEach(([t, n]) => wl(`    ${t.padEnd(20)} ${n} symbols`));
  }
  if (r.worst_10?.length) {
    wl(''); wl('  ⚠️  Worst symbols:');
    r.worst_10.forEach(s => wl(`    ${(s.symbol??'').padEnd(10)} score: ${(s.score??0).toFixed(1)}`));
  }
  sep();
}

async function runIntegrityBreadth() {
  sep(); wl('  📊 PHASE 20 — MARKET BREADTH HISTORY'); sep();
  const r = await pythonIntegrityBreadth({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Computed: ${r.n_dates_computed ?? '?'} trading dates`);
  if (r.latest) wl(`  Latest: +${r.latest.advancing} / -${r.latest.declining}  AD-line: ${r.latest.ad_line}`);
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 21 — UMCG
// ─────────────────────────────────────────────────────────────────────────

async function runUMCGBuild() {
  sep(); wl('  🕸️  PHASE 21 — UMCG: Building graph…'); sep();
  const r = await pythonUMCGBuild({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Nodes: ${r.n_nodes}  Edges: ${r.n_edges}`);
  if (r.node_type_dist) wl(`  Node types: ${JSON.stringify(r.node_type_dist)}`);
  sep();
}

async function runUMCGSnapshot() {
  sep(); wl('  🕸️  PHASE 21 — UMCG: Weekly Snapshot'); sep();
  const r = await pythonUMCGSnapshot({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.top_pagerank) {
    wl('  🔵 Top nodes by PageRank:');
    r.top_pagerank.forEach(n => wl(`    ${String(n.name??n.node_id).padEnd(25)} [${(n.node_type??'').padEnd(8)}] PR: ${(n.pagerank??0).toFixed(5)}`));
  }
  sep();
}

async function runUMCGStatus() {
  sep(); wl('  🕸️  PHASE 21 — UMCG: Latest Snapshot'); sep();
  const r = await pythonUMCGGetSnapshot({});
  if (r.n_nodes !== undefined) wl(`  Nodes: ${r.n_nodes}  Edges: ${r.n_edges}`);
  wl(JSON.stringify(r, null, 2));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 22 — Causal Discovery Engine
// ─────────────────────────────────────────────────────────────────────────

async function runCausalDiscFull() {
  sep(); wl('  🔗 PHASE 22 — CAUSAL DISCOVERY: Full pipeline'); sep();
  const r = await pythonCausalBuildFull({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Transfer entropy links: ${r.transfer_entropy?.n_links_found ?? 0}`);
  wl(`  Lagged inference validated: ${r.lagged_inference?.n_validated ?? 0}`);
  wl(`  Causal stability — avg: ${r.causal_stability?.avg_stability ?? 0}  unstable: ${r.causal_stability?.n_unstable ?? 0}`);
  sep();
}

async function runCausalDiscTransfer() {
  sep(); wl('  🔗 PHASE 22 — Transfer Entropy'); sep();
  const r = await pythonCausalTransferEntropy({ tau_max: 5, n_sectors: 10 });
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Links found: ${r.n_links_found}  Sectors: ${r.n_sectors}`);
  (r.top_links ?? []).forEach(l => wl(`    ${(l.source??'').padEnd(20)} → ${(l.target??'').padEnd(20)} lag:${l.lag}d r:${l.strength}`));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 23 — Failure Memory Engine
// ─────────────────────────────────────────────────────────────────────────

async function runFailureDailyScan() {
  sep(); wl('  🧠 PHASE 23 — FAILURE MEMORY: Daily scan'); sep();
  const r = await pythonFailureDailyScan({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.warnings?.length) {
    wl(`  ⚠️  ${r.warnings.length} warnings:`);
    r.warnings.slice(0, 10).forEach(w => wl(`    ${(w.symbol??'').padEnd(10)} ${w.archetype}  risk: ${w.risk_level}`));
  } else { wl('  ✅ No failure warnings today'); }
  sep();
}

async function runFailureReport() {
  sep(); wl('  🧠 PHASE 23 — FAILURE MEMORY: Report'); sep();
  const r = await pythonFailureReport({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.archetype_summary) {
    wl('  📊 Failure Archetypes:');
    r.archetype_summary.forEach(a => wl(`    ${(a.failure_archetype??'').padEnd(30)} ${a.n} events  sev: ${(a.avg_severity??0).toFixed(2)}`));
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 24 — Explosion Physics Engine
// ─────────────────────────────────────────────────────────────────────────

async function runExplosionWatchlist() {
  sep(); wl('  💥 PHASE 24 — EXPLOSION WATCHLIST'); sep();
  const r = await pythonExplosionWatchlist({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.watchlist) {
    wl(`  Date: ${r.date}  Regime: ${r.regime}  Candidates: ${r.n_total_candidates}`);
    wl('');
    r.watchlist.slice(0, 15).forEach(c =>
      wl(`    ${(c.symbol??'').padEnd(10)} ${String(c.score??0).padStart(5)}  ${(c.archetype??'').padEnd(22)}  ${(c.sector??'').padEnd(15)}  ${c.failure_mode??''}`));
  }
  sep();
}

async function runExplosionReadiness() {
  sep(); wl('  💥 PHASE 24 — EXPLOSION READINESS SCORES'); sep();
  const r = await pythonExplosionReadiness({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Date: ${r.date}  Regime: ${r.regime}  Computed: ${r.n_computed}`);
  if (r.score_stats) wl(`  Stats: max=${r.score_stats.max}  avg=${r.score_stats.avg}  above70=${r.score_stats.above_70}`);
  if (r.top_candidates) {
    wl(''); wl('  🔥 Top candidates:');
    r.top_candidates.slice(0, 10).forEach(c =>
      wl(`    ${(c.symbol??'').padEnd(10)} ${String(c.score??0).padStart(5)}  ${(c.archetype??'').padEnd(20)}  ${c.sector??''}`));
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 25 — Market DNA Engine
// ─────────────────────────────────────────────────────────────────────────

async function runDNABuild() {
  sep(); wl('  🧬 PHASE 25 — MARKET DNA: Building (percentile-ranked)…'); sep();
  const r = await pythonDNABuildFull({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.archetype_distribution) {
    wl('  📊 Archetype Distribution:');
    Object.entries(r.archetype_distribution).forEach(([k, v]) => wl(`    ${k.padEnd(30)} ${v}`));
  }
  sep();
}

async function runDNAMutations() {
  sep(); wl('  🧬 PHASE 25 — MARKET DNA: Mutations'); sep();
  const r = await pythonDNAMutations({});
  if (r.mutations?.length) {
    wl(`  Mutations: ${r.mutations.length}`);
    r.mutations.slice(0, 10).forEach(m => wl(`    ${(m.symbol??'').padEnd(10)} ${m.from_archetype} → ${m.to_archetype}`));
  } else { wl('  No mutations detected'); }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 26 — Adaptive Research Loop
// ─────────────────────────────────────────────────────────────────────────

async function runResearchAssess() {
  sep(); wl('  🔬 PHASE 26 — RESEARCH LOOP: Law Health'); sep();
  const r = await pythonResearchAssessLaws({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.law_health) {
    r.law_health.forEach(l => wl(`    ${(l.pattern_name??l.pattern_id??'').padEnd(25)} ${(l.law_status??'').padEnd(12)} prec: ${(l.precision??0).toFixed(3)}`));
  }
  if (r.all_degrading) wl(`  🚨 ${r.action_required}`);
  sep();
}

async function runResearchEvolution() {
  sep(); wl('  🔬 PHASE 26 — RESEARCH LOOP: Evolution cycle'); sep();
  const r = await pythonResearchEvolution({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.summary) {
    wl(`  Best precision after: ${r.summary.best_precision_after}`);
    wl(`  Avg precision after:  ${r.summary.avg_precision_after}`);
    wl(`  New laws promoted:    ${r.summary.n_new_laws}`);
    wl(`  Mutations tested:     ${r.summary.n_mutations}`);
  }
  sep();
}

async function runResearchDirectives() {
  sep(); wl('  🔬 PHASE 26 — RESEARCH LOOP: Directives'); sep();
  const r = await pythonResearchDirectives({});
  if (r.priority_list) {
    r.priority_list.forEach(d => wl(`    [${d.priority}] ${(d.directive_type??'').padEnd(25)} → ${d.target}`));
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 27 — Execution Reality Engine
// ─────────────────────────────────────────────────────────────────────────

async function runExecutionFeasibility() {
  sep(); wl('  ⚖️  PHASE 27 — EXECUTION REALITY: Feasibility scan'); sep();
  const r = await pythonExecutionScanFeasibility({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  wl(`  Feasible: ${r.n_feasible}  Borderline: ${r.n_borderline}  Infeasible: ${r.n_infeasible}`);
  if (r.ranked_list) {
    wl(''); wl('  ✅ Top feasible picks:');
    r.ranked_list.filter(x => x.feasibility === 'FEASIBLE').slice(0, 10).forEach(x =>
      wl(`    ${(x.symbol??'').padEnd(10)} tier: ${(x.tier??'').padEnd(10)} realistic: ${x.realistic_return_pct}%  friction: ${x.total_friction_bps}bps`));
  }
  sep();
}

async function runExecutionLiquidity() {
  sep(); wl('  ⚖️  PHASE 27 — EXECUTION REALITY: Liquidity profiles'); sep();
  const r = await pythonExecutionLiquidityProfiles({});
  if (r.error) { wl(`  ❌ ${r.error}`); return; }
  if (r.tier_distribution) {
    wl('  💧 Liquidity Tiers:');
    Object.entries(r.tier_distribution).forEach(([t, n]) => wl(`    ${t.padEnd(10)} ${n} stocks`));
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 28 — Unified Daily Synthesis (THE CROWN JEWEL)
// ─────────────────────────────────────────────────────────────────────────

async function runSynthesis() {
  sep(); wl('  👑 PHASE 28 — UNIFIED DAILY SYNTHESIS — THE CROWN JEWEL'); sep();
  const r = await pythonSynthesisBuild({});
  if (!r.success) { wl(`  ❌ ${r.error}`); return; }
  const s = r.summary;
  wl(`  Report: ${r.report_id}  (${r.duration_s}s)`);
  wl(`  Date: ${s.date}  Regime: ${s.regime}`);
  wl(`  Top candidate: ${s.top_explosion_candidate ?? 'N/A'}  Score: ${s.top_explosion_score}`);
  wl(`  Causal chains: ${s.n_causal_chains}  Active laws: ${s.n_active_laws}`);
  wl(`  Open directives: ${s.n_open_directives}  Feasible picks: ${s.n_feasible_picks}`);
  wl('');
  wl(`  ${s.narrative_ar}`);
  wl(`  ${s.narrative_en}`);
  if (r.key_risks?.length) {
    wl(''); wl('  🚨 Key Risks:');
    r.key_risks.forEach(risk => wl(`    • ${risk}`));
  }
  sep();
}

async function runSynthesisBrief() {
  sep(); wl('  👑 PHASE 28 — UNIFIED DAILY SYNTHESIS — BRIEF'); sep();
  const r = await pythonSynthesisDailyBrief({});
  if (r.brief_text) wl(r.brief_text);
  sep();
}

async function runSynthesisStatus() {
  sep(); wl('  👑 PHASE 28 — SYNTHESIS STATUS'); sep();
  const r = await pythonSynthesisStatus({});
  if (r.data_sources) {
    wl(`  Readiness: ${r.readiness_pct}%  (${r.n_available}/${r.n_total})`);
    if (r.last_synthesis?.date) wl(`  Last synthesis: ${r.last_synthesis.date}`);
    wl('');
    Object.entries(r.data_sources).forEach(([_, info]) => {
      const icon = info.available ? '✅' : '❌';
      wl(`    ${icon} ${(info.label??'').padEnd(30)} ${(info.n_rows??0).toLocaleString()} rows`);
    });
  }
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 29 — Intelligence Prioritization Layer
// ─────────────────────────────────────────────────────────────────────────

async function runPrioritizeBrief() {
  header('Phase 29 — Daily Intelligence Brief');
  const r = await pythonPrioritizerDailyBrief({});
  if (r?.market_state) {
    wl(h2(`📅 ${r.date}  |  ${r.market_state}  |  ${r.risk_level}`));
    wl(`   ${r.brief_summary ?? ''}`);
    wl(`   Dominant Force: ${r.dominant_force}`);
    wl(`   Regime: ${r.regime_stability}  Actionable: ${r.actionable_today ? '✅' : '⛔'}`);
    if (r.top_3_insights?.length) {
      wl(h2('Top Insights'));
      r.top_3_insights.forEach(i =>
        wl(`   [${i.rank}] ${(i.symbol??'').padEnd(8)} ${i.insight_text}`));
    }
    if (r.top_5_symbols?.length) {
      wl(h2('Top Symbols'));
      r.top_5_symbols.forEach(s =>
        wl(`   ${String(s.symbol).padEnd(10)} ${s.score?.toFixed(1)}  ${s.reason}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runPrioritizeScores() {
  header('Phase 29 — Intelligence Scores');
  const r = await pythonPrioritizerRun({});
  if (r?.top_10) {
    wl(`   Scored: ${r.n_scored}  Avg: ${r.avg_score?.toFixed(1)}`);
    wl(h2('Top 10'));
    r.top_10.forEach(s =>
      wl(`   ${String(s.symbol).padEnd(10)} ${s.intelligence_score?.toFixed(1).padStart(5)}  ${(s.primary_driver??'').padEnd(20)} tier:${s.tier}`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runPrioritizeAnomaly() {
  header('Phase 29 — Anomaly Detection');
  const r = await pythonPrioritizerAnomaly({});
  if (r?.anomalies !== undefined) {
    wl(`   Anomalies: ${r.n_anomalies}  Most severe: ${r.most_severe ?? 'none'}`);
    r.anomalies?.filter(a => a.severity !== 'LOW').forEach(a =>
      wl(`   ⚠️  ${String(a.symbol).padEnd(10)} [${a.severity}] ${a.anomaly_type}: ${a.description}`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 30 — Episodic Market Memory
// ─────────────────────────────────────────────────────────────────────────

async function runMemoryAnalogy() {
  header('Phase 30 — Market Analogy Report');
  const r = await pythonEpisodicAnalogy({});
  if (r?.analogy) {
    wl(`   📅 ${r.date}`);
    wl(`   🔄 ${r.analogy}`);
    wl(`   📜 ${r.historical_outcome}`);
    wl(`   🎯 Confidence: ${r.confidence}`);
    if (r.current_fingerprint_description) {
      wl(h2('Market Character'));
      Object.entries(r.current_fingerprint_description).forEach(([k, v]) =>
        wl(`   ${k.padEnd(18)} ${v}`));
    }
    const fp = r.forward_probability;
    if (fp) wl(`\n   Bull: ${(fp.bull*100).toFixed(0)}%  Bear: ${(fp.bear*100).toFixed(0)}%  Sideways: ${(fp.sideways*100).toFixed(0)}%`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runMemorySimilar() {
  header('Phase 30 — Similar Historical Episodes');
  const r = await pythonEpisodicFindSimilar({ top_k: 5 });
  if (r?.similar_episodes) {
    wl(`   Consensus: ${r.consensus_outlook}  Bull: ${(r.probability_bull*100).toFixed(0)}%  Bear: ${(r.probability_bear*100).toFixed(0)}%`);
    wl(h2('Similar Episodes'));
    r.similar_episodes.forEach(e =>
      wl(`   ${(e.episode_id??'').padEnd(30)} sim:${e.similarity?.toFixed(3)}  7d:${(e.outcome_7d*100).toFixed(1)}%  30d:${(e.outcome_30d*100).toFixed(1)}%  [${e.outcome_label}]`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 31 — Meta-Learning Engine
// ─────────────────────────────────────────────────────────────────────────

async function runMetaDirectives() {
  header('Phase 31 — Meta-Learning Directives');
  const r = await pythonMetaDirectives({});
  if (r?.directives) {
    wl(h2('Research Directives'));
    r.directives.slice(0, 8).forEach(d =>
      wl(`   [${d.priority}] ${(d.type??'').padEnd(22)} ${d.instruction}`));
    wl(h2('Research Budget'));
    Object.entries(r.research_budget_allocation ?? {}).forEach(([k, v]) =>
      wl(`   ${k.padEnd(20)} ${(v*100).toFixed(0)}%`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runMetaMap() {
  header('Phase 31 — Predictability Map');
  const r = await pythonMetaPredictabilityMap({});
  if (r?.predictability_map) {
    wl(h2('Sector × Regime Predictability'));
    Object.entries(r.predictability_map).slice(0, 8).forEach(([sector, regimes]) => {
      const scores = Object.entries(regimes).map(([reg, sc]) => `${reg}:${(sc*100).toFixed(0)}%`).join('  ');
      wl(`   ${String(sector).padEnd(22)} ${scores}`);
    });
    wl(h2('Best Opportunities'));
    r.best_opportunities?.slice(0, 5).forEach(o =>
      wl(`   ${(o.sector??'').padEnd(20)} ${(o.regime??'').padEnd(15)} opp:${o.opportunity_score?.toFixed(2)}`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 32 — Portfolio Cognition System
// ─────────────────────────────────────────────────────────────────────────

async function runPortfolioBuild() {
  header('Phase 32 — Portfolio Cognition (100,000 EGP)');
  const r = await pythonPortfolioBuildFull({ capital: 100000 });
  const alloc = r?.allocation ?? r;
  if (alloc?.portfolio) {
    wl(`   Positions: ${alloc.n_positions}  Score: ${alloc.portfolio_score?.toFixed(1)}  Mode: ${alloc.concentration_mode}`);
    wl(`   Regime: ${alloc.regime}  Friction: ${alloc.total_friction_bps}bps`);
    wl(h2('Allocation'));
    alloc.portfolio.forEach(p =>
      wl(`   ${String(p.symbol).padEnd(10)} ${(p.weight*100).toFixed(1)}%  ${(p.amount_egp??0).toLocaleString()} EGP  score:${p.intelligence_score}  ${p.size_rationale??''}`));
    if (r?.risk?.systemic_risk_score !== undefined)
      wl(`\n   Systemic Risk: ${r.risk.systemic_risk_score?.toFixed(1)}/100  [${r.risk.risk_level}]`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runPortfolioConcentrate() {
  header('Phase 32 — Adaptive Concentration');
  const r = await pythonPortfolioAdaptiveConcentration({});
  if (r?.regime) {
    wl(`   Regime: ${r.regime}`);
    wl(`   Mode:   ${r.concentration_mode}`);
    wl(`   Max positions: ${r.max_positions}  Max single: ${(r.max_single_pct*100).toFixed(0)}%  Max sector: ${(r.max_sector_pct*100).toFixed(0)}%`);
    wl(`   ${r.rationale}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 33 — Regime Transition Forecaster
// ─────────────────────────────────────────────────────────────────────────

async function runTransitionAlert() {
  header('Phase 33 — Regime Transition Alert');
  const r = await pythonTransitionAlert({});
  if (r?.headline) {
    const levelEmoji = { WATCH: '👀', WARNING: '⚠️', ALERT: '🚨', CRITICAL: '🔴' };
    wl(`   ${levelEmoji[r.alert_level] ?? '📊'} [${r.alert_level}] ${r.headline}`);
    wl(`   EWI: ${r.early_warning_index?.toFixed(1)}/100`);
    if (r.key_signals?.length) {
      wl(h2('Key Signals'));
      r.key_signals.forEach(s => wl(`   • ${s}`));
    }
    if (r.recommended_actions?.length) {
      wl(h2('Actions'));
      r.recommended_actions.forEach(a => wl(`   → ${a}`));
    }
    wl(`\n   ${r.historical_context ?? ''}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runTransitionEWI() {
  header('Phase 33 — Early Warning Index');
  const r = await pythonTransitionEWI({});
  if (r?.ewi !== undefined) {
    const bars = '█'.repeat(Math.round(r.ewi/10)) + '░'.repeat(10 - Math.round(r.ewi/10));
    wl(`   EWI: ${r.ewi?.toFixed(1)}/100  [${r.ewi_level}]  ${bars}`);
    if (r.signal_scores) {
      wl(h2('Signal Scores'));
      Object.entries(r.signal_scores).forEach(([k, v]) =>
        wl(`   ${k.padEnd(30)} ${(v*100).toFixed(0)}%`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runTransitionProbability() {
  header('Phase 33 — Transition Probability');
  const r = await pythonTransitionProbability({});
  if (r?.early_warning_index !== undefined) {
    wl(`   EWI: ${r.early_warning_index?.toFixed(1)}/100  [${r.ewi_level}]`);
    wl(`   Current: ${r.current_regime}  →  Most likely next: ${r.most_likely_next_regime}`);
    wl(h2('Probabilities'));
    wl(`   5-day:  ${(r.probabilities?.transition_5d*100).toFixed(0)}%`);
    wl(`   10-day: ${(r.probabilities?.transition_10d*100).toFixed(0)}%`);
    wl(`   20-day: ${(r.probabilities?.transition_20d*100).toFixed(0)}%`);
    if (r.signal_breakdown) {
      wl(h2('Signals'));
      Object.entries(r.signal_breakdown).forEach(([k, v]) =>
        wl(`   ${k.padEnd(25)} ${v.score?.toFixed(2)}  ${v.raw_value??''}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 34 — Cognitive Arbitration Layer
// ─────────────────────────────────────────────────────────────────────────

async function runArbitrateDailyDecisions() {
  header('Phase 34 — Daily ENTER Decisions (Arbitration)');
  const r = await pythonArbitrateDailyDecisions({});
  if (r?.top_decisions !== undefined) {
    wl(`   📅 ${r.date}  Regime: ${r.regime}  ENTER: ${r.n_enter}`);
    if (!r.top_decisions?.length) {
      wl(warn('No ENTER decisions today — market conditions unfavorable'));
    } else {
      wl(h2('Actionable Decisions'));
      r.top_decisions.forEach(d =>
        wl(`   ✅ ${String(d.symbol).padEnd(10)} conf:${d.confidence?.toFixed(0)}%  size:${d.suggested_size_pct?.toFixed(1)}%  ${d.reasoning}`));
    }
    if (r.portfolio_stats)
      wl(`\n   Total allocation: ${r.portfolio_stats.total_allocation_pct?.toFixed(1)}%`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runArbitrateAll() {
  header('Phase 34 — Arbitrate All Symbols');
  const r = await pythonArbitrateAll({});
  if (r?.decisions) {
    wl(`   Regime: ${r.regime}  EWI: ${r.ewi?.toFixed(1)}`);
    wl(`   ✅ ENTER:${r.n_enter}  ⏳ WAIT:${r.n_wait}  ⛔ AVOID:${r.n_avoid}  🚫 VETO:${r.n_veto}`);
    const enters = r.decisions.filter(d => d.decision === 'ENTER').slice(0, 10);
    if (enters.length) {
      wl(h2('Top ENTER'));
      enters.forEach(d =>
        wl(`   ${String(d.symbol).padEnd(10)} conf:${d.confidence?.toFixed(0)}%  ${d.score?.toFixed(1)}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runArbitrateConstitution() {
  header('Phase 34 — Decision Constitution');
  const r = await pythonArbitrateConstitution({});
  if (r?.regime) {
    wl(`   Regime: ${r.regime}  EWI: ${r.ewi?.toFixed(1)}  Posture: ${r.market_posture}`);
    wl(`   Philosophy: ${r.dominant_philosophy}`);
    wl(h2('Active Constitution Weights'));
    Object.entries(r.constitution_weights ?? {}).forEach(([k, v]) =>
      wl(`   ${k.padEnd(28)} ${(v*100).toFixed(0)}%`));
    if (r.veto_rules_active?.length) {
      wl(h2('Active Veto Rules'));
      r.veto_rules_active.forEach(vr => wl(`   🚫 ${vr}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 35 — Anti-Laws Engine
// ─────────────────────────────────────────────────────────────────────────

async function runAntiLawsDaily() {
  header('Phase 35 — Anti-Laws Daily Scan');
  const r = await pythonAntiLawsDailyScan({});
  if (r?.n_veto !== undefined) {
    wl(`   📅 ${r.date}`);
    wl(`   🚫 VETO: ${r.n_veto}  ⚠️  CAUTION: ${r.n_caution}  ✅ SAFE: ${r.n_safe}`);
    wl(`   Market anti-law breadth: ${(r.anti_law_market_breadth * 100)?.toFixed(0)}%`);
    if (r.veto_symbols?.length) {
      wl(h2('VETO Symbols — Avoid Today'));
      r.veto_symbols.slice(0, 15).forEach(s => wl(`   🚫 ${s}`));
    }
    if (r.most_dangerous_pattern)
      wl(`\n   Most dangerous: ${r.most_dangerous_pattern}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runAntiLawsReport() {
  header('Phase 35 — Anti-Laws Landscape Report');
  const r = await pythonAntiLawsReport({});
  if (r?.market_failure_risk) {
    wl(`   Library: ${r.library_size} anti-laws  |  Most active: ${r.most_active_type}`);
    wl(`   Market failure risk: ${r.market_failure_risk}`);
    if (r.highest_risk_symbols?.length) {
      wl(h2('Highest Risk Symbols'));
      r.highest_risk_symbols.slice(0, 8).forEach(s =>
        wl(`   🔴 ${String(s.symbol).padEnd(10)} ${s.active_anti_laws?.join(', ')}`));
    }
    if (r.key_warnings?.length) {
      wl(h2('Key Warnings'));
      r.key_warnings.forEach(w => wl(`   ⚠️  ${w}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 36 — Statistical Grounding Engine
// ─────────────────────────────────────────────────────────────────────────

async function runStatGrade() {
  header('Phase 36 — Statistical Law Grading');
  const r = await pythonStatGradeAllLaws({});
  if (r?.grade_distribution) {
    wl(`   Graded: ${r.n_graded}  Grounding Score: ${r.grounding_score?.toFixed(1)}/100`);
    wl(`   Significant: ${r.n_significant}  Should Retire: ${r.n_should_retire}`);
    const G = r.grade_distribution;
    wl(`\n   Grades: 🟢A:${G.A??0}  🔵B:${G.B??0}  🟡C:${G.C??0}  🟠D:${G.D??0}  🔴F:${G.F??0}`);
    if (r.top_A_laws?.length) {
      wl(h2('Grade A Laws (Confirmed Edge)'));
      r.top_A_laws.slice(0, 8).forEach(l =>
        wl(`   🟢 ${String(l.law_name ?? l.law_id).padEnd(35)} prec:${(l.precision*100).toFixed(0)}%  EAE:${l.eae?.toFixed(4)}`));
    }
    if (r.bottom_laws_to_retire?.length) {
      wl(h2('Laws to Retire'));
      r.bottom_laws_to_retire.slice(0, 5).forEach(l =>
        wl(`   🔴 ${String(l.law_name ?? l.law_id).padEnd(35)} grade:${l.grade}  ${l.recommendation}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runStatExpectancy() {
  header('Phase 36 — Execution-Adjusted Expectancy');
  const r = await pythonStatExpectancyReport({});
  if (r?.n_positive_eae !== undefined) {
    wl(`   ✅ Positive EAE: ${r.n_positive_eae}  ❌ Negative EAE: ${r.n_negative_eae}`);
    wl(`   Avg EAE: ${r.avg_eae?.toFixed(4)}`);
    if (r.best_eae_laws?.length) {
      wl(h2('Best Expectancy Laws'));
      r.best_eae_laws.slice(0, 8).forEach(l =>
        wl(`   ${String(l.law).padEnd(35)} EAE:${l.eae?.toFixed(4)}  prec:${(l.precision*100).toFixed(0)}%  ${l.grade}`));
    }
    if (r.laws_to_retire?.length) {
      wl(h2('Retire These (Negative EAE)'));
      r.laws_to_retire.slice(0, 5).forEach(l =>
        wl(`   ❌ ${String(l.law).padEnd(35)} EAE:${l.eae?.toFixed(4)}  ${l.reason}`));
    }
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Phase 37 — Intelligence Reliability Observatory

async function runObservatoryFull() {
  header('Phase 37 — Intelligence Reliability Observatory');
  const r = await pythonObservatoryBuildFull({});
  if (r?.sts !== undefined) {
    const s = r.status === 'OPERATIONAL' ? '🟢' : r.status === 'DEGRADED' ? '🟡' : '🔴';
    wl(`   ${s} STS: ${r.sts?.toFixed(1)}/100  |  ${r.status}`);
    wl(`   Safe to trade: ${r.safe_to_trade ? '✅ YES' : '❌ NO'}`);
    wl(`   Healthy engines: ${r.n_healthy}`);
    if (r.report_summary) wl(`   ${r.report_summary}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runObservatoryHealth() {
  header('Phase 37 — Engine Health Scan');
  const r = await pythonObservatoryEngineHealth({});
  if (r?.engines) {
    wl(`   ✅ Healthy: ${r.n_healthy}  ⚠️ Degraded: ${r.n_degraded}  ❌ Missing: ${r.n_missing}`);
    wl(`   Avg health: ${r.avg_health?.toFixed(1)}/100`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runObservatoryTrustability() {
  header('Phase 37 — System Trustability Score');
  const r = await pythonObservatoryTrustability({});
  if (r?.sts !== undefined) {
    wl(`   STS: ${r.sts?.toFixed(1)}/100  |  Safe: ${r.safe_to_trade ? 'YES ✅' : 'NO ❌'}`);
    wl(`   📋 ${r.recommendation}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 38 — Cognitive Compression Engine

async function runCompressionMII() {
  header('Phase 38 — Market Intelligence Index');
  const r = await pythonCompressionMII({});
  if (r?.mii !== undefined) {
    const em = r.mii >= 80 ? '🌟' : r.mii >= 60 ? '✅' : r.mii >= 40 ? '⚪' : r.mii >= 20 ? '⚠️' : '🚨';
    wl(`   ${em} MII: ${r.mii?.toFixed(1)}/100  |  ${r.interpretation}`);
    wl(`   📋 ${r.recommendation}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runCompressionBriefing() {
  header('Phase 38 — Market Briefing');
  const r = await pythonCompressionBriefing({});
  if (r?.arabic_briefing) {
    wl(`   🇸🇦 ${r.arabic_briefing}`);
    wl(`   🇬🇧 ${r.english_briefing}`);
    wl(`   Vector: ${r.market_vector?.toFixed(3)}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runCompressionForces() {
  header('Phase 38 — Dominant Market Forces');
  const r = await pythonCompressionForces({});
  if (r?.forces) {
    wl(`   Dominant: ${r.dominant_force}  |  Vector: ${r.market_vector?.toFixed(3)}`);
    r.forces.slice(0, 5).forEach(f => {
      const dir = f.direction > 0 ? '↑' : f.direction < 0 ? '↓' : '→';
      wl(`   ${String(f.force_type).padEnd(20)} ${dir} ${(f.magnitude*100).toFixed(0)}%  ${f.evidence}`);
    });
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 39 — Uncertainty Quantification Engine

async function runUncertaintyReport() {
  header('Phase 39 — Uncertainty Report');
  const r = await pythonUncertaintyReport({});
  if (r?.total_market_uncertainty !== undefined) {
    wl(`   Epistemic:  ${(r.market_epistemic*100).toFixed(1)}%`);
    wl(`   Aleatoric:  ${(r.market_aleatoric*100).toFixed(1)}%`);
    wl(`   Total:      ${(r.total_market_uncertainty*100).toFixed(1)}%`);
    if (r.ood?.ood_level) wl(`   OOD Level:  ${r.ood.ood_level}`);
    wl(`   📋 ${r.trading_recommendation}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runUncertaintyOOD() {
  header('Phase 39 — Out-of-Distribution Detection');
  const r = await pythonUncertaintyOOD({});
  if (r?.ood_score !== undefined) {
    const em = r.ood_level === 'IN_DISTRIBUTION' ? '✅' : r.ood_level === 'MODERATE_OOD' ? '🟡' : '🔴';
    wl(`   ${em} OOD: ${(r.ood_score*100).toFixed(1)}%  |  ${r.ood_level}`);
    wl(`   Action: ${r.action}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 40 — Autonomous Research Sandbox

async function runSandboxCycle() {
  header('Phase 40 — Autonomous Research Cycle');
  const r = await pythonSandboxRunCycle({});
  if (r?.cycle_id !== undefined) {
    wl(`   Cycle: ${r.cycle_id}`);
    wl(`   Generated: ${r.n_generated}  Tested: ${r.n_tested}  ✅ Promoted: ${r.n_promoted}`);
    wl(`   Promotion rate: ${(r.promotion_rate*100)?.toFixed(1)}%`);
    if (r.promoted_laws?.length)
      r.promoted_laws.slice(0, 5).forEach(l =>
        wl(`   🧬 ${String(l.law_name).padEnd(35)} prec:${(l.precision*100).toFixed(0)}%`));
    wl(`   ${r.cycle_summary}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runSandboxReport() {
  header('Phase 40 — Sandbox Report');
  const r = await pythonSandboxReport({});
  if (r?.total_hypotheses !== undefined) {
    wl(`   Total: ${r.total_hypotheses}  Promoted: ${r.n_promoted}  Rate: ${(r.promotion_rate*100)?.toFixed(1)}%`);
    wl(`   Best source: ${r.best_source}  |  Health: ${r.sandbox_health}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 41 — Governance Constitution Engine

async function runGovernanceFull() {
  header('Phase 41 — Governance Constitution');
  const r = await pythonGovernanceBuildFull({});
  if (r?.constitution_health) {
    const em = r.constitution_health === 'CLEAN' ? '✅' : r.constitution_health === 'DEGRADED' ? '⚠️' : '🔴';
    wl(`   ${em} ${r.constitution_health}  |  Violations: ${r.n_violations}  Warnings: ${r.n_warnings}`);
    wl(`   Halt: ${r.should_halt ? '🚨 YES' : '✅ NO'}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runGovernanceAudit() {
  header('Phase 41 — Constitution Audit');
  const r = await pythonGovernanceAudit({});
  if (r?.constitution_health) {
    wl(`   Health: ${r.constitution_health}  |  Issues: ${r.n_violations + r.n_warnings}`);
    if (r.violations?.length)
      r.violations.slice(0, 5).forEach(v => wl(`   ⚠️  [${v.rule}] ${v.detail}`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runGovernanceHalt() {
  header('Phase 41 — Halt Condition Check');
  const r = await pythonGovernanceHaltCheck({});
  if (r?.should_halt !== undefined) {
    wl(`   ${r.should_halt ? '🚨 HALT TRIGGERED' : '✅ System clear'}`);
    wl(`   STS:${r.sts?.toFixed(1) ?? 'N/A'}  Uncertainty:${(r.total_uncertainty*100)?.toFixed(0) ?? 'N/A'}%  MII:${r.mii?.toFixed(1) ?? 'N/A'}`);
    wl(`   📋 ${r.recommendation}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 42 — Central Cognitive Bus

async function runBusFull() {
  header('Phase 42 — Central Cognitive Bus');
  const r = await pythonBusBuildFull({});
  if (r?.directive) {
    const dem = {ENGAGE:'🟢',WAIT:'🟡',AVOID:'🟠',DEFENSIVE:'⚠️',HALT:'🔴'}[r.directive] ?? '?';
    wl(`   ${dem} Directive: ${r.directive}  |  Coherence: ${r.coherence_score?.toFixed(1)}/100`);
    wl(`   Narrative: ${r.narrative_direction}  |  Contradictions: ${r.n_contradictions}`);
    wl(`   Global confidence: ${(r.global_confidence*100)?.toFixed(0)}%`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runBusDirective() {
  header('Phase 42 — Bus Directive');
  const r = await pythonBusDirective({});
  if (r?.directive) {
    const dem = {ENGAGE:'🟢',WAIT:'🟡',AVOID:'🟠',DEFENSIVE:'⚠️',HALT:'🔴'}[r.directive] ?? '?';
    wl(`   ${dem} ${r.directive}  (${(r.confidence*100)?.toFixed(0)}% confidence)`);
    wl(`   ${r.reason}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runBusCoherence() {
  header('Phase 42 — Cognitive Coherence');
  const r = await pythonBusCoherence({});
  if (r?.coherence_score !== undefined) {
    wl(`   Coherence: ${r.coherence_score?.toFixed(1)}/100  (${r.coherence_level})`);
    wl(`   Narrative: ${r.narrative_direction}  |  Agreement: ${(r.direction_coherence_fraction*100)?.toFixed(0)}%`);
    if (r.contradiction_pairs?.length)
      wl(`   Contradictions: ${r.contradiction_pairs.length}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 43 — Guided Research Pressure Zones

async function runPressureZones() {
  header('Phase 43 — Research Pressure Zones');
  const r = await pythonPressureIdentify({});
  if (r?.n_zones !== undefined) {
    wl(`   Active zones: ${r.n_zones}  |  Top: ${r.top_zone}`);
    r.zones?.slice(0, 5).forEach(z =>
      wl(`   🎯 [${z.zone_type}] urgency:${(z.urgency_score*100).toFixed(0)}%  ${z.description?.slice(0, 60)}`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runPressureCycle() {
  header('Phase 43 — Guided Research Cycle');
  const r = await pythonPressureCycle({});
  if (r?.cycle_id !== undefined) {
    wl(`   Cycle: ${r.cycle_id}  |  Promoted: ${r.n_promoted}  (${(r.promotion_rate*100)?.toFixed(1)}%)`);
    r.promoted_laws?.slice(0, 5).forEach(l =>
      wl(`   🎯 [${l.zone_type}] ${String(l.law_name).slice(0,40)} prec:${(l.precision*100).toFixed(0)}%`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 44 — Execution Reality Engine

async function runExecReality() {
  header('Phase 44 — Execution Reality Check');
  const r = await pythonExecRealityCheck({});
  if (r?.n_laws !== undefined) {
    wl(`   Laws checked: ${r.n_laws}  |  Survival rate: ${(r.reality_survival_rate*100)?.toFixed(0)}%`);
    wl(`   Cost drag: ${r.avg_cost_drag_bps?.toFixed(0)} bps  |  Killed: ${r.n_killed_by_costs}`);
    wl(`   ${r.reality_assessment}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runExecCalendar() {
  header('Phase 44 — Liquidity Calendar');
  const r = await pythonExecCalendar({});
  if (r?.calendar) {
    wl(`   Best day: ${r.best_day}  |  Worst: ${r.worst_day}`);
    r.calendar.forEach(d => {
      const em = d.liquidity_score >= 70 ? '🟢' : d.liquidity_score >= 50 ? '🟡' : '🔴';
      wl(`   ${em} ${String(d.day).padEnd(12)} liq:${d.liquidity_score?.toFixed(0)}  spread:${d.spread_estimate_bps?.toFixed(0)}bps  ${d.optimal_entry_window}`);
    });
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// Phase 37 Extension

async function runObservatoryEnhanced() {
  header('Phase 37 — Enhanced Observatory Health');
  const r = await pythonObservatoryEnhanced({});
  if (r?.overall_enhancement_score !== undefined) {
    const em = r.overall_status === 'HEALTHY' ? '✅' : r.overall_status === 'DEGRADED' ? '⚠️' : '🔴';
    wl(`   ${em} Enhancement: ${r.overall_enhancement_score?.toFixed(1)}/100  (${r.overall_status})`);
    if (r.alerts?.length) r.alerts.forEach(a => wl(`   🚨 ${a}`));
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

async function runObservatoryEntropyCheck() {
  header('Phase 37 — Model Entropy');
  const r = await pythonObservatoryEntropy({});
  if (r?.system_entropy !== undefined) {
    const em = r.entropy_level === 'HEALTHY' ? '✅' : r.entropy_level === 'LOW' ? '🟢' : '🔴';
    wl(`   ${em} System entropy: ${(r.system_entropy*100)?.toFixed(1)}%  (${r.entropy_level})`);
    wl(`   Arbitration: ${(r.arbitration_entropy*100)?.toFixed(1)}%  |  ${r.interpretation}`);
  } else wl(warn(r?.error ?? 'No data'));
  sep();
}

// ─────────────────────────────────────────────────────────────────────────

async function runQuantLoop() {
  const t0 = Date.now();

  sep();
  wl('  🧠 QUANT FUND MODE — Autonomous Self-Evolving Research');
  wl('  الذكاء الاصطناعي يُحلّل السوق كـ Quant Researcher مستقل');
  sep();

  const r = await pythonQuantLoop();
  if (!r.success) { wl(`  ${warn('خطأ: ' + r.error)}`); return; }

  const report = r.report || {};
  const SECTIONS = [
    ['1_discovered',          '🔍 ما اكتُشف'],
    ['2_disproven',           '❌ ما تم دحضه'],
    ['3_causal_drivers',      '🧠 المحركات السببية الحقيقية'],
    ['4_transition_probs',    '📊 احتماليات الانتقال'],
    ['5_duration_findings',   '⏳ نتائج المدة'],
    ['6_regime_dependencies', '🌍 اعتمادية الريجيم'],
    ['7_sector_differences',  '🏭 الفروق بين القطاعات'],
    ['8_failure_scenarios',   '⚠️  سيناريوهات الفشل'],
    ['9_hypotheses',          '🚀 فرضيات جديدة للاختبار'],
    ['10_architecture',       '⚙️  تحسينات معمارية'],
    ['11_opportunities',      '🎯 أفضل الفرص الحالية'],
    ['12_hidden_risks',       '📉 المخاطر الخفية'],
    ['13_learned',            '🧬 ما تعلّمه النظام هذه الجلسة'],
  ];

  for (const [key, label] of SECTIONS) {
    const lines = report[key] || [];
    if (!lines.length) continue;
    h2(label);
    for (const line of lines) wl(`  ${line}`);
  }

  wl('');
  sep();
  wl(`  ⏱  إجمالي وقت البحث: ${r.elapsed_sec}s (تشغيل: ${Date.now()-t0}ms)`);
  sep();
}

// ─────────────────────────────────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────────────────────────────────

async function main() {
  const t0 = Date.now();

  sep();
  wl('  🔬 EGX Advanced Analysis — Dr. Husam');
  wl(`  Section: ${SECTION} | ${new Date().toLocaleString('ar-EG')}`);
  sep();

  const all = SECTION === 'all';

  try {
    if (all || SECTION === 'macro')        await runMacroRegime();             // Macro Economics Dashboard
    if (all || SECTION === 'evolution')   await runMarketEvolution();         // Meta-Learning Cognitive Map
    if (all || SECTION === 'evolving')    await runEvolvingStructure();       // Evolving Structure — Alpha Decay
    if (all || SECTION === 'adaptive')    await runAdaptiveMemory();          // Bayesian Living Beliefs
    if (all || SECTION === 'conditional') await runConditionalTransitions(); // 5D Surface — أعمق تحليل
    if (all || SECTION === 'transitions') await runStateTransitions();        // Markov Chain
    if (all || SECTION === 'events')    await runEventSignals();              // Event Engine
    // ── Latent Engine ─────────────────────────────────────────────────────
    if (SECTION === 'forces')     await runBehavioralForces();    // 6-force behavioral decomposition
    if (SECTION === 'duration')   await runDurationAnalysis();    // P(TR) × duration
    if (SECTION === 'sector')     await runSectorMarkov();        // Sector Markov matrices
    if (SECTION === 'latent')     await runLatentCompress();      // PCA behavioral space
    if (SECTION === 'invariants') await runInvariantDiscovery();  // Cross-time invariants
    if (SECTION === 'temporal')   await runTemporalStability();   // Rolling window stability
    if (SECTION === 'quant')      await runQuantLoop();           // Full research loop
    // ── Force Field Engine (Phase 2) ──────────────────────────────────────
    if (SECTION === 'forcenow')   await runForceFieldNow();          // 9-force snapshot
    if (SECTION === 'coupling')   await runForceInteractions();      // coupling matrix
    if (SECTION === 'forceevo')   await runForceEvolution();         // decay + half-life
    if (SECTION === 'memory')     await runMarketMemoryForce();      // alpha decay
    if (SECTION === 'failphys')   await runFailurePhysicsForce();    // why reversals fail
    if (SECTION === 'attractors') await runForceAttractorsDisplay(); // stability basins
    if (SECTION === 'forceall')   await runForceFieldFullReport();   // full force field
    // ── Propagation Engine (Phase 3) ───────────────────────────────────
    if (SECTION === 'propnow')    await runPropagationNow();         // live snapshot
    if (SECTION === 'contagion')  await runContagionChains();        // P(B follows A)
    if (SECTION === 'sectortrans')await runSectorTransmission();     // lag matrix
    if (SECTION === 'cascades')   await runInstabilityCascades();    // cascade physics
    if (SECTION === 'roles')      await runRoleClassification();     // SOURCE/ABSORBER/etc
    if (SECTION === 'diffusion')  await runDiffusionAnalysis();      // diffusion metrics
    if (SECTION === 'regimes')    await runRegimeNetworks();         // regime topology
    if (SECTION === 'propfull')   await runPropagationFullReport();  // all combined
    // ── Energy Flow Engine (Phase 4) ───────────────────────────────────
    if (SECTION === 'energynow')    await runEnergyNow();
    if (SECTION === 'energyflow')   await runEnergyFlow();
    if (SECTION === 'energyaccum')  await runEnergyAccumulation();
    if (SECTION === 'energytrans')  await runEnergyTransformation();
    if (SECTION === 'energypersist')await runEnergyPersistence();
    if (SECTION === 'energyregime') await runRegimeEnergy();
    if (SECTION === 'energyfail')   await runFailurePhysicsEnergy();
    if (SECTION === 'energylaws')   await runEnergyInvariants();
    if (SECTION === 'energyfull')   await runEnergyFullReport();
    // ── Temporal Causality Engine (Phase 5) ────────────────────────────────
    if (SECTION === 'causalnow')    await runCausalNow();          // live causal events
    if (SECTION === 'causalchains') await runCausalChains();       // A→B→C patterns
    if (SECTION === 'feedback')     await runFeedbackLoops();      // amplifying/dampening cycles
    if (SECTION === 'causalmem')    await runTemporalMemory();     // causal half-life
    if (SECTION === 'causalroles')  await runSectorCausalRoles();  // sector causal roles
    if (SECTION === 'causalfail')   await runCausalFailure();      // why chains broke
    if (SECTION === 'causalregimes')await runRegimeCausality();    // per-regime graphs
    if (SECTION === 'causallaws')   await runCausalInvariants();   // universal laws
    if (SECTION === 'causalfull')   await runCausalFullReport();   // complete report

    // ── Adaptive Decision Engine (Phase 6) ────────────────────────────────
    if (SECTION === 'decisionnow')  await runDecisionNow();             // live EBP snapshot
    if (SECTION === 'oppscan')      await runOpportunityScan();         // full EBP decomposition
    if (SECTION === 'portfolio6')   await runPortfolioOptimize();       // portfolio optimizer
    if (SECTION === 'uncertmap')    await runUncertaintyMap();          // uncertainty mapping
    if (SECTION === 'regimedec')    await runRegimeDecisions();         // regime-dependent policies
    if (SECTION === 'inaction')     await runInactionAnalysis();        // when NOT to trade
    if (SECTION === 'decisionfail') await runDecisionFailureAnalysis(); // failure profiles
    if (SECTION === 'thresholds')   await runAdaptiveThresholds();      // self-evolving thresholds
    if (SECTION === 'decisionfull') await runDecisionFullReport();      // full decision report

    // ── World–Market Coupling Engine (Phase 8) ────────────────────────────
    if (SECTION === 'worldnow')       await runWorldCouplingNow();
    if (SECTION === 'worldfx')        await runWorldFxImpact();
    if (SECTION === 'worldmacro')     await runWorldMacroRegimes();
    if (SECTION === 'worldliq')       await runWorldLiquidityCycle();
    if (SECTION === 'worldsectors')   await runWorldSectorCoupling();
    if (SECTION === 'worldmem')       await runWorldShockMemory();
    if (SECTION === 'worldcontagion') await runWorldContagionScan();
    if (SECTION === 'worldstable')    await runWorldCouplingStability();
    if (SECTION === 'worldadaptive')  await runWorldAdaptive();
    if (SECTION === 'worldfull')      await runWorldCouplingFull();

    // ── Cognitive Orchestrator (Phase 9) ──────────────────────────────────
    if (SECTION === 'orchhealth')    await runOrchHealth();
    if (SECTION === 'orchnow')       await runOrchNow();
    if (SECTION === 'orcharb')       await runOrchArbitrate();
    if (SECTION === 'orchconf')      await runOrchConfidence();
    if (SECTION === 'orchconflicts') await runOrchConflicts();
    if (SECTION === 'orchposture')   await runOrchPosture();
    if (SECTION === 'orchwatch')     await runOrchWatch();
    if (SECTION === 'orchsync')      await runOrchSync();
    if (SECTION === 'orchreport')    await runOrchReport();
    if (SECTION === 'orchfull')      await runOrchFull();

    // ── Market Operating System (Phase 10) ────────────────────────────────
    if (SECTION === 'ospipeline')    await runOsPipelineRun();
    if (SECTION === 'osstatus')      await runOsPipelineStatus();
    if (SECTION === 'osdashboard')   await runOsDashboard();
    if (SECTION === 'osalert')       await runOsAlertScan();
    if (SECTION === 'osarchive')     await runOsArchive();
    if (SECTION === 'oshealth')      await runOsHealth();
    if (SECTION === 'osresilience')  await runOsResilience();
    if (SECTION === 'osobserve')     await runOsObservability();
    if (SECTION === 'osreplay')      await runOsReplay();
    if (SECTION === 'osfull')        await runOsFull();

    // ── Telegram Report Formatter (Phase 11) ──────────────────────────────
    if (SECTION === 'tgtest')        await runTgTestFormat();
    if (SECTION === 'tgformat')      await runTgFormatDaily();
    if (SECTION === 'tgalert')       await runTgFormatAlert();
    if (SECTION === 'tgdelta')       await runTgFormatDelta();

    // ── Deep Market Intelligence Discovery (Phase 12) ─────────────────────
    if (SECTION === 'dmids')         await runDmidsStatus();
    if (SECTION === 'dmids:profiles')await runDmidsProfiles();
    if (SECTION === 'dmids:explode') await runDmidsExplosions();
    if (SECTION === 'dmids:precursors') await runDmidsPrecursors();
    if (SECTION === 'dmids:sectors') await runDmidsSectors();
    if (SECTION === 'dmids:report')  await runDmidsReport();
    if (SECTION === 'dmids:full')    await runDmidsFull();

    // ── Phase 13 — DHVD: Deep Historical Validation ───────────────────────
    if (SECTION === 'dhvd')              await runDhvdStatus();
    if (SECTION === 'dhvd:laws')         await runDhvdValidateLaws();
    if (SECTION === 'dhvd:families')     await runDhvdFamilies();
    if (SECTION === 'dhvd:regimes')      await runDhvdRegimes();
    if (SECTION === 'dhvd:breakouts')    await runDhvdFalseBreakouts();
    if (SECTION === 'dhvd:hypotheses')   await runDhvdHypotheses();
    if (SECTION === 'dhvd:report')       await runDhvdReport();
    if (SECTION === 'dhvd:full')         await runDhvdFull();

    // ── Phase 14 — Law Synthesis Engine ───────────────────────────────────
    if (SECTION === 'ls')               await runLsStatus();
    if (SECTION === 'ls:stability')     await runLsStability();
    if (SECTION === 'ls:counterfactuals') await runLsCounterfactuals();
    if (SECTION === 'ls:mutations')     await runLsMutations();
    if (SECTION === 'ls:interactions')  await runLsInteractions();
    if (SECTION === 'ls:network')       await runLsNetwork();
    if (SECTION === 'ls:physics')       await runLsPhysics();
    if (SECTION === 'ls:regimes')       await runLsRegimeSystems();
    if (SECTION === 'ls:report')        await runLsReport();
    if (SECTION === 'ls:full')          await runLsFull();

    // ── Phase 15 — Self-Learning Market Evolution ──────────────────────────
    if (SECTION === 'evo')              await runEvoStatus();
    if (SECTION === 'evo:experience')   await runEvoExperience();
    if (SECTION === 'evo:confidence')   await runEvoConfidence();
    if (SECTION === 'evo:reinforce')    await runEvoReinforce();
    if (SECTION === 'evo:failures')     await runEvoFailures();
    if (SECTION === 'evo:stocks')       await runEvoStocks();
    if (SECTION === 'evo:hypotheses')   await runEvoHypotheses();
    if (SECTION === 'evo:regimes')      await runEvoRegimes();
    if (SECTION === 'evo:full')         await runEvoFull();

    // ── Self-Evolving Intelligence (Phase 7) ──────────────────────────────
    if (SECTION === 'evostatus')   await runMetaStatus();              // system health dashboard
    if (SECTION === 'evodecay')    await runDecayScan();               // model decay detection
    if (SECTION === 'evohypo')     await runHypothesisGen();           // new hypotheses
    if (SECTION === 'evocompete')  await runArchCompete();             // architecture competition
    if (SECTION === 'evotaxon')    await runTaxonomyAudit();           // taxonomy audit
    if (SECTION === 'evoregimes')  await runRegimeIntelligencePh7();   // regime-phase reliability
    if (SECTION === 'evomemory')   await runEvolutionMemory();         // evolutionary history
    if (SECTION === 'evometadec')  await runMetaDecision();            // trust/rebuild decision
    if (SECTION === 'evorewrite')  await runSelfRewrite();             // redesign proposals
    if (SECTION === 'evofull')     await runEvolutionFullReport();     // full evolution report

    // ── Phase 16 — Autonomous Market Cognition Engine ─────────────────────
    if (SECTION === 'cog')            await runCogStatus();
    if (SECTION === 'cog:stock_dna')  await runCogStockDNA();
    if (SECTION === 'cog:sector_dna') await runCogSectorDNA();
    if (SECTION === 'cog:explosions') await runCogExplosions();
    if (SECTION === 'cog:laws')       await runCogLaws();
    if (SECTION === 'cog:memory')     await runCogMemory();
    if (SECTION === 'cog:evolve')     await runCogEvolve();
    if (SECTION === 'cog:full')       await runCogFull();

    // ── Phase 17 — Graph Contagion Engine ─────────────────────────────────
    if (SECTION === 'graph:full')        await runGraphFull();
    if (SECTION === 'graph:pagerank')    await runGraphPagerank();
    if (SECTION === 'graph:communities') await runGraphCommunities();

    // ── Phase 18 — RL Environment & Walk-Forward ──────────────────────────
    if (SECTION === 'rl:walkforward')    await runRLWalkForward();
    if (SECTION === 'rl:report')         await runRLReport();

    // ── Phase 19 — SHAP Explainability Engine ─────────────────────────────
    if (SECTION === 'explain:daily')     await runExplainDaily();
    if (SECTION === 'explain:importance')await runExplainImportance();
    if (SECTION === 'explain:report')    await runExplainReport();

    // ── Phase 5 Extension — PCMCI Causal Discovery ────────────────────────
    if (SECTION === 'causal:pcmci')      await runCausalPCMCI();

    // ── Phase 20 — Historical Integrity Engine ────────────────────────────
    if (SECTION === 'integrity:report')  await runIntegrityReport();
    if (SECTION === 'integrity:breadth') await runIntegrityBreadth();

    // ── Phase 21 — Unified Market Cognition Graph ─────────────────────────
    if (SECTION === 'umcg:build')        await runUMCGBuild();
    if (SECTION === 'umcg:snapshot')     await runUMCGSnapshot();
    if (SECTION === 'umcg:status')       await runUMCGStatus();

    // ── Phase 22 — Causal Discovery Engine ───────────────────────────────
    if (SECTION === 'causal:disc:full')     await runCausalDiscFull();
    if (SECTION === 'causal:disc:transfer') await runCausalDiscTransfer();

    // ── Phase 23 — Failure Memory Engine ─────────────────────────────────
    if (SECTION === 'failure:scan')      await runFailureDailyScan();
    if (SECTION === 'failure:report')    await runFailureReport();

    // ── Phase 24 — Explosion Physics Engine ──────────────────────────────
    if (SECTION === 'explosion:watchlist')  await runExplosionWatchlist();
    if (SECTION === 'explosion:readiness')  await runExplosionReadiness();

    // ── Phase 25 — Market DNA Engine ──────────────────────────────────────
    if (SECTION === 'dna:build')         await runDNABuild();
    if (SECTION === 'dna:mutations')     await runDNAMutations();

    // ── Phase 26 — Adaptive Research Loop ────────────────────────────────
    if (SECTION === 'research:assess')     await runResearchAssess();
    if (SECTION === 'research:evolve')     await runResearchEvolution();
    if (SECTION === 'research:directives') await runResearchDirectives();

    // ── Phase 27 — Execution Reality Engine ──────────────────────────────
    if (SECTION === 'execution:feasibility') await runExecutionFeasibility();
    if (SECTION === 'execution:liquidity')   await runExecutionLiquidity();

    // ── Phase 28 — Unified Daily Synthesis ───────────────────────────────
    if (SECTION === 'synthesis:run')     await runSynthesis();
    if (SECTION === 'synthesis:brief')   await runSynthesisBrief();
    if (SECTION === 'synthesis:status')  await runSynthesisStatus();

    // ── Phase 29 — Intelligence Prioritization Layer ───────────────────────
    if (SECTION === 'prioritize:brief')   await runPrioritizeBrief();
    if (SECTION === 'prioritize:scores')  await runPrioritizeScores();
    if (SECTION === 'prioritize:anomaly') await runPrioritizeAnomaly();

    // ── Phase 30 — Episodic Market Memory ─────────────────────────────────
    if (SECTION === 'memory:analogy')     await runMemoryAnalogy();
    if (SECTION === 'memory:similar')     await runMemorySimilar();

    // ── Phase 31 — Meta-Learning Engine ───────────────────────────────────
    if (SECTION === 'meta:directives')    await runMetaDirectives();
    if (SECTION === 'meta:map')           await runMetaMap();

    // ── Phase 32 — Portfolio Cognition ────────────────────────────────────
    if (SECTION === 'portfolio:build')    await runPortfolioBuild();
    if (SECTION === 'portfolio:concentrate') await runPortfolioConcentrate();

    // ── Phase 33 — Regime Transition Forecaster ───────────────────────────
    if (SECTION === 'transition:alert')   await runTransitionAlert();
    if (SECTION === 'transition:ewi')     await runTransitionEWI();
    if (SECTION === 'transition:prob')    await runTransitionProbability();

    // ── Phase 34 — Cognitive Arbitration Layer ────────────────────────────
    if (SECTION === 'arbitrate')          await runArbitrateDailyDecisions();
    if (SECTION === 'arbitrate:all')      await runArbitrateAll();
    if (SECTION === 'arbitrate:constitution') await runArbitrateConstitution();

    // ── Phase 35 — Anti-Laws Engine ───────────────────────────────────────
    if (SECTION === 'antilaws:daily')     await runAntiLawsDaily();
    if (SECTION === 'antilaws:report')    await runAntiLawsReport();

    // ── Phase 36 — Statistical Grounding ──────────────────────────────────
    if (SECTION === 'stat:grade')         await runStatGrade();
    if (SECTION === 'stat:expectancy')    await runStatExpectancy();

    // Phase 37 — Observatory
    if (SECTION === 'observatory')           await runObservatoryFull();
    if (SECTION === 'observatory:health')    await runObservatoryHealth();
    if (SECTION === 'observatory:trust')     await runObservatoryTrustability();

    // Phase 38 — Compression
    if (SECTION === 'compression:mii')       await runCompressionMII();
    if (SECTION === 'compression:briefing')  await runCompressionBriefing();
    if (SECTION === 'compression:forces')    await runCompressionForces();

    // Phase 39 — Uncertainty
    if (SECTION === 'uncertainty:report')    await runUncertaintyReport();
    if (SECTION === 'uncertainty:ood')       await runUncertaintyOOD();

    // Phase 40 — Research Sandbox
    if (SECTION === 'sandbox:cycle')         await runSandboxCycle();
    if (SECTION === 'sandbox:report')        await runSandboxReport();

    // Phase 41 — Governance
    if (SECTION === 'governance')            await runGovernanceFull();
    if (SECTION === 'governance:audit')      await runGovernanceAudit();
    if (SECTION === 'governance:halt')       await runGovernanceHalt();

    // Phase 42 — Cognitive Bus
    if (SECTION === 'bus')                   await runBusFull();
    if (SECTION === 'bus:directive')         await runBusDirective();
    if (SECTION === 'bus:coherence')         await runBusCoherence();

    // Phase 43 — Pressure Zones
    if (SECTION === 'pressure')              await runPressureZones();
    if (SECTION === 'pressure:cycle')        await runPressureCycle();

    // Phase 44 — Execution Reality
    if (SECTION === 'exec:reality')          await runExecReality();
    if (SECTION === 'exec:calendar')         await runExecCalendar();

    // Phase 37 Extension
    if (SECTION === 'observatory:enhanced')  await runObservatoryEnhanced();
    if (SECTION === 'observatory:entropy')   await runObservatoryEntropyCheck();

    // ── Traditional ────────────────────────────────────────────────────────
    if (all || SECTION === 'ensemble')  await runEnsemble();
    if (all || SECTION === 'portfolio') await runPortfolioFilter();
    if (all || SECTION === 'regime')    await runRegime();
    if (all || SECTION === 'sectors')   await runSectorRotation();
    if (all || SECTION === 'pairs')     await runPairsTrading();
    if (all || SECTION === 'universe')  await runUniverse();
    if (all || SECTION === 'shap')      await runShap();   // أبطأ — في الآخر
  } catch (e) {
    wl(err(`خطأ: ${e.message}`));
    process.exit(1);
  }

  sep();
  wl(`  ⏱️  إجمالي الوقت: ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  sep();
}

main().catch(e => {
  process.stderr.write(`💥 ${e.message}\n${e.stack}\n`);
  process.exit(1);
});
