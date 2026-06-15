# Phase 9 — Research Engines Graduation Report

**Date:** 2026-06-15  
**Status:** ✅ Executed — engines PASS · delivered pipeline fixed · P6 accumulating

---

## Executive summary

| Deliverable | Result |
|-------------|--------|
| `egx:phase9:graduation` | ✅ **8/8 OK** |
| Automation verify | **111/111 PASS** |
| Session ready | **18/18 PASS** |
| MED 0.4 acceptance | ✅ PASS |
| LRE 4.0 acceptance | ✅ **15/15 PASS** |
| MDE client-grade | ✅ PASS |
| Client delivered sync | **9 symbols** (was 5) |
| P6 ULTRA safety-filtered | 3/30 @ **100%** |
| P6 delivered safe | 0/30 (awaiting t5 on EGCH/UEFM/ROTO) |

---

## Fix 1 — Delivered sync ignored live sends

**Root cause:** `notification_delivery_audit` rows for 2026-06-14 had `deliverable=0` but `send_success=1` — sync required `deliverable=1`.

**Fix:** `syncDeliveredOutcomes` + `seedDeliveredOutcomes` now match **any successful live send**.

| Symbol | Date | Before | After |
|--------|------|--------|-------|
| EGCH | 2026-06-14 | not tracked | ✅ `client_delivered=1` |
| UEFM | 2026-06-14 | not tracked | ✅ `client_delivered=1` |
| ROTO | 2026-06-14 | not tracked | ✅ `client_delivered=1` |

---

## Fix 2 — Delivered safety-filtered P6 track

New metric: `getProofLoopMetrics({ deliveredOnly: true, safetyFiltered: true })`

Counts only **client-sent** signals that also pass current delivery safety rules — the true client beta gate.

---

## Fix 3 — Phase 9 graduation bundle

**New:** `scripts/egx_phase9_graduation.mjs` + `npm run egx:phase9:graduation`

Pipeline:
1. Seed + sync delivered outcomes
2. `track_outcomes` fill
3. P6 safety + delivered metrics
4. MED 0.4 / LRE 4.0 / MDE client-grade acceptance (shadow)

Output: `data/phase9_graduation_last.json`

---

## Research engines status

| Engine | Verdict | Notes |
|--------|---------|-------|
| **MED 0.4** | PASS acceptance | Shadow · calibration chain wired |
| **LRE 4.0** | PASS acceptance 15/15 | Integration PASS · accumulating live OOS |
| **MDE** | PASS client-grade | Candidates reranked · policy saved |

All engines remain **shadow mode** — no client path bypass.

---

## P6 accumulation plan (live)

| Track | Current | Target | ETA |
|-------|---------|--------|-----|
| ULTRA safety-filtered | 3/30 @ 100% | 30 @ ≥60% | ~27 winning sessions |
| Delivered safe | 0/30 | 30 @ ≥60% | EGCH/UEFM t5 pending |

**Next live sessions:** 2026-06-17, 2026-06-18, …

```bash
npm run egx:prod:prepare-send
npm run egx:telegram:cron
npm run egx:post:session      # closed loop + P6 sync
npm run egx:phase9:graduation # weekly gate
```

---

## Phase 10 — Next

1. **P6 graduation** — 27 more safety-filtered ULTRA samples
2. **Delivered WR** — EGCH/UEFM/ROTO t5 outcomes after 2026-06-19
3. **MED client signal** — `MED_CLIENT_SIGNAL=1` when P6 delivered gate passes
4. **LRE feed boost** — `EGX_LRE_FEED_BOOST=1` after forward OOS target
5. **MDE → actionable** — client-grade candidates → `final_signals` bridge (shadow pilot)

---

## Operator commands

```bash
npm run egx:phase9:graduation     # full Phase 9 bundle
npm run egx:p6:delivered:orchestrator
npm run egx:med:phase4:verify
npm run egx:lre:verify
npm run egx:mde:client-grade
```

**Phase 9 complete.**
