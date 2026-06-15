# Phase 14 — Graduation + Live Probes Report

**Date:** 2026-06-15  
**Status:** ✅ Complete — env activated, probes wired

---

## 1. Env activation ✅

Applied via `npm run egx:env:activate-phase14`:

```bash
EGX_PHASE11_AUTO_PROMOTE=1
EGX_MED_CLIENT_SHADOW=1
EGX_MDE_PILOT_PROMOTE=1
EGX_P6_GRADUATION_MODE=historical_bootstrap
EGX_MED_SHADOW_BACKFILL=1
```

Also: `npm run egx:phase14:graduation -- --activate-env`

---

## 2. Phase 14 deliverables

| Component | Purpose |
|-----------|---------|
| `egx_env_activate_phase14.mjs` | Writes operator env bundle to `.env` |
| `phase14_graduation.mjs` | Gate evaluator (shadow / A/B streak / MDE stability) |
| `med_client_signal_shadow.py` | Historical backfill → 5 sessions from OHLCV |
| `med_client_signal_probe.py` | MED_CLIENT_SIGNAL=1 probe (no Telegram) |
| `med_feed_ab_pilot.py` | Boost win streak tracking |
| `mde_pilot_stability.py` | 14-day behavior memory gate |
| `ops_digest.mjs` | P6 KPI line in success alerts |

---

## 3. Auto-promotion rules (when `EGX_PHASE11_AUTO_PROMOTE=1`)

| Env | Enables when |
|-----|--------------|
| `MED_CLIENT_SIGNAL=1` | Shadow validation PASS (5 sessions @ ≥50% WR t5) |
| `MED_FEED_BOOST=1` | A/B boost wins ≥5 sessions in a row |
| `EGX_MDE_BEHAVIOR_MEMORY=1` | MDE pilot stable ≥14 days |

---

## 4. Operator commands

```bash
npm run egx:env:activate-phase14
npm run egx:phase14:graduation
npm run egx:post:session
```

Outputs: `data/phase14_graduation_last.json`, `data/research_client_env.json`

---

## Phase 16 — Next

1. Live beta session monitoring post sign-off
2. MED_FEED_BOOST when A/B streak = 5
3. MDE behavior memory day 14

**Phase 15 runs via `npm run egx:phase15:client-beta`.**
