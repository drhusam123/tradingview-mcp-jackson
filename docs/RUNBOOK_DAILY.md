# EGX Daily Runbook

Operational rules for daily OHLCV sync and reporting around the EGX trading calendar.

## When to run daily update

| Condition | Action |
|-----------|--------|
| Trading day, after 15:30 Cairo | Run `node scripts/daily_update.mjs` or `npm run egx:tv-auto -- --launch` |
| Official holiday / weekend | **Skip** — no automatic fetch |
| Holiday but manual backfill needed | `node scripts/daily_update.mjs --force` |
| Single symbol test on holiday | `node scripts/daily_update.mjs --symbol COMI --force` |

## Freshness (not calendar days)

Staleness is measured in **trading sessions**, via `scripts/python/event_calendar.py`:

```bash
python3 scripts/python/event_calendar.py staleness '{"data_date":"2026-05-25","ref_date":"2026-05-30"}'
```

- Before 15:30 Cairo, the reference date is the previous calendar day.
- During Eid or other seeded holidays, `staleness_trading_days: 0` means data is fresh for the last session.

## Canonical EOD pipeline (P1)

Single production path — `egx_tv_auto_update.mjs` (also `npm run egx:daily`):

```
event_calendar repair
  → daily_update (if stale) → tv_data_reconcile --repair
  → rebuild_indicators → pine local backfill → scan_today --cache-only
  → phase21 + explosion_ml + predict_ensemble
  → score_all → final_signals
  → cognitive_arbitration → apply_arbitration_veto
  → track_outcomes → forward_test fill → bayesian_wr (phase46)
  → alpha decay_check → opportunity_score_v2
  → egx_validate --quick → egx_telegram_daily
```

Weekly alpha loop: `python3 scripts/python/research_director.py morning_run` (grid→rank→kill→evolve→re-grid).

`run_daily.mjs` without `--legacy` delegates to this path. Client Telegram uses only `egx_telegram_daily.mjs` + `telegram_report.py` reading `final_signals.actionable=1`.

## Schema migrations

```bash
npm run egx:migrate          # apply pending SQL migrations
npm run egx:migrate -- --check   # show status only
```

## Pre-deploy

```bash
npm run egx:preflight        # migrations + tests + validate + acceptance
npm run egx:accept           # production acceptance gate only
npm run egx:go-live          # preflight + Telegram dry-run
npm run egx:go-live:send     # preflight + live client send
```

See [PRODUCTION_READINESS.md](./PRODUCTION_READINESS.md).

## Production automation (2026-06)

```bash
npm run egx:automation:status   # runbook + digest + cron log scan
npm run egx:runbook             # today's cron schedule + delivery status
npm run egx:session:ready       # upstream + cron gates
npm run egx:verify:fast         # structural verify (no CDP)
npm run egx:cron:telegram:dry   # full telegram cron dry-run
npm run egx:ops:digest          # delivery reconcile summary
npm run egx:cron:log-check      # scan logs for failures (48h)
```

Cron chain (Cairo, Sun–Thu): verify 05:15 → status 07:00 → session ready 07:10 → log scan 07:15 → TV sync 16:30 → Telegram 17:20 → post-session 17:45.

Ops alerts: failures (`EGX_ALERT_TELEGRAM=1`) + success digest after send (`EGX_OPS_SUCCESS_ALERT=1`).

## Commands

```bash
# Official daily (recommended)
npm run egx:daily

# Same via compat wrapper
node scripts/run_daily.mjs

# Legacy 30+ step chain (research only)
node scripts/run_daily.mjs --legacy

# Status (trading-day freshness)
npm run egx:status

# Full validation
npm run egx:validate -- --quick

# Force OHLCV sync
node scripts/daily_update.mjs --force
```

## TradingView CDP (macOS)

Do **not** spawn the Desktop binary with `--remote-debugging-port` directly. Use:

- `tv_launch` MCP tool (uses `open -a TradingView --args --remote-debugging-port=9222`)
- `scripts/lib/ensure_tv.mjs`

Or set `TV_CDP_BROWSER=chrome` for Chrome CDP.

## Telegram timeout

`runTelegramReport` uses `PY_TIMEOUT` (default 120s). Increase if `egx:validate` reports `parse_error` on Telegram checks.
