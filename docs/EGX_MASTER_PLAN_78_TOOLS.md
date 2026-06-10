# EGX ML × TradingView MCP — Master Plan (Full 78-Tool Utilization)
**Audit-Lead Final Design | 2026-05-30 | Owner: Dr. Husam**

> Governing rule (from this audit): **every TV tool feeds a validation gate before it
> touches the model; no WinRate is quoted to a client until measured live.**
> Verified facts this plan rests on: CDP live ✅ · EGX_DLY: feed ✅ · EGX30 index ✅ · feed is DELAYED ⚠️

---

## A. The Edge, Stated Honestly

Four edges TV MCP lets us build that no individual — and few desks covering 259 thin
EGX names — can sustain by hand. Each maps to specific tools:

| Edge | Mechanism | Tools that build it |
|---|---|---|
| **E1 Breadth** | all 259 scored every session, multi-timeframe | `batch_run`, `data_get_ohlcv`, `chart_set_symbol/timeframe` |
| **E2 Discipline** | regime gate + Kelly + validation, no emotion | `quote_get`, `symbol_info`, validator layer |
| **E3 Trap-avoidance** | pump/dump + corporate-action + DOM thinness | `depth_get`, `data_get_ohlcv`, `data_get_study_values` |
| **E4 Proof** | every signal tracked to outcome, no cherry-pick | `quote_get`, `alert_create`, outcome tracker |

We do NOT sell speed (feed is delayed) or information (no insider/L2 depth on EGX).

---

## B. Tool-to-Purpose Map — all 78 assigned a role

### USED HEAVILY (data backbone — 11 tools)
| Tool | Role in system | Cadence |
|---|---|---|
| `batch_run` | pull D/W bars for 259 + index in one sweep | EOD daily, W weekend |
| `data_get_ohlcv` | raw bars → `ohlcv_history` / `ohlcv_weekly` | per symbol |
| `quote_get` | live(delayed) price → entry + forward-test fill | daily + on-demand |
| `symbol_info` | exchange/sector/type → fundamentals + sector map | weekly |
| `symbol_search` | resolve new EGX listings → universe maintenance | weekly |
| `depth_get` | DOM bid/ask imbalance → order-flow feature + thinness guard | on signals |
| `data_get_study_values` | read live RSI/MACD/BB for cross-check vs Python | on signals |
| `chart_set_symbol` | navigate the 259 | constant |
| `chart_set_timeframe` | D / W / (M) sweeps | per job |
| `chart_get_state` | confirm symbol/TF before every read (anti-mismatch) | every read |
| `watchlist_get` | pull the live EGX watchlist as the universe-of-record | daily |

### USED FOR FEATURES (Pine live extractor — 7 tools)
| Tool | Role |
|---|---|
| `pine_set_source` | inject the **EGX-Feature** indicator (groups E–H, T-1 indexed) |
| `pine_smart_compile` / `pine_compile` | build it on the chart |
| `pine_check` | validate Pine server-side before injection |
| `pine_analyze` | offline lint (no chart needed) — CI safety |
| `pine_get_errors` / `pine_get_console` | confirm clean compile + read log.info diagnostics |
| `data_get_pine_tables` | **read the feature table** → live inference vector |
| `data_get_pine_lines/labels/boxes` | read S/R levels, Wyckoff zones, POC bands |

### USED FOR VALIDATION & BACKTEST PROOF (8 tools)
| Tool | Role |
|---|---|
| `replay_start/step/autoplay/status/stop` | **bar-replay forward-sim** of a signal before it goes live (no look-ahead) |
| `replay_trade` | simulate buy/stop/target on historical bars → realistic fill check |
| `data_get_strategy_results` / `data_get_trades` / `data_get_equity` | if a Pine strategy is loaded, pull its metrics to cross-validate ML |

### USED FOR CLIENT DELIVERY (8 tools)
| Tool | Role |
|---|---|
| `alert_create` | auto-arm price alert at entry+confirmation for each ULTRA signal |
| `alert_list` / `alert_delete` | manage the daily alert set (clear stale) |
| `draw_shape` | draw entry/stop/T1/T2 lines on the chart for the client screenshot |
| `draw_list` / `draw_remove_one` / `draw_clear` | manage annotation lifecycle |
| `capture_screenshot` | produce the annotated chart image for the client report |

### USED FOR SCALE / MULTI-VIEW (9 tools)
| Tool | Role |
|---|---|
| `pane_set_layout` / `pane_set_symbol` / `pane_focus` / `pane_list` | 2x2 sector dashboard (4 leaders at once) |
| `tab_new` / `tab_switch` / `tab_close` / `tab_list` | parallel symbol streams during sweeps |
| `layout_list` / `layout_switch` | load saved "EGX-Scan" and "EGX-Signal" layouts |

### USED AS ESCAPE HATCH / AUTOMATION (12 tools)
| Tool | Role |
|---|---|
| `ui_evaluate` | **the power tool** — any internal TV API call the 77 others can't reach |
| `tv_discover` | map available API paths before relying on them |
| `tv_health_check` / `tv_ui_state` | pre-flight check before every batch run |
| `ui_open_panel` | open pine-editor / strategy-tester / DOM programmatically |
| `ui_click/hover/find_element/keyboard/mouse_click/type_text/scroll/fullscreen` | drive dialogs (alert creation, fundamentals panel) when no API exists |
| `tv_launch` | cold-start TV if the process is down (cron resilience) |

### CONFIGURED, LOW-PRIORITY (3 tools)
| Tool | Why deferred |
|---|---|
| `chart_set_type` | chart cosmetics — no model value |
| `chart_scroll_to_date` / `chart_set_visible_range` | only for screenshot framing |
| `indicator_set_inputs` / `indicator_toggle_visibility` | only if using TV-native studies vs Pine |

**Result: every one of the 78 tools has an assigned role or an explicit defer reason.**

---

## C. The 6 Pipelines (how the tools combine)

### Pipeline 1 — DAILY DATA SYNC (E1) `egx_p1_sync.py`
```
tv_health_check → watchlist_get (universe of record)
for each of 259:  chart_set_symbol(EGX_DLY:<sym>) → chart_set_timeframe(D)
                  → data_get_ohlcv(10)  [or batch_run for the sweep]
chart_set_symbol(EGX_DLY:EGX30) → data_get_ohlcv(500)   # the index
→ Validation Gate → ohlcv_history (+ EGX30)
```
Solves: 5-day staleness, real RS basis.

### Pipeline 2 — LIVE FEATURE EXTRACTION (E1/E3) `egx_p2_features.py`
```
pine_check(EGX_Feature_v1.pine) → pine_set_source → pine_smart_compile
for each signal candidate:
   chart_set_symbol → data_get_pine_tables("EGX Features")
   → live inference vector (bb_width, vcp, obv_slope, wyckoff, rs_vs_mkt …) [1]-indexed
   cross-check vs Python feature_row; disagreement → flag
```
Solves: feature freshness, look-ahead discipline (Pine uses `[1]`).

### Pipeline 3 — MACRO + LIQUIDITY + ORDER FLOW (E2/E3) `egx_p3_context.py`
```
quote_get × 9 (USDEGP,VIX,XAU,UKOIL,SPY,EEM,US10Y,DXY,EURUSD) → cross_market_daily
depth_get on each signal → bid/ask imbalance, spread%, thinness → liquidity_profile
symbol_info → sector, type → fill financial_data.sector (66→259)
```
Solves: groups G (macro) + H (liquidity), smart-money & thin-book trap filter.

### Pipeline 4 — REPLAY FORWARD-SIM (E4 pre-check) `egx_p4_replay.py`
```
for each ULTRA candidate:
   replay_start(date = signal_date - 1)
   replay_step ×5  → did entry→T1 fill before stop?  (realistic, no look-ahead)
   replay_trade(buy@entry, stop, target) → simulated R-multiple
→ only candidates that survive replay reach the client
```
Solves: pre-live sanity on each signal (catches illiquid no-fill traps).

### Pipeline 5 — DUAL-ENGINE FUSION (the alpha) `egx_p5_fusion.py`
```
ml_prob = predict_ensemble(sym)          # clean 54→68 feat model
rule_score = scorer.js via python_bridge # WR=69% rule engine
regime gate (BULL .60 / NEUTRAL .68 / BEAR .82)
ULTRA = both agree ; HIGH = one strong ; else drop
```
Solves: combines two independent edges; agreement = signal.

### Pipeline 6 — CLIENT DELIVERY + PROOF LOOP (E4) `egx_p6_deliver.py`
```
for each ULTRA:
   draw_shape(entry,stop,T1,T2) → capture_screenshot → client report image
   alert_create(entry×1.02 confirmation)
   INSERT forward_test_predictions(PENDING)
daily: quote_get(pending) → return_t1/t5/t10 → WIN/LOSS/FLAT
weekly: if ≥30 ULTRA done → live WinRate = client headline
```
Solves: executable output + the measured track record (no promised numbers).

---

## D. Build Sequence (phased, honest)

| Phase | Pipelines | Days | Exit gate |
|---|---|---|---|
| **P0 Connect** | — | 0.5 | TV MCP in `~/.claude.json`; `tv_health_check` green |
| **P1 Sync** | P1 + Validator | 1 | OHLCV gap ≤1d; EGX30 present; bad bars rejected |
| **P2 Clean retrain** | (code) Phase3 clean labels + real RS | 1 | rs_* non-constant; AUC re-measured |
| **P3 Context** | P3 | 1 | macro/liquidity T-1 fresh; sector filled |
| **P4 Features** | P2 (Pine) | 2 | 54→68 feat; Pine⊕Python agree |
| **P5 Fusion** | P5 | 1 | ULTRA tier emitted; replay-checked |
| **P6 Deliver** | P6 | 1 | annotated report + alerts + tracker live |
| **P7 PROOF** | — | calendar | ≥30 ULTRA live, WinRate ≥60% → client beta |

P0–P6 ≈ 1 week. **P7 is market-bound — the only true gate.**

---

## E. What "best edge for the client" concretely means at P7

A daily, brand-able product:
```
EGX ULTRA SIGNALS — <date> — regime: <BULL/BEAR>
1. <SYM> <name>  conf <ml%/rule%>  entry <p> stop <p> T1 <p> T2 <p>
   size <kelly%>  R:R <x>  expiry 5d   [annotated chart image]
   why: <top-3 drivers in plain Arabic>
   liquidity: max safe order <EGP>  | pump-risk: <low/med/high>
LIVE TRACK RECORD: <N> signals, WinRate <x%>, avg R <y>  (rolling 30)
```
The headline number is **measured**, regime-aware, manipulation-screened,
liquidity-aware, and reproducible — that is the defensible edge.
