# LRE-3.6A — Historical Walk-Forward Capped Shadow Pilot

**Generated:** 2026-06-14 16:14 UTC
**Verdict:** `RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED`

> Forward-like shadow pilot validated ≠ production / Telegram / actionable / client path.

## Invariants

```json
{
  "EGX_LRE_SHADOW": "1",
  "EGX_LRE_OPP_BOOST": "0",
  "no_client_path": true,
  "no_veto": true,
  "no_suppression": true,
  "no_actionable_change": true,
  "phase": "LRE-3.6A",
  "client_path_allowed": false,
  "shadow_pilot_only": true
}
```

## A. Why walk-forward?

- Simulates each historical day as if it were “today” without using future bars in signal generation.
- Surfaces threshold leakage and overfitting before waiting months of real forward data.
- Complements LRE-3.5 replay (which used full-sample calibration).

## B. Methodology

- Simulation window: **2025-01-01 → 2026-06-11**
- Calibration warmup from **2020-12-10**
- **Expanding window:** train/calibrate on all sessions before `trade_date`.
- **Rolling window:** last **500** sessions before `trade_date`.
- **STATIC_THRESHOLDS:** LRE-3.x full-sample A-sim calibration (leakage risk flagged).
- **WALK_FORWARD_RECALIBRATED_THRESHOLDS:** A-sim percentiles only from prior events.
- MDE rows read from `egx_market_discovery_daily` per day (metrics_json analog fields may carry backfill leakage — see leakage audit).
- Caps in pilot path: symbol 10%, sector 25%, finance 25%.

## C. Results (primary: expanding + walk-forward thresholds, same-day entry)

| Metric | Value |
|--------|-------|
| Trades | 93 |
| PF@100bps | 2.14 |
| PF@150bps | 1.97 |
| Median return | 2.286% |
| Hit +5% | 43.0% |
| Top-10 dominance | 44.0% |
| Finance exposure | 23.7% |

### Buckets

- **Clean_Confluence_Core:** n=58 PF@100=2.3 median=2.942%
- **Controlled_4B_Monitor:** n=35 PF@100=1.96 median=-0.32%
- **Core_plus_4B:** n=93 PF@100=2.14 median=2.286%
- **New_Pattern_Monitor:** n=0 PF@100=None median=None%
- **All_eligible:** n=93 PF@100=2.14 median=2.286%

## D. Caps impact

- **raw:** n=117 PF=1.86 top10=31.5% finance=38.5%
- **sector_cap_only:** n=93 PF=2.14 top10=44.0% finance=23.7%
- **finance_cap_25:** n=93 PF=2.14 top10=44.0% finance=23.7%
- **symbol_cap_only:** n=117 PF=1.86 top10=31.5% finance=38.5%
- **symbol_sector_finance_cap_25:** n=93 PF=2.14 top10=44.0% finance=23.7%

## E. Entry timing

- **same_day_close:** n=93 PF=2.14 median=2.286%
- **next_day_close:** n=93 PF=1.76 median=0.811%
- **next_day_not_extended:** n=80 PF=1.6 median=-0.022%
- **wait_1d_confirmation:** n=48 PF=1.59 median=1.059%

## F. Leakage audit

- Static threshold source: `STATIC_RESEARCH_THRESHOLD`
- Warning: Calibrated from full lre_explosion_events sample — may contain research calibration leakage.
- Static PF@100: 2.14 vs Walk-forward PF@100: 2.14
- Collapse without static: False

## G. Final decision

**`RESEARCH_EDGE_FORWARD_LIKE_BUT_CONCENTRATED`**

## Answers

1. **هل confluence يعيش في walk-forward؟** — نعم جزئياً — PF@100=2.14 على n=93 (مقابل replay 1.7)
1. **هل النتيجة مشابهة لـ replay أم أضعف؟** — قريبة من replay
1. **هل caps تنجح تاريخياً بدون قتل edge؟** — combined caps PF=2.14 vs raw 1.86
1. **هل thresholds فيها leakage؟** — خطر leakage مع STATIC؛ recalibrated قريبة من static
1. **هل Clean Core أفضل من 4B؟** — Core PF=2.3 vs 4B PF=1.96
1. **هل نستمر في forward الحقيقي أم نعود للتعديل؟** — راقب / عدّل thresholds قبل forward

---
*Shadow research only. `client_path_allowed=False` always.*