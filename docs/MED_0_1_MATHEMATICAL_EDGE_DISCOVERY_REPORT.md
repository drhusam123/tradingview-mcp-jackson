# MED-0/1 — Mathematical Edge Discovery Field Report

**As-of:** 2026-06-11 | **Run:** 2026-06-14 | **Mode:** Shadow / Research Only

---

## 1. Executive Verdict

**`PASS_MED_0_1_RESEARCH_FEED`** — LRE backfilled (345 sessions), replay OOS complete, **18/18** acceptance. MED selective filter shows **+0.43% median lift** and **−5.2pp stop8** vs LRE on OOS. No HIGH_CONVICTION bucket on latest day. MED remains **shadow-only**.

---

## 2. Why MED Is a Field, Not an Indicator

MED estimates `P(R_{i,t+h} ≥ θ | X_{i,t})` as a **conditional probability field** over a 40+ dimensional state vector. It does not emit buy signals. It layers:

- Physics (stored energy, force, pressure)
- Microstructure (absorption, price impact, friction)
- Behavioral shift (distribution PSI/KS/Wasserstein)
- Failure geometry (KNN failure similarity)
- Crowding/chase math

Outputs feed **research tables** and **hypothetical_boost** (max 3.0) only.

---

## 3. Data Coverage

| Source | Coverage |
|--------|----------|
| OHLCV (`ohlcv_history_execution`) | **269 symbols**, **78,810 bars**, 2025-01-01 → 2026-06-11 |
| LRE (`lre_daily_scores`) | **66,381 rows**, **345 sessions** (2025-01-02 → 2026-06-11) |
| MDE (`egx_market_discovery_daily`) | ~58K rows, multi-day |
| MED hist rows (OOS features) | **34,418** symbol-days |
| MED daily scores | **202** symbols (LRE universe) |
| Conditional edge rows | **1,760** (88 primary keys @ 20d/10%) |

---

## 4–14. Equations & Fields (Implemented)

All spec equations implemented in:

- `med_0_1_math_features.py` — State vector, Stored Energy v2, Absorption, Physics, Crowding
- `med_0_1_distribution_shift.py` — PSI + W_shift + KS
- `med_0_1_failure_patterns.py` — Causal KNN failure similarity (K=50)
- `med_0_1_path_profiles.py` — MFE/MAE/PathQuality/StopHit
- `med_0_1_conditional_edges.py` — Condition keys + edge aggregation
- `med_0_1_sample_quality.py` — Bootstrap confidence + adjusted edge

**Causal rule enforced:** features at `t` use data ≤ `t` only; forward returns used for evaluation/edges only.

---

## 15. Replay Results (OOS 2025-01-01 → 2026-06-11)

| Mode | n | PF@100 | median_return | hit+10 | stop8 | top10_dom |
|------|---|--------|---------------|--------|-------|-----------|
| LRE_only | 34,208 | 22.8 | +1.72% | 25.6% | 26.1% | 4.9% |
| MDE_only | 40,354 | 23.9 | +1.71% | 24.8% | 24.9% | 4.3% |
| LRE_MDE | 34,062 | 22.8 | +1.71% | 25.6% | 26.1% | 5.0% |
| MED_only | 1,229 | 96.2 | +1.87% | 26.4% | 20.3% | 10.6% |
| **MED_LRE** | **1,105** | **94.9** | **+2.15%** | **27.1%** | **20.9%** | 11.0% |
| **MED_LRE_MDE** | **1,096** | **94.9** | **+2.15%** | **27.1%** | **20.9%** | 11.0% |
| crowding_on | 21,207 | 14.6 | +1.67% | 23.0% | 22.0% | 5.1% |

**Incremental lift (MED selective filter):**
- MED_LRE vs LRE: median **+0.43%**, stop8 **−5.2pp**, n=1,105 (highly selective)
- MED_LRE_MDE vs LRE_MDE: median **+0.44%**, stop8 **−5.2pp**

Replay: expanding + rolling + walk-forward (`static_only: false`).

---

## 16. Incremental Lift vs LRE/MDE

✅ **Measurable after LRE backfill** (`npm run egx:lre:backfill`). MED acts as a **selective math filter** on top of LRE/MDE — fewer names, better median return and lower stop8 rate on OOS replay.

---

## 17. Top Candidates — 2026-06-11

| Rank | Symbol | MED Score | Bucket | Stored Energy | Failure Sim | Dist Shift |
|------|--------|-----------|--------|---------------|-------------|------------|
| 1 | EFIH | 92.1 | MED_MONITOR | 0.004 | 0.24 | 0.82 |
| 2 | ETEL | 64.4 | INSUFFICIENT_SAMPLE | 0.000 | 0.12 | 0.42 |
| 3 | ABUK | 63.4 | INSUFFICIENT_SAMPLE | 0.004 | 0.08 | 0.23 |
| 4 | RAYA | 60.4 | FAILURE_WARNING | 0.000 | 0.32 | 0.51 |
| 5 | VLMRA | 60.4 | INSUFFICIENT_SAMPLE | 0.000 | 0.22 | 0.23 |

No `MED_HIGH_CONVICTION_RESEARCH` on this session.

---

## 18. Failure Warnings — 2026-06-11

**111 symbols** in `MED_FAILURE_WARNING` (55% of universe): high failure similarity, crowding, or DoNotChase math. Examples: RAYA, AALR, ACAMD, ACAP, ADCI.

---

## 19. Safety / No Client Leakage

```
MED_SHADOW=1 | MED_CLIENT_SIGNAL=0 | MED_OPP_BOOST=0 | MED_FEED_BOOST=0
client_path_allowed=0 (all 202 rows) | research_only=1 | shadow_only=1
max hypothetical_boost=1.0 | no final_signals | no actionable | no Telegram
```

Acceptance: **18/18 PASS** — `PASS_MED_0_1_RESEARCH_FEED`

---

## 20. Final Decision & Next Phase

| Decision | **PASS research feed** — remain shadow; no client promotion |
|----------|-------------------------------------------------------------|
| Graduation blockers | 40+ OOS closed trades live; PF@100 ≥ 1.3 on live shadow ledger |
| Next phase | MED-2: analogue kernel + forward shadow ledger + threshold recalibration |

**npm commands:**
```bash
npm run egx:lre:backfill    # one-time OOS LRE history
npm run egx:med:daily
npm run egx:med:replay-audit
npm run egx:med:verify
npm run egx:med:phase2      # MED-2: analogue + forward shadow
npm run egx:med:phase2:verify
```

---

## MED-2 Update (2026-06-14)

| Component | Status |
|-----------|--------|
| Analogue kernel (K=50, 40,513 library) | ✅ 202 symbols scored |
| Threshold snapshots | ✅ med_score / stored_energy / analogue_p_tail |
| OOS research ledger | ✅ **1,105 closed** (MED_LRE filter) |
| Live forward ledger | ⏳ starts **2026-06-12** (0 entries yet) |
| Acceptance | **PASS_MED_2_RESEARCH_LAYER** (9/9) |

**OOS research ledger:** PF@100 **94.9** | median **+2.15%** | aligns with replay MED_LRE.

**Top analogue p_tail today:** ICID/LUTS **46%** | COMI med_score **80.2** + analogue **24%**

**Graduation:** `live_closed=0/40` — live forward begins after 2026-06-12.

---

## MED Pipeline Integration (2026-06-14)

Wired into `egx_tv_auto_update.mjs` when `EGX_MED_ENABLED !== '0'`:
1. `med_0_2_daily_chain.py` — daily + analogue + forward (no OOS backfill)
2. `med_0_2_status.py` — graduation tracker

**npm:** `egx:med:run` | `egx:med:integration-test`

**Integration:** `PASS_MED_INTEGRATION` — actionable 4→4 unchanged, opp_v2 unchanged

**Manifest:** `data/discovery_med_manifest.json` → discovery fabric (read-only)
