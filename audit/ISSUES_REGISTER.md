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
| AUD-020 | P1 | Data | kpi_exclusions_consistent delta>5 | Orphan exclusions in `data_quality_bar_exclusions` | **Fixed** — `audit_deep_scan` purge |
| AUD-021 | P2 | Audit | SYSTEM_MAP missing orphan analysis | §25 auto-append from deep scan | **Fixed** |

---

## Full entries — Institutional suite

### AUD-016
```text
ID: AUD-016
Severity: High
Layer: Ops / Monitoring
File(s): scripts/python/system_health_check.py, package.json
Symptom: No single command to verify DB, freshness, delivery, locks, engines
Root Cause: Health checks scattered across verify scripts
Evidence: Pre-wave: 175 checks in automation_verify but no operator `egx:health`
Impact: Silent degradation until telegram fails
Fix Plan: Unified health script + JSON artifact
Verification Command: npm run egx:health -- --quick
Status: Fixed / Verified
```

### AUD-017
```text
ID: AUD-017
Severity: High
Layer: Automation / DAG
File(s): scripts/egx_full_cycle.mjs
Symptom: Manual ordering of tv_auto → session → post_session
Root Cause: No orchestrated daily runner with stop-on-failure
Evidence: Operators ran 10+ npm commands ad hoc
Impact: Skipped steps, stale scores before send
Fix Plan: `egx:full-cycle` with logging + `data/full_cycle_last.json`
Verification Command: npm run egx:full-cycle -- --skip-cdp --fast
Status: Fixed / Verified
```

### AUD-020
```text
ID: AUD-020
Severity: Medium
Layer: Data quality
File(s): scripts/lib/audit_deep_scan.mjs, scripts/egx_data_layer_audit.mjs
Symptom: kpi_exclusions_consistent FAIL (delta=17)
Root Cause: ACTIVE exclusions referencing purged ohlcv_history bars
Evidence: audit/DATA_PIPELINE_AUDIT.md raw-exec vs exclusions mismatch
Impact: False data-layer FAIL; operators ignore real failures
Fix Plan: DELETE orphan exclusions before data_layer_audit in audit:all
Verification Command: npm run egx:audit:all && npm run egx:audit:data-pipeline
Status: Fixed / Verified
```

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
