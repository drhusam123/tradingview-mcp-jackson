# Data Pipeline Audit

**Generated:** 2026-06-15T20:06:36.046Z
**Signal date:** 2026-06-15

## Pipeline fields

| Field | Value |
|-------|-------|
| Data Source | TradingView CDP → `egx_tv_auto_update` → `ohlcv_history` |
| Universe Size | 6 |
| Expected Symbols | 269 |
| Actual Symbols | 6 |
| Missing Symbols | 263 |
| Latest Date | 2026-06-15 |
| Freshness Status | PASS |
| Bad Rows | 0 |
| Duplicates | 0 |
| Artifacts | See L0/L1 checks |

## L0/L1 checks

| Check | Status | Detail |
|-------|--------|--------|
| l0_execution_view | ✅ | ohlcv_history_execution present |
| l0_ohlcv_rows | ✅ | raw=80395 execution=78049 latest=2026-06-15 |
| l0_stock_universe | ✅ | 301 symbols | last_fetch=2026-06-15 |
| l1_indicators_cache | ✅ | 3279 rows | latest=2026-06-15 test_rows=0 |
| l0_intraday_60min | ✅ | bars=74223 symbols=254 (core target ≥20) |
| l0_intraday_15min | ✅ | bars=75014 symbols=254 |
| l1_cache_coverage | ✅ | 238/180 on 2026-06-15 |
| l1_getOHLCV_execution | ✅ | COMI bars=5 vol>0=true |
| l0_history_stats | ✅ | 269 symbols | 80395 bars |
| l0_parquet_snapshot | ✅ | ohlcv parquet rows=78452 age_h=0.9 |
| l1_parquet_indicators | ✅ | indicators parquet rows=3244 (run egx:parquet:export) |
| l0_parquet_universe | ✅ | universe parquet rows=301 |
| l0_parquet_intraday | ✅ | ohlcv_60min parquet rows=74223 (run egx:parquet:export) |
| hydrate_l0_wired | ✅ | HYDRATE_CMDS includes L0 targets |
| hydrate_exit_codes | ✅ | subprocess exit validated |
| getOHLCV_execution_view | ✅ | getOHLCV prefers execution view |
| batch_get_ohlcv_unified | ✅ | batch_run uses getOhlcv from data.js |
| mcp_data_get_ohlcv | ✅ | MCP data_get_ohlcv registered |
| mcp_quote_get | ✅ | MCP quote_get registered |
| kpi_universe_ghosts | ✅ | unarchived_ghosts=0 (target ≤5, ideal 0) |
| kpi_exclusion_ratio | ✅ | execution/raw=97.1% exclusions=2346 |
| kpi_weekly_gap | ✅ | daily=269 weekly=269 gap=0 |
| kpi_ml_meta_coverage | ✅ | meta_label @ 2026-06-15: 239 symbols (target ≥200) |
| kpi_explosion_stored | ✅ | explosion @ 2026-06-15: 253 stored (all scored, target ≥150) |
| kpi_cross_market_fresh | ✅ | lag_days=0 (target ≤1) |
| kpi_intraday_liquid | ✅ | intraday symbols=254 (liquid tier target ≥40, goal 80) |
| kpi_trust_score | ✅ | ohlcv_history trust=100 (target ≥85) |
| kpi_exclusions_consistent | ✅ | raw-exec=2346 exclusions=2346 delta=0 |
| kpi_tv_discovery | ✅ | tv_discovery @ 2026-06-15: 50 symbols (Phase 3 target ≥40) |
| kpi_ensemble_coverage | ✅ | ensemble/explosion @ 2026-06-15: 253 symbols (target ≥150) |
| kpi_pipeline_lineage | ✅ | last tv_auto summary 20.3h ago (target ≤72h) |

## Fixes Applied

- Automated via `egx_data_layer_audit.mjs` + `audit_deep_scan`

## Remaining Risks

- None critical

