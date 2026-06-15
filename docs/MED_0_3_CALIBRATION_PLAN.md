# MED-0.3 — تدقيق الرأي + خطة المعايرة المثلى (EGX Shadow)

> **النطاق:** بحث فقط — `MED_SHADOW=1`, `client_path_allowed=0`, لا Telegram، لا opp_v2، لا prioritizer.  
> **المرجع:** `scripts/python/med_0_3_audit.py` → `data/med_0_3_audit_last.json`

---

## 1. الحكم على تشخيصك (مؤكَّد / معدَّل / مرفوض)

| ادّعاء | الحكم | الدليل من النظام (2026-06-11) |
|--------|--------|-------------------------------|
| **Scale mismatch** — `stored_energy>=0.2` ميتة | ✅ **مؤكَّد** | MED: max≈0.015، 0 سهم ≥0.2. LRE نفس اليوم: avg≈64، max=100 (مقياس مختلف تماماً) |
| **مدخلات ثابتة** regime/sector/breadth | ✅ **مؤكَّد** | `med_0_1_math_features.py` سطور 236–239 = 0.5/0.75 ثابت |
| **Failure Warning يقتل نصف السوق** | ⚠️ **جزئي** | 111/202 (55%) — لكن **108/111 بسبب `do_not_chase`** (r20>15% أو r40>25% أو crowding≥0.75). KNN `failure_similarity≥0.6` = **8 فقط** |
| **KNN failure يعمّم بسرعة** | ⚠️ **ثانوي** | avg failure_similarity≈0.32؛ ليس المحرّك الرئيسي للـ bucket اليوم |
| **HIGH_CONVICTION=0 لأن med_score<80** | ❌ **مرفوض** | COMI ms=80.2 لكن **sample_quality max=0.31** و 0 سهم sq≥0.5 — البوابة الحقيقية هي **sq + condition_key ضيّق** |
| **MED_LRE بلا قيمة** | ❌ **مرفوض** | OOS n=1,105: median +2.15% vs LRE +1.72% (+0.43pp)، stop8 20.9% vs 26.1% |
| **Analogue منفصل** | ✅ **مؤكَّد** | تداخل top20 = 3 أسهم (15%): ABUK, HELI, ADIB — ICID/LUTS 46% p_tail خارج top MED |
| **لا نلمس feature_store** | ✅ **متفق** | 1.7M صف، تكرار مع OHLCV → leakage risk؛ لدينا بدائل أنظف |

**الخلاصة:** فكرتك صحيحة في **المقياس والعتبات الثابتة والمدخلات الوهمية**. لكن ترتيب الأولويات يتغيّر: **A5 (فصل Chase)** و **إصلاح sample_quality/condition keys** قبل ضبط KNN failure.

---

## 2. ما يعمل فعلاً اليوم (لا نكسره)

```
MED_LRE filter: stored_energy>=0.2 OR hidden_energy_flag
                → 100% عبر hidden_energy (1,228 OOS)
                → +0.43pp median، stop8 −5.2pp vs LRE_only
```

- **hidden_energy** (`vol_z≥2`, `|r5|≤5%`, `extended≤0.3`) هو المحرّك الحقيقي لـ MED_LRE — ليس Stored Energy v2 الخام.
- **Replay + forward OOS ledger** متسقان (n=1,105).
- **Acceptance MED-0/1 + MED-2 + integration** PASS — أي تغيير يمر عبر `egx:med:integration-test`.
- **Live forward** من 2026-06-12: 0 صفقات مغلقة — التخرج يحتاج 40 صفقة live.

---

## 3. مصادر البيانات الحقيقية (جاهزة للربط)

| الحقل | المصدر | التغطية | ملاحظة |
|-------|--------|---------|--------|
| `regime_state` / `regime_fit` | `markov_regime_daily` (+ parquet) | 305 يوم (2025-03-02 → 2026-06-11) | causal join بـ `date≤trade_date` |
| `breadth_state` | `market_breadth_enhanced` parquet | 324 يوم | ad_ratio، advances/declines |
| `sector_strength` | `sector_rotation_daily` | 23,501 صف (2023+) | rotation_score + sector للسهم |
| `failure_prior` | `failure_reconstruction` | 84,796 حدث، 255 سهم | تكميل KNN لا استبدال كامل |
| `closing_pressure` | `closing_pressure_daily` | 77k صف | اختياري لـ PathQ |
| **لا** | `feature_store` | — | مؤجّل MED-0.4+ |

**قاعدة anti-leakage:** كل join بـ `date <= trade_date` فقط؛ لا forward columns من parquet.

---

## 4. خطة التنفيذ المثلى (مرتّبة بالعائد / المخاطر)

### Sprint 0 — Baseline محفوظ (يوم 0)
- [ ] `npm run egx:med:audit` → حفظ `med_0_3_audit_last.json`
- [ ] تثبيت baseline metrics: buckets، MED_LRE lift، analogue overlap، integration PASS

### Sprint 1 — إصلاح الـ Buckets (أعلى أثر، أقل مخاطرة) — **أسبوع 1**

#### 1A. فصل ChaseRisk عن Failure/Crowding (**A5 — أولوية 1**)
```text
ChaseRisk     = f(r_20>0.15, r_40>0.25, upper_wick)   → bucket: DO_NOT_CHASE (لا FAILURE)
CrowdingRisk  = crowding_penalty >= p75_hist
FailureRisk   = failure_similarity >= p80_hist
MED_FAILURE_WARNING ← FailureRisk OR CrowdingRisk (ليس Chase وحده)
```
**هدف:** Failure Warning **25–35%** (اليوم 55% بسبب دمج chase).

#### 1B. Stored Energy معايرة EGX (**A1**)
```text
SE_raw  = C × V × A × (1 - ExtendedPenalty)
SE_rank = rank_cross_section(SE_raw, universe_day)
med_ok  = SE_rank >= p70(SE_rank_history) OR hidden_energy_flag
```
- إزالة العتبة المطلقة `0.2` من replay/forward/analogue filters.
- **لا** نعيد استخدام مقياس LRE 0–100 داخل MED — معادلات مختلفة.

#### 1C. MED_score expanding history (**A3**)
```text
MED_core  = f(P_cond, E[ret], SE_rank, Abs_rank, Dist_rank, PathQ, Liq)  # كلها ranks
MED_score = 100 × percentile(MED_core, expanding_500d_cross_section)
```
- العتبات اليومية (p85, p70) من **تاريخ MED_core** لا من 202 سهم يوم واحد.

**اختبارات Sprint 1:**
1. Failure Warning → 25–35%
2. HIGH_CONVICTION → 3–8 أسهم/يوم (بعد 1D)
3. `egx:med:integration-test` PASS
4. MED_LRE median ≥ baseline −0.1pp (لا نكسّر الـ +0.43pp)

---

### Sprint 2 — بيانات النظام الحقيقية (**B**)

ملف جديد: `med_0_3_regime_context.py` (قراءة فقط من SQLite/parquet)

```python
regime_fit    = f(markov.state_base, markov.roll20_percentile, breadth.ad_ratio)
sector_strength = sector_rotation_score(symbol.sector, date)
breadth_state = normalize(breadth.ad_ratio, breadth.pct_above_20d)
```

- استبدال الثوابت في `compute_math_fields` / `MED_adjusted`.
- fallback: إذا لا بيانات لتاريخ < 2025-03 → 0.5 neutral (مو 0.75 متفائل).

**اختبار:** توزيع `regime_fit` يتغيّر عبر 305 يوم؛ لا ثابت 0.75 في أي صف.

---

### Sprint 3 — sample_quality + Conditional Edges (**C** — سبب HIGH_CONVICTION=0)

#### 3A. تقليص condition keys (7 → 4 buckets)
```text
LRE_bucket | MDE_gate | MED_math_bucket | Market_regime
```
- يزيد `n` per edge → يرفع sample_quality فوق 0.5.

#### 3B. Bayesian shrinkage على hit_rate
```text
p_hat = (hits + α) / (n + α + β)    # α=2, β=8 → prior ~20%
```
- لا edge يُعرض إلا `n≥30` و `time_dispersion≥0.4`.

#### 3C. بوابة HIGH_CONVICTION ديناميكية
```text
HIGH_CONVICTION if score≥p85_hist AND P_tail≥p70_hist AND Risk≤p40_hist AND sq≥p60_hist
```
- إزالة `sq>=0.50` المطلق الذي يمنع **كل** الأسهم اليوم.

---

### Sprint 4 — دمج Analogue (**D**)

```text
P_tail = 0.6 × P_cond + 0.4 × P_analogue
MED_raw_v2 = 0.20×P_tail + 0.15×E[ret]⁺ + 0.15×SE_rank + 0.10×Abs + 0.10×Dist + 0.10×PathQ + 0.05×Liq
```

- KNN analogue: K=20 sector-aware (من 50).
- **هدف:** تداخل top20 analogue ∩ MED ≥ **30%**.

---

### Sprint 5 — Failure KNN refinement (**A4** — بعد فصل Chase)

- K=20 + sector neighbors
- threshold ديناميكي `p80(failure_similarity_history)` بدل 0.35/0.60 ثابت
- optional: `failure_prior` من `failure_reconstruction` كـ prior في shrinkage

---

## 5. ما لا نفعله (متفق — Phase E)

| لا | السبب |
|----|--------|
| مسح 281 جدول | noise + leakage |
| رفع MED للعميل | live proof = 0 صفقات |
| PF@100 كمعيار وحيد | 1,105 صف متداخل — مضلّل |
| feature_store الآن | تكرار OHLCV |
| horizons/thresholds جديدة | 5×4 كافية |

---

## 6. المعادلة المستهدفة v2 (بعد Sprint 1–4)

```text
X_{i,t}     = state vector (معاير + causal + regime حقيقي)
P_tail      = 0.6 × P_cond_shrunk + 0.4 × P_analogue
Risk        = max(FailureRisk, CrowdingRisk)     # ChaseRisk منفصل
Quality     = SampleQuality × RegimeFit × LiquidityFitness

MED_core    = P_tail × E[return|bucket] × (1 - Risk)
MED_score   = 100 × rank_expanding(MED_core, 500d)

Bucket:
  HIGH_CONVICTION  if score≥p85_hist AND P_tail≥p70_hist AND Risk≤p40_hist AND Quality≥p50_hist
  DO_NOT_CHASE     if ChaseRisk (لا يُصنَّف FAILURE)
  FAILURE_WARNING  if Risk≥p75_hist
  else MONITOR / INSUFFICIENT
```

**كل العتبات percentiles تاريخية** — لا 0.2، 0.35، 80 سحرية.

---

## 7. معايير النجاح (4 اختبارات فقط)

| # | المقياس | Baseline | هدف MED-0.3 |
|---|---------|----------|-------------|
| 1 | Failure Warning rate | 55% | **25–35%** |
| 2 | HIGH_CONVICTION / يوم | 0 | **3–8** |
| 3 | MED_LRE vs LRE (40 live closed) | +0.43pp OOS | **≥ baseline** live |
| 4 | Analogue ∩ MED top20 | 15% | **≥30%** |

+ دائماً: `egx:med:integration-test` PASS، invariants unchanged.

---

## 8. هيكل الملفات المقترح

```
scripts/python/
  med_0_3_audit.py              ✅ (هذا السبرنت)
  med_0_3_calibration.py        Sprint 1 — SE rank + MED expanding + buckets
  med_0_3_regime_context.py     Sprint 2 — parquet/sqlite loaders
  med_0_3_edges.py              Sprint 3 — bucket collapse + shrinkage
  med_0_3_acceptance.py         gates جديدة
  med_0_3_daily_chain.py        يربط السلسلة shadow

npm:
  egx:med:audit
  egx:med:calibrate      (بعد Sprint 1)
  egx:med:phase3:verify
```

---

## 9. ترتيب البدء الموصى به

```
Sprint 1A (فصل Chase)  →  Sprint 1B (SE rank)  →  Sprint 1C (expanding score)
        ↓
Sprint 2 (regime حقيقي)
        ↓
Sprint 3 (edges + sq)   ← يفتح HIGH_CONVICTION
        ↓
Sprint 4 (analogue merge)
        ↓
Sprint 5 (KNN failure refine)
```

**أول commit:** `med_0_3_calibration.py` — **1A + 1B + 1C** في diff واحد صغير، ثم regime في commit ثانٍ.

---

## 10. حماية الجلسات المستقبلية

1. **لا تغيير في client path** — كل سكربت يستدعي `assert_med_invariants()` من `med_common.py`.
2. **Audit JSON يومي** — مقارنة قبل/بعد كل sprint.
3. **Forward ledger منفصل** — لا نخلط OOS backfill مع live من 2026-06-12.
4. **لا نعيد كتابة LRE** — MED يقرأ `lre_daily_scores` كما هو؛ فقط نصلح فلتر `med_ok`.
5. **TRADING_LESSONS.md** — بعد كل sprint نُحدّث درس واحد إن تغيّر سلوك bucket مثبت.

---

*آخر تدقيق: شغّل `npm run egx:med:audit` بعد أي تغيير في المعادلات.*
