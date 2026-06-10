/**
 * EGX Module — نقطة الدخول الموحّدة
 * ====================================
 * استورد كل شيء من هنا:
 *   import { scoreSetup, rankStocks, backtestSymbol, getStats } from './src/egx/index.js';
 */

export { scoreSetup, rankStocks, SETUP_TYPES, FILTERS }       from './scorer.js';
export { getDB, saveScan, saveTrade, savePostMortem,
         updateSetupPerformance, getStats, getBestSetups,
         saveOHLCV, getOHLCV, upsertStockUniverse, getHistoryStats,
         saveRulePerformance, getRulePerformance, getAllRulesPerformance,
         saveFinancialData, getFinancialData, getUndervaluedStocks,
         addNote, searchNotes,
         saveDailyReport, getLastReport, getReportByDate,
         getOHLCVRange, getStaleSymbols,
         saveIndicatorsCache, getLatestIndicators,
         getSignalsFromCache, getIndicatorsCacheStats }           from './database.js';
export { analyzeByDayOfWeek, analyzeVolumeZones,
         analyzeWinRateTrend, analyzeBySymbol,
         generateLearningReport,
         buildCorrelationMatrix, walkForwardValidation,
         discoverNewPatterns, getVolumePercentile,
         calculateSystemSharpe,
         analyzeRsiReturnCurve, analyzeScoreCalibration,
         quickComboScan }                                        from './learning.js';
export { calculateIndicators, quickScan }                        from './indicators.js';
export { runPythonAnalysis, pythonFullStats, pythonReturnAnalysis,
         pythonSignalBacktest, pythonSectorMomentum,
         pythonRollingStats, pythonExportCSV,
         checkPythonBridge,
         pythonParamSweep, pythonWalkForward,
         pythonMLSignal, pythonEgxPatterns,
         pythonShapAnalysis, pythonRegimeDetection,
         pythonEnsembleSignal, pythonActiveUniverse,
         pythonSectorRotation, pythonPairsTrading,
         pythonMacroData, eventSignals,
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
         runForceFieldAnalysis,
         pythonForceFieldNow,
         pythonForceInteractions,
         pythonForceEvolution,
         pythonMarketMemory,
         pythonFailurePhysics,
         pythonForceAttractors,
         pythonForceFieldFull,
         runPropagationAnalysis,
         pythonPropagationNow,
         pythonContagionChains,
         pythonSectorTransmission,
         pythonInstabilityCascades,
         pythonRoleClassification,
         pythonDiffusionAnalysis,
         pythonRegimeNetworks,
         pythonPropagationFull,
         runEnergyAnalysis,
         pythonEnergyNow,
         pythonEnergyFlow,
         pythonEnergyAccumulation,
         pythonEnergyTransformation,
         pythonEnergyPersistence,
         pythonRegimeEnergy,
         pythonFailurePhysicsEnergy,
         pythonEnergyInvariants,
         pythonEnergyFull,
         runCausalAnalysis,
         pythonCausalNow,
         pythonCausalChains,
         pythonFeedbackLoops,
         pythonTemporalMemory,
         pythonSectorCausalRoles,
         pythonCausalFailure,
         pythonRegimeCausality,
         pythonCausalInvariants,
         pythonCausalFull,
         runDecisionAnalysis,
         pythonDecisionNow,
         pythonOpportunityScan,
         pythonPortfolioOptimize,
         pythonUncertaintyMap,
         pythonRegimeDecisions,
         pythonInactionAnalysis,
         pythonDecisionFailure,
         pythonAdaptiveThresholds,
         pythonDecisionFull,
         runEvolutionAnalysis,
         pythonMetaStatus, pythonDecayScan, pythonHypothesisGen,
         pythonArchCompete, pythonTaxonomyAudit, pythonRegimeIntelligence,
         pythonEvolutionMemory, pythonMetaDecision, pythonSelfRewrite,
         pythonEvolutionFull,
         // Phase 8 — World–Market Coupling
         runWorldCoupling,
         pythonCouplingNow, pythonFxImpact, pythonWorldMacroRegimes,
         pythonLiquidityCycle, pythonSectorCoupling, pythonShockMemory,
         pythonContagionScan, pythonCouplingStability,
         pythonAdaptiveWorldModel, pythonCouplingFull,
         // Phase 9 — Cognitive Orchestrator
         runOrchestrator,
         pythonOrchHealth, pythonOrchNow, pythonOrchArbitrate,
         pythonOrchConfidence, pythonOrchConflicts, pythonOrchPosture,
         pythonOrchWatch, pythonOrchSync, pythonOrchReport, pythonOrchFull,
         // Phase 10 — Market Operating System
         runMarketOS,
         pythonOsPipelineRun, pythonOsPipelineStatus, pythonOsDashboard,
         pythonOsAlertScan, pythonOsArchive, pythonOsHealth,
         pythonOsResilience, pythonOsObservability, pythonOsReplay, pythonOsFull,
         // Phase 11 — Telegram Report
         runTelegramReport,
         pythonTgFormatDaily, pythonTgFormatAlert, pythonTgFormatPosture,
         pythonTgFormatDelta, pythonTgTestFormat,
         // Phase 12 — DMIDS
         runDMIDS,
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
         pythonEvoHypotheses, pythonEvoRegimes, pythonEvoFull, pythonEvoP6Sync,
         // Phase 16 — Autonomous Market Cognition Engine
         pythonCogStatus, pythonCogStockDNA, pythonCogSectorDNA,
         pythonCogExplosions, pythonCogLaws, pythonCogMemory,
         pythonCogEvolve, pythonCogReport, pythonCogFull,
         // Phase 17 — Graph Contagion Engine
         pythonGraphBuild, pythonGraphPagerank, pythonGraphCommunity,
         pythonGraphContagion, pythonGraphCascade, pythonGraphCentrality,
         pythonGraphSpillover, pythonGraphFull,
         // Phase 18 — RL Environment & Walk-Forward Backtesting
         pythonRLStateVector, pythonRLBacktest, pythonRLWalkForward,
         pythonRLOptimize, pythonRLReport,
         // Phase 19 — Explainability Engine (SHAP + LightGBM)
         pythonExplainTrain, pythonExplainStock, pythonExplainImportance,
         pythonExplainDaily, pythonExplainReport, pythonExplainRetrain,
         // Phase 5 Enhancement — tigramite PCMCI
         pythonCausalPCMCI,
         // Global Macro Fetcher
         pythonMacroFetchAll, pythonMacroFetchNow,
         pythonMacroFetchStatus, pythonMacroFetchReport,
         // Phase 20 — Historical Integrity Engine
         pythonIntegrityScanAll, pythonIntegrityScanSymbol, pythonIntegrityBreadth,
         pythonIntegrityReport, pythonIntegrityConfidence, pythonIntegrityAnomalies,
         // Phase 21 — Unified Market Cognition Graph (UMCG)
         pythonUMCGBuild, pythonUMCGMetrics, pythonUMCGCommunities,
         pythonUMCGFragility, pythonUMCGSnapshot, pythonUMCGPaths, pythonUMCGGetSnapshot,
         // Phase 22 — Causal Discovery Engine
         pythonCausalTransferEntropy, pythonCausalLaggedInference, pythonCausalStability,
         pythonCausalRegime, pythonCausalMacroTransmission, pythonCausalBuildFull,
         // Phase 23 — Failure Memory Engine
         pythonFailureAnalyzeAll, pythonFailureClassify, pythonFailureFamilies,
         pythonFailurePredictive, pythonFailureRecurrence, pythonFailureDailyScan,
         pythonFailureReport, pythonFailureBuildFull,
         // Phase 24 — Explosion Physics Engine
         pythonExplosionReadiness, pythonExplosionSignatures, pythonExplosionFalseAnatomy,
         pythonExplosionSectorPhysics, pythonExplosionWatchlist, pythonExplosionBuildFull,
         // Phase 25 — Market DNA Engine
         pythonDNABuild, pythonDNAMutations, pythonDNAClusters,
         pythonDNAProfile, pythonDNASectorRefresh, pythonDNABuildFull,
         // Phase 26 — Adaptive Research Loop
         pythonResearchAssessLaws, pythonResearchDiscover, pythonResearchMutate,
         pythonResearchDirectives, pythonResearchEvolution, pythonResearchLawTree,
         // Phase 27 — Execution Reality Engine
         pythonExecutionLiquidityProfiles, pythonExecutionAdjustReturns,
         pythonExecutionPortfolioStress, pythonExecutionScanFeasibility, pythonExecutionProfile,
         // Phase 28 — Unified Daily Synthesis (THE CROWN JEWEL)
         pythonSynthesisBuild, pythonSynthesisDailyBrief, pythonSynthesisGetReport,
         pythonSynthesisGetSection, pythonSynthesisStatus,
         // Phase 29 — Intelligence Prioritization Layer
         pythonPrioritizerRun, pythonPrioritizerTopInsights, pythonPrioritizerAnomaly,
         pythonPrioritizerScoreSymbol, pythonPrioritizerDailyBrief, pythonPrioritizerBuildFull,
         // Phase 30 — Episodic Market Memory Engine
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
         pythonStatOOSValidation, pythonStatExpectancyReport, pythonStatFDR, pythonStatBuildFull,
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
         pythonGovernanceAudit, pythonGovernanceEnforce, pythonGovernanceResolve,
         pythonGovernanceHaltCheck, pythonGovernanceReport, pythonGovernanceBuildFull,
         // Phase 42 — Central Cognitive Bus
         pythonBusCollectSignals, pythonBusCoherence, pythonBusDirective,
         pythonBusRead, pythonBusContradictions, pythonBusBuildFull,
         // Phase 43 — Guided Research Pressure Zones
         pythonPressureIdentify, pythonPressureMandates, pythonPressureCycle,
         pythonPressureReport, pythonPressureBuildFull,
         // Phase 44 — Execution Reality Engine
         pythonExecSimulateEntry, pythonExecSimulateExit, pythonExecRealisticPnL,
         pythonExecCalendar, pythonExecRealityCheck, pythonExecBuildFull,
         // Phase 37 Extension — Enhanced Observatory Metrics
         pythonObservatoryLatencyDrift, pythonObservatoryFreshness,
         pythonObservatoryRegimeDisagreement, pythonObservatoryCausalSpikes,
         pythonObservatoryEntropy, pythonObservatoryFragmentation,
         pythonObservatoryEnhanced,
         // Phase 47 — Multi-Horizon Intelligence Engine
         pythonHorizonAnalyze, pythonHorizonMultiView, pythonHorizonConflict,
         pythonHorizonDominant, pythonHorizonBuildFull,
         // Phase 48 — Capital Intelligence Engine
         pythonCapitalExposure, pythonCapitalSizing, pythonCapitalDrawdown,
         pythonCapitalExploration, pythonCapitalReport, pythonCapitalBuildFull,
         // Phase 49 — Deep History Engine
         pythonDeepHistoryCoverage, pythonDeepHistoryRegime, pythonDeepHistoryVolatility,
         pythonDeepHistoryPattern, pythonDeepHistoryCycles, pythonDeepHistorySector,
         pythonDeepHistoryBuildFull,
         // Phase 50 — Intraday Intelligence Layer
         pythonIntradaySession, pythonIntradayCoverage, pythonIntradayWindow,
         pythonIntradayGaps, pythonIntradayMomentum, pythonIntradayBuildProfiles,
         pythonIntradayBuildFull,
         // Phase 51 — Cross-Market Coupling Engine
         pythonCrossMarketCoverage, pythonCrossMarketRiskOn, pythonCrossMarketUsdEgp,
         pythonCrossMarketCoupling, pythonCrossMarketMacro, pythonCrossMarketContext,
         pythonCrossMarketBuildFull,
         // Phase 52 — Liquidity Microstructure Engine
         pythonLiquiditySymbol, pythonLiquidityTiers, pythonLiquidityFilter,
         pythonLiquidityMaxSize, pythonLiquidityBuildProfiles, pythonLiquidityReport,
         pythonLiquidityBuildFull,
         // Phase 53 — Pine Analytics Bridge
         pythonPineStore, pythonPineVolumeProfile, pythonPineRSRanking,
         pythonPineVWAP, pythonPineCorpEvents, pythonPineCoverage, pythonPineBuildFull,
         // Phase 54 — Corporate Actions Tracker
         pythonCorpScanSymbol, pythonCorpScanAll, pythonCorpListEvents,
         pythonCorpConfirm, pythonCorpImpact, pythonCorpWarning, pythonCorpBuildFull,
         // Phase 55 — Unified Data Quality Gate
         pythonQualityOHLCV, pythonQualityGaps, pythonQualityContinuity,
         pythonQualityStale, pythonQualityFullAudit, pythonQualityTrustScores,
         pythonQualityOpenIssues, pythonQualityQuarantine, pythonQualityQuarantined,
         pythonQualityBuildFull,
         // Phase 56 — Market Breadth Engine
         pythonBreadthCompute, pythonBreadthAD, pythonBreadthMA,
         pythonBreadthHighsLows, pythonBreadthMcClellan, pythonBreadthSector,
         pythonBreadthSignal, pythonBreadthHistory, pythonBreadthBuildFull,
         // Phase 57 — Alert Automation
         pythonAlertGetTargets, pythonAlertLogCreated, pythonAlertListActive,
         pythonAlertSyncStatus, pythonAlertClearExpired, pythonAlertSummary,
         pythonAlertBuildFull,
         // Phase 58 — Technical Confluence
         pythonTechSaveIndicators, pythonTechScoreSymbol, pythonTechScoreBatch,
         pythonTechReport, pythonTechCoverage, pythonTechBuildFull,
         // Phase 59 — Strategy Backtest
         pythonStrategyGenerate, pythonStrategyList, pythonStrategyParse,
         pythonStrategyValidate, pythonStrategyRank, pythonStrategyBuildFull,
         // Phase 60 — Chart Visualizer
         pythonVizGetDrawSpecs, pythonVizGetTopPicksDraws, pythonVizLogScreenshot,
         pythonVizFinalizeReport, pythonVizListScreenshots, pythonVizReportSummary,
         pythonVizBuildFull,
         // Phase 61 — Intraday Monitor
         pythonMonitorSessionStatus, pythonMonitorSaveDom, pythonMonitorSaveQuotes,
         pythonMonitorExecution, pythonMonitorSpread, pythonMonitorLiveSnapshot,
         pythonMonitorBuildFull,
         // Phase 62 — Feature Factory
         pythonFeatBuildFeatures, pythonFeatGetFeatures, pythonFeatImportance,
         pythonFeatCoverage, pythonFeatBuildFull,
         // Phase 73 — Portfolio Optimizer
         pythonPortKelly, pythonPortMaxSharpe, pythonPortRiskParity, pythonPortReport,
         // Phase 74 — Walk-Forward Lab + Monte Carlo
         pythonWFSignals, pythonWFLaws, pythonWFMonteCarlo, pythonWFParamStability, pythonWFReport,
         // Phase 75 — Hidden Regime HMM
         pythonHMMFit, pythonHMMDetect, pythonHMMHistory, pythonHMMExplosionCorr, pythonHMMReport,
         // Phase 76 — Genetic Strategy Evolution
         pythonGeneticEvolve, pythonGeneticTop, pythonGeneticValidate,
         // Phase 77 — tsfresh Feature Extraction
         pythonTsfreshSymbols, pythonTsfreshExplosions, pythonTsfreshCompare, pythonTsfreshReport,
         // Phase 78 — Causal Discovery
         pythonCausalGranger, pythonCausalLag, pythonCausalMI, pythonCausalReport,
         // Phase 79 — Regime-Specific ML
         pythonRegimeMLAssign, pythonRegimeMLTrain, pythonRegimeMLEvaluate,
         pythonRegimeMLPredict, pythonRegimeMLAdversarial, pythonRegimeMLImportance, pythonRegimeMLReport,
         // Phase 80 — Triple Barrier + Meta Labeling
         pythonTBLabel, pythonTBMetaLabel, pythonTBPurgedCV, pythonTBStability, pythonTBBetSizing, pythonTBReport,
         // Phase 81 — MLflow
         pythonMLflowInit, pythonMLflowLogRun, pythonMLflowLogRegime, pythonMLflowCompare,
         pythonMLflowRegister, pythonMLflowReport,
         // Phase 82 — Event Backtesting
         pythonBTRunStrategy, pythonBTPortfolio, pythonBTWalkForward, pythonBTExecCost, pythonBTReport,
         // Phase 83 — Regime Transition
         pythonRegimeTransMatrix, pythonRegimeTransLeading, pythonRegimeTransWarning,
         pythonRegimeTransForecast, pythonRegimeTransVolAccel, pythonRegimeTransReport,
         // Phase 84 — Feature Store
         pythonFSRefresh, pythonFSGet, pythonFSDrift, pythonFSLineage, pythonFSReport,
         // Phase 63 — Explosion ML
         pythonMLTrain, pythonMLOptunaTune, pythonMLPredictToday, pythonMLPredictSymbol,
         pythonMLEvaluate, pythonMLFeatureImportance, pythonMLShapExplain, pythonMLBuildFull,
         // Phase 64 — Regime-Conditional Laws
         pythonRegimeAnalyze, pythonRegimeSignals, pythonRegimeLawMatrix,
         pythonRegimeUpdate, pythonRegimeBuildFull, pythonRegimePopulateMut,
         // Phase 65 — Signal Integration (UES)
         pythonSigScoreSymbol, pythonSigScoreAll, pythonSigDailySignals,
         pythonSigConviction, pythonSigHistory, pythonSigBuildFull,
         // Phase 66 — Realistic Backtest
         pythonBTSymbol, pythonBTUniverse, pythonBTOOS,
         pythonBTCompareLaws, pythonBTCostHurdle, pythonBTBuildFull,
         // Phase 67 — Scientific Refinement Cycle
         pythonRefineMeasure, pythonRefinePrune, pythonRefineCondition,
         pythonRefineSynthesize, pythonRefineRunCycle, pythonRefineHistory,
         pythonRefineBuildFull,
         // Phase 68 — Hypothesis DSL
         pythonHypGenerate, pythonHypList, pythonHypAdd,
         pythonHypEvaluate, pythonHypBuildFull,
         // Phase 69 — Research Grid
         pythonGridRun, pythonGridRunSingle, pythonGridStatus,
         pythonGridTopResults, pythonGridBuildFull, pythonGridVbtBacktest,
         // Phase 70 — Alpha Ranker
         pythonAlphaRankAll, pythonAlphaKill, pythonAlphaDecay,
         pythonAlphaLeader, pythonAlphaEvolve, pythonAlphaBuildFull,
         // Phase 71 — Research Director
         pythonDirectorMorning, pythonDirectorStatus, pythonDirectorTopAlpha,
         pythonDirectorHistory, pythonDirectorReport, pythonDirectorBuildFull }
  from './python_bridge.js';
// Phase 49-55 — New DB functions
export { initPhase49to55Schema,
         saveOHLCVTimeframe, getOHLCVTimeframe,
         saveCrossMarket, getCrossMarket, getCrossMarketCoverage,
         getTimeframeCoverage,
         saveDOMSnapshot, getLiquidityProfile, getLiquidityTierSummary,
         savePineAnalytics }                                       from './database.js';
export { analyzeIndicatorsDistribution, buildReturnCorrelationDF,
         topMomentumStocks, analyzeReturnsByDayDF,
         loadIndicatorsDF, loadOHLCVDF, exportIndicatorsToCSV,
         loadScansDF, pivotSetupByDay }                          from './dataframe_analysis.js';
export { testNormality, quickVaR, calculateHurst,
         analyzeMarketRegime, analyzeAutocorrelation,
         quickMonteCarloFromDB, calculateRiskMetrics,
         monteCarloSimulation }                                  from './advanced_stats.js';
export { backtestSymbol, backtestPortfolio }                    from './backtest.js';
export { createAlertsFromScores, createMorningAlerts,
         clearOldEGXAlerts }                                    from './alerts.js';
export { sendDailyReport, sendSignalAlert, sendMacroUpdate,
         sendTelegram, testTelegramConnection,
         isTelegramConfigured, telegramStatus }                 from './notify.js';

// ── EGX Universe الكامل — 268 سهم صالح (مُحدَّث من TradingView Screener + تنظيف مايو 2026) ─
// مصدر: scanner.tradingview.com/egypt/scan — تم حذف 32 رمزاً غير موجود في Screener
// تم إضافة 14 رمزاً جديداً: HAVC, FTNS, HBCO, LUTS, SNFI, GGRN, KORA, GTEX, TWSA, NMIN, MLIC, EGSA, EFAC, GEOS
export const EGX_UNIVERSE = [
  'AALR', 'ABUK', 'ACAMD', 'ACAP', 'ACFR', 'ACGC', 'ACTF', 'ADCI', 'ADIB', 'ADPC',
  'ADRI', 'AFDI', 'AFMC', 'AIDC', 'AIFI', 'AIH', 'AJWA', 'ALCN', 'ALEX', 'ALUM',
  'AMER', 'AMES', 'AMIA', 'AMOC', 'AMPI', 'ANCC', 'ANFI', 'APPC', 'APSW', 'ARAB',
  'ARCC', 'AREH', 'ARVA', 'ASCM', 'ASPI', 'ATLC', 'ATQA', 'AXPH', 'BIDI', 'BIGP',
  'BINV', 'BIOC', 'BONY', 'BTFH', 'CAED', 'CANA', 'CCAP', 'CCRS', 'CEFM', 'CERA',
  'CFGH', 'CICH', 'CIEB', 'CIRA', 'CLHO', 'CNFN', 'COMI', 'COPR', 'COSG', 'CPCI',
  'CPME', 'CRST', 'CSAG', 'DAPH', 'DCCC', 'DCRC', 'DEIN', 'DGTZ', 'DOMT', 'DSCW',
  'DTPP', 'EALR', 'EASB', 'EAST', 'EBSC', 'ECAP', 'EDFM', 'EEII', 'EFAC', 'EFIC',
  'EFID', 'EFIH', 'EGAL', 'EGAS', 'EGBE', 'EGCH', 'EGREF', 'EGSA', 'EGTS', 'EGWA',
  'EHDR', 'EITP', 'ELEC', 'ELKA', 'ELNA', 'ELSH', 'ELWA', 'EMFD', 'ENGC', 'EOSB',
  'EPCO', 'EPPK', 'ETEL', 'ETRS', 'EXPA', 'FAIT', 'FAITA', 'FCMD', 'FIRE', 'FNAR',
  'FTNS', 'FWRY', 'GBCO', 'GDWA', 'GEOS', 'GGCC', 'GGRN', 'GIHD', 'GMCI', 'GPIM',
  'GPPL', 'GRCA', 'GSSC', 'GTEX', 'GTHE', 'GTWL', 'HAVC', 'HBCO', 'HDBK', 'HDST',
  'HELI', 'HRHO', 'IBCT', 'ICFC', 'ICID', 'ICLE', 'IDRE', 'IEEC', 'IFAP', 'INEG',
  'INFI', 'IRAX', 'IRON', 'ISMA', 'ISMQ', 'ISPH', 'JUFO', 'KABO', 'KNGC', 'KORA',
  'KRDI', 'KWIN', 'KZPC', 'LCSW', 'LKGP', 'LUTS', 'MAAL', 'MASR', 'MBEG', 'MBSC',
  'MCQE', 'MCRO', 'MEGM', 'MENA', 'MEPA', 'MFPC', 'MFSC', 'MHOT', 'MICH', 'MILS',
  'MIPH', 'MISR', 'MKIT', 'MLIC', 'MMAT', 'MOED', 'MOIL', 'MOIN', 'MOSC', 'MPCI',
  'MPCO', 'MPRC', 'MTIE', 'NAHO', 'NARE', 'NBKE', 'NCCW', 'NCGC', 'NDRL', 'NEDA',
  'NHPS', 'NINH', 'NIPH', 'NMIN', 'OBRI', 'OCDI', 'OCPH', 'ODIN', 'OFH', 'OIH',
  'OLFI', 'ORAS', 'ORHD', 'ORWE', 'PACH', 'PHAR', 'PHDC', 'PHTV', 'POCO', 'POUL',
  'PRCL', 'PRDC', 'PRMH', 'QNBE', 'RACC', 'RAKT', 'RAYA', 'RKAZ', 'RMDA', 'ROTO',
  'RREI', 'RTVC', 'RUBX', 'SAIB', 'SAUD', 'SCEM', 'SCFM', 'SCTS', 'SDTI', 'SEIG',
  'SEIGA', 'SIEG', 'SIPC', 'SKPC', 'SMFR', 'SMPP', 'SNFC', 'SNFI', 'SPHT', 'SPIN',
  'SPMD', 'SUCE', 'SUGR', 'SVCE', 'SWDY', 'TALM', 'TANM', 'TAQA', 'TMGH', 'TORA',
  'TRTO', 'TWSA', 'UBEE', 'UEFM', 'UEGC', 'UNIP', 'UNIT', 'UPMS', 'UTOP', 'VALU',
  'VERT', 'VLMR', 'VLMRA', 'WATP', 'WCDF', 'WKOL', 'ZEOT', 'ZMID',
];

// الـ universe الأساسي — 25 سهم موثوق (مُحدَّث مايو 2026)
// حُذفت: QNBA→QNBE، MNHD→CIRA، EKHW→ARVA، ESRS→TAQA، SIDI→ELWA، OBGI→VALU
export const EGX_UNIVERSE_CORE = [
  'COMI','HDBK','CIEB','QNBE',
  'TMGH','PHDC','OCDI','HELI','CLHO','CIRA',
  'SWDY','IRON','AMOC','ACGC','ORWE',
  'POUL','JUFO','EFID',
  'ETEL',
  'ABUK','GBCO','ARVA','TAQA','ELWA','VALU',
];

// ── إعدادات EGX الافتراضية ────────────────────────────────────────────────
export const EGX_CONFIG = {
  timeframe:       'D',       // يومي
  scanDelay:       1800,      // ms بين كل سهم
  minScore:        55,        // الحد الأدنى للإدراج
  minScoreBrief:   65,        // الحد الأدنى لبرايف التوصية
  topN:            10,        // عدد الأسهم في البرايف
  dataDir:         'data/',
  dbFile:          'data/egx_trading.db',
  // Grid Search optimal (مُكتشَف مايو 2026)
  bestRsiThreshold: 25,
  bestAdxMin:       20,
  bestAdxMax:       25,
  bestHoldDays:     7,
  bestWinRate:      78.4,
};

// ── خريطة القطاعات (EGX sector map) ─────────────────────────────────────
export const EGX_SECTORS = {
  Banking:         ['COMI','HDBK','CIEB','QNBE','SAIB','AIDC','ADIB','AAIB'],
  RealEstate:      ['TMGH','PHDC','MNHD','OCDI','HELI','CLHO','ORHD','RREI','MASR'],
  Telecom:         ['ETEL','MENA','VALU'],
  Food:            ['POUL','JUFO','SUGR','DCRC','AMIA','NAHO'],
  Construction:    ['ARCC','ACGC','IRON','SWDY','ELKA','EGCH'],
  Chemicals:       ['AMOC','SKPC','KZPC','COPR'],
  Pharma:          ['ISPH','EGAS','PHAR','OCPH','AXPH','MIPH'],
  Textile:         ['SPIN','ORWE','ARVA'],
  Technology:      ['MCRO','RTVC','MKIT','DGTZ'],
  Finance:         ['EFID','EFIC','EFIH','AIDC','INFI','ACAP'],
  Tourism:         ['EGREF','CLHO'],
  Energy:          ['TAQA','EGAS','AMOC'],
  Retail:          ['CIRA','RAYA','ORAS'],
  Media:           ['ELWA','PHTV'],
  Diversified:     ['ABUK','GBCO','EAST','AREH'],
};

// ── حاسبة حجم المركز (Position Sizing — Kelly Criterion & Fixed Risk) ──
/**
 * احسب حجم المركز الأمثل بناءً على إحصائيات الاستراتيجية
 *
 * @param {object} opts
 * @param {number} opts.capital        - رأس المال الكلي (جنيه)
 * @param {number} opts.winRate        - نسبة الربح (0-1)، مثال: 0.64
 * @param {number} opts.avgWin         - متوسط الربح (%) عند الكسب
 * @param {number} opts.avgLoss        - متوسط الخسارة (%) عند الخسارة
 * @param {number} opts.riskPct        - الحد الأقصى للمخاطرة من رأس المال (افتراضي 1%)
 * @param {number} opts.stopLossPct    - وقف الخسارة (%) من سعر الدخول (افتراضي 5%)
 * @param {string} opts.method         - 'kelly' | 'fixed_risk' | 'half_kelly'
 * @returns {{ shares: number, capitalAtRisk: number, kellyCriterion: number, recommendation: string }}
 */
export function calcPositionSize({
  capital    = 100000,
  winRate    = 0.64,
  avgWin     = 3.0,
  avgLoss    = 2.0,
  riskPct    = 0.01,
  stopLossPct= 0.05,
  method     = 'fixed_risk',
} = {}) {
  const b     = avgWin / avgLoss;        // نسبة الربح/الخسارة
  const p     = winRate;
  const q     = 1 - p;
  const kelly = (p * b - q) / b;        // Kelly Criterion
  const halfKelly = kelly / 2;

  let positionPct;
  if (method === 'kelly')      positionPct = Math.max(0, Math.min(kelly, 0.25));
  else if (method === 'half_kelly') positionPct = Math.max(0, Math.min(halfKelly, 0.15));
  else /* fixed_risk */        positionPct = riskPct / stopLossPct;

  const positionValue = capital * Math.min(positionPct, 0.20); // cap 20% per trade
  const capitalAtRisk = positionValue * stopLossPct;

  return {
    method,
    kellyCriterion:  +kelly.toFixed(4),
    halfKelly:       +halfKelly.toFixed(4),
    positionPct:     +(positionPct * 100).toFixed(1),
    positionValue:   Math.round(positionValue),
    capitalAtRisk:   Math.round(capitalAtRisk),
    riskPct:         +(capitalAtRisk / capital * 100).toFixed(2),
    recommendation:  kelly > 0.15 ? '✅ إشارة قوية — يمكن زيادة الحجم'
                   : kelly > 0.05 ? '⚠️  إشارة متوسطة — الحجم المحسوب مناسب'
                   :                '❌ إشارة ضعيفة — تجنب أو حجم صغير جداً',
    edge:            +(p * avgWin - q * avgLoss).toFixed(3),
  };
}

// ── دالة مساعدة: قطاع السهم ────────────────────────────────────────────
export function getStockSector(symbol) {
  for (const [sector, symbols] of Object.entries(EGX_SECTORS)) {
    if (symbols.includes(symbol)) return sector;
  }
  return 'Unknown';
}

/**
 * Portfolio Filter — من قائمة الإشارات، اختر أفضل N صفقة مع:
 *   - تنويع القطاعات (max 1 سهم per sector)
 *   - ترتيب حسب composite_score
 *   - تجنب الأسهم الضعيفة جداً
 *
 * @param {Object[]} signals  - مصفوفة إشارات من ensemble_signal {symbol, composite_score, ...}
 * @param {number}   maxPos   - أقصى عدد مراكز (افتراضي 5)
 * @param {number}   minScore - الحد الأدنى للـ score (افتراضي 50)
 * @returns {Object[]}         - الصفقات المختارة مع allocation %
 */
export function filterPortfolio(signals, maxPos = 5, minScore = 50) {
  if (!signals || signals.length === 0) return [];

  // فلتر: score فوق الحد الأدنى
  const qualified = signals
    .filter(s => (s.composite_score ?? s.score ?? 0) >= minScore)
    .sort((a, b) => (b.composite_score ?? b.score ?? 0) - (a.composite_score ?? a.score ?? 0));

  const selected   = [];
  const usedSectors = new Set();

  for (const sig of qualified) {
    if (selected.length >= maxPos) break;

    const sector = getStockSector(sig.symbol);

    // max 1 per sector (تنويع)
    if (sector !== 'Unknown' && usedSectors.has(sector)) continue;

    selected.push({ ...sig, sector, allocationPct: 0 });
    usedSectors.add(sector);
  }

  // Equal-weight allocation (Half-Kelly cap at 15%)
  const equalWeight = Math.min(15, Math.floor(100 / (selected.length || 1)));
  return selected.map((s, i) => ({
    ...s,
    allocationPct:   equalWeight,
    rank:            i + 1,
    riskEGP:         Math.round(100000 * equalWeight / 100 * 0.05), // 5% stop-loss
  }));
}

// ── دالة مساعدة: schema version للـ DB ──────────────────────────────────
export const EGX_SCHEMA_VERSION = '3.3.0'; // يونيو 2026 — P0-P3 production gates + migrations
