# Phase 17 — Promotion Activation Report

**Date:** 2026-06-15  
**Role:** Chief System Auditor + Senior Backend Engineer  
**Verdict:** Infrastructure ✅ · auto-apply wired · penalize track correctly retained

---

## Scope (from Phase 16 handoff)

1. **Auto-apply promotions** to `.env` when gates PASS (`EGX_PROMOTION_AUTO_APPLY=1`)
2. **Live delivery correlation** — Telegram sends × MED opp delta on delivered symbols
3. **MDE stability backfill** (optional `EGX_MDE_PILOT_BACKFILL_STABILITY=1`)
4. **Ops digest** promotion line in success alerts

---

## Deliverables

| Artifact | Status |
|----------|--------|
| `scripts/lib/promotion_activation.mjs` | ✅ Gate → env patch evaluator + auto-apply |
| `scripts/python/med_live_delivery_correlation.py` | ✅ Delivery × MED track correlation |
| `scripts/egx_phase17_promotion_activation.mjs` | ✅ Phase bundle |
| `scripts/egx_env_activate_phase17.mjs` | ✅ Operator env bundle |
| `egx:phase17:promotion-activation` | ✅ Wired |
| Post-session chain | ✅ Phase 17 after Phase 16 |
| `mde_pilot_stability.py` backfill | ✅ Optional 14d bootstrap |

---

## Promotion verdicts

| Verdict | Meaning |
|---------|---------|
| `APPLY` | Production graduated + gates PASS → env vars written |
| `MONITOR` | Graduated but no promotion gates PASS (expected: keep penalize) |
| `BLOCKED_GRADUATION` | Production not graduated — no env changes |

**Current data (AB backfill 10 sessions):** penalize wins every session → **MED_FEED_BOOST stays 0** (correct — boost track not dominant).

---

## Operator commands

```bash
npm run egx:env:activate-phase17
npm run egx:phase17:promotion-activation
npm run egx:phase17:promotion-activation -- --skip-phase16
npm run egx:phase17:promotion-activation -- --apply-env --mde-backfill
npm run egx:post:session   # phases 10→17
```

**Phase 17 env bundle:**
```
EGX_PROMOTION_AUTO_APPLY=1
EGX_MED_AB_BACKFILL=1
(+ Phase 14 defaults)
```

**Outputs:**  
`data/phase17_promotion_activation_last.json`  
`data/promotion_activation_last.json`  
`data/med_live_delivery_correlation_last.json`

---

## Safety invariants

- `EGX_MDE_OPP_BOOST=0` never auto-applied  
- Promotions require `production_graduated`  
- `MED_FEED_BOOST=1` only when A/B boost streak ≥ 5  
- `EGX_MDE_BEHAVIOR_MEMORY=1` only when pilot stability PASS  

---

## Phase 18 — Next

1. **Live session 2026-06-17** — validate delivery correlation on real send  
2. **P6 delivered KPI** — track EGCH/UEFM t5 outcomes  
3. **LRE OOS 40/40** — gate for `EGX_LRE_FEED_BOOST=1`  
4. **Full prod:ready** weekly with CDP + tests  

**Phase 17 complete.** → **Phase 18:** see [PHASE_18_LIVE_OPS_REPORT.md](./PHASE_18_LIVE_OPS_REPORT.md)
