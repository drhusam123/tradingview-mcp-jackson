# EGX Market Discovery Engine — Integration Contract

## Identity

```text
MDE Core        = discovery brain (hidden repricing before explosion)
MDE Integration = safe plug-in to existing machine (deferred until shadow proof)
```

## Phase 2.10E guarantees (current)

- `mde_forward_paper_trading.py` — COMP_001B historical replay + forward paper gate
- `mde_signal_provider.py` — daily shadow signal provider (`mde_shadow_signals_daily`)
- Provider tier: **RESEARCH_SHADOW** — `client_grade_eligible=0`
- `score_all` logs `shadow_mde_*` fields only — **no actionable change**
- Client-grade gate: **RESEARCH_EDGE_ONLY** (net PF 1.49 < 2.0) — no client path

## Phase 2.7 guarantees (current)

- `mde_walkforward_shadow.py` — causal walk-forward: memory built only from data before T
- Compares baseline MDE vs memory-adjusted confidence (shadow only)
- `EGX_MDE_BEHAVIOR_MEMORY=0` remains default — walk-forward must prove edge first

## Phase 2.6 guarantees

- `mde_full_history_mining.py` backfills **full OHLCV history** into `egx_market_discovery_daily`
- Behavioral outputs: profiles, families, rules, sector map
- `EGX_MDE_BEHAVIOR_MEMORY=0` (default) — memory hook optional, confidence only
- **Still no** opp_v2, UES, promotion, Telegram, veto, or Phase 3 boost

## Phase 2 guarantees

- `mine_egx_mde()` proposes `mde_*` fabric atoms (boost only, `hard_negative=0`)
- `mde_oos_attribution.py` → `discovery_mde_manifest.json` + `mde_shadow_attribution_last.json`
- Manifest lists: `mde_watch_atoms`, `mde_boost_atoms`, `mde_priority_atoms`, `mde_rejected_atoms`
- **Excluded** from `discovery_ml_manifest.json` `penalize_atoms` / `priority_atoms`
- **Still does NOT** modify: UES, score_all, promotion, arbitration, Telegram, opp_v2, `final_signals`

## Phase 1 guarantees

- `egx_market_discovery_engine.py` runs in **shadow mode**
- Outputs: `egx_market_discovery_daily`, `data/mde_shadow_last.json`
- **Does NOT** modify: UES, score_all, promotion, arbitration, Telegram, opp_v2

## Feature flags

| Flag | Default | Meaning |
|------|---------|---------|
| `EGX_MDE_ENABLED` | `1` (set `0` to skip) | Run engine in EOD pipeline |
| `EGX_MDE_SHADOW` | `1` | Log/store only |
| `EGX_MDE_OPP_BOOST` | `0` | No opp_v2 influence |
| `EGX_MDE_PAPER_REFRESH` | `0` | Set `1` to run full 2.10E paper replay in EOD pipeline |

## MDE signal provider (Phase 2.10E)

| Field | Value |
|-------|-------|
| Provider ID | `MDE_2_10E` |
| Table | `mde_shadow_signals_daily` |
| Tracks | `COMP_001B`, `PRDC_SPECIAL` |
| Tier | `RESEARCH_SHADOW` |
| npm | `egx:mde:signal-provider` |

States: `NEW_SIGNAL` | `WAIT_CONFIRMATION` | `OPEN_PAPER_TRADE` | `REJECTED_AFTER_TRIGGER` | `INVALIDATED`

## LRE integration (Phase 2.0 — companion engine)

See `docs/EGX_LIQUIDITY_ROTATION_CONTRACT.md` for full LRE contract.

| Field | Value |
|-------|-------|
| Provider ID | `LRE_2_0` |
| Table | `lre_shadow_signals_daily` |
| Tier | `RESEARCH_SHADOW` |
| npm daily | `egx:lre:daily` |
| npm provider | `egx:lre:signal-provider` |

- `score_all` logs `shadow_lre_*` fields only — **no actionable change**
- Five daily lists: awakening / silent / ignition / do-not-chase / next-rotation
- Complements MDE: rotation graph vs hidden repricing

## OOS tiers (MDE atoms — Phase 2+)

| Tier | PF | lift | n | Effect |
|------|-----|------|---|--------|
| Research / Shadow | ≥1.15 | ≥1.03 | ≥40 | watch atoms only |
| Production Boost | ≥1.25 | ≥1.07 | ≥50 | optional opp +5 max |
| Client-Influence | ≥1.40 | ≥1.10 | stable | future |

## Hard rules

1. MDE pass → boost / flag (later). MDE fail → **nothing** (never blocks legacy path).
2. No `hard_neg` MDE atoms in v1.
3. Client path unchanged: `final_signals.actionable=1` + safety + prep.
