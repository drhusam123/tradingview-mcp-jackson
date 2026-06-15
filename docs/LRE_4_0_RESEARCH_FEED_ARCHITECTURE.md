# LRE-4.0 — Research Feed Architecture

**Generated:** 2026-06-13 | **Verdict:** Integrated as research feeder

## A. Purpose

Consolidate LRE-2.0 through LRE-3.6A into one **daily research feeding source** that powers:

- Discovery Fabric (L11) — `egx_lre_miner` atoms
- Opportunity Score v2 (L7) — +0..+3 boost via `lre_opp_bridge`
- Intelligence Prioritizer — watchlist boost

Does **not** touch `final_signals`, Telegram, or promotion.

## B. Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│  INPUTS (causal daily)                                      │
├─────────────────────────────────────────────────────────────┤
│  lre_daily_scores (2.0 radar)                               │
│  lre_mde_dual_gate_audit OR causal confluence (3.3–3.6A)   │
│  lre_dual_gate_shadow_pilot (3.5 caps context)              │
│  egx_market_discovery_daily (MDE per day)                   │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
              lre_4_0_research_feed.py
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
 lre_research_feed_daily   discovery_lre_manifest.json
         │                 lre_learning_snapshot.json
         ├─► discovery_domain_miners.mine_egx_lre()
         ├─► opportunity_score_v2 + lre_opp_bridge
         └─► intelligence_prioritizer.load_lre_research_map()
```

## C. Feed tiers

| Tier | Condition | opp_boost |
|------|-----------|-----------|
| `LRE_CLEAN_CORE` | Clean confluence + pilot eligible | +3.0 |
| `LRE_CONFLUENCE_CAPPED` | Pilot eligible (any bucket) | +2.5 |
| `LRE_4B_MONITOR` | 4B + eligible | +2.0 |
| `LRE_CONFLUENCE` | Confluence not capped | +2.0 |
| `LRE_GATE` | Stage 3/4, EPS≥50 | +1.0 |
| `LRE_MONITOR` | EPS≥35 | +0 |

## D. Fabric atoms (L11)

- `lre_ignition_candidates`, `lre_next_rotation`, `lre_silent_accumulation`
- `lre_gate_candidates`, `lre_confluence_clean_core`
- `lre_mde_confluence`, `lre_mde_confluence_capped`

## E. Pipeline position

```text
LRE daily → signal provider → dual-gate daily → 3.5 pilot
    → LRE-4.0 feed → 3.6B forward ledger (dates ≥ 2026-06-12)
    → discovery fabric light → opportunity_score_v2 → …
```

| Step | Script | npm |
|------|--------|-----|
| Dual-gate daily (causal upsert) | `lre_dual_gate_daily.py` | `egx:lre:dual-gate-daily` |
| Research feed | `lre_4_0_research_feed.py` | `egx:lre:research-feed` |
| Forward shadow ledger | `lre_3_6b_forward_shadow_pilot.py` | `egx:lre:forward-shadow` |
| Integration test | `lre_4_0_integration_test.py` | `egx:lre:integration-test` |

**Dual-gate daily** refreshes `lre_mde_dual_gate_audit` for one `trade_date` only (fast path).
Feeds confluence tiers into LRE-4.0; does not wipe historical audit rows.

**LRE-3.6B** appends eligible confluence rows to `lre_forward_shadow_ledger` for live OOS
tracking after the 3.6A walk-forward window (`FORWARD_START = 2026-06-12`).

**Prioritizer** runs after `opportunity_score_v2` in EOD pipeline; `load_lre_research_map()`
applies confluence watch boosts (+0..+1.5 scaled).

## F. Integration test

`npm run egx:lre:integration-test` — verifies:

- `final_signals.actionable` unchanged
- opp delta ≤ +3 on feed symbols only
- feed table populated
