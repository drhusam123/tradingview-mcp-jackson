# MDE Phase 2.10E — Forward Paper-Trading + Historical Replay Final Gate

**Generated:** 2026-06-13T01:12:53.843007+00:00
**Verdict:** RESEARCH_EDGE_ONLY

Remain Research Edge — forward paper continues

## Phase Goal

Prove whether COMP_001B and PRDC can move from Research Edge to Client-Grade Shadow Edge.

## Invariants (locked)

```text
EGX_MDE_SHADOW=1 | EGX_MDE_OPP_BOOST=0 | EGX_MDE_BEHAVIOR_MEMORY=0
No Phase 3 | No client path | No promotion | No Telegram | No real trades
No veto | No suppression | Paper-only
```

## 1. Historical Replay — COMP_001B (sanitized ledger)

Filters: COMP_001B + confirmation + tradeability≥70 + 100bps + dedup 10d + artifact/ghost/late excluded

- trade_count: **77**
- net_PF_100bps: **1.49** (gate ≥2.0)
- median_return: **1.222%**
- win_rate: 29.9%
- avg_win / avg_loss: 8.86 / -5.325
- max_drawdown (monthly cum): -51.31%
- winning_months / losing_months: 9 / 3
- max_losing_streak: 11
- top_10_trade_contribution: 55.5%
- tradeability_median: 100
- capacity: ~77 trades / 12 months

**Interpretation:** Same 2.10E gates historically produce positive median (+1.22%) but net PF 1.49 < 2.0 — research-grade survives, client-grade not proven.

## 2. Forward Monitor — COMP_001B

- latest_date: 2026-06-11
- historical_paper_trades: 77
- latest_day_signals: 0
- state_counts: {"OPEN_PAPER_TRADE": 18, "REJECTED_AFTER_TRIGGER": 7}

Daily decisions: NEW_SIGNAL | WAIT_CONFIRMATION | OPEN_PAPER_TRADE | HOLD | EXIT | INVALIDATED | REJECTED_AFTER_TRIGGER

## 3. PRDC Special Track

- monitor_state: **OPEN_PAPER_TRADE**
- confirmation_achieved: True
- hidden_cause_latent: True
- metaorder_stage: mid (not exhausted: True)
- analog_fusion_score: 52.2
- client_grade_eligible: **False** (individual track only)

## 4. Client-Grade Gate

- execution_filtered_PF: 1.49

- forward_paper_trades_gte_20: ✓
- net_PF_100bps_gte_2: ✗
- median_return_gt_0: ✓
- execution_filtered_PF_gte_1_8: ✗
- max_drawdown_acceptable: ✗
- no_top10_dominance: ✗
- tradeability_median_gte_70: ✓

**Gates passed:** 3/7

## Decision

```text
MDE = Research Edge (strong)
Client-grade = NOT proven
COMP_001B = research-grade, historically paper-viable @ PF 1.49
PRDC = best individual candidate, special shadow track only
Next: continue forward paper; discuss Client-Grade Shadow Pilot ONLY if gate passes
```

```text
Paper only. No client path.
```