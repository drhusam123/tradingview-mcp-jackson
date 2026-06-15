# LRE Integration Contract — Research Feed Only

**Phase:** LRE-4.0 | **Status:** ACTIVE (shadow / additive)

## Invariants (non-negotiable)

```text
EGX_LRE_SHADOW=1
EGX_LRE_OPP_BOOST=0          # client opp boost locked until OOS PASS
EGX_LRE_FEED_BOOST=1         # L7 research modifier (+3 max)
FEED_SYSTEM=1
CLIENT_SIGNAL=0
client_path_allowed=False
additive_only=True
no_veto=True
no_suppression=True
no_actionable_change=True
```

## What LRE may do

| Layer | Action | Max impact |
|-------|--------|------------|
| L2 | `lre_research_feed_daily`, shadow signals | Data only |
| L7 | `opportunity_score_v2` +0..+3 | Ranking research |
| L11 | `lre_*` fabric atoms | Manifest / miners |
| Prioritizer | intelligence_score +0..+1.5 | Watchlist research |
| Gate audit | `shadow_lre_*` fields | Observe only |

## What LRE must NOT do

- Write `final_signals.actionable=1`
- Telegram / client reports
- Arbitration veto or suppression
- Change promotion thresholds
- `EGX_LRE_OPP_BOOST=1` without explicit graduation gate

## Graduation gate (future)

Requires: 40+ live forward OOS, PF≥1.3, top-10 dominance <35%, fabric Production tier.

## Daily pipeline (LRE-4.0 stack)

```text
lre_dual_gate_daily → lre_3_5 pilot → lre_4_0_research_feed → lre_3_6b_forward_shadow
    → discovery fabric → opportunity_score_v2
```

| Script | npm | Role |
|--------|-----|------|
| `lre_dual_gate_daily.py` | `egx:lre:dual-gate-daily` | Causal audit upsert (feeds confluence) |
| `lre_4_0_research_feed.py` | `egx:lre:research-feed` | Unified research feed |
| `lre_3_6b_forward_shadow_pilot.py` | `egx:lre:forward-shadow` | Live OOS ledger (≥ 2026-06-12) |
| `lre_4_0_integration_test.py` | `egx:lre:integration-test` | Before/after opp test |
| `lre_4_0_acceptance.py` | `egx:lre:acceptance` | Invariant gate |
| `lre_4_0_status.py` | `egx:lre:status` | Health + graduation tracker |

**3.6B** skips dates before `FORWARD_START=2026-06-12` by design.
