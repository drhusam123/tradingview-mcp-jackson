# MDE Phase 2.7 — Deep Behavioral Memory Audit

**Generated:** 2026-06-12T22:46:50.445716+00:00

> Shadow research only. `EGX_MDE_BEHAVIOR_MEMORY=0` | No Phase 3 | No veto | No suppression

## Executive Answers

### لماذا تحسنت؟
Memory يفلتر HR الضعيف ويعزز الإشارات المتوافقة مع أفضل setup تاريخي للسهم. OOS 252/20: hit_5d 22.9% → 25.0% (+2.1pp)، PF 1.2 → 1.28. أقوى مساهمة setup: failed_breakdown (+4.2pp).

### أين تحسنت؟
القطاعات: Finance (2.3pp)، Process Industries، Industrial Services. التركيز الرمزي: top-10 يفسر 28.1% من التحسن. النوافذ: 43/62 تحسنت.

### متى فشلت؟
12 نافذة ساءت — غالبًا في downtrend أو عينات HR قليلة. مثال: 2023-09-12 → 2023-10-11 (memory boosted low-quality HR in choppy window)

### هل التحسن قابل للتكرار؟
positive_window_rate=69.4% — تحسن متوسط لكن ليس حاسمًا. أنواع الذاكرة الثمانية أعطت نفس Δhit تقريبًا → التحسن من الفلترة لا من نوع الذاكرة.

### هل الذاكرة اكتشفت DNA حقيقي؟
Stable DNA: 1 سهم فقط من 248. 203 Regime-Switch → ذاكرة ثابتة per-symbol غير آمنة. الذاكرة مفيدة كـ setup-level لا symbol-DNA ثابت.

## Institutional Decision

**A) Keep disabled**

### Rationale

- avg_delta_hit_5d: 1.97
- positive_window_pct: 69.4
- stable_dna_count: 1
- symbol_concentration_narrow: False
- best_memory_type: A_equal

### What to feed now (shadow-only)

- false discovery confidence penalties (docs only)
- persistence/effective>60 research tags
- sequence patterns classified Reliable/Weak

### Defer

- EGX_MDE_BEHAVIOR_MEMORY=1 activation
- sector-wide memory without more Stable DNA
- hard worst-setup penalties

### Reject

- Phase 3 integration
- opp_v2 / UES / promotion / Telegram changes
- veto or suppression based on memory

### Needs more data

- closing_pressure wiring into MDE
- TV features daily coverage >40%
- multi-week live shadow with memory confidence simulation
- more Stable DNA symbols (currently 1)

## 1. Full Performance Decomposition

| horizon | base hit | mem hit | Δhit | base PF | mem PF | ΔPF | base avg | mem avg | Δret | base DD | mem DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5d | 22.9 | 25.0 | 2.1 | 1.2 | 1.28 | 0.08 | 1.83 | 2.07 | 0.24 | -12.09 | -12.18 | -0.09 |
| 10d | 31.2 | 33.4 | 2.2 | 1.62 | 1.77 | 0.15 | 3.01 | 3.39 | 0.38 | -12.09 | -12.18 | -0.09 |
| 20d | 40.5 | 42.5 | 2.0 | 2.25 | 2.33 | 0.08 | 5.09 | 5.49 | 0.4 | -12.09 | -12.18 | -0.09 |

False discovery: baseline 45.6% → memory 44.3% | MAE 5d: -4.52% → -4.84% | MFE 5d: 7.33% → 7.86%

## 2. Window Stability

Improved: 43 | Worsened: 12 | Strong+: 6 | Strong-: 1

## 3. Sector Attribution (top deltas)

- Energy Minerals: Δhit=19.0 contribution=None%
- Industrial Services: Δhit=4.7 contribution=None%
- Unknown: Δhit=3.7 contribution=None%
- Transportation: Δhit=3.3 contribution=None%
- Process Industries: Δhit=3.1 contribution=None%
- Commercial Services: Δhit=3.0 contribution=None%
- Finance: Δhit=2.3 contribution=None%
- Distribution Services: Δhit=2.0 contribution=None%

## 4. Symbol Concentration

Top-10 explain 28.1% — memory improvement is moderately distributed

## 5. Setup Attribution

- accum_breakout: Δhit=None events=0
- pullback_accum: Δhit=1.8 events=486
- failed_breakdown: Δhit=4.2 events=216
- sector_follower: Δhit=0.5 events=1782
- absorption_pre_break: Δhit=0.6 events=1164
- impact_expansion: Δhit=2.7 events=3107

## 6. DNA Stability

Stable DNA: 1 | Regime-Switch: 203

## 7. Worst Setup Penalty

- GTHE/pullback_accum: hit=0% penalty_rec=True
- DEIN/impact_expansion: hit=0% penalty_rec=True
- IRAX/sector_follower: hit=0% penalty_rec=True
- TRTO/sector_follower: hit=0% penalty_rec=True
- GDWA/impact_expansion: hit=0% penalty_rec=True

## 9. Shrinkage Evidence Gates

- high: Δhit=0.5 events=5138 risk=high
- medium: Δhit=0.5 events=5138 risk=low
- low: Δhit=2.1 events=5947 risk=low

## 10. Persistence & Timing

multi-day persistence slightly improves hit rate but may delay entry
Early 58.3% | On-time 15.3% | Late 7.2% | False 19.2%

## 11–12. Sequences

- pullback_accum → impact_expansion: n=387 hit=16.1% class=Reliable Sequence
- impact_expansion → pullback_accum: n=383 hit=18.5% class=Reliable Sequence
- HR → sector_follower → move: n=347 hit=100.0% class=Reliable Sequence
- sector_follower → pullback_accum → HR: n=125 hit=20.3% class=Reliable Sequence
- impact_expansion → absorption_pre_break: n=119 hit=21.0% class=Reliable Sequence

## 13. False Discovery Rules

- impact_expansion + low liquidity + below MA50 → weak (n=412, weight=-5)
- sector_follower in Finance without strong breadth → weak (n=272, weight=-4)
- HR after large extension near 60d high → late signal (n=65, weight=-6)

## 15. Liquidity Buckets

- <1M: hit=19.5% mem_Δ=3.2
- 1-3M: hit=23.9% mem_Δ=2.5
- 3-10M: hit=23.3% mem_Δ=2.8
- 10-50M: hit=23.1% mem_Δ=1.6
- >50M: hit=25.5% mem_Δ=0.4

## 8. Memory Type Tournament

- A_equal: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- B_rolling_252: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- C_rolling_504: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- D_exp_decay_126: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- E_exp_decay_252: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- F_sector_adjusted: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- G_regime_aware: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low
- H_shrinkage: Δhit=2.58 robust=78.3% conc_top10=16.5% overfit=low

Best overall: A_equal | Most robust: A_equal

## 14. Opportunity Novelty

MDE-only recurring: ['FTNS', 'SNFC', 'BIDI', 'ADRI', 'AMPI'] | ARAB pattern: recurring

## 16. TV / Closing Pressure

TV coverage too low to conclude — CLV proxy is partial substitute

```text
EGX_MDE_BEHAVIOR_MEMORY=0 — NOT ENABLED
EGX_MDE_OPP_BOOST=0 | No Phase 3 | No veto | No suppression
```
