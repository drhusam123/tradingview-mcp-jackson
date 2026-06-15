# EGX Liquidity Rotation Engine (LRE) — Integration Contract

## Identity

```text
LRE = additive discovery engine (liquidity rotation + pre-explosion archaeology)
Does NOT replace MDE, UES, score_all, promotion, or Telegram
Does NOT veto, suppress, or block existing signals
```

## Phase LRE-1.0 guarantees

- `egx_liquidity_rotation_engine.py archaeology` — full market, full OHLCV history
- Outputs: `lre_explosion_events` table + JSON archaeology/families/fingerprints
- **Zero client impact** — no `final_signals` / `actionable` changes
- Supply to system only after future OOS gate pass (like MDE 2.10E)

## Phase LRE-2.0 guarantees

- `egx_liquidity_rotation_engine.py daily` — per-symbol EPS, stage machine (0–7), 5 lists
- `lre_signal_provider.py run` — shadow signals in `lre_shadow_signals_daily`
- Outputs: `lre_daily_scores`, `lre_market_daily`, `lre_radar_last.json`, rotation graph
- `gate_audit_snapshots.shadow_lre_*` — observe only in score_all
- **Zero client impact** — no `final_signals.actionable` changes

### Five daily lists

| List | Stage / rule |
|------|----------------|
| Volume Awakening | Stage 2 |
| Silent Accumulation | Stage 1–2, move < 6% |
| Ignition Candidates | Stage 3–4, EPS ≥ 50 |
| Do Not Chase | Stage 5–7 or move ≥ 15% |
| Next Rotation | Lead-lag trigger, move < 12% |

### Stage machine

0 Dead → 1 Silent Accumulation → 2 Volume Awakening → 3 Supply Absorption →
4 Pre-Breakout Compression → 5 Ignition → 6 Public Chase → 7 Distribution Trap

## Phase LRE-3.0 guarantees

- `lre_forward_paper_trading.py` — historical ignition replay + forward monitor + gate
- Track: **IGNITION_CANDIDATES** (Stage 3–4, EPS ≥ 50, not chase)
- Outputs: `lre_forward_paper_trades.json`, `lre_client_grade_gate_status.json`
- Gate tiers: `RESEARCH_EDGE_ONLY` | `RESEARCH_EDGE_FORWARD_VALIDATED` | `CLIENT_GRADE_SHADOW_PILOT_READY`
- **client_path_allowed always False in v3** — paper proves edge only
- npm: `egx:lre:paper-trading` | EOD refresh: `EGX_LRE_PAPER_REFRESH=1`

## Feature flags

| Flag | Default | Meaning |
|------|---------|-------|
| `EGX_LRE_ENABLED` | `1` (set `0` to skip) | Run LRE in EOD pipeline |
| `EGX_LRE_SHADOW` | `1` | Log/store only |
| `EGX_LRE_OPP_BOOST` | `0` | No opp_v2 influence |
| `EGX_LRE_ARCHAEOLOGY_REFRESH` | `0` | Set `1` for weekly full archaeology in EOD |
| `EGX_LRE_PAPER_REFRESH` | `0` | Set `1` to run LRE-3.0 paper replay in EOD |
| `EGX_LRE_FILTER_REFRESH` | `0` | Set `1` to run LRE-3.1 filter tightening replay in EOD |
| `EGX_LRE_STAGE_REBUILD_REFRESH` | `0` | Set `1` to run LRE-3.2 stage rebuild diagnostic in EOD |

## Phase LRE-3.1 guarantees

- `lre_3_1_filter_tightening.py` — A-family DNA filter + stop-prone audit (keeps 3.0 baseline)
- Modes: `baseline_3_0` | `balanced_research` | `conservative` | `ultra_conservative`
- Outputs: `lre_3_1_*` JSON + `LRE_PHASE_3_1_FILTER_TIGHTENING_REPORT.md`
- npm: `egx:lre:filter-tightening`
- **client_path_allowed=False** — monitoring / research only

## Phase LRE-3.2 guarantees

- `lre_3_2_stage_rebuild.py` — sub-stages 3A/3B/4A/4B/4X + threshold/timing/stop diagnostic
- Preserves LRE-3.0 + LRE-3.1 for three-way comparison
- npm: `egx:lre:stage-rebuild` | EOD: `EGX_LRE_STAGE_REBUILD_REFRESH=1`
- **No Rotation Graph, no client path**

## Hard rules

1. LRE pass → watch / research atoms only. LRE fail → **nothing**.
2. No `hard_neg` LRE atoms in v1.
3. Client path unchanged: `final_signals.actionable=1` + safety + prep.
4. LRE complements MDE — rotation graph vs hidden repricing.

## Relationship to MDE

| Engine | Question |
|--------|----------|
| MDE | Is the stock in hidden repricing? |
| LRE | Is liquidity rotating toward this stock before explosion? |
