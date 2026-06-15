# DB Optimization Audit

**Generated:** 2026-06-15T20:50:19.969734+00:00

| Table | Rows | Indexes Before | Index Added | Status |
|-------|------|----------------|-------------|--------|
| ohlcv_history | 80395 | 5 | idx_ohlcv_symbol_time | applied |
| ohlcv_history | 80395 | 6 | idx_ohlcv_date | applied |
| indicators_cache | 3279 | 7 | idx_ic_symbol_date | applied |
| final_signals | 3207 | 5 | idx_fs_trade_date | applied |
| final_signals | 3207 | 6 | idx_fs_signal_date | fail:no such column: signal_date |
| final_signals | 3207 | 6 | idx_fs_symbol | applied |
| gate_audit_snapshots | 3207 | 2 | idx_gas_signal_date | applied |
| explosion_predictions | 4438 | 1 | idx_ep_pred_date | applied |
| meta_label_scores | 924 | 1 | idx_mls_date | applied |
| notification_delivery_audit | 192 | 2 | idx_nda_signal_date | applied |

**Backup:** `/Users/dr.husam/tradingview-mcp-jackson/data/backups/egx_trading_20260615_205019.db`
**Indexes applied:** 9

