# System Architecture (Overview)

```
TradingView Desktop (CDP :9222)
        ↓
egx_tv_auto_update.mjs → ohlcv_history / indicators_cache
        ↓
validate_market_data.py → data/market_data_validation_last.json
        ↓
Feature engines (MED, LRE, MDE shadow, Fabric, discovery*)
        ↓
score_all → gates → gate_audit_snapshots
        ↓
promotion → final_signals.actionable
        ↓
egx_prod_prepare_send → egx_telegram_cron → notification_delivery_audit
        ↓
egx_post_session_ops → system_health_check.py
```

## Layers

| Layer | Key Files | Output |
|-------|-----------|--------|
| Data | `egx_tv_auto_update.mjs`, `validate_market_data.py` | `ohlcv_history`, validation JSON |
| Features | `rebuild_indicators.mjs`, `med_0_3_daily_chain.py` | `indicators_cache` |
| Scoring | `score_all`, gate engines | `gate_audit_snapshots` |
| Actionable | `signal_integration.py`, promotion | `final_signals` |
| Delivery | `egx_telegram_cron.mjs` | Telegram + `notification_delivery_audit` |
| Ops | `egx_full_cycle.mjs`, `system_health_check.py` | `full_cycle_last.json`, health JSON |

## Device Profile

- Config: `config/performance.json`
- Daily: **fast cycle** (`--fast`) — no heavy research
- Weekly cron: deep research engines (Sunday jobs)

## Registry

- Discovery: `scripts/lib/discovery_engine_registry.mjs`
- Layers: `docs/LAYER_REGISTRY.md`
