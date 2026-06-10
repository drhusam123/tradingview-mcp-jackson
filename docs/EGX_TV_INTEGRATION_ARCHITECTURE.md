# EGX ML × TradingView MCP — Integration Architecture
**Audit-Lead Design Document v1.0 | 2026-05-30**
Owner: Dr. Husam | Status: APPROVED FOR BUILD (phased)

---

## 0. Audit Principle (non-negotiable)

> **No performance number enters a client report until it is measured on out-of-sample
> live data. We promise process, not outcomes.**

The single most damaging defect this audit found was a model trained on 66.5%
contaminated labels that *looked* excellent (AUC 0.79) but was learning noise.
Every TradingView integration below is therefore wrapped in a **validation gate**
before it touches the model. We do not repeat that mistake by ingesting live data blindly.

---

## 0.1 LIVE CONNECTIVITY PROOF (executed 2026-05-30, not assumed)

Ran the exact MCP data path against the live chart over CDP:9222:

```
✅ CDP responsive            Chrome/140, Protocol 1.3, chart tab live
✅ TradingViewApi present    pulled 3 live OHLCV bars (BATS:SCHP weekly)
✅ EGX data EXISTS           EGX_DLY:COMI / EGX_DLY:EGX30 / EGX_DLY:HRHO resolve
✅ EGX30 index available     EGX_DLY:EGX30 → close 52,658.8, 300 bars  ← solves RS
⚠️ EGX feed is DELAYED       exchange prefix is **EGX_DLY:** (not real-time)
⚠️ setSymbol needs settle    first bar read after switch returns null (~3.5s settle)
❌ CASE:  prefix invalid     returns null; correct prefix = EGX_DLY:
```

**Two findings change the build:**
1. **Correct symbol prefix is `EGX_DLY:`** — all sync jobs must use it (the DB's plain
   `COMI` symbols map to `EGX_DLY:COMI`). EGX30 index = `EGX_DLY:EGX30`.
2. **The feed is DELAYED, not real-time** — this is now *proven*, not feared. It
   hard-caps the edge thesis: we cannot and will not market a speed/latency edge.

## 1. Honest Edge Thesis (what we actually sell)

We do **NOT** beat institutions on speed, latency, order flow, or information.
**PROVEN:** the EGX feed is `EGX_DLY:` (delayed). Institutions have DMA and analyst teams.

Our **defensible edge** is four things a human (retail *or* an institutional desk
covering 259 thin names) cannot do consistently by hand:

1. **Breadth** — every one of 259 EGX names scored every session, no blind spots.
2. **Discipline** — regime-gated thresholds; the system refuses to signal in BEAR
   (the clean model's "0% confidence in BEAR" is a *feature*, not a bug — capital protection).
3. **Manipulation avoidance** — Phase67 pump/dump detector (AUC 0.9998) + corporate-action
   filter; we systematically *avoid* the traps retail chases.
4. **Survivorship-honest backtesting** — CPCV + walk-forward, no look-ahead.

TradingView MCP raises the **ceiling** of this edge by improving *data quality and
operational freshness* — it does not invent alpha. The market's predictability is the
hard cap; more features only help up to that cap.

---

## 2. Current State (verified 2026-05-30, not assumed)

| Component | State | Note |
|---|---|---|
| `ohlcv_history` (daily) | 76,172 rows / 259 syms | **5 days stale**; median coverage 93%, min 1% |
| `ohlcv_60min/15min/weekly/monthly` | **0 rows** | genuinely empty |
| `cross_market_daily` | 2,329 rows / 9 assets | **stale** (DXY,VIX,USDEGP,XAU,UKOIL,SPY,EEM,US10Y,EURUSD) |
| `financial_data` | 267 rows, **66 with real P/E** | partial + stale, `sector` mostly NULL |
| `liquidity_profile` | 2,317 rows, latest 05-27 | slippage model wired |
| `corporate_actions` | 539 rows, latest 05-21 | split/dividend protection wired |
| `pine_analytics` | **1 row** | effectively unused |
| `dom_snapshots` | **0 rows** | order-flow unused |
| `scorer.js` rule engine | WR=69% on 75K candles | **not fused with ML** |
| TV MCP ↔ Claude | **not connected** | exists at `src/server.js`, CDP:9222 live |

**Binding constraint:** not "missing pipelines" — it is **(a) no automated refresh,
(b) no validation layer, (c) ML and rule engine live in separate silos.**

---

## 3. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                         TradingView Desktop (CDP :9222)                  │
│   EGX daily/weekly bars · cross-market · symbol fundamentals · DOM       │
└───────────────┬──────────────────────────────────────────────────────────┘
                │  TV MCP (src/server.js)  — batch_run, data_get_ohlcv,
                │  quote_get, depth_get, data_get_pine_tables, symbol_info
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 1 — INGEST ORCHESTRATOR  (egx_tv_sync.py)                         │
│    • EOD pull: daily bars for 259 + EGX30 index + 9 macro assets        │
│    • Weekly pull: weekly/monthly bars (HTF context)                      │
│    • Adjusted-data pull (corporate actions) — split/div safe            │
└───────────────┬──────────────────────────────────────────────────────────┘
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 2 — VALIDATION GATE  (egx_data_validator.py)   [MANDATORY]        │
│    • range/spike checks · split detection · gap fill · dup removal      │
│    • writes data_quality_log + data_trust_scores                         │
│    • REJECTS bad bars BEFORE they reach feature store                    │
└───────────────┬──────────────────────────────────────────────────────────┘
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 3 — FEATURE STORE  (existing explosion_ml + new groups E–H)      │
│    Daily real RS (EGX30) · macro context · liquidity · HTF context       │
└───────────────┬──────────────────────────────────────────────────────────┘
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 4 — DUAL ENGINE                                                   │
│    ML ensemble (AUC≈0.79 clean)  ⊕  Rule scorer (WR≈69%)  → Fusion       │
│    Fusion = AGREEMENT gate, not weighted blend (see §6)                  │
└───────────────┬──────────────────────────────────────────────────────────┘
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 5 — OUTPUT + PROOF LOOP                                           │
│    client report · forward_test auto-fill (quote_get) · alert_create     │
│    → WinRate measured weekly, fed back to threshold tuning               │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. LAYER 1 — Ingest Orchestrator (`egx_tv_sync.py`)

### 4.1 Hard latency budget (verified constraint)
`batch_run` default delay = 2000ms + chart-load ≈ 3–5s/symbol.
- 259 symbols × 1 timeframe ≈ **15–22 min** (daily EOD) → acceptable once/day.
- 259 × 4 timeframes ≈ **60–90 min** → only feasible as a **weekend backfill**, NOT hourly.

**Design decision:** Intraday (15m/60m) is **deferred** — EGX is thin; intraday bars are
gappy and add little to a 5-day-horizon model. Build **Daily + Weekly + EGX30 + Macro** first.

### 4.2 Jobs
| Job | Cadence | Tool calls | Writes |
|---|---|---|---|
| `sync_daily` | EOD (Thu close) | `batch_run(259, ["D"], get_ohlcv, count=10)` | `ohlcv_history` |
| `sync_index` | EOD | `chart_set_symbol("EGX30"/"CASE30")` + `data_get_ohlcv(500)` | `ohlcv_history['EGX30']` |
| `sync_macro` | EOD | `quote_get` ×9 assets | `cross_market_daily` |
| `sync_weekly` | weekend | `batch_run(259,["W"],get_ohlcv,250)` | `ohlcv_weekly` |
| `sync_funda` | weekly | `symbol_info` + financials panel read | `financial_data` |

### 4.3 Idempotency
All writes use `INSERT OR IGNORE` (already in `saveOHLCV`). Re-running a job never
duplicates. Each job logs `{rows_new, rows_skipped, rejected}` to `pipeline_runs`.

---

## 5. LAYER 2 — Validation Gate (`egx_data_validator.py`)  **[the part the prior plan omitted]**

This is the firewall that prevents re-contamination. Every incoming bar passes:

1. **Range check** — `high>=low`, `low<=open,close<=high`, `volume>=0`.
2. **Spike check** — single-bar move > 50% with volume < 2× avg → flag as suspected
   corporate action, route to `corporate_actions`, **do not** feed model until adjusted.
3. **Split detection** — gap ratio ∈ {≈0.5, ≈2, ≈10} on near-zero volume → split,
   apply `adjustment_factor`, re-base history.
4. **Staleness** — last bar > 7 cal days → mark symbol `STALE`, exclude from signals
   (matches the F4 filter already in `predict_ensemble`).
5. **Trust score** — per source, written to `data_trust_scores`; a symbol below trust
   threshold is *scored but not signalled*.

Output: only **clean, adjusted** bars reach Layer 3. Rejections logged, never silent.

---

## 6. LAYER 4 — Dual Engine Fusion (corrected)

The prior plan proposed a weighted blend with a hard ">85%" promise. **Rejected.**
Correct design is an **agreement gate** with *measured* — not assumed — tiers:

```
ml_prob   ∈ [0,1]   from predict_ensemble (clean model)
rule_score∈ [0,100] from scorer.js via python_bridge
regime    ∈ {BULL,NEUTRAL,BEAR}

TIER assignment (gate, then rank):
  ULTRA  : ml_prob ≥ T_regime  AND rule_score ≥ 60   (both engines agree)
  HIGH   : ml_prob ≥ T_regime  XOR rule_score ≥ 70   (one strong)
  WATCH  : ml_prob ≥ 0.50      (model only, below regime gate) — not sent to client
  none   : otherwise

T_regime = {BULL:0.60, NEUTRAL:0.68, BEAR:0.82}   (already in code)
```

- **Why a gate, not a blend:** the two engines have *different failure modes*
  (ML overfits patterns; rules miss novel setups). Agreement is the signal;
  averaging hides disagreement.
- **The ">85%" number is forbidden** in any client-facing material until
  `forward_test_predictions` reports ≥30 completed ULTRA signals. Until then the
  client sees: *"historical walk-forward P@10 = 80–90%; live track record building."*

---

## 7. New Feature Groups (only after validation + EGX30 land)

| Group | Features | Source table | Look-ahead rule |
|---|---|---|---|
| **E — Volume Profile** | poc, vah, val, dist_from_poc | `pine_analytics` | use prior session POC |
| **F — HTF context** | wk_trend, wk_vol_ratio, mtf_align | `ohlcv_weekly` | weekly bar T-1 |
| **G — Macro** | usdegp_chg, vix_level, risk_on, oil_chg | `cross_market_daily` | T-1 close |
| **H — Liquidity** | advt_10d, amihud, max_safe_order_egp, tier | `liquidity_profile` | T-1 |

**Pine extractor caveat (corrected):** `table.new` exposes **only the last bar**.
It is valid for *live inference*, **invalid for building training rows**. Training
features for groups E–H are computed in Python from the stored bars (T-1 indexed),
exactly like the existing 54. The Pine table is an *inference convenience*, not a
training source. Any Pine feature MUST reference `[1]` (previous bar) to match the
T-1 training convention and avoid re-introducing look-ahead bias.

Target: 54 → ~68 features. Expected AUC lift is **unknown until measured** — do not
pre-quote a number.

---

## 8. LAYER 5 — Proof Loop (closes the audit's biggest gap)

```
egx_outcome_tracker.py  (daily, already built)
   reads forward_test_predictions WHERE status='PENDING'
   quote_get(symbol)  → return_t1/t5/t10  → WIN/LOSS/FLAT
   when ≥30 ULTRA completed → compute live WinRate, Sharpe, hit-rate
   → THIS number (not backtest) becomes the client headline
```

Go-live gate for client beta: **live ULTRA WinRate ≥ 60% over ≥30 signals.**
First read: ~June 3 (the 27-May batch matures).

---

## 9. Build Phases (honest timeline)

| Phase | Work | Depends on | Exit criterion |
|---|---|---|---|
| **P0 — Connect** | add TV MCP to `~/.claude.json` | — | `tv_health_check` green |
| **P1 — Daily fresh** | `sync_daily`+`sync_index`+validator | P0 | OHLCV gap ≤ 1 day; EGX30 present |
| **P2 — Real RS** | recompute rs_* from EGX30; retrain Phase2 | P1 | rs features non-constant; AUC re-measured |
| **P3 — Phase3 clean** | add `return_5d>=0.07` to Phase3 query | — (code) | regime models on clean labels |
| **P4 — Macro+Liquidity refresh** | `sync_macro`, refresh liquidity | P1 | groups G,H populated T-1 |
| **P5 — Fusion** | wire scorer.js ⊕ ML agreement gate | P2,P3 | ULTRA tier emitted |
| **P6 — Proof** | outcome tracker ≥30 ULTRA | P5 + time | live WinRate computed |
| **P7 — Client beta** | report + alerts | P6 ≥60% | gate passed |

P0–P4 are days. **P6 is calendar-bound (market must pass).** No shortcut exists.

---

## 10. What this explicitly does NOT claim

- Does not beat institutional latency or order flow.
- Does not promise a WinRate before P6 measures it.
- Does not use intraday 15m (deferred; thin-market noise).
- Does not treat Pine `table` output as training data.
- Does not ingest any TV bar that fails the Layer-2 validator.

---

## 11. Immediate next action (P0)

Add to `~/.claude.json`:
```json
"mcpServers": {
  "tradingview": { "command": "node",
    "args": ["/Users/dr.husam/tradingview-mcp-jackson/src/server.js"] }
}
```
Then: `tv_health_check` → `sync_daily` (dry-run, 5 symbols) → validate → scale to 259.
