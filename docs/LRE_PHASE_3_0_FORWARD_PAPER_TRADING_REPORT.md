# LRE Phase 3.0 — Forward Paper-Trading + Historical Replay Gate

**Generated:** 2026-06-14T15:47:53.033317+00:00
**Verdict:** RESEARCH_EDGE_ONLY

Remain Research Edge — forward paper continues

## Invariants

```text
EGX_LRE_SHADOW=1 | EGX_LRE_OPP_BOOST=0
No client path | No promotion | No Telegram | Paper-only
```

## 1. Historical Replay — Ignition Candidates (Stage 3–4)

Filters: stage 3–4 + EPS≥50.0 + not chase + structural stop + 100bps + dedup 10d

- trade_count: **2760**
- net_PF_100bps: **0.96** (gate ≥2.0)
- median_return: **-1.61%**
- win_rate: 15.1%
- eps_median: 55.9
- stop_hit_ratio: 60.8%
- max_drawdown (monthly cum): -550.08%

## 2. Forward Monitor

- latest_date: 2026-06-14
- historical_paper_trades: 2760
- latest_open_or_pending: 94
- state_counts: {"REJECTED_AFTER_TRIGGER": 117, "OPEN_PAPER_TRADE": 76, "WAIT_CONFIRMATION": 18}

## 3. Top-EPS Special Track

- symbol: **ADCI**
- monitor_state: WAIT_CONFIRMATION
- eps: 75.0

## 4. Client-Grade Gate

- execution_filtered_PF: 0.97

- forward_paper_trades_gte_20: ✓
- net_PF_100bps_gte_2: ✗
- median_return_gt_0: ✗
- execution_filtered_PF_gte_1_8: ✗
- max_drawdown_acceptable: ✗
- no_top10_dominance: ✓
- eps_median_gte_50: ✓

**Gates passed:** 3/7

## Decision

```text
LRE = Research Edge until gate passes
Client-grade = NOT proven until net PF ≥ 2.0 with full gates
Continue forward paper — no actionable impact
```