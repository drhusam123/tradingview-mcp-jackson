# LRE-3.1 — Tight Filter Replay & Stop-Prone Audit

**Generated:** 2026-06-14T15:49:07.009097+00:00
**Final Decision:** RESEARCH_EDGE_WEAK_BUT_IMPROVED

ultra_conservative improved PF/stop/median but top-10 dominance 95.3% — curve-fit risk

## A. Why LRE-3.0 Failed

- **stop_prone:** baseline stop_hit=60.9% — majority of losses
- **fake_ignition:** baseline stage_5_6=0.0% leakage into extended moves
- **artifact:** artifact contribution 0.0%
- **low_liquidity:** low-liq 0.0%
- **late_entry:** already-exploded contamination 36.8%
- **wide_filter:** 2755 trades @ PF 0.96 — filter too permissive at EPS>=50

## B. Filter Tightening Results (Full Sample)

| Mode | Trades | PF@100bps | Median% | WR% | StopHit% | A-Purity% |
|------|--------|-----------|---------|-----|----------|-----------|
| LRE-3.0 Baseline | 2768 | 0.96 | -1.613 | 15.1 | 60.9 | 97.3 |
| Balanced Research | 296 | 0.94 | -1.544 | 15.5 | 53.0 | 100.0 |
| Conservative | 197 | 1.32 | -0.863 | 20.8 | 41.1 | 100.0 |
| Ultra Conservative | 70 | 1.29 | 0.086 | 25.7 | 34.3 | 100.0 |

## C. OOS Results (2025–2026)

- **LRE-3.0 Baseline**: n=2736 PF=0.95 median=-1.613% stop=60.9%
- **Balanced Research**: n=294 PF=0.84 median=-1.544% stop=53.1%
- **Conservative**: n=195 PF=1.17 median=-0.863% stop=41.0%
- **Ultra Conservative**: n=69 PF=1.31 median=0.25% stop=33.3%

### Latest 6m / 3m

**latest_6m**
  - baseline_3_0: n=955 PF=0.93
  - balanced_research: n=115 PF=0.91
  - conservative: n=73 PF=1.65
  - ultra_conservative: n=26 PF=2.36

**latest_3m**
  - baseline_3_0: n=249 PF=2.28
  - balanced_research: n=47 PF=1.62
  - conservative: n=33 PF=2.8
  - ultra_conservative: n=13 PF=7.09


## D. Family A Purity

Thresholds calibrated from 519 A events: balanced≥85.0 conservative≥87.3 ultra≥88.1

## E. Stop-Prone Analysis

- baseline_3_0: full stop_hit 60.9% (baseline 60.9%)
- balanced_research: full stop_hit 53.0% (baseline 60.9%)
- conservative: full stop_hit 41.1% (baseline 60.9%)
- ultra_conservative: full stop_hit 34.3% (baseline 60.9%)

## F. Candidate Review

### OLFI
- verdict: **mixed** | eps=71.8 A=84.0 stop_prone=14.0
  - baseline_3_0: PASS []
  - balanced_research: FAIL ['A_similarity', 'compression']
  - conservative: FAIL ['A_similarity', 'compression']
  - ultra_conservative: FAIL ['A_similarity', 'compression']

### HBCO
- verdict: **mixed** | eps=52.6 A=65.9 stop_prone=26.0
  - baseline_3_0: FAIL ['stage', 'move_extended', 'do_not_chase']
  - balanced_research: FAIL ['stage', 'eps', 'A_similarity', 'move_extended', 'prior_20d', 'prior_40d', 'already_exploded', 'do_not_chase', 'compression']
  - conservative: FAIL ['stage', 'eps', 'A_similarity', 'move_extended', 'prior_20d', 'prior_40d', 'already_exploded', 'do_not_chase', 'compression']
  - ultra_conservative: FAIL ['stage', 'eps', 'A_similarity', 'move_extended', 'prior_20d', 'prior_40d', 'already_exploded', 'do_not_chase', 'compression']

### EFIC
- verdict: **mixed** | eps=48.5 A=80.5 stop_prone=14.0
  - baseline_3_0: FAIL ['eps']
  - balanced_research: FAIL ['eps', 'A_similarity', 'volume_band']
  - conservative: FAIL ['eps', 'A_similarity', 'volume_band']
  - ultra_conservative: FAIL ['stage', 'eps', 'A_similarity', 'supply_exhaustion', 'volume_band']

### EGAS
- verdict: **mixed** | eps=61.3 A=86.2 stop_prone=12.0
  - baseline_3_0: PASS []
  - balanced_research: FAIL ['prior_40d', 'already_exploded', 'recent_stage_6_7', 'volume_band', 'compression']
  - conservative: FAIL ['A_similarity', 'prior_40d', 'already_exploded', 'recent_stage_6_7', 'volume_band', 'compression']
  - ultra_conservative: FAIL ['stage', 'eps', 'A_similarity', 'move_extended', 'prior_40d', 'already_exploded', 'recent_stage_6_7', 'volume_band', 'compression']


## G. Answers

1. **هل الفشل في 3.0 بسبب فلتر واسع؟** — نعم — 2768 صفقة @ PF 0.96 و stop_hit 60.9%
2. **هل عزل عائلة A يحسن النتائج؟** — Conservative OOS PF 1.17 vs baseline 0.95 | A-purity 100.0%
3. **هل stop-prone score خفّض stop hits؟** — baseline stop 60.9% → conservative 41.1%
4. **أي mode أفضل؟** — ultra_conservative
5. **OLFI/HBCO/EFIC/EGAS حقيقيون أم EPS عالي؟** — OLFI=mixed, HBCO=mixed, EFIC=mixed, EGAS=mixed
6. **LRE-4.0 أم monitoring-only؟** — monitoring-only — tight filter improves edge but not LRE-4.0 gate

## LRE-4.0 Gate

**Proceed to Rotation Graph Optimization:** NO — monitoring-only

```text
Shadow only. client_path_allowed=False.
```