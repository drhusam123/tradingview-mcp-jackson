# LRE-3.2 — Stage Rebuild, Threshold & Timing Audit

**Generated:** 2026-06-14T15:53:31.312618+00:00
**Verdict:** RESEARCH_EDGE_MONITOR_ONLY

3.2 sub-stages improve radar classification; edge delayed/thin — not standalone gate; pair with MDE

## Sub-Stage Strength

- **3A**: full n=2347 PF=0.96 | OOS PF=0.96
- **4X**: full n=713 PF=1.39 | OOS PF=1.39
- **4B**: full n=775 PF=0.93 | OOS PF=0.92
- **3B**: full n=422 PF=1.19 | OOS PF=1.14
- **4A**: full n=973 PF=1.14 | OOS PF=1.09

## A_similarity Bands (OOS)

- 80-85: n=161 PF=1.63 median=-1.0% stop=29.2%
- 85-87.5: n=215 PF=0.97 median=-1.851% stop=29.3%
- >87.5: n=390 PF=0.88 median=-1.418% stop=32.8%
- >90: n=395 PF=0.99 median=-0.384% stop=26.6%

## Entry Timing

- **same_day**: n=344 PF=0.77 median=-1.277%
- **pullback**: n=309 PF=0.81 median=-1.377%
- **confirmation**: n=209 PF=16.05 median=-0.383%

## Stop Diagnostic

- stop_6pct: PF=1.0 stop_hit=22.3%
- stop_8pct: PF=0.97 stop_hit=15.2%
- atr_stop: PF=0.95 stop_hit=34.7%
- base_low: PF=0.99 stop_hit=28.6%
- no_stop_10d: PF=1.1 stop_hit=0.0%
- no_stop_20d: PF=2.79 stop_hit=0.0%

## Mode Comparison (OOS)

- lre_31_conservative: n=192 PF=0.93 median=-0.207% stop=25.0%
- lre_32_rebuilt: n=344 PF=0.77 median=-1.277% stop=30.5%
- lre_32_confirmation: n=209 PF=16.05 median=-0.383% stop=17.7%
- lre_32_pullback: n=309 PF=0.81 median=-1.377% stop=31.4%
- lre_32_monitoring: n=2164 PF=0.0 median=-1.0% stop=0.0%

## Candidate Review

### OLFI
- legacy=Pre_Breakout_Compression → **4B** (Controlled_Pre_Ignition)
- 31 fails: ['A_similarity'] | 32 fails: ['compression']
- pullback=False confirmation=False monitoring=False

### HBCO
- legacy=Ignition → **S5** (S5)
- 31 fails: ['stage', 'eps', 'A_similarity', 'move_extended', 'prior_20d', 'prior_40d', 'already_exploded', 'do_not_chase'] | 32 fails: ['sub_stage=S5', 'eps', 'A_sim', 'prior_20d', 'exploded', 'compression']
- pullback=False confirmation=False monitoring=False

### EFIC
- legacy=Supply_Absorption → **3A** (Early_Absorption)
- 31 fails: ['eps', 'A_similarity'] | 32 fails: ['sub_stage=3A', 'eps', 'volume']
- pullback=False confirmation=False monitoring=True

### EGAS
- legacy=Supply_Absorption → **3A** (Early_Absorption)
- 31 fails: ['A_similarity', 'prior_40d', 'already_exploded', 'recent_stage_6_7'] | 32 fails: ['sub_stage=3A', 'exploded', 'volume', 'compression']
- pullback=False confirmation=False monitoring=True


## Answers

1. **هل المشكلة threshold أم stage scoring؟** — كلاهما — A threshold 85+ يقتل العينة؛ sub-stage 4X كان يمر كـ Stage 4. أقوى sub-stage: 4X
2. **هل A_similarity 85+ منطقي أم مبالغ؟** — مبالغ للدخول — أفضل باند تشخيصي: 80-85 (min=80.0). باند >90 غالباً عينة صغيرة
3. **أي sub-stage أقوى: 3B أم 4A أم 4B؟** — 4X: OOS PF=1.39, 3B: OOS PF=1.14, 4A: OOS PF=1.09
4. **هل same-day سبب الفشل؟** — same-day OOS PF=0.77 median=-1.277%
5. **هل pullback/follow-through يحسن؟** — pullback PF=0.81 (dom=19.2%) | confirmation PF=16.05 (dom=92.8% — invalid outlier) vs same-day 0.77
6. **هل stop -8% مناسب لـ EGX؟** — stop_8 PF=0.97 hit=15.2% | post-stop MFE≥5%: 56.0% — stop_too_tight_moves_later
7. **هل LRE trade gate مستقل؟** — لا حتى الآن — PF OOS rebuilt < 1.3 أو median سالب
8. **monitoring-only + MDE confirmation؟** — نعم — RESEARCH_EDGE_MONITOR_ONLY أو TIMING_DEPENDENT؛ LRE يرصد التحول، MDE يؤكد hidden repricing

```text
Shadow only. client_path_allowed=False.
```