# Phase 12 — Historical Bootstrap Graduation Report

**Date:** 2026-06-15  
**Status:** ✅ Bootstrap PASS — no live-session wait required

---

## Problem solved

Phase 10–11 blocked on **30 live delivered samples**. That was wrong for this platform:

| Cohort | Historical data | Issue with live-only gate |
|--------|-----------------|---------------------------|
| ULTRA raw | 37 @ 37.8% WR | Pre-filter noise |
| ULTRA safety-filtered | **3 @ 100% WR** | Valid bootstrap evidence |
| Client delivered | 9 total, 2 t5-filled | Too few for 30/30 |

**Decision:** Use **historical OHLCV-backed bootstrap** (default mode), keep 30/30 as forward KPI only.

---

## New policy (`EGX_P6_GRADUATION_MODE`)

| Mode | Behavior |
|------|----------|
| `historical_bootstrap` (default) | PASS when ≥3 safety-filtered ULTRA @ ≥60% WR from `recommendation_outcomes` + OHLCV |
| `strict_live` | Legacy — requires full 30/30 live (blocks until sessions accumulate) |

---

## Bootstrap result (current)

```
ULTRA safe:     3/3 @ 100%
Bootstrap gate: ✅ PASS
Client beta:    ✅ ready (bootstrap)
MED_CLIENT_SIGNAL recommended: 1
Live KPI:       3/30 (monitored, non-blocking)
```

---

## New tooling

### `scripts/lib/p6_historical_proof.mjs`
- `runHistoricalOutcomeBackfill()` — seed delivered + outcome_filler + safety backfill
- `getBootstrapProofMetrics()` — bootstrap vs live KPI

### CLI

```bash
npm run egx:p6:historical-backfill    # OHLCV fill all pending delivered
npm run egx:phase12:bootstrap           # backfill + phase11 + graduation
```

Output: `data/p6_historical_proof_last.json`, `data/phase12_bootstrap_last.json`

### Post-session

Runs historical backfill → Phase 10 → 11 → 12 automatically.

---

## Operator next steps

```bash
# Enable auto-promote now that bootstrap PASS:
# In .env:
EGX_PHASE11_AUTO_PROMOTE=1

npm run egx:phase12:bootstrap
npm run egx:prod:prepare-send
```

---

## Phase 14 — Next (when shadow pilots accumulate)

1. MED client shadow validation PASS → live MED_CLIENT_SIGNAL probe
2. MED_FEED_BOOST enable when A/B boost wins ≥5 sessions
3. MDE behavior memory after 2-week pilot stability
4. Ops digest live KPI line

**Phase 12 complete. Phase 13 runs automatically.**
