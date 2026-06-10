/**
 * EGX Technical Indicators Engine
 * =================================
 * حساب كل المؤشرات التقنية اللازمة للـ scoring و analysis و discovery
 * يستخدم مكتبة technicalindicators (npm) + simple-statistics
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import TI from 'technicalindicators';
import * as ss from 'simple-statistics';

/**
 * حساب كل المؤشرات التقنية لسهم معين
 * @param {Array<{time,open,high,low,close,volume}>} bars - شمعات مرتبة تصاعدياً (الأقدم أولاً)
 * @param {Object} opts - خيارات (اختيارية)
 * @returns {Object} كائن يحتوي على آخر قيمة لكل مؤشر + مصفوفات كاملة
 */
export function calculateIndicators(bars, opts = {}) {
  if (!bars || bars.length < 10) return null;

  const closes  = bars.map(b => b.close);
  const highs   = bars.map(b => b.high);
  const lows    = bars.map(b => b.low);
  const volumes = bars.map(b => b.volume ?? 0);
  const opens   = bars.map(b => b.open);

  // helper: آخر عنصر في مصفوفة
  const last = arr => arr && arr.length > 0 ? arr[arr.length - 1] : null;

  // ─── EMA Series ──────────────────────────────────────────────────────────
  const ema10  = bars.length >= 10  ? TI.EMA.calculate({ period: 10,  values: closes }) : [];
  const ema20  = bars.length >= 20  ? TI.EMA.calculate({ period: 20,  values: closes }) : [];
  const ema50  = bars.length >= 50  ? TI.EMA.calculate({ period: 50,  values: closes }) : [];
  const ema200 = bars.length >= 200 ? TI.EMA.calculate({ period: 200, values: closes }) : [];

  // ─── RSI ─────────────────────────────────────────────────────────────────
  const rsiArr = bars.length >= 15 ? TI.RSI.calculate({ period: 14, values: closes }) : [];

  // ─── MACD ────────────────────────────────────────────────────────────────
  let macdArr = [];
  if (bars.length >= 35) {
    macdArr = TI.MACD.calculate({ fastPeriod: 12, slowPeriod: 26, signalPeriod: 9, values: closes, SimpleMAOscillator: false, SimpleMASignal: false });
  }
  const macdLast = last(macdArr);

  // ─── Bollinger Bands ────────────────────────────────────────────────────
  let bbArr = [];
  if (bars.length >= 20) {
    bbArr = TI.BollingerBands.calculate({ period: 20, stdDev: 2, values: closes });
  }
  const bbLast = last(bbArr);

  // ─── ATR ────────────────────────────────────────────────────────────────
  let atrArr = [];
  if (bars.length >= 15) {
    atrArr = TI.ATR.calculate({ period: 14, high: highs, low: lows, close: closes });
  }

  // ─── ADX ─────────────────────────────────────────────────────────────────
  let adxArr = [];
  if (bars.length >= 15) {
    try {
      adxArr = TI.ADX.calculate({ period: 14, high: highs, low: lows, close: closes });
    } catch { adxArr = []; }
  }
  const adxLast = last(adxArr); // { adx, pdi, mdi }

  // ─── Stochastic ─────────────────────────────────────────────────────────
  let stochArr = [];
  if (bars.length >= 17) {
    try {
      stochArr = TI.Stochastic.calculate({ period: 14, signalPeriod: 3, high: highs, low: lows, close: closes });
    } catch { stochArr = []; }
  }
  const stochLast = last(stochArr); // { k, d }

  // ─── OBV ─────────────────────────────────────────────────────────────────
  const obvArr = TI.OBV.calculate({ close: closes, volume: volumes });

  // ─── CCI ─────────────────────────────────────────────────────────────────
  let cciArr = [];
  if (bars.length >= 20) {
    try {
      cciArr = TI.CCI.calculate({ period: 20, high: highs, low: lows, close: closes });
    } catch { cciArr = []; }
  }

  // ─── Williams %R ────────────────────────────────────────────────────────
  let williamsArr = [];
  if (bars.length >= 14) {
    try {
      williamsArr = TI.WilliamsR.calculate({ period: 14, high: highs, low: lows, close: closes });
    } catch { williamsArr = []; }
  }

  // ─── Volume SMA (20) ─────────────────────────────────────────────────────
  const volSma20 = bars.length >= 20 ? TI.SMA.calculate({ period: 20, values: volumes }) : [];
  const avgVol20 = last(volSma20) ?? (volumes.length > 0 ? ss.mean(volumes.slice(-20)) : 0);

  // ─── Price Statistics ───────────────────────────────────────────────────
  const closes10 = closes.slice(-10);
  const priceZScore10 = closes.length >= 10
    ? (closes[closes.length - 1] - ss.mean(closes10)) / (ss.standardDeviation(closes10) || 1)
    : 0;

  // ─── OBV Divergence ─────────────────────────────────────────────────────
  // سعر يرتفع لكن OBV يهبط = هبوط محتمل
  // سعر يهبط لكن OBV يرتفع = صعود محتمل (bullish divergence)
  let obvDivergence = 'none';
  if (obvArr.length >= 10 && closes.length >= 10) {
    const n = 10;
    const priceTrend = closes[closes.length - 1] - closes[closes.length - n];
    const obvTrend   = obvArr[obvArr.length - 1] - obvArr[obvArr.length - n];
    if (priceTrend < 0 && obvTrend > 0)  obvDivergence = 'bullish'; // سعر هابط + OBV صاعد
    if (priceTrend > 0 && obvTrend < 0)  obvDivergence = 'bearish'; // سعر صاعد + OBV هابط
  }

  // ─── Trend Strength ─────────────────────────────────────────────────────
  const currentClose = closes[closes.length - 1];
  const ema20val     = last(ema20);
  const ema50val     = last(ema50);
  const ema200val    = last(ema200);

  // EMA صاعدة أم هابطة؟ (مقارنة آخر 5 قيم)
  const ema20trending = ema20.length >= 5
    ? ema20[ema20.length - 1] > ema20[ema20.length - 5] ? 'up' : 'down'
    : 'unknown';
  const ema50trending = ema50.length >= 5
    ? ema50[ema50.length - 1] > ema50[ema50.length - 5] ? 'up' : 'down'
    : 'unknown';

  // ─── Candle Analysis ────────────────────────────────────────────────────
  const lastBar = bars[bars.length - 1];
  const candleRange  = lastBar.high - lastBar.low;
  const closePos     = candleRange > 0 ? (lastBar.close - lastBar.low) / candleRange : 0.5;
  const bodyRatio    = candleRange > 0 ? Math.abs(lastBar.close - lastBar.open) / candleRange : 0;
  const isHammer     = closePos >= 0.65 && bodyRatio <= 0.35 && lastBar.close >= lastBar.open;
  const isDoji       = bodyRatio <= 0.1;
  const isBullishEngulfing = bars.length >= 2
    ? bars[bars.length - 2].close < bars[bars.length - 2].open && // شمعة سابقة حمراء
      lastBar.close > lastBar.open &&                              // شمعة اليوم خضراء
      lastBar.open < bars[bars.length - 2].close &&               // فتح أسفل من إغلاق الأمس
      lastBar.close > bars[bars.length - 2].open                  // إغلاق فوق فتح الأمس
    : false;

  // ─── Volume Surge Detection ─────────────────────────────────────────────
  const currentVol = volumes[volumes.length - 1];
  const volRatio   = avgVol20 > 0 ? currentVol / avgVol20 : 1;
  const isVolSurge = volRatio >= 2.5;

  // ─── Support/Resistance (بسيط) ──────────────────────────────────────────
  const last20Highs = highs.slice(-20);
  const last20Lows  = lows.slice(-20);
  const nearestResistance = Math.min(...last20Highs.filter(h => h > currentClose));
  const nearestSupport    = Math.max(...last20Lows.filter(l => l < currentClose));

  // ─── ATH Proximity ──────────────────────────────────────────────────────
  const periodHigh  = Math.max(...highs);
  const athProximity = periodHigh > 0 ? (periodHigh - currentClose) / periodHigh : 0;
  const isNearATH   = athProximity <= 0.05;

  // ─── Momentum ──────────────────────────────────────────────────────────
  const mom5  = closes.length >= 5  ? (currentClose - closes[closes.length - 5])  / closes[closes.length - 5]  : 0;
  const mom10 = closes.length >= 10 ? (currentClose - closes[closes.length - 10]) / closes[closes.length - 10] : 0;
  const mom20 = closes.length >= 20 ? (currentClose - closes[closes.length - 20]) / closes[closes.length - 20] : 0;

  return {
    // ── Raw arrays (for charting/analysis) ─────────────────────────────
    arrays: {
      ema10, ema20, ema50, ema200,
      rsi: rsiArr, macd: macdArr, bb: bbArr, atr: atrArr,
      adx: adxArr, stoch: stochArr, obv: obvArr, cci: cciArr,
      williams: williamsArr, volSma20,
    },

    // ── Latest scalar values ─────────────────────────────────────────────
    rsi:    last(rsiArr),
    macd:   macdLast ? { macd: macdLast.MACD, signal: macdLast.signal, histogram: macdLast.histogram } : null,
    bb:     bbLast   ? { upper: bbLast.upper, middle: bbLast.middle, lower: bbLast.lower, pb: bbLast.pb } : null,
    atr:    last(atrArr),
    adx:    adxLast  ? { adx: adxLast.adx, pdi: adxLast.pdi, mdi: adxLast.mdi } : null,
    stoch:  stochLast ? { k: stochLast.k, d: stochLast.d } : null,
    obv:    last(obvArr),
    cci:    last(cciArr),
    williams: last(williamsArr),

    // ── EMAs ────────────────────────────────────────────────────────────
    ema10val:  last(ema10),
    ema20val,
    ema50val,
    ema200val,
    ema20trending,
    ema50trending,
    aboveEma20:  ema20val  ? currentClose > ema20val  : null,
    aboveEma50:  ema50val  ? currentClose > ema50val  : null,
    aboveEma200: ema200val ? currentClose > ema200val : null,

    // ── Volume ──────────────────────────────────────────────────────────
    avgVol20,
    volRatio:  +volRatio.toFixed(2),
    isVolSurge,

    // ── OBV Divergence ──────────────────────────────────────────────────
    obvDivergence,

    // ── Price Stats ─────────────────────────────────────────────────────
    priceZScore10: +priceZScore10.toFixed(2),
    mom5:  +(mom5  * 100).toFixed(2),
    mom10: +(mom10 * 100).toFixed(2),
    mom20: +(mom20 * 100).toFixed(2),

    // ── Candle Patterns ─────────────────────────────────────────────────
    closePos: +closePos.toFixed(2),
    bodyRatio: +bodyRatio.toFixed(2),
    isHammer,
    isDoji,
    isBullishEngulfing,

    // ── Levels ──────────────────────────────────────────────────────────
    nearestResistance: isFinite(nearestResistance) ? +nearestResistance.toFixed(3) : null,
    nearestSupport:    isFinite(nearestSupport)    ? +nearestSupport.toFixed(3)    : null,
    isNearATH,
    athProximityPct: +(athProximity * 100).toFixed(1),
    periodHigh: +periodHigh.toFixed(3),
  };
}

/**
 * حساب مؤشرات خفيفة (سريعة) لـ scanning من DB
 * يعمل بشكل أسرع من calculateIndicators لأنه يحسب فقط ما يحتاجه الـ scorer
 */
export function quickScan(bars) {
  if (!bars || bars.length < 5) return null;

  const closes  = bars.map(b => b.close);
  const highs   = bars.map(b => b.high);
  const lows    = bars.map(b => b.low);
  const volumes = bars.map(b => b.volume ?? 0);

  const last = arr => arr && arr.length > 0 ? arr[arr.length - 1] : null;

  // RSI
  const rsi = bars.length >= 15 ? last(TI.RSI.calculate({ period: 14, values: closes })) : null;

  // ATR
  const atr = bars.length >= 15 ? last(TI.ATR.calculate({ period: 14, high: highs, low: lows, close: closes })) : null;

  // ADX
  let adx = null;
  if (bars.length >= 15) {
    try { adx = last(TI.ADX.calculate({ period: 14, high: highs, low: lows, close: closes })); } catch {}
  }

  // EMA20 & EMA50
  const ema20 = bars.length >= 20 ? last(TI.EMA.calculate({ period: 20, values: closes })) : null;
  const ema50 = bars.length >= 50 ? last(TI.EMA.calculate({ period: 50, values: closes })) : null;

  // Volume ratio
  const avgVol = volumes.length >= 5 ? ss.mean(volumes.slice(-Math.min(20, volumes.length - 1))) : 0;
  const volRatio = avgVol > 0 ? volumes[volumes.length - 1] / avgVol : 1;

  // Close position in candle
  const last5 = bars.slice(-5);
  const todayBar = last5[last5.length - 1];
  const range = todayBar.high - todayBar.low;
  const closePos = range > 0 ? (todayBar.close - todayBar.low) / range : 0.5;

  return { rsi, atr, adx, ema20, ema50, volRatio: +volRatio.toFixed(2), closePos: +closePos.toFixed(2) };
}

export default { calculateIndicators, quickScan };
