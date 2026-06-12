# EGX Layer Registry

Single reference for **who writes** and **who reads** each production layer.
Client-facing output must flow through `final_signals.actionable=1` only.

## Layer map

| Layer | ID | Primary tables | Writer(s) | Reader(s) |
|-------|-----|----------------|-----------|-----------|
| Market ingest | L0 | `ohlcv_history` | `daily_update.mjs`, `tv_data_reconcile.mjs` | `rebuild_indicators.mjs`, all analytics |
| Data validation | L2 | `data_quality_log`, `data_trust_scores` | `data_quality_gate.py gate_daily` (in `egx_tv_auto_update`) | blocks ML if `blocked=true` |
| Indicator cache | L1 | `indicators_cache` | `rebuild_indicators.mjs` | `scan_today.mjs` (`--cache-only`), `signal_integration.py` |
| Pine analytics | L2 | `pine_analytics` | `fetch_pine_analytics.mjs` | `signal_integration.py`, `egx_ml_trainer.py` phase11 |
| Rules scan | L3 | `scans` | `scan_today.mjs` + `scorer.js` | `signal_integration.py` `score_all` |
| ML predictions | L4 | `explosion_predictions`, `feature_store` | `egx_explosion_ml.mjs`, `egx_ml_trainer.py` `predict_ensemble` | `signal_integration.py` UES |
| Product gate | L5 | `final_signals` | `signal_integration.py` `write_final_signal` | Telegram, alerts, cards, `egx_client_report.py` |
| Arbitration | L6 | `arbitration_decisions` | `cognitive_arbitration.py` | `apply_arbitration_veto` → updates L5 |
| Opportunity rank | L7 | `opportunity_score_v2`, `quant_discovery_rules` | `opportunity_score_v2.py`, `quant_discovery.py` | UES, promotion, research |
| Outcomes / WR | L8 | `recommendation_outcomes`, `forward_test_predictions`, `bayesian_wr` | `track_outcomes`, `egx_outcome_tracker.py`, `phase46` | `egx_status.mjs`, drift monitors |
| Alpha research | L9 | `grid_runs`, `alpha_rankings`, `evolved_hypotheses`, `structural_laws_*` | `research_director.py`, `night_lab.py`, `egx_discover.mjs` | UES modifiers, fabric miners |
| Delivery | L10 | `notification_delivery_audit` | `egx_telegram_cron.mjs` | Client Telegram only |
| Discovery fabric | L11 | `discovery_atom_registry`, `discovery_fabric_runs` | `egx_discovery_fabric.mjs`, `discovery_domain_miners.py` | `opportunity_score_v2`, `quant_discovery` manifest |

## Canonical daily orchestrator

**Production:** `scripts/egx_tv_auto_update.mjs` (`npm run egx:daily`)

**Discovery refresh (post-loop):** `egx_discovery_refresh.mjs` — TV micro → fabric light → opp_v2 → promotion

**DMIDS → Fabric:** `structural_laws_bridge.mjs` + `mine_dmids_structural()` unify DMIDS laws into L11 atoms

**Compat wrapper:** `scripts/run_daily.mjs` (delegates unless `--legacy`)

**Overnight research:** `scripts/python/night_lab.py` (must not overwrite EOD `final_signals` without explicit rescore)

## Single sources of truth

| Concern | SOT | Notes |
|---------|-----|-------|
| Client recommendations | `final_signals` | `unified_signals` is research mirror |
| OHLCV bars | `ohlcv_history` | UPSERT on `(symbol, bar_time)` |
| Freshness | `event_calendar.py` `staleness_trading_days` | Not calendar days |
| Telegram format | `telegram_report.py` `format_daily` | Sent only via `egx_telegram_daily.mjs` |
| Trading calendar | `event_calendar` table + `scripts/lib/egx_calendar.mjs` | Cairo timezone, 15:30 cutoff |

## Layer health checks

| Check | Command |
|-------|---------|
| Full validation | `npm run egx:validate -- --quick` |
| Status dashboard | `npm run egx:status` |
| TV MCP audit | `node scripts/tv_mcp_audit.mjs` |
| Pipeline steps | `pipeline_step_runs` table |

## Do not use for client output

- `scans` alone (pre-gate)
- `unified_signals` without `final_signals` filter
- `explosion_predictions` / ML scores directly
- `daily_report.mjs` `--notify` (blocked by policy)
- `telegram_send_cards.py` without `egx_telegram_daily.mjs` QA gate
