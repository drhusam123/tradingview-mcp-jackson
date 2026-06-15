# EGX Automation Runbook

**Last updated:** 2026-06-15  
**Companion:** [PRODUCTION_AUTOMATION.md](./PRODUCTION_AUTOMATION.md) · [RUNBOOK_DAILY.md](./RUNBOOK_DAILY.md)

---

## One-command daily ops

```bash
npm run egx:full-cycle              # full DAG (TV + dry-run telegram)
npm run egx:full-cycle -- --skip-cdp   # no TradingView CDP
npm run egx:full-cycle -- --send       # live Telegram (explicit)
npm run egx:health                     # PASS/WARN/FAIL snapshot
```

Artifacts: `data/full_cycle_last.json` · `data/system_health_last.json` · `logs/full_cycle_YYYYMMDD.log`

---

## Daily schedule (Cairo, Sun–Thu)

| Time | Job | Command |
|------|-----|---------|
| 05:15 | Full verify | `egx_full_verify --skip-tests --skip-cdp` |
| 07:00 | Prod status | `egx:prod:status` |
| 07:10 | Session ready | `egx:session:ready` |
| 16:30 | TV sync + score | `egx_tv_auto_update --launch --pine --tech` |
| 17:20 | Telegram cron | `egx_telegram_cron` |
| 17:45 | Post-session + health | `egx_post_session_ops` → `system_health_check --quick` |

**Weekly (Sun):** `egx_system_audit_orchestrator --all` · `egx:prod:ready --skip-cdp`

---

## Verification commands

```bash
npm run egx:health
npm run egx:verify:fast
npm run egx:prod:ready
npm run egx:audit:all
node scripts/egx_automation_verify.mjs
```

---

## Dry-run vs live-send

| Mode | Command |
|------|---------|
| Dry-run | `npm run egx:prod:prepare-send` (default) |
| Live | `npm run egx:prod:send` or `egx:full-cycle -- --send` |
| Recovery | `npm run egx:notify:recovery` |

**Block rule:** stale OHLCV/ML blocks send with explicit reason in `pre_send_check` — never silent skip.

---

## Stuck lock recovery

```bash
ls logs/*.lock
# If age >6h and no process running:
rm logs/egx-post-session.lock   # example — only if confirmed idle
npm run egx:post:session
```

Health flags stale locks: `egx:health` → `lock_conflicts`

---

## Failure scenarios

| Symptom | Action |
|---------|--------|
| HEALTH: FAIL | `npm run egx:health:json` → fix P0 checks |
| Pending deliveries | `npm run egx:notify:recovery` |
| Stale OHLCV | `npm run egx:ohlcv:catchup` |
| TV CDP down | `egx:full-cycle -- --skip-cdp` |
| Cron silent | `npm run egx:cron:log-check` |

---

## Audit reports

| Report | Command |
|--------|---------|
| All | `npm run egx:audit:all` |
| DB | `npm run egx:audit:db` |
| Data pipeline | `npm run egx:audit:data-pipeline` |
| Engines | `npm run egx:audit:engines` |
| Gates | `npm run egx:audit:gates` |
| Notification | `npm run egx:audit:notification` |
| Automation | `npm run egx:audit:automation` |

Output: `audit/*.md` + `data/system_audit_snapshot.json`

---

## Install / activate cron

```bash
npm run egx:prod:activate    # env + cron + verify
npm run egx:cron:show
```

---

## Institutional audit index

- [SYSTEM_MAP.md](../audit/SYSTEM_MAP.md)
- [ISSUES_REGISTER.md](../audit/ISSUES_REGISTER.md)
- [FULL_REPAIR_PLAN.md](../audit/FULL_REPAIR_PLAN.md)
- [FINAL_SYSTEM_AUDIT_REPORT.md](../audit/FINAL_SYSTEM_AUDIT_REPORT.md)
- [AUDIT_CLOSED.md](../audit/AUDIT_CLOSED.md)
