# LRE-3.4 — Confluence Robustness & Dominance Detox

**Generated:** 2026-06-14T16:13:23.654350+00:00
**Verdict:** `RESEARCH_EDGE_PROMISING_BUT_CONCENTRATED` — Edge real but concentrated — raw dom=36.1%, exclude_top_1 PF=1.74, exclude_top_3 PF=1.5

## A. Why 3.3 Did Not Pass

LRE-3.3 confluence OOS: PF=1.86, median=+1.91%, hit+5%=44%, stop=31% — but top-10 dominance=36.1% > 35% threshold.

## B. Dominance Detox Results

| Test | Trades | PF | Median | Stop% | Top-10 |
|------|--------|-----|--------|-------|--------|
| raw_confluence | 116 | 1.86 | 1.907% | 31.0% | 36.1% |
| exclude_top_1 | 114 | 1.74 | 1.363% | 31.6% | 35.5% |
| exclude_top_3 | 109 | 1.5 | 0.457% | 32.1% | 33.3% |
| exclude_top_5 | 104 | 1.33 | 0.153% | 33.7% | 32.7% |
| exclude_top_10 | 94 | 0.96 | -2.264% | 37.2% | 25.2% |
| equal_weight_per_symbol | 116 | 21.3 | 1.056% | —% | 36.1% |
| cap_symbol_10pct | 116 | 2.18 | 1.907% | 31.0% | 36.1% |
| cap_sector_25pct | 116 | 1.82 | 1.907% | 31.0% | 36.1% |

## C. Leave-One-Symbol-Out

Top contributors: ORAS, HELI, ORHD, HDBK, TMGH
Removal improves edge: 0 symbols

## D. Leave-One-Sector-Out

- Finance: sector_pnl=200.841 PF_without=1.52 delta=-0.34
- Consumer Durables: sector_pnl=91.482 PF_without=1.64 delta=-0.22
- Industrial Services: sector_pnl=49.894 PF_without=1.75 delta=-0.11
- Process Industries: sector_pnl=-33.653 PF_without=2.3 delta=0.44
- Distribution Services: sector_pnl=32.7 PF_without=1.8 delta=-0.06
- Non-Energy Minerals: sector_pnl=32.337 PF_without=1.84 delta=-0.02

## E. Bootstrap Robustness

- P(PF>1.0) = 99.5%
- P(PF>1.3) = 94.8%
- P(median>0) = 81.1%
- P(hit+5%>40) = 79.6%
- PF p25/median/p75 = 1.61/1.88/2.17

## F. Entry / Cost / Stop Robustness

- Entry same_day_close: PF=1.86 median=1.907% stop=31.0%
- Entry next_day_open: PF=1.86 median=1.907% stop=31.0%
- Entry next_day_close: PF=1.86 median=1.907% stop=31.0%
- Entry next_day_not_extended: PF=1.27 median=-0.258% stop=32.3%
- Entry wait_1d_confirmation: PF=1.86 median=1.907% stop=31.0%
- Entry wait_2d_confirmation: PF=1.86 median=1.907% stop=31.0%

- Cost 50bps: PF=2.11 median=2.407%
- Cost 100bps: PF=1.86 median=1.907%
- Cost 150bps: PF=1.71 median=1.407%
- Cost 200bps: PF=1.5 median=0.907%

- Stop no_stop_20d: PF=1.55 stop_hit=0.0%
- Stop stop_6pct: PF=1.99 stop_hit=33.1%
- Stop stop_8pct: PF=1.88 stop_hit=23.5%
- Stop stop_10pct: PF=1.99 stop_hit=14.8%
- Stop atr_stop: PF=1.9 stop_hit=49.2%
- Stop base_low_stop: PF=1.86 stop_hit=31.0%

## G. OLFI Review

{
  "symbol": "OLFI",
  "review_date": "2026-06-11",
  "current_audit": {
    "trade_date": "2026-06-11",
    "symbol": "OLFI",
    "lre_stage": 4,
    "lre_sub_stage": "4B",
    "lre_sub_label": "Controlled_Pre_Ignition",
    "lre_eps": 75.3,
    "lre_candidate_type": "Pre_Breakout_Compression",
    "lre_reason_codes": [],
    "lre_risk_flags": [],
    "lre_monitoring_only": true,
    "lre_monitoring_valid": true,
    "artifact_flag": false,
    "liquidity_flag": false,
    "already_exploded_flag": false,
    "mde_stage": "EARLY_ACCUMULATION",
    "mde_score": 66.37,
    "mde_gate_passed": 1,
    "mde_reason_codes": [
      "hidden_repricing",
      "COMP_001B",
      "confirmation_ok",
      "tradeability_ok"
    ],
    "mde_risk_flags": [],
    "dual_gate_type": "LRE_MDE_CONFLUENCE",
    "dual_gate_score": 75.5,
    "dual_gate_passed_shadow": 1,
    "dual_gate_reason": "LRE 3B/4A/4B + MDE confirmation",
    "client_path_allowed": 0,
    "mfe_20d": 3.032,
    "mae_20d": 0.0,
    "hit_5pct_20d": 0,
    "hit_10pct_20d": 0,
    "hit_15pct_30d": 0
  },
  "lre_sub_stage": "4B",
  "dual_gate_type": "LRE_MDE_CONFLUENCE",
  "dual_gate_score": 75.5,
  "mde_gate_passed": 1,
  "sector": "Consumer Non-Durables",
  "historical_confluence_trades": 0,
  "historical_confluence_metrics": null,
  "top_contributor_symbols": [
    "ORAS",
    "HELI",
    "ORHD",
    "HDBK",
    "TMGH",
    "SDTI",
    "MASR",
    "FWRY",
    "ACAMD",
    "EGAL"
  ],
  "resembles_top_contributor_pattern": false,
  "sector_dominance_pct": 3.4,
  "sector_trade_count": 4,
  "comparable_symbols_same_sector": [
    "AJWA",
    "EAST",
    "JUFO"
  ],
  "after_dominance_detox": {
    "note": "OLFI has 0 historical OOS confluence trades in replay \u2014 current-only confluence",
    "in_top_10_contributors": false,
    "median_contributor_pnl": 9.092
  },
  "monitoring_only": true,
  "clean_confluence": true,
  "outlier_family_risk": false
}

## H. Final Decision

**RESEARCH_EDGE_PROMISING_BUT_CONCENTRATED** — Edge real but concentrated — raw dom=36.1%, exclude_top_1 PF=1.74, exclude_top_3 PF=1.5

## Answers

1. **هل confluence edge حقيقي أم outlier-driven؟** — raw PF=1.86 | exclude_top_1 PF=1.74 | exclude_top_3 PF=1.5 | bootstrap P(PF>1.3)=94.8% — edge حقيقي لكن مركز
1. **هل top-10 dominance مشكلة قاتلة أم هامشية؟** — raw dom=36.1% — هامشية (+1.1% فوق الحد) لكن تمنع PASS
1. **هل حذف top 1/3/5 يقتل PF؟** — ex1 PF=1.74 | ex3 PF=1.5 | ex5 PF=1.33 — لا يقتل
1. **هل edge يتحمل التكاليف؟** — 100bps PF=1.86 | 150bps PF=1.71 | 200bps PF=1.5
1. **هل edge موزع زمنياً أم محصور في فترة؟** — 2025_H1 PF=8.87 n=3 | 2025_H2 PF=1.51 n=77 | 2026_YTD PF=3.02 n=44
1. **هل OLFI حالة نظيفة أم من نفس عائلة outliers؟** — dual_gate=LRE_MDE_CONFLUENCE hist_trades=0 resembles_top=False clean=True
1. **هل نرفع confluence إلى shadow pilot أم يبقى monitoring-only؟** — RESEARCH_EDGE_PROMISING_BUT_CONCENTRATED — monitoring-only / shadow log only

---
*Shadow only — no production / client path.*