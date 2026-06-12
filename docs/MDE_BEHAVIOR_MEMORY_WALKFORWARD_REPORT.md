# MDE Behavioral Memory Walk-Forward Report (Phase 2.7)

**Generated:** 2026-06-12T22:17:43.065031+00:00

## Executive Summary

- **events:** 58405
- **walk_forward_windows:** 62
- **avg_delta_hit_5d:** 1.97
- **positive_window_pct:** 69.4
- **stable_dna_symbols:** 1
- **memory_edge_verdict:** Memory adds OOS edge

## Walk-Forward Windows

| train | test | mem | base hit% | mem hit% | delta |
|---:|---:|---|---:|---:|---:|
| 126 | 20 | equal | 0.0 | 0.0 | 0.0 |
| 126 | 20 | equal | 10.0 | 0.0 | -10.0 |
| 126 | 20 | equal | 11.8 | 25.0 | 13.2 |
| 126 | 20 | equal | 21.4 | 9.1 | -12.3 |
| 126 | 20 | equal | 0.0 | 0.0 | 0.0 |
| 126 | 20 | equal | 25.9 | 24.0 | -1.9 |
| 126 | 20 | equal | 37.5 | 42.9 | 5.4 |
| 126 | 20 | equal | 29.6 | 32.0 | 2.4 |
| 126 | 20 | equal | 25.8 | 27.6 | 1.8 |
| 126 | 20 | equal | 7.1 | 8.3 | 1.2 |
| 126 | 20 | equal | 10.0 | 25.0 | 15.0 |
| 126 | 20 | equal | 0.0 | 0.0 | 0.0 |
| 126 | 20 | equal | 16.7 | 17.6 | 0.9 |
| 126 | 20 | equal | 10.3 | 9.5 | -0.8 |
| 126 | 20 | equal | 52.0 | 68.8 | 16.8 |

## Memory Type Comparison

- **equal** (252d train): avg Δhit=2.58 | overfit=low
- **last_252** (252d train): avg Δhit=2.58 | overfit=low
- **exp_decay_126** (252d train): avg Δhit=2.58 | overfit=low
- **regime_aware** (252d train): avg Δhit=2.58 | overfit=low
- **sector_adjusted** (252d train): avg Δhit=2.58 | overfit=low

## Persistence Cohorts

- hr_1day: n=7286 hit_5d=22.7% PF=1.16
- hr_2day: n=1758 hit_5d=23.0% PF=1.22
- hr_3plus: n=1421 hit_5d=22.6% PF=1.38
- hr_conf_gt_70: n=7400 hit_5d=23.1% PF=1.1
- hr_eff_gt_60: n=1423 hit_5d=26.4% PF=1.06
- hr_no_setup: n=5112 hit_5d=25.2% PF=1.21
- hr_with_setup: n=5353 hit_5d=20.4% PF=1.17

## Decision

Memory adds OOS edge

```text
EGX_MDE_BEHAVIOR_MEMORY=0 — not enabled.
No Phase 3. No opp_v2/UES/promotion/Telegram/veto.
```
