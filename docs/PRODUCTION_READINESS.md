# EGX Production Readiness

Checklist after P0–P3 hardening (May–June 2026).

## Readiness scorecard

| Area | Status | Gate |
|------|--------|------|
| Trading calendar freshness | ✅ | `egx_calendar.mjs` + `event_calendar.py` |
| TV CDP launch (macOS) | ✅ | `ensure_tv.mjs` / `tv_launch` |
| Telegram timeout + cwd | ✅ | `TG_TIMEOUT_MS`, project root |
| Holiday guard | ✅ | `daily_update.mjs` |
| Canonical daily path | ✅ | `egx_tv_auto_update.mjs` |
| `final_signals` SOT | ✅ | `signal_integration.py` |
| ML ordering | ✅ | `predict_ensemble` before `score_all` |
| Scan cache | ✅ | `scan_today --cache-only` |
| Forward WR | ✅ | `track_outcomes` + `egx_outcome_tracker` + `phase46` |
| CI + migrations | ✅ | `.github/workflows/ci.yml`, `npm run egx:migrate` |
| Docs | ✅ | `LAYER_REGISTRY`, `DATA_FLOW`, `RUNBOOK_DAILY` |

## Pre-deploy command

```bash
npm run egx:prod:ready        # full automation gate (skip CDP)
npm run egx:prod:ready:full   # includes TV CDP verify + unit tests
npm run egx:preflight         # migrations + tests + acceptance
npm run egx:automation:status # runbook + digest + cron log scan
```

`egx:prod:ready` runs: automation verify → session ready → log scan → reconcile → acceptance → full verify.

Ops alerts: `EGX_ALERT_TELEGRAM=1` (failures) + `EGX_OPS_SUCCESS_ALERT=1` (post-send digest).

## Go-live checklist

1. `npm run egx:migrate` — DB at latest schema
2. `npm run egx:preflight` — all gates PASS
3. TradingView CDP: `TV_CDP_BROWSER=chrome` or `egx:daily --launch`
4. Cron: `node scripts/install_cron.mjs` (single Telegram owner)
5. `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
6. Go-live: `npm run egx:go-live` (dry-run) then `npm run egx:go-live:send`
   - Or with data refresh: `node scripts/egx_go_live.mjs --update --send`
7. Monitor: `npm run egx:status` — Pine POC %, forward_test pending, Bayesian CI

## Weekly research (non-client)

```bash
python3 scripts/python/research_director.py morning_run '{}'
```

Runs grid → rank → kill (≤5) → evolve → re-grid. Does not replace daily EOD.

## Known limitations

| Item | Mitigation |
|------|------------|
| Pine POC mostly OHLCV fallback | Weekly `egx:daily --pine` with TV connected |
| `forward_test` pending rows | Auto-filled by daily `egx_outcome_tracker` |
| Bayesian CI < 45% | Warning prepended in `egx_telegram_daily.mjs` (Ph46) when n≥20 |
| E2E tests need CDP | CI runs `test:ci` only (no TV) |

## Related docs

- [PRODUCTION_AUTOMATION.md](./PRODUCTION_AUTOMATION.md) — complete ops reference
- [RUNBOOK_DAILY.md](./RUNBOOK_DAILY.md)
- [LAYER_REGISTRY.md](./LAYER_REGISTRY.md)
- [DATA_FLOW.md](./DATA_FLOW.md)
