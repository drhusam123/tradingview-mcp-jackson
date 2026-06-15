# Database Audit

**Generated:** 2026-06-15T08:35:31.485851+00:00
**Database:** `data/egx_trading.db`
**Tables:** 287 | **Audited (important):** 68

| Table | Rows | Latest Date | Written By | Read By | Schema | Action |
|-------|------|-------------|------------|---------|--------|--------|
| `feature_store` | 1,742,891 | — | — | — | — | none |
| `counterfactual_events` | 575,828 | 2026-06-14 | — | — | — | none |
| `data_quality_log` | 358,037 | 2026-06-11 | — | — | — | none |
| `market_experience` | 298,516 | 2026-06-14 | — | — | — | none |
| `failure_reconstruction` | 84,837 | 2026-06-15 | — | — | — | none |
| `ohlcv_history` | 80,389 | 2026-06-14 | daily_update, egx_tv_auto_update | scan, ML, gates | — | none |
| `closing_pressure_daily` | 77,369 | 2026-06-14 | — | — | — | none |
| `ohlcv_15min` | 75,014 | 2026-06-14 | — | — | — | none |
| `ohlcv_60min` | 74,223 | 2026-06-14 | — | — | — | none |
| `ohlcv_weekly` | 68,953 | 2026-06-11 | — | — | — | none |
| `lre_daily_scores` | 66,592 | 2026-06-14 | — | — | — | none |
| `lre_mde_dual_gate_audit` | 62,442 | 2026-06-14 | — | — | — | none |
| `egx_market_discovery_daily` | 58,615 | 2026-06-14 | mde engine | LRE, MDE shadow | — | none |
| `ohlcv_monthly` | 50,332 | 2026-06-01 | — | — | — | none |
| `tsfresh_daily` | 47,251 | 2026-06-14 | — | — | — | none |
| `macro_edge_audit` | 26,972 | 2026-06-14 | — | — | — | none |
| `sector_rotation_daily` | 24,385 | — | — | — | — | none |
| `contagion_network` | 23,172 | — | — | — | — | none |
| `explosive_moves` | 14,552 | 2026-06-14 | — | — | — | none |
| `med_analogue_scores_daily` | 12,549 | 2026-06-14 | — | — | — | none |
| `med_daily_scores` | 12,549 | 2026-06-14 | med_0_3_daily_engine | med_feed_ab, MED | — | none |
| `med_failure_patterns` | 12,549 | 2026-06-14 | — | — | — | none |
| `med_research_feed` | 12,549 | 2026-06-14 | — | — | — | none |
| `ml_adv_events` | 9,139 | — | — | — | — | none |
| `unified_signals` | 6,403 | 2026-06-14 | score_all | gates, outcomes | — | none |
| `med_conditional_edge_tables` | 6,320 | 2026-06-14 | — | — | — | none |
| `anti_law_daily_scan` | 6,233 | — | — | — | — | none |
| `sector_breadth_daily` | 5,717 | — | — | — | — | none |
| `false_breakout_anatomy` | 5,642 | 2026-06-14 | — | — | — | none |
| `arbitration_decisions` | 5,010 | — | — | — | — | none |
| `law_competition` | 4,987 | — | — | — | — | none |
| `data_integrity` | 4,808 | — | — | — | — | none |
| `spectral_shadow_log` | 4,596 | 2026-06-15 | — | — | — | none |
| `liquidity_profile` | 4,469 | — | — | — | — | none |
| `explosion_predictions` | 4,200 | 2026-06-14 | egx_ml_trainer | signal_integration | — | none |
| `stock_profiles_deep` | 4,200 | — | — | — | — | none |
| `stock_tomorrow_forecast` | 3,981 | 2026-06-14 | — | — | — | none |
| `explosion_readiness` | 3,947 | — | — | — | — | none |
| `recommendation_outcomes` | 3,378 | 2026-06-14 | outcome_filler | P6 KPI, graduation | — | none |
| `scans` | 3,328 | 2026-06-14 | scan_today | discovery | — | none |
| `bayesian_wr` | 3,304 | 2026-06-15 | — | — | — | none |
| `feature_matrix` | 3,301 | — | — | — | — | none |
| `indicators_cache` | 3,239 | 2026-06-14 | rebuild_indicators | scan_today, scorer | — | none |
| `cross_market_daily` | 3,213 | 2026-06-14 | — | — | — | none |
| `final_signals` | 2,953 | 2026-06-14 | signal_integration, promotion | telegram, audit | — | none |
| `gate_audit_snapshots` | 2,953 | 2026-06-14 | gate_doctor | audit, diagnostics | — | none |
| `market_cycles` | 2,839 | — | — | — | — | none |
| `market_physics` | 2,560 | 2026-05-14 | — | — | — | none |
| `law_quality_history` | 2,474 | — | — | — | — | none |
| `data_quality_bar_exclusions` | 2,416 | 2026-05-25 | — | — | — | none |
| `med_feed_ab_ledger` | 2,307 | 2026-06-14 | — | — | — | none |
| `intraday_live_quotes` | 2,146 | — | — | — | — | none |
| `opportunity_score_v2` | 1,509 | 2026-06-14 | — | — | — | none |
| `anti_laws` | 1,493 | — | — | — | — | none |
| `tv_data_reconcile_items` | 1,436 | 2026-06-10 | — | — | — | none |
| `pine_analytics` | 1,384 | 2026-06-14 | — | — | — | none |
| `umcg_edges` | 1,318 | 2026-06-13 | — | — | — | none |
| `spectral_reliability` | 1,261 | — | — | — | — | none |
| `lre_walk_forward_shadow_pilot` | 1,256 | 2026-06-14 | — | — | — | none |
| `market_breadth_history` | 1,233 | — | — | — | — | none |
