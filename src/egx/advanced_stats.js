/**
 * EGX Advanced Statistics (@stdlib + simple-statistics)
 * =======================================================
 * إحصاء متقدم لا تتضمنه simple-statistics:
 *
 *   1. Kolmogorov-Smirnov Test  — هل العوائد تتبع Normal distribution؟
 *   2. Fat-Tail Risk Metrics    — VaR، CVaR، Max Drawdown
 *   3. Regime Detection         — هل السوق في Trending أم Mean-Reverting؟
 *   4. Hurst Exponent           — قياس الذاكرة في السلاسل الزمنية
 *   5. Monte Carlo Simulation   — محاكاة مستقبل المحفظة
 *   6. Autocorrelation Analysis — هل غد مرتبط بأمس؟
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import * as ss from 'simple-statistics';
import { createRequire } from 'module';
const require = createRequire(import.meta.url);

// @stdlib — KS test للتوزيع الطبيعي
const kstest      = require('@stdlib/stats-kstest');
const normalCDF   = require('@stdlib/stats-base-dists-normal-cdf');

import { getDB } from './database.js';

// ─── 1. Kolmogorov-Smirnov Test ──────────────────────────────────────────

/**
 * اختبار هل عوائد EGX تتبع Normal Distribution
 * النتيجة المتوقعة من الـ 75K bar analysis: Kurtosis=26.8 → لا يتبع Normal
 *
 * @param {string|null} symbol - null = كل الأسهم (sample)
 * @param {number} sampleSize  - حجم العينة للاختبار
 */
export function testNormality(symbol = null, sampleSize = 2000) {
  const db = getDB();
  let sql = `
    SELECT (close - LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time)) /
           LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time) * 100 as ret
    FROM ohlcv_history
    WHERE volume > 0
  `;
  if (symbol) sql += ` AND symbol = '${symbol}'`;
  sql += ` ORDER BY RANDOM() LIMIT ${sampleSize}`;

  const rows   = db.prepare(sql).all();
  const returns = rows.map(r => r.ret).filter(r => r != null && !isNaN(r) && Math.abs(r) < 25);

  if (returns.length < 50) return { error: 'بيانات غير كافية' };

  const mean   = ss.mean(returns);
  const std    = ss.standardDeviation(returns);
  const skew   = ss.sampleSkewness(returns);
  const kurt   = ss.sampleKurtosis(returns);

  // KS Test: نمرر CDF function مباشرة لأن @stdlib لا يقبل 'normal' كـ string
  let ksResult;
  try {
    // normalCDF(x, mean, std) — مقارنة كل قيمة بالتوزيع الطبيعي المعايَر
    ksResult = kstest(returns, x => normalCDF(x, mean, std));
  } catch (e) {
    ksResult = { pValue: null, statistic: null };
  }

  // تفسير النتيجة
  // pValue=0 يعني رفض قوي جداً للـ normality (لا تعني undefined)
  const isNormal  = (ksResult.pValue ?? 0) > 0.05;
  const tailRisk  = kurt > 6 ? 'عالي جداً' : kurt > 3 ? 'مرتفع' : 'طبيعي';
  const fatTailPct = returns.filter(r => Math.abs(r - mean) > 3 * std).length / returns.length * 100;

  return {
    symbol:       symbol ?? 'EGX (عينة)',
    sampleSize:   returns.length,
    mean:         +mean.toFixed(4),
    std:          +std.toFixed(4),
    skewness:     +skew.toFixed(3),
    kurtosis:     +kurt.toFixed(3),
    excessKurtosis: +(kurt - 3).toFixed(3),
    ksStatistic:  ksResult.statistic != null ? +ksResult.statistic.toFixed(4) : null,
    ksPValue:     ksResult.pValue    != null ? +ksResult.pValue.toFixed(6)    : null,
    isNormalDist: isNormal,
    tailRiskLevel: tailRisk,
    fatTailPct:   +fatTailPct.toFixed(2),
    interpretation: [
      `التوزيع: ${isNormal ? '✅ قريب من Normal' : '❌ ليس Normal — fat tails'}`,
      `Excess Kurtosis=${(kurt-3).toFixed(1)} (Normal=0, EGX~23.8)`,
      `${fatTailPct.toFixed(1)}% من العوائد خارج 3σ (Normal يتوقع 0.3%)`,
      `الخطورة: ${tailRisk} — ${isNormal ? 'Standard deviation كافي لقياس المخاطر' : 'استخدم CVaR بدل Standard Deviation'}`,
      skew > 0 ? `Positive skew +${skew.toFixed(2)}: احتمال أرباح كبيرة مرتفع` : `Negative skew ${skew.toFixed(2)}: احتمال خسائر حادة`,
    ],
  };
}

// ─── 2. Fat-Tail Risk Metrics ─────────────────────────────────────────────

/**
 * حساب VaR و CVaR (Expected Shortfall) — أدق من Standard Deviation للـ fat tails
 *
 * @param {number[]} returns - عوائد يومية كـ %
 * @param {number} confidence - مستوى الثقة (0.95 أو 0.99)
 * @param {number} capital - رأس المال بالجنيه (لحساب الخسارة الفعلية)
 */
export function calculateRiskMetrics(returns, confidence = 0.95, capital = 100000) {
  if (!returns?.length || returns.length < 20) {
    return { error: 'يحتاج ≥ 20 قيمة' };
  }

  const sorted   = [...returns].sort((a, b) => a - b);
  const n        = sorted.length;
  const varIdx   = Math.floor((1 - confidence) * n);
  const varValue = sorted[varIdx];

  // CVaR (Expected Shortfall): متوسط أسوأ الخسائر
  const tailReturns = sorted.slice(0, varIdx + 1);
  const cvarValue   = tailReturns.length > 0 ? ss.mean(tailReturns) : varValue;

  // Max Drawdown من العوائد التراكمية (بالترتيب الزمني، لا مرتبة)
  let equity = 1.0, peak = 1.0, maxDD = 0;
  for (const r of returns) {
    equity *= (1 + r / 100);
    if (equity > peak) peak = equity;
    const dd = (peak - equity) / peak * 100;
    if (dd > maxDD) maxDD = dd;
  }

  const mean = ss.mean(returns);
  const std  = ss.standardDeviation(returns);

  // Sortino Ratio (يستخدم downside deviation فقط)
  const negReturns     = returns.filter(r => r < 0);
  const downsideStd    = negReturns.length > 1 ? ss.standardDeviation(negReturns) : std;
  const sortinoRatio   = downsideStd > 0 ? +(mean / downsideStd * Math.sqrt(252)).toFixed(2) : null;

  return {
    confidence:    `${(confidence * 100).toFixed(0)}%`,
    sampleSize:    n,
    var1d:         +varValue.toFixed(3),
    cvar1d:        +cvarValue.toFixed(3),
    varCapital:    +Math.abs(varValue / 100 * capital).toFixed(0),
    cvarCapital:   +Math.abs(cvarValue / 100 * capital).toFixed(0),
    maxDrawdownPct: +maxDD.toFixed(2),
    sortinoRatio,
    mean:          +mean.toFixed(3),
    std:           +std.toFixed(3),
    interpretation: [
      `VaR(${(confidence*100).toFixed(0)}%): يوم واحد من 20 ستخسر أكثر من ${Math.abs(varValue).toFixed(2)}%`,
      `CVaR: في السيناريوهات الأسوأ ${(1-confidence)*100}% متوسط الخسارة ${Math.abs(cvarValue).toFixed(2)}%`,
      `على رأس مال ${capital.toLocaleString()} جنيه: VaR = ${Math.abs(varValue / 100 * capital).toLocaleString()} جنيه`,
      `Max Drawdown من العينة: ${maxDD.toFixed(2)}%`,
      sortinoRatio != null ? `Sortino Ratio: ${sortinoRatio} ${sortinoRatio > 1 ? '✅' : sortinoRatio > 0 ? '⚠️' : '❌'}` : '',
    ].filter(Boolean),
  };
}

/**
 * VaR سريع من قاعدة البيانات
 * @param {string|null} symbol
 * @param {number} confidence
 * @param {number} capital
 */
export function quickVaR(symbol = null, confidence = 0.95, capital = 100000) {
  const db = getDB();
  let sql = `
    SELECT (close - LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time)) /
           LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time) * 100 as ret
    FROM ohlcv_history WHERE volume > 0
  `;
  if (symbol) sql += ` AND symbol = '${symbol}'`;
  sql += ` ORDER BY bar_time DESC LIMIT 500`;

  const returns = db.prepare(sql).all()
    .map(r => r.ret)
    .filter(r => r != null && !isNaN(r) && Math.abs(r) < 25);

  return calculateRiskMetrics(returns, confidence, capital);
}

// ─── 3. Hurst Exponent ───────────────────────────────────────────────────

/**
 * حساب Hurst Exponent لسهم أو السوق
 *
 * H < 0.5 → Mean-Reverting (أسهم تعود للمتوسط) ← EGX المتوقع
 * H = 0.5 → Random Walk (لا يمكن التنبؤ)
 * H > 0.5 → Trending (الاتجاه يستمر)
 *
 * @param {number[]} prices - أسعار الإغلاق
 * @returns {number} H ∈ [0, 1]
 */
export function calculateHurst(prices) {
  if (!prices || prices.length < 20) return null;

  // R/S Analysis
  const lags = [4, 8, 16, 32, 64].filter(l => l < prices.length / 2);
  if (lags.length < 3) return null;

  const rsList = [];
  for (const lag of lags) {
    const chunks = Math.floor(prices.length / lag);
    const rs = [];
    for (let i = 0; i < chunks; i++) {
      const chunk = prices.slice(i * lag, (i + 1) * lag);
      const returns = chunk.slice(1).map((p, j) => Math.log(p / chunk[j]));
      if (returns.length < 2) continue;
      const mean = ss.mean(returns);
      const std  = ss.standardDeviation(returns);
      if (std === 0) continue;
      let cum = 0, maxCum = -Infinity, minCum = Infinity;
      for (const r of returns) { cum += (r - mean); maxCum = Math.max(maxCum, cum); minCum = Math.min(minCum, cum); }
      const R = maxCum - minCum;
      rs.push(R / std);
    }
    if (rs.length > 0) rsList.push({ lag, rs: ss.mean(rs) });
  }

  if (rsList.length < 3) return null;

  // OLS: log(RS) = H * log(lag) + C
  const x = rsList.map(r => Math.log(r.lag));
  const y = rsList.map(r => Math.log(r.rs));
  const reg = ss.linearRegression(x.map((xi, i) => [xi, y[i]]));

  return +reg.m.toFixed(3);
}

/**
 * تحليل Hurst لكل أسهم EGX — يحدد سوق mean-reversion أم trending
 * @param {string[]} symbols
 */
export function analyzeMarketRegime(symbols) {
  const db = getDB();
  const results = { trending: [], meanReverting: [], random: [], summary: {} };

  for (const sym of symbols) {
    const prices = db.prepare(`
      SELECT close FROM ohlcv_history WHERE symbol = ? AND volume > 0
      ORDER BY bar_time DESC LIMIT 200
    `).all(sym).map(r => r.close).reverse();

    if (prices.length < 64) continue;

    const H = calculateHurst(prices);
    if (H == null) continue;

    const item = {
      symbol: sym,
      hurst: H,
      regime: H < 0.45 ? 'mean_reverting' : H > 0.55 ? 'trending' : 'random_walk',
      label: H < 0.45 ? '🔄 Mean-Reverting' : H > 0.55 ? '📈 Trending' : '🎲 Random Walk',
      strategy: H < 0.45
        ? 'RSI extremes + OBV divergence أفضل إشارة (مؤكَّد إحصائياً)'
        : H > 0.55
        ? 'EMA cross + ADX ≥ 25 + momentum strategy'
        : 'لا توجد ميزة إحصائية واضحة',
    };

    if (item.regime === 'mean_reverting') results.meanReverting.push(item);
    else if (item.regime === 'trending')  results.trending.push(item);
    else                                  results.random.push(item);
  }

  const all = [...results.trending, ...results.meanReverting, ...results.random];
  if (all.length > 0) {
    const hurstVals = all.map(r => r.hurst);
    results.summary = {
      totalAnalyzed:      all.length,
      avgHurst:           +ss.mean(hurstVals).toFixed(3),
      trendingCount:      results.trending.length,
      meanRevertingCount: results.meanReverting.length,
      randomCount:        results.random.length,
      dominantRegime:     results.meanReverting.length > results.trending.length
        ? '🔄 Mean-Reverting (السوق يعود للمتوسط — إشارات RSI+OBV أفضل)'
        : '📈 Trending (استخدم momentum + EMA strategies)',
    };
  }

  return results;
}

// ─── 4. Autocorrelation ──────────────────────────────────────────────────

/**
 * Autocorrelation Analysis — هل عائد اليوم يتنبأ بعائد الغد؟
 * نتيجة مهمة: إذا lag-1 autocorrelation سلبية → mean reversion مؤكَّد
 *
 * @param {string} symbol
 * @param {number} maxLag - أقصى lag للاختبار
 */
export function analyzeAutocorrelation(symbol, maxLag = 10) {
  const db      = getDB();
  const prices  = db.prepare(`
    SELECT close FROM ohlcv_history WHERE symbol = ? AND volume > 0
    ORDER BY bar_time DESC LIMIT 500
  `).all(symbol).map(r => r.close).reverse();

  if (prices.length < maxLag + 20) return { error: 'بيانات غير كافية' };

  const returns = prices.slice(1).map((p, i) => (p - prices[i]) / prices[i] * 100);
  const mean    = ss.mean(returns);
  const variance = ss.variance(returns);

  const acf = [];
  for (let lag = 1; lag <= maxLag; lag++) {
    let cov = 0;
    for (let i = lag; i < returns.length; i++) {
      cov += (returns[i] - mean) * (returns[i - lag] - mean);
    }
    cov /= (returns.length - lag);
    const correlation = variance > 0 ? cov / variance : 0;
    acf.push({ lag, acf: +correlation.toFixed(4) });
  }

  const lag1 = acf[0]?.acf ?? 0;
  const significantThreshold = 2 / Math.sqrt(returns.length); // 95% CI

  return {
    symbol,
    sampleSize: returns.length,
    acf,
    lag1Acf: lag1,
    significantThreshold: +significantThreshold.toFixed(4),
    interpretation: [
      lag1 < -significantThreshold
        ? `✅ Lag-1 ACF = ${lag1} (سلبية وهامة) → MEAN-REVERSION مؤكَّد`
        : lag1 > significantThreshold
        ? `📈 Lag-1 ACF = ${lag1} (إيجابية وهامة) → MOMENTUM/TRENDING`
        : `🎲 Lag-1 ACF = ${lag1} (غير هامة) → Random Walk`,
      `Strategy المناسبة: ${lag1 < 0 ? 'RSI Oversold + OBV Divergence (WR=69% مؤكَّد)' : lag1 > 0 ? 'EMA Cross + Breakout' : 'لا توجد ميزة واضحة'}`,
    ],
  };
}

// ─── 5. Monte Carlo Portfolio Simulation ─────────────────────────────────

/**
 * Monte Carlo Simulation للمحفظة
 * يحاكي 1000 سيناريو مستقبلي بناءً على التوزيع التاريخي الفعلي
 * (يستخدم historical bootstrap لا Normal → يعكس fat tails)
 *
 * @param {number[]} returns    - عوائد يومية تاريخية
 * @param {number} capitalEGP   - رأس المال بالجنيه
 * @param {number} simDays      - أيام المحاكاة
 * @param {number} simulations  - عدد المحاكاة
 */
export function monteCarloSimulation(returns, capitalEGP = 100000, simDays = 22, simulations = 1000) {
  if (!returns || returns.length < 30) return { error: 'يحتاج ≥ 30 يوم بيانات' };

  const finalValues = [];
  for (let sim = 0; sim < simulations; sim++) {
    let value = capitalEGP;
    for (let d = 0; d < simDays; d++) {
      // Bootstrap من التاريخ الفعلي (يحافظ على fat tails)
      const r = returns[Math.floor(Math.random() * returns.length)];
      value *= (1 + r / 100);
    }
    finalValues.push(value);
  }

  finalValues.sort((a, b) => a - b);
  const n = finalValues.length;

  return {
    capitalEGP,
    simDays,
    simulations,
    results: {
      worst1pct:    +finalValues[Math.floor(n * 0.01)].toFixed(0),
      worst5pct:    +finalValues[Math.floor(n * 0.05)].toFixed(0),
      worst10pct:   +finalValues[Math.floor(n * 0.10)].toFixed(0),
      median:       +finalValues[Math.floor(n * 0.50)].toFixed(0),
      best10pct:    +finalValues[Math.floor(n * 0.90)].toFixed(0),
      best5pct:     +finalValues[Math.floor(n * 0.95)].toFixed(0),
      best1pct:     +finalValues[Math.floor(n * 0.99)].toFixed(0),
    },
    pnl: {
      worst5pct:  +(finalValues[Math.floor(n*0.05)] - capitalEGP).toFixed(0),
      median:     +(finalValues[Math.floor(n*0.50)] - capitalEGP).toFixed(0),
      best5pct:   +(finalValues[Math.floor(n*0.95)] - capitalEGP).toFixed(0),
    },
    probProfit:  +(finalValues.filter(v => v > capitalEGP).length / n * 100).toFixed(1),
    probLoss5pct: +(finalValues.filter(v => v < capitalEGP * 0.95).length / n * 100).toFixed(1),
    interpretation: [
      `محاكاة ${simulations.toLocaleString()} سيناريو لـ ${simDays} يوم تداول`,
      `احتمال الربح: ${+(finalValues.filter(v => v > capitalEGP).length / n * 100).toFixed(1)}%`,
      `الأسوأ 5%: خسارة ${Math.abs(+(finalValues[Math.floor(n*0.05)] - capitalEGP).toFixed(0)).toLocaleString()} جنيه`,
      `المتوسط: ${(+(finalValues[Math.floor(n*0.50)] - capitalEGP).toFixed(0) > 0 ? '+' : '')}${(+(finalValues[Math.floor(n*0.50)] - capitalEGP).toFixed(0)).toLocaleString()} جنيه`,
    ],
  };
}

/**
 * Monte Carlo سريع من قاعدة البيانات لسهم أو السوق
 */
export function quickMonteCarloFromDB(symbol = null, capital = 100000, days = 22) {
  const db = getDB();
  let sql = `
    SELECT (close - LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time)) /
           LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time) * 100 as ret
    FROM ohlcv_history WHERE volume > 0
  `;
  if (symbol) sql += ` AND symbol = '${symbol}'`;
  sql += ` ORDER BY bar_time DESC LIMIT 500`;

  const returns = db.prepare(sql).all()
    .map(r => r.ret)
    .filter(r => r != null && !isNaN(r) && Math.abs(r) < 20);

  return monteCarloSimulation(returns, capital, days);
}
