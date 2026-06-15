# Full Repair Plan — Institutional Audit

**Date:** 2026-06-15  
**Priority order:** P0 → P1 → P2 → P3

---

## P0 — System breakers (all resolved)

| ID | Files | Change | Verification | Result |
|----|-------|--------|--------------|--------|
| AUD-001 | `egx_tv_auto_update.mjs` | Decouple `--force` from full OHLCV | `npm run egx:daily` | ✅ Verified Phase 2 |
| AUD-002 | `portfolio_reconcile`, delivery | Unblock cap | `egx:notify:reconcile` | ✅ Verified Phase 3 |

**P0 open count: 0**

---

## P1 — Signal loss / silent failure (resolved + monitoring)

| ID | Files | Change | Verification | Status |
|----|-------|--------|--------------|--------|
| AUD-016 | `system_health_check.py` | Unified health gate | `npm run egx:health` | ✅ Fixed |
| AUD-017 | `egx_full_cycle.mjs` | Daily DAG runner | `npm run egx:full-cycle -- --skip-cdp` | ✅ Fixed |
| AUD-007 | `delivered_outcomes.mjs` | Sync client_delivered | `egx:post:session` | ✅ Fixed |
| AUD-008 | `promotion_activation.mjs` | Auto-apply promotions | `egx:graduation:complete` | ✅ Fixed |

**P1 open count: 0** (live KPIs monitored via health WARN)

---

## P2 — Audit / logging / manual steps

| ID | Files | Change | Verification | Status |
|----|-------|--------|--------------|--------|
| AUD-018 | orchestrator + audit/*.md | Generate 8 audit reports | `npm run egx:audit:all` | ✅ Fixed |
| AUD-019 | `install_cron.mjs` | Health after post-session | `egx:cron:show` | ✅ Fixed |
| AUD-011–015 | — | Live gates — no code bypass | `data/audit_close_last.json` | ⏳ Accumulating |

---

## P3 — Documentation

| ID | Files | Change | Verification | Status |
|----|-------|--------|--------------|--------|
| DOC-001 | `docs/AUTOMATION_RUNBOOK.md` | Operator runbook | file exists | ✅ |
| DOC-002 | `audit/FINAL_SYSTEM_AUDIT_REPORT.md` | Executive report | file exists | ✅ |
| DOC-003 | `audit/SYSTEM_MAP.md` §24 | Link audit suite | review | ✅ |

---

## Rollback

- Health/full-cycle: remove npm scripts; cron unchanged until `egx:cron:install`
- Audit files: delete `audit/*_AUDIT.md` — regenerated on demand
- No schema changes in this wave

---

## Next actions (operator)

```bash
npm run egx:health
npm run egx:full-cycle -- --skip-cdp
npm run egx:audit:all
node scripts/egx_automation_verify.mjs
```
