# Phase 15 — Client Beta Sign-Off Report

**Date:** 2026-06-15  
**Status:** ✅ Infrastructure complete — sign-off when required checks PASS

---

## Executive summary

| Deliverable | Result |
|-------------|--------|
| `med_opp_delta_monitor.py` | ✅ MED penalize vs boost delta on top opp symbols |
| `client_beta_signoff.mjs` | ✅ Required/optional checklist |
| `egx:phase15:client-beta` | ✅ Wired |
| Ops digest | ✅ Client beta line in success alerts |
| Post-session + EOD | ✅ Wired |

---

## Sign-off checklist (required)

| Check | Purpose |
|-------|---------|
| bootstrap_pass | Historical P6 bootstrap |
| phase14_ready | Shadow + probe infrastructure |
| med_client_signal | `MED_CLIENT_SIGNAL=1` effective |
| med_shadow_sessions | 5/5 shadow sessions |
| safety_veto | `EGX_SAFETY_VETO=1` |
| mde_opp_off | `EGX_MDE_OPP_BOOST=0` |
| telegram_configured | Delivery path ready |
| opp_delta_logged | Phase 15 delta monitor ran |

**Optional (non-blocking):** live session count, feed A/B streak, MDE 14d memory, live KPI, verify

---

## Phase 15 monitoring

### MED opp delta
Compares `penalize_pts` vs `boost_pts` on top 15 opportunity symbols.

Output: `data/med_opp_delta_last.json`, `med_opp_delta_ledger`, `med_live_session_ledger`

### Pending auto-promotions (tracked, not blocking sign-off)

| Gate | Condition |
|------|-----------|
| `MED_FEED_BOOST=1` | A/B boost streak ≥5 sessions |
| `EGX_MDE_BEHAVIOR_MEMORY=1` | MDE pilot ≥14 days |

---

## Operator commands

```bash
npm run egx:phase15:client-beta
npm run egx:phase15:client-beta -- --skip-phase14   # fast sign-off refresh
npm run egx:post:session
```

Outputs: `data/phase15_client_beta_last.json`, `data/client_beta_signoff_last.json`

---

## Phase 16 — Next

1. Live beta session with signed-off env — monitor Telegram + opp delta
2. Enable `MED_FEED_BOOST` when A/B streak hits 5
3. MDE behavior memory day 14
4. Production graduation: `npm run egx:prod:ready:full` gate in sign-off

**Phase 15 complete.**
