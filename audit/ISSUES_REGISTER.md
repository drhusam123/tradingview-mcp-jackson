# Issues Register — Institutional Audit

**Updated:** 2026-06-15  
**Format:** `ID | Severity | Layer | Status`

---

## Verified (historical — Phases 1–6)

| ID | Sev | Layer | Symptom | Root Cause | Status |
|----|-----|-------|---------|------------|--------|
| AUD-001 | P0 | Data | ETIMEDOUT aborted full pipeline | `--force` coupled to full OHLCV re-fetch | **Verified** |
| AUD-002 | P0 | Delivery | Portfolio cap blocked sends | Paper reconciliation missing | **Verified** |
| AUD-003 | P1 | Data | ROTO OHLCV gap | Per-symbol lag undetected | **Verified** |
| AUD-004 | P1 | Schema | indicators_cache insert fail | Dynamic placeholders missing | **Verified** |
| AUD-005 | P1 | Automation | MED 0.3 verify drift | Stale automation script refs | **Verified** |
| AUD-006 | P2 | Hygiene | Test fixtures in prod DB | No purge job | **Verified** |

## Fixed (Phases 7–26)

| ID | Sev | Layer | Symptom | Fix | Status |
|----|-----|-------|---------|-----|--------|
| AUD-007 | P1 | Graduation | P6 delivered not synced | `delivered_outcomes.mjs` seed+sync | **Fixed** |
| AUD-008 | P1 | Promotion | Env toggles manual | `promotion_activation.mjs` auto-apply | **Fixed** |
| AUD-009 | P2 | Ops | No post-grad session track | Phases 18–21 live anchor gates | **Fixed** |
| AUD-010 | P2 | LRE | OOS not accumulating | `lre_oos_accumulator.py` in post-session | **Fixed** |

## Open — Live accumulation (not code defects)

| ID | Sev | Layer | Symptom | Impact | Status |
|----|-----|-------|---------|--------|--------|
| AUD-011 | P2 | MED | Boost streak 0/5 | Keep penalize (correct) | **Open** — by design |
| AUD-012 | P2 | P6 | Delivered 2/30 @ 0% WR | Live KPI accumulating | **Open** |
| AUD-013 | P2 | LRE | OOS 0/40 live | Forward shadow accumulating | **Open** |
| AUD-014 | P2 | Outcomes | t5 EGCH/UEFM 0/5 | Closure ~2026-06-19 | **Open** |
| AUD-015 | P2 | Anchor | Pre-validated bootstrap only | Real session 2026-06-17 | **Open** |

## Fixed — Institutional suite (this wave)

| ID | Sev | Layer | Symptom | Fix | Status |
|----|-----|-------|---------|-----|--------|
| AUD-016 | P1 | Ops | No unified health command | `system_health_check.py` + `egx:health` | **Fixed** |
| AUD-017 | P1 | Ops | No daily production DAG | `egx_full_cycle.mjs` | **Fixed** |
| AUD-018 | P2 | Audit | Missing formal audit files | `egx_system_audit_orchestrator.mjs` | **Fixed** |
| AUD-019 | P2 | Automation | Post-session without health | Cron chains `--quick` health | **Fixed** |

---

## Template (new issues)

```text
ID: AUD-0XX
Severity: Critical / High / Medium / Low
Layer:
File(s):
Symptom:
Root Cause:
Evidence:
Impact:
Fix Plan:
Verification Command:
Status: Open / Fixed / Verified
```
