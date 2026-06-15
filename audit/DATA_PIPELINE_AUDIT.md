# Data Pipeline Audit

**Generated:** 2026-06-15T10:06:42.088Z
**Signal date:** 2026-06-14

## Pipeline fields

| Field | Value |
|-------|-------|
| Data Source | TradingView CDP → `egx_tv_auto_update` → `ohlcv_history` |
| Universe Size | 229 |
| Expected Symbols | 269 |
| Actual Symbols | 229 |
| Missing Symbols | 40 |
| Latest Date | 2026-06-14 |
| Freshness Status | PASS |
| Bad Rows | 0 |
| Duplicates | 0 |
| Artifacts | See L0/L1 checks |

## L0/L1 checks

| Check | Status | Detail |
|-------|--------|--------|
| l0_execution_view | ✅ | ohlcv_history_execution present |
| l0_ohlcv_rows | ✅ | raw=80389 execution=78043 latest=2026-06-14 |
| l0_stock_universe | ✅ | 301 symbols | last_fetch=2026-06-15 |
| l1_indicators_cache | ✅ | 3239 rows | latest=2026-06-14 test_rows=0 |
| l0_intraday_60min | ✅ | bars=74223 symbols=254 (core target ≥20) |
| l0_intraday_15min | ✅ | bars=75014 symbols=254 |
| l1_cache_coverage | ✅ | 228/180 on 2026-06-14 |
| l1_getOHLCV_execution | ✅ | COMI bars=5 vol>0=true |
| l0_history_stats | ✅ | 269 symbols | 80389 bars |
| l0_parquet_snapshot | ✅ | ohlcv parquet rows=79503 age_h=12.3 |
| l1_parquet_indicators | ✅ | indicators parquet rows=3080 (run egx:parquet:export) |
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
| kpi_ml_meta_coverage | ✅ | meta_label @ 2026-06-14: 229 symbols (target ≥200) |
| kpi_explosion_stored | ✅ | explosion @ 2026-06-14: 198 stored (all scored, target ≥150) |
| kpi_cross_market_fresh | ✅ | lag_days=0 (target ≤1) |
| kpi_intraday_liquid | ✅ | intraday symbols=254 (liquid tier target ≥40, goal 80) |
| kpi_trust_score | ✅ | ohlcv_history trust=100 (target ≥85) |
| kpi_exclusions_consistent | ✅ | raw-exec=2346 exclusions=2346 delta=0 |
| kpi_tv_discovery | ✅ | tv_discovery @ 2026-06-14: 66 symbols (Phase 3 target ≥40) |
| kpi_ensemble_coverage | ✅ | ensemble/explosion @ 2026-06-14: 198 symbols (target ≥150) |
| kpi_pipeline_lineage | ✅ | last tv_auto summary 10.3h ago (target ≤72h) |

## Fixes Applied

- Automated via `egx_data_layer_audit.mjs` + `audit_deep_scan`

## Remaining Risks

- None critical

