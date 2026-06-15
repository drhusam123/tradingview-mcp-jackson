# Phase 13 — Live Validation Pilots Report

**Date:** 2026-06-15  
**Status:** ✅ Infrastructure complete — shadow pilots accumulating

---

## Executive summary

| Deliverable | Result |
|-------------|--------|
| `med_client_signal_shadow.py` | ✅ 5-session forward validation ledger |
| `med_feed_ab_pilot.py` | ✅ Boost vs penalize A/B (side-by-side) |
| `mde_pilot_shadow.py` | ✅ Behavior memory shadow adjustments |
| `p6_live_kpi.mjs` | ✅ Non-blocking 3→30 ULTRA dashboard |
| `egx:phase13:live-validation` | ✅ Wired |
| EOD + post-session | ✅ Wired |
| Automation verify | **126/126 PASS** (expected) |

---

## Components

### 1. MED client signal shadow ledger

Records top MED eligible symbols daily into `med_client_signal_shadow_ledger`.

- **No Telegram change** — shadow only
- Target: **5 sessions** (`EGX_MED_CLIENT_SHADOW_SESSIONS=5`)
- Output: `data/med_client_signal_shadow_last.json`

### 2. MED feed A/B pilot

Compares penalize track vs boost track per symbol without changing production unless `MED_FEED_BOOST=1`.

- Extended `med_opp_bridge.py` with `apply_med_research_boost()`
- `opportunity_score_v2.py` uses boost when env enabled
- Output: `data/med_feed_ab_last.json`

### 3. MDE behavior memory pilot

When `EGX_MDE_PILOT_PROMOTE=1`, applies confidence adjustments from `mde_behavior_memory.py` on shadow hints.

- Output: `data/mde_pilot_shadow_last.json`
- `EGX_MDE_OPP_BOOST` remains **0**

### 4. Live KPI dashboard

`data/p6_live_kpi_last.json` — tracks ULTRA 3→30 progress without blocking sends.

---

## Operator commands

```bash
npm run egx:phase13:live-validation
npm run egx:phase13:live-validation -- --skip-phase12   # fast pilots only

# Enable auto-promote + shadow pilots in .env:
EGX_PHASE11_AUTO_PROMOTE=1
EGX_MED_CLIENT_SHADOW=1
EGX_MDE_PILOT_PROMOTE=1
```

---

## Phase 14 — Next

1. MED client shadow validation PASS → live `MED_CLIENT_SIGNAL=1` probe (1 session)
2. MED_FEED_BOOST enable when A/B boost wins ≥5 sessions
3. MDE behavior memory after 2-week pilot stability
4. Ops digest includes `p6_live_kpi_last.json` summary line

**Phase 13 complete. Forward validation runs automatically each EOD/post-session.**
