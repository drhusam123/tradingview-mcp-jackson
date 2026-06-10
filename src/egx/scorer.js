/**
 * EGX Setup Score Engine
 * ========================
 * يطبّق فلاتر TRADING_LESSONS.md تلقائياً على كل سهم
 * ويعطيه نقاط من 100 بناءً على جودة الإعداد
 *
 * المالك: Dr. Husam | آخر تحديث: 3 مايو 2026
 */

import * as ss from 'simple-statistics';
import { ATR } from 'technicalindicators';

// ─── أنواع الإعدادات وأولوياتها (من TRADING_LESSONS.md) ────────────────────
export const SETUP_TYPES = {
  // ── مكتشف إحصائياً: WR=69% على 75K شمعة EGX (مايو 2026) ──────────────
  RSI_OBV_COMBO:              { id: 'rsi_obv_combo',             priority: 0, baseScore: 80, label: 'RSI+OBV Combo 🔥' },
  INSTITUTIONAL_RETEST:       { id: 'institutional_retest',      priority: 1, baseScore: 75, label: 'Institutional Retest 🏆' },
  POST_BREAKOUT_CONSOLIDATION:{ id: 'post_breakout_consolidation',priority: 2, baseScore: 65, label: 'Post-Breakout Consolidation ✅' },
  POWER_BREAKOUT:             { id: 'power_breakout',            priority: 3, baseScore: 55, label: 'Power Breakout ⚡' },
  VOLUME_ACCUMULATION:        { id: 'volume_accumulation',       priority: 4, baseScore: 50, label: 'Volume Accumulation 📦' },
  NEAR_ATH_BREAKOUT:          { id: 'near_ath_breakout',         priority: 5, baseScore: 40, label: 'Near ATH Breakout ⚠️' },
  TREND_CONTINUATION:         { id: 'trend_continuation',        priority: 6, baseScore: 45, label: 'Trend Continuation 📈' },
  // ── Mean Reversion — مكتشف إحصائياً: EGX سوق mean-reversion ──────────
  MEAN_REVERSION_OVERSOLD:    { id: 'mean_reversion_oversold',   priority: 2, baseScore: 60, label: 'Mean Reversion Oversold 🔄' },
  UNKNOWN:                    { id: 'unknown',                   priority: 99, baseScore: 20, label: 'Unknown Setup' },
};

// ─── حدود الفلاتر الإلزامية ──────────────────────────────────────────────
export const FILTERS = {
  ATH_MIN_VOLUME_RATIO:     2.5,   // Near ATH يحتاج ≥ 2.5x حجم
  BREAKOUT_VOLUME_RATIO:    2.0,   // Breakout يحتاج ≥ 2.0x
  MIN_VOLUME_RATIO:         1.5,   // الحد الأدنى للدخول
  VOLUME_COLLAPSE_THRESHOLD: 0.4,  // إذا حجم اليوم < 40% من يوم الـ breakout = لا تدخل
  ENTRY_OVERSHOOT_PCT:      0.005, // 0.5% فوق منطقة الدخول = أعد الحساب
  MIN_RR:                   1.2,   // أقل نسبة R:R مقبولة
  MIN_RR_PREFERRED:         1.5,   // R:R مفضّل
  CANDLE_TOP_THIRD:         0.60,  // الإغلاق في الـ 60% العلوية = إيجابي
  ATH_PROXIMITY_PCT:        0.05,  // 5% من ATH = "Near ATH"
  ATH_MIN_LOOKBACK:         300,   // isNearATH يحتاج 300 شمعة لتجنب إشارات كاذبة
  VOLUME_LOOKBACK:          20,    // عدد الشمعات لحساب متوسط الحجم
};

// ─── القطاعات المصرفية والدفاعية الحقيقية في EGX ──────────────────────────
const DEFENSIVE_SECTORS_EGX = ['banking', 'telecom', 'utilities', 'financial_services'];
const DEFENSIVE_SYMBOLS_EGX  = ['COMI','HDBK','CIEB','ETEL','EGTS','QNBA'];

/**
 * الدالة الرئيسية: تقييم سهم وإعطاءه نقاط
 * @param {Object} stockData - بيانات السهم من TradingView scan
 * @returns {Object} نتيجة التقييم الكاملة
 */
export function scoreSetup(stockData) {
  // all_bars: تاريخ كامل (اختياري) — يُحسّن دقة isNearATH وATR
  // indicators: نتيجة calculateIndicators() أو quickScan() — يُحسّن الـ classifier
  const { symbol, ohlcv, quote, last_5_bars, all_bars, indicators } = stockData;

  // ─── 1. استخراج البيانات الأساسية ──────────────────────────────────────
  const bars = last_5_bars || [];
  if (bars.length < 3) {
    return buildResult(symbol, 0, SETUP_TYPES.UNKNOWN, [], ['بيانات غير كافية (< 3 شمعات)'], {});
  }

  const today     = bars[bars.length - 1];
  const yesterday = bars[bars.length - 2];
  const prevBars  = bars.slice(0, -1);

  const currentClose  = quote?.close ?? today.close;
  const currentHigh   = quote?.high  ?? today.high;
  const currentLow    = quote?.low   ?? today.low;
  const currentOpen   = quote?.open  ?? today.open;
  const currentVolume = quote?.volume ?? today.volume;

  // ─── 2. حساب الحجم ─────────────────────────────────────────────────────
  const prevVolumes   = prevBars.map(b => b.volume).filter(v => v > 0);
  const avgVolume     = prevVolumes.length > 0 ? ss.mean(prevVolumes) : 1;
  const maxPrevVolume = Math.max(...prevVolumes);
  const maxVolIdx     = prevVolumes.indexOf(maxPrevVolume);

  // baseline avg بدون شمعة الـ breakout — أدق للتقييم
  const baseVols    = prevVolumes.filter((_, i) => i !== maxVolIdx);
  const baselineAvg = baseVols.length > 0 ? ss.mean(baseVols) : avgVolume;

  const volumeRatio    = currentVolume / baselineAvg;
  const volumeCollapse = currentVolume / maxPrevVolume;

  // ─── 3. حساب مؤشرات السعر ──────────────────────────────────────────────
  const candleRange   = today.high - today.low;
  const closePosition = candleRange > 0 ? (currentClose - today.low) / candleRange : 0.5;
  const priceChange   = yesterday.close > 0 ? (currentClose - yesterday.close) / yesterday.close : 0;

  // ATH: لا نُصنّف Near ATH إلا مع 300 شمعة كما في TRADING_LESSONS.md.
  const athBars       = all_bars?.length >= FILTERS.ATH_MIN_LOOKBACK ? all_bars : [];
  const hasAthLookback= athBars.length >= FILTERS.ATH_MIN_LOOKBACK;
  const periodHigh    = hasAthLookback ? Math.max(...athBars.map(b => b.high)) : null;
  const athProximity  = periodHigh ? (periodHigh - currentClose) / periodHigh : null;
  const isNearATH     = athProximity != null && athProximity <= FILTERS.ATH_PROXIMITY_PCT;

  // ─── 4. ATR (14 شمعة إذا متاحة، وإلا 4 شمعات) — للعرض فقط ────────────
  let atrValue = null;
  try {
    const atrBars  = all_bars?.length >= 15 ? all_bars.slice(-15) : bars;
    const atrPeriod = Math.min(14, atrBars.length - 1);
    if (atrPeriod >= 3) {
      const atrResult = ATR.calculate({
        high:   atrBars.map(b => b.high),
        low:    atrBars.map(b => b.low),
        close:  atrBars.map(b => b.close),
        period: atrPeriod,
      });
      atrValue = atrResult.length > 0 ? atrResult[atrResult.length - 1] : null;
    }
  } catch { /* تجاهل */ }

  // ─── 5. تصنيف النمط ────────────────────────────────────────────────────
  // استخراج RSI و OBV من indicators إذا مُمرَّرة (تُحسّن الكشف عن RSI_OBV_COMBO)
  const passedRsi = indicators?.rsi ?? null;
  const passedObv = indicators?.obvDivergence ?? null;

  const setupType = classifySetup({
    volumeRatio, volumeCollapse, closePosition, priceChange,
    isNearATH, bars, currentClose, currentVolume, avgVolume,
    rsiVal: passedRsi, obvDivergence: passedObv,
  });

  // ─── 6. تطبيق الفلاتر الإلزامية (REJECT) ──────────────────────────────
  const rejections = [];
  const warnings   = [];
  const bonuses    = [];

  if (!hasAthLookback) {
    warnings.push(`⚠️ لم يتم تقييم Near ATH: يحتاج ${FILTERS.ATH_MIN_LOOKBACK} شمعة`);
  }

  // فلتر #1: Near ATH بدون حجم كافٍ → رفض
  // ملاحظة: لا يُطبَّق على Institutional Retest أو Post-Breakout — هذه الأنماط طبيعي تكون قريبة من أعلى مستوى breakout
  const isBreakoutPattern = [
    SETUP_TYPES.INSTITUTIONAL_RETEST.id,
    SETUP_TYPES.POST_BREAKOUT_CONSOLIDATION.id,
    SETUP_TYPES.POWER_BREAKOUT.id,
  ].includes(setupType.id);

  if (isNearATH && !isBreakoutPattern && volumeRatio < FILTERS.ATH_MIN_VOLUME_RATIO) {
    rejections.push(
      `❌ Near ATH (${(athProximity*100).toFixed(1)}% من القمة) لكن الحجم ${volumeRatio.toFixed(2)}x — يحتاج ≥ ${FILTERS.ATH_MIN_VOLUME_RATIO}x`
    );
  }

  // فلتر #2: انهيار الحجم بعد breakout
  if (volumeCollapse < FILTERS.VOLUME_COLLAPSE_THRESHOLD && maxPrevVolume > avgVolume * 2) {
    rejections.push(
      `❌ حجم اليوم ${(volumeCollapse*100).toFixed(0)}% من أعلى حجم سابق — انهيار حجمي (< ${FILTERS.VOLUME_COLLAPSE_THRESHOLD*100}%)`
    );
  }

  // فلتر #3: حجم دون الحد الأدنى
  if (volumeRatio < FILTERS.MIN_VOLUME_RATIO && setupType.id !== SETUP_TYPES.POST_BREAKOUT_CONSOLIDATION.id) {
    warnings.push(`⚠️ حجم منخفض: ${volumeRatio.toFixed(2)}x (الحد الأدنى ${FILTERS.MIN_VOLUME_RATIO}x)`);
  }

  // ─── 7. حساب النقاط ────────────────────────────────────────────────────
  let score = setupType.baseScore;

  // ══ مكافآت الإشارات المكتشفة إحصائياً (75K شمعة EGX) ════════════════════
  // RSI+OBV Combo: WR=69% — أعلى مكافأة ممكنة
  if (setupType.id === SETUP_TYPES.RSI_OBV_COMBO.id) {
    score += 15; bonuses.push('+15: 🔥 RSI+OBV Combo مكتشف إحصائياً');
  }
  // OBV Bullish Divergence وحده: WR=56%
  if (passedObv === 'bullish' && setupType.id !== SETUP_TYPES.RSI_OBV_COMBO.id) {
    score += 8; bonuses.push('+8: ↑OBV Bullish Divergence');
  }
  // RSI ≤ 30 oversold: WR=52%
  if (passedRsi != null && passedRsi <= 30) {
    score += 6; bonuses.push(`+6: RSI=${passedRsi.toFixed(1)} Oversold`);
  } else if (passedRsi != null && passedRsi <= 35) {
    score += 3; bonuses.push(`+3: RSI=${passedRsi.toFixed(1)} منطقة oversold`);
  }
  // ADX نطاق 20-30 أفضل من ≥30 في EGX (mean-reversion characteristic)
  if (indicators?.adx != null) {
    const adxVal = indicators.adx.adx ?? indicators.adx;
    if (adxVal >= 20 && adxVal < 30) {
      score += 4; bonuses.push(`+4: ADX=${adxVal.toFixed(1)} النطاق المثالي (20-30)`);
    } else if (adxVal >= 30) {
      score -= 2; bonuses.push(`-2: ADX=${adxVal.toFixed(1)} مرتفع جداً (EGX: mean-reversion)`);
    }
  }
  // Mean Reversion setup: مكافأة إضافية إذا تحت EMA200 (WR=54.8% في EGX!)
  if (setupType.id === SETUP_TYPES.MEAN_REVERSION_OVERSOLD.id) {
    score += 8; bonuses.push('+8: Mean Reversion Oversold (EGX سوق mean-reversion)');
  }
  // ════════════════════════════════════════════════════════════════════════

  // نقاط الحجم
  if (volumeRatio >= 3.0) { score += 18; bonuses.push(`+18: حجم مرتفع جداً ${volumeRatio.toFixed(2)}x — يحتاج تأكيد متابعة`); }
  else if (volumeRatio >= 2.5) { score += 22; bonuses.push(`+22: حجم في النطاق الأمثل ${volumeRatio.toFixed(2)}x`); }
  else if (volumeRatio >= 2.0) { score += 15; bonuses.push(`+15: حجم قوي ${volumeRatio.toFixed(2)}x`); }
  else if (volumeRatio >= 1.5) { score += 8;  bonuses.push(`+8: حجم مقبول ${volumeRatio.toFixed(2)}x`); }
  else                          { score -= 10; bonuses.push(`-10: حجم ضعيف ${volumeRatio.toFixed(2)}x`); }

  // نقاط موضع الإغلاق في الشمعة
  // ملاحظة: للـ mean_reversion_oversold إغلاق في الأسفل هو المطلوب (عكس باقي الإعدادات)
  if (setupType.id === SETUP_TYPES.MEAN_REVERSION_OVERSOLD.id ||
      setupType.id === SETUP_TYPES.RSI_OBV_COMBO.id) {
    // في mean reversion: الإغلاق في الثلث السفلي يعني الـ signal لم يُطلق بعد
    if (closePosition <= 0.40) { score += 5; bonuses.push('+5: إغلاق في القاع — mean reversion opportunity'); }
  } else {
    if (closePosition >= 0.75) { score += 10; bonuses.push('+10: إغلاق في الربع العلوي'); }
    else if (closePosition >= FILTERS.CANDLE_TOP_THIRD) { score += 5; bonuses.push('+5: إغلاق فوق المنتصف'); }
    else if (closePosition < 0.35) { bonuses.push('0: إغلاق في الثلث السفلي — القرار للـ SL الهيكلي وR:R'); }
  }

  // نقاط النمط المثالي (Institutional Retest)
  if (setupType.id === SETUP_TYPES.INSTITUTIONAL_RETEST.id) {
    score += 10; bonuses.push('+10: نمط Institutional Retest (الأعلى أولوية)');
  }

  // القطاع الدفاعي الحقيقي
  if (DEFENSIVE_SYMBOLS_EGX.includes(symbol)) {
    score += 5; bonuses.push(`+5: قطاع دفاعي حقيقي في EGX (${symbol})`);
  }

  // الإغلاق أعلى من أمس بنسبة جيدة
  if (priceChange >= 0.05) { score += 8; bonuses.push(`+8: تغيّر يومي قوي +${(priceChange*100).toFixed(1)}%`); }
  else if (priceChange < -0.02) { score -= 5; bonuses.push(`-5: تغيّر سلبي ${(priceChange*100).toFixed(1)}%`); }

  // خصم على rejections
  score -= rejections.length * 30;
  score = Math.max(0, Math.min(100, score));

  // ─── 8. حساب مناطق الدخول والأهداف (بناءً على هيكل السعر) ──────────────
  const levels = calculateLevels({ bars, currentClose, currentLow, currentHigh, setupType });

  // ─── 9. تحديد Best Safe / Best Aggressive ──────────────────────────────
  const isBestSafe       = score >= 75 && rejections.length === 0 &&
                           [SETUP_TYPES.INSTITUTIONAL_RETEST.id, SETUP_TYPES.POST_BREAKOUT_CONSOLIDATION.id]
                           .includes(setupType.id);
  // Bug fix: لا يجوز أن يكون Best Aggressive وهو مرفوض
  const isBestAggressive = rejections.length === 0 &&
                           volumeRatio >= 2.5 && closePosition >= 0.65 && priceChange >= 0.05;

  // ─── 10. درجة الثقة (1-10) ─────────────────────────────────────────────
  const confidence = Math.round((score / 10) * 10) / 10;

  return buildResult(symbol, score, setupType, bonuses, rejections, {
    closePrice:     +currentClose.toFixed(3),   // السعر الحقيقي (يُحفظ في DB)
    volumeRatio:    +volumeRatio.toFixed(2),
    avgVolume:      Math.round(avgVolume),
    volumeCollapse: +volumeCollapse.toFixed(2),
    closePosition:  +closePosition.toFixed(2),
    isNearATH,
    athProximityPct: athProximity == null ? null : +(athProximity * 100).toFixed(1),
    priceChangePct:  +(priceChange * 100).toFixed(2),
    atr: atrValue ? +atrValue.toFixed(3) : null,
    levels,
    warnings,
    isBestSafe,
    isBestAggressive,
    confidence: Math.min(10, Math.max(1, Math.round(confidence))),
  });
}

// ─── تصنيف النمط تلقائياً ────────────────────────────────────────────────
// يأخذ الآن rsiVal و obvDivergence إذا كانا متاحَين (من indicators.js)
function classifySetup({ volumeRatio, volumeCollapse, closePosition, priceChange, isNearATH, bars, currentClose, currentVolume, avgVolume, rsiVal, obvDivergence }) {
  const prevBars   = bars.slice(0, -1);
  const prevVols   = prevBars.map(b => b.volume).filter(v => v > 0);
  const today      = bars[bars.length - 1];

  // ── 0. RSI+OBV COMBO — مكتشف إحصائياً: WR=69% (أعلى أولوية مطلقة) ─────
  // الشرط: RSI≤35 + OBV Bullish Divergence (سعر ينزل + OBV يصعد)
  if (rsiVal != null && rsiVal <= 35 && obvDivergence === 'bullish') {
    return SETUP_TYPES.RSI_OBV_COMBO;
  }

  // ── إيجاد شمعة الـ Breakout الحقيقية ──────────────────────────────────
  const maxVol      = prevVols.length > 0 ? Math.max(...prevVols) : 0;
  const maxVolIdx   = prevVols.indexOf(maxVol);
  const isRecent    = maxVolIdx >= prevVols.length - 4; // خلال آخر 4 شمعات (كافٍ لنافذة 5 شمعات)

  // حساب المتوسط بدون شمعة الـ breakout (للمقارنة الأدق)
  const baseVols       = prevVols.filter((_, i) => i !== maxVolIdx);
  const baselineAvg    = baseVols.length > 0 ? ss.mean(baseVols) : avgVolume;
  const breakoutRatio  = baselineAvg > 0 ? maxVol / baselineAvg : 1;
  const currentVsBase  = baselineAvg > 0 ? currentVolume / baselineAvg : 1;
  const hadRealBreakout= breakoutRatio >= 2.0 && isRecent;

  // دلتا السعر في شمعة الـ breakout
  const breakoutBar    = prevBars[maxVolIdx];
  const breakoutMove   = breakoutBar ? Math.abs(breakoutBar.close - breakoutBar.open) / breakoutBar.open : 0;

  // عمق الـ retest (كم انخفض السهم من الفتح في الشمعة الحالية)
  const retestDepth    = today.open > 0 ? (today.open - today.low) / today.open : 0;

  // ── 1. Institutional Retest (أعلى أولوية) ─────────────────────────────
  // شرط: breakout حقيقي + حجم معقول (≥40% baseline أو تحرك سعري قوي) + إغلاق إيجابي
  const strongPriceMove = Math.abs(today.close - today.open) / today.open >= 0.05;
  const volumeOK        = currentVsBase >= 0.4; // ≥40% من baseline — يشمل Follow-through bars
  if (hadRealBreakout && volumeOK && closePosition >= 0.35 &&
      (retestDepth >= 0.02 || breakoutMove >= 0.05 || strongPriceMove)) {
    return SETUP_TYPES.INSTITUTIONAL_RETEST;
  }

  // ── 2. Post-Breakout Consolidation ────────────────────────────────────
  // breakout سابق + حجم طبيعي اليوم + سعر يتماسك
  if (hadRealBreakout && currentVsBase < 0.6 && Math.abs(priceChange) <= 0.04 && closePosition >= 0.3) {
    return SETUP_TYPES.POST_BREAKOUT_CONSOLIDATION;
  }

  // ── 3. Power Breakout (اليوم هو يوم الـ breakout) ─────────────────────
  if (currentVsBase >= 2.0 && closePosition >= 0.6 && priceChange >= 0.03) {
    return SETUP_TYPES.POWER_BREAKOUT;
  }
  if (volumeRatio >= 2.5 && closePosition >= 0.65 && priceChange >= 0.05) {
    return SETUP_TYPES.POWER_BREAKOUT;
  }

  // ── 4. Near ATH — فقط إذا لم يكن retest أو breakout ─────────────────
  if (isNearATH) return SETUP_TYPES.NEAR_ATH_BREAKOUT;

  // ── 5. Volume Accumulation: حجم عالٍ + سعر ثابت ──────────────────────
  if (currentVsBase >= 1.5 && Math.abs(priceChange) <= 0.015) {
    return SETUP_TYPES.VOLUME_ACCUMULATION;
  }

  // ── 6. Mean Reversion Oversold — مكتشف إحصائياً: EGX سوق mean-reversion ──
  // الشرط: RSI≤35 أو تراجع ≥5% في 3 أيام مع إغلاق في الثلث السفلي
  // (أضعف من RSI_OBV_COMBO لأنه بدون تأكيد OBV)
  if (rsiVal != null && rsiVal <= 35 && closePosition <= 0.45) {
    return SETUP_TYPES.MEAN_REVERSION_OVERSOLD;
  }
  // تراجع سعري حاد + close في الأسفل (mean reversion بدون RSI)
  const closeArr = bars.map(b => b.close);
  const change3d = closeArr.length >= 3
    ? (closeArr[closeArr.length-1] - closeArr[closeArr.length-4]) / closeArr[closeArr.length-4]
    : 0;
  if (change3d <= -0.05 && closePosition <= 0.40 && !isNearATH) {
    return SETUP_TYPES.MEAN_REVERSION_OVERSOLD;
  }

  // ── 7. Trend Continuation ─────────────────────────────────────────────
  const closes    = bars.map(b => b.close);
  const isUptrend = closes.every((c, i) => i === 0 || c >= closes[i-1] * 0.965);
  if (isUptrend && priceChange >= 0) return SETUP_TYPES.TREND_CONTINUATION;

  return SETUP_TYPES.UNKNOWN;
}

// ─── حساب مستويات الدخول والأهداف والـ SL (بناءً على هيكل السعر) ─────────
// المنطق: SL = تحت أدنى مستوى دعم هيكلي — أكثر واقعية من ATR × مضاعف
// الأهداف: R:R ثابت (2:1 لـ T1 و 3.5:1 لـ T2)
function calculateLevels({ bars, currentClose, currentLow, currentHigh, setupType }) {
  const prevBars = bars.slice(0, -1);

  // ─── منطقة الدخول: ±0.3% حول سعر الإغلاق ────────────────────────────
  const entryLow  = +(currentClose * 0.997).toFixed(3);
  const entryHigh = +(currentClose * 1.003).toFixed(3);

  // ─── SL: بناءً على نوع الإعداد والهيكل السعري ────────────────────────
  let rawSL;

  if (setupType.id === SETUP_TYPES.INSTITUTIONAL_RETEST.id) {
    // SL تحت low شمعة الـ retest (الشمعة الأخيرة قبل يوم الدخول)
    const retestBar = prevBars[prevBars.length - 1];
    rawSL = retestBar ? retestBar.low * 0.993 : currentLow * 0.990;

  } else if (setupType.id === SETUP_TYPES.POWER_BREAKOUT.id) {
    // SL تحت low يوم الـ Breakout نفسه (هامش 0.7%)
    rawSL = currentLow * 0.993;

  } else if (setupType.id === SETUP_TYPES.POST_BREAKOUT_CONSOLIDATION.id) {
    // SL تحت أدنى low في نافذة التماسك (آخر 3 شمعات)
    const consolidationLows = prevBars.slice(-3).map(b => b.low);
    rawSL = (Math.min(...consolidationLows) || currentLow) * 0.991;

  } else {
    // باقي الإعدادات: SL تحت أدنى low في نافذة الـ 5 شمعات
    const windowLow = Math.min(...bars.map(b => b.low));
    rawSL = windowLow * 0.991;
  }

  // ─── ضمان حدود المخاطرة: 1.5% (أدنى) إلى 10% (أقصى) ──────────────
  const maxRisk = currentClose * 0.10;  // لا تتجاوز 10% مخاطرة
  const minRisk = currentClose * 0.015; // لا تقل عن 1.5% مخاطرة
  const sl = +(Math.max(
    currentClose - maxRisk,
    Math.min(currentClose - minRisk, rawSL)
  )).toFixed(3);

  // ─── الأهداف: R:R ثابت على أساس حجم المخاطرة الفعلي ─────────────────
  const risk = currentClose - sl;
  const t1   = +(currentClose + risk * 2.0).toFixed(3);   // R:R = 2:1
  const t2   = +(currentClose + risk * 3.5).toFixed(3);   // R:R = 3.5:1
  const rr1  = 2.0;
  const rr2  = 3.5;

  return { entryLow, entryHigh, sl, t1, t2, rr1, rr2 };
}

// ─── بناء نتيجة التقييم ───────────────────────────────────────────────────
function buildResult(symbol, score, setupType, bonuses, rejections, meta) {
  const isRejected = rejections.length > 0;
  return {
    symbol,
    score: isRejected ? Math.min(score, 25) : score,
    grade: gradeFromScore(isRejected ? 0 : score),
    setupType: setupType.label,
    setupId:   setupType.id,
    priority:  setupType.priority,
    rejected:  isRejected,
    rejections,
    bonuses,
    ...meta,
    timestamp: new Date().toISOString(),
  };
}

function gradeFromScore(score) {
  if (score >= 85) return 'A+ 🌟';
  if (score >= 75) return 'A  ✅';
  if (score >= 65) return 'B+ 👍';
  if (score >= 55) return 'B  📊';
  if (score >= 45) return 'C  ⚠️';
  return 'D  ❌';
}

/**
 * تصنيف وترتيب قائمة أسهم كاملة
 * @param {Array} stockList - مصفوفة من { symbol, ohlcv, quote, last_5_bars }
 * @returns {Array} قائمة مرتّبة حسب النقاط
 */
export function rankStocks(stockList, { includeRejected = false } = {}) {
  const scored = stockList.map(s => scoreSetup(s));
  // Bug fix: الأسهم المرفوضة لا تُدرج في القائمة الرئيسية إلا بطلب صريح
  return scored
    .filter(r => includeRejected ? true : !r.rejected)
    .sort((a, b) => b.score - a.score);
}

export default { scoreSetup, rankStocks, SETUP_TYPES, FILTERS };
