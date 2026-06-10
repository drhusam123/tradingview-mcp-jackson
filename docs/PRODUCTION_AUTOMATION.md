# EGX Production Automation — Complete Reference

Last verified: 2026-06-11 | Gate: `npm run egx:prod:ready:full` → **7/7 PASS**

## One-command confidence

```bash
npm run egx:prod:ready        # daily gate (skip CDP)
npm run egx:prod:ready:full     # + TV CDP + unit tests
npm run egx:automation:status   # runbook + digest + cron log scan
```

Reports: `data/prod_ready_last.json` | `data/full_verify_last.json` | `data/session_ready_last.json`

## Delivery pipeline (client)

```
score_all → gates → promote → egx_safety_check (TRADING_LESSONS)
  → pre_send_check → egx_telegram_daily → Telegram
  → notification_delivery_audit
```

Cron wrapper: `egx_telegram_cron.mjs` = prepare-send → live → reconcile → audit.

## Daily cron (Sun–Thu Cairo, UTC+2 summer)

| Time | Job | Log |
|------|-----|-----|
| 05:15 | `egx_full_verify --skip-tests --skip-cdp` | `logs/full_verify.log` |
| 07:00 | `egx:prod:status` | `logs/prod_status.log` |
| 07:10 | `egx_session_ready` | `logs/session_ready.log` |
| 07:15 | `egx_cron_log_check --hours 48` | `logs/cron_log_check.log` |
| 12:30 | `fetch_intraday_live` | `logs/tv_live.log` |
| 15:15 | `fetch_intraday_live` | `logs/tv_live.log` |
| 16:30 | `egx_tv_auto_update --launch --pine --tech` | `logs/tv_auto_daily.log` |
| 17:20 | `egx_telegram_cron` | `logs/telegram.log` |
| 17:45 | `egx_post_session_ops` | `logs/post_session.log` |

**Weekly (Sun 06:30):** `egx_quality_weekly` → `build_full` deep audit → `logs/quality_weekly.log`

**Weekly (Sun 06:45):** `egx_prod_ready --skip-cdp` → `logs/prod_ready.log`

**Daily L2 gate:** `gate_daily` inside `egx_tv_auto_update` (~1s, blocks ML if stale/corrupt)

**P6 proof loop:** `npm run egx:proof:forensic` → `data/proof_forensic_last.json`

**Master closed loop (Sun 06:40 + post-session):** `egx:closed:loop` → delivered sync + learning + runtime rules + research directives + opportunity quality + discovery feedback + `p6_research_context.json` + opportunity followup

**P6 → evolution/cognition:** `egx_evolution.mjs` / `egx_cognition.mjs` read `data/p6_research_context.json` (ULTRA losses, downrank hints) and wire live outcomes into behavioral memory

**Opportunity trend:** `egx:opportunity:followup` — alerts from `opportunity_quality_history.json`

**Learning sub-loop:** `egx:learning:loop` → forensic + counterfactual + `delivery_laws_*.json`

**Loss autopsy:** `npm run egx:loss:autopsy` → `data/loss_autopsy_last.json` (residual ULTRA loss patterns)

Locks: `egx-tv-sync` | `egx-telegram` | `egx-post-session` (separate — no blocking)

## Ops alerts (Telegram)

| Env | Default | Purpose |
|-----|---------|---------|
| `EGX_ALERT_TELEGRAM` | 1 | Failure alerts |
| `EGX_OPS_SUCCESS_ALERT` | 1 | Post-send success digest |
| `EGX_AUTO_BACKFILL` | 0 | Auto-recover pending in post-session |

**Failures:** `CRON_DELIVERY_FAILED` | `TV_SYNC_FAILED` | `SESSION_READY_FAIL` | `FULL_VERIFY_FAILED` | `CRON_LOG_FAILURES` | `PROD_READY_FAIL`

**Success:** `CRON_DELIVERY_OK` | `POST_SESSION_OK` | `PROD_READY_OK`

Test: `npm run egx:alert:test` | `npm run egx:alert:test:success`

## Verify matrix

| Command | What |
|---------|------|
| `egx:automation:verify` | 40 checks (cron, env, scripts) |
| `egx:automation:verify:ci` | 27 structural (GitHub Actions) |
| `egx:verify:fast` | MCP + automation + reconcile + decision bot |
| `egx:verify:all` | + TV CDP + unit tests |
| `egx:tv:verify` | 19 TV MCP integration checks |
| `egx:accept` | 11 production acceptance gates |
| `test:ci` | 53 unit tests |
| `egx:learning:loop` | forensic + counterfactual + delivery laws |
| `egx:loss:autopsy` | ULTRA loss pattern autopsy |
| `egx:cache:backfill` | Historical indicators_cache gaps |
| `egx:p6:status` | P6 samples + counterfactual WR |
| `egx:closed:loop` | Master closed loop (9 stages incl. P6 context) |
| `egx:opportunity:followup` | Opportunity quality trend alerts |
| `egx:loop:audit` | Closed-loop artifact freshness + wiring audit |
| `egx:p6:sync` | Full evolution + cognition consume P6 context |
| `egx:p6:sync:light` | Lightweight P6 bridge (~15s, post-session) |
| `egx:quality:gate` | Fast L2 `gate_daily` |
| `egx:proof:forensic` | ULTRA WR breakdown |

## Manual recovery

```bash
npm run egx:notify:reconcile     # gaps?
npm run egx:notify:recovery        # dry-run pending
EGX_AUTO_BACKFILL=1 npm run egx:notify:recovery -- --send
npm run egx:cron:telegram:dry      # full cron dry-run
```

## Activate / reinstall

```bash
npm run egx:prod:activate   # cron (51 jobs) + env sync + verify
npm run egx:env:sync        # merge .env.template → .env
```

## Current status (2026-06-11)

- Delivery: 4/4 sent (latest NARE 2026-06-10)
- Reconcile: 0 pending
- Next session: **2026-06-14** (Sunday) — fully automated
