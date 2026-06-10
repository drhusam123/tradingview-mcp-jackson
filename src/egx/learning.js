/**
 * EGX Pattern Learning Engine
 * ============================
 * يحلل تاريخ الصفقات ويكتشف أنماطاً مخفية:
 * - أي setup ينجح أكثر في أي يوم من الأسبوع؟
 * - أي نطاق حجم يعطي أفضل نتائج؟
 * - كيف تتطور Win Rate مع الوقت؟
 *
 * المالك: Dr. Husam | آخر تحديث: 3 مايو 2026
 */

import * as ss from 'simple-statistics';
import { getDB, getBestSetups, getSignalsFromCache } from './database.js';
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const { PolynomialRegression } = require('ml-regression');

// SQLite %w: 0=الأحد...6=السبت — EGX يتداول الأحد-الخميس (0-4)
// Bug fix: أضفنا الجمعة والسبت لتجنب undefined إذا أُدخل تاريخ خاطئ
const DAY_NAMES = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت'];

// ─── التحليل الرئيسي ──────────────────────────────────────────────────────

/**
 * تحليل أداء كل setup حسب يوم الأسبوع
 */
export function analyzeByDayOfWeek() {
  const db     = getDB();
  const trades = db.prepare(`
    SELECT t.setup_type, t.entry_date, t.pnl_pct, t.result,
           strftime('%w', t.entry_date) as dow
    FROM trades t
    WHERE t.result IN ('win','loss','breakeven')
    AND t.entry_date IS NOT NULL
  `).all();

  if (trades.length === 0) {
    return { message: 'لا يوجد بيانات كافية بعد. يحتاج ≥ 10 صفقات لتحليل مفيد.', trades: 0 };
  }

  const grouped = {};
  for (const t of trades) {
    const key = `${t.setup_type}__${t.dow}`;
    if (!grouped[key]) grouped[key] = { setup: t.setup_type, dow: +t.dow, pnls: [], wins: 0, total: 0 };
    grouped[key].pnls.push(t.pnl_pct ?? 0);
    grouped[key].total++;
    if (t.result === 'win') grouped[key].wins++;
  }

  return Object.values(grouped).map(g => ({
    setup:      g.setup,
    dayOfWeek:  g.dow,
    dayName:    DAY_NAMES[g.dow] ?? `يوم ${g.dow}`,
    trades:     g.total,
    winRate:    +(g.wins / g.total * 100).toFixed(1),
    avgPnl:     +(ss.mean(g.pnls)).toFixed(2),
    stdDev:     g.pnls.length > 1 ? +(ss.standardDeviation(g.pnls)).toFixed(2) : 0,
    bestPnl:    +(Math.max(...g.pnls)).toFixed(2),
    worstPnl:   +(Math.min(...g.pnls)).toFixed(2),
  })).sort((a, b) => b.winRate - a.winRate || b.avgPnl - a.avgPnl);
}

/**
 * اكتشاف أفضل نطاق حجم للدخول
 * هل 2x أفضل أم 3x أم 4x؟
 */
export function analyzeVolumeZones() {
  const db     = getDB();
  const trades = db.prepare(`
    SELECT t.pnl_pct, t.result, s.volume_ratio
    FROM trades t
    JOIN scans s ON t.scan_id = s.id
    WHERE t.result IN ('win','loss','breakeven')
    AND s.volume_ratio IS NOT NULL
  `).all();

  if (trades.length < 5) {
    return { message: 'بيانات غير كافية (يحتاج ≥ 5 صفقات)', trades: trades.length };
  }

  const zones = {
    '1.5–2.0x': { min: 1.5, max: 2.0,  trades: [] },
    '2.0–2.5x': { min: 2.0, max: 2.5,  trades: [] },
    '2.5–3.0x': { min: 2.5, max: 3.0,  trades: [] },
    '3.0x+':    { min: 3.0, max: 999,   trades: [] },
  };

  for (const t of trades) {
    for (const [label, zone] of Object.entries(zones)) {
      if (t.volume_ratio >= zone.min && t.volume_ratio < zone.max) {
        zone.trades.push({ pnl: t.pnl_pct ?? 0, win: t.result === 'win' });
      }
    }
  }

  return Object.entries(zones).map(([label, z]) => {
    if (z.trades.length === 0) return { zone: label, trades: 0, winRate: 0, avgPnl: 0 };
    const pnls = z.trades.map(t => t.pnl);
    const wins = z.trades.filter(t => t.win).length;
    return {
      zone:     label,
      trades:   z.trades.length,
      winRate:  +(wins / z.trades.length * 100).toFixed(1),
      avgPnl:   +(ss.mean(pnls)).toFixed(2),
      bestPnl:  +(Math.max(...pnls)).toFixed(2),
      worstPnl: +(Math.min(...pnls)).toFixed(2),
    };
  }).filter(z => z.trades > 0);
}

/**
 * تحليل تطور Win Rate عبر الزمن (هل نتحسن؟)
 */
export function analyzeWinRateTrend() {
  const db     = getDB();
  const pms    = db.prepare(`
    SELECT session_date, win_rate, total_trades, wins, losses
    FROM postmortems
    ORDER BY session_date
  `).all();

  if (pms.length === 0) {
    return { sessions: 0, trend: 'لا بيانات', message: 'يحتاج جلسات post-mortem محفوظة' };
  }

  const rates = pms.map(p => p.win_rate ?? 0);
  const trend = rates.length >= 3 ?
    (ss.linearRegressionLine(ss.linearRegression(rates.map((r, i) => [i, r])))(rates.length - 1) >
     ss.linearRegressionLine(ss.linearRegression(rates.map((r, i) => [i, r])))(0)
      ? '📈 تحسّن' : '📉 تراجع') : '➡️ غير كافٍ';

  return {
    sessions:    pms.length,
    latestWR:    rates[rates.length - 1],
    avgWR:       +(ss.mean(rates)).toFixed(1),
    bestSession: pms.reduce((a, b) => (a.win_rate ?? 0) > (b.win_rate ?? 0) ? a : b),
    trend,
    history:     pms.map(p => ({ date: p.session_date, winRate: p.win_rate, trades: p.total_trades })),
  };
}

/**
 * اكتشاف أفضل سهم بشكل مستمر
 */
export function analyzeBySymbol() {
  const db = getDB();
  return db.prepare(`
    SELECT symbol,
           COUNT(*) as trades,
           SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
           ROUND(AVG(pnl_pct), 2) as avg_pnl,
           ROUND(MAX(pnl_pct), 2) as best_pnl,
           ROUND(MIN(pnl_pct), 2) as worst_pnl,
           ROUND(100.0 * SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) / COUNT(*), 1) as win_rate
    FROM trades
    WHERE result IN ('win','loss','breakeven')
    GROUP BY symbol
    HAVING COUNT(*) >= 2
    ORDER BY win_rate DESC, avg_pnl DESC
  `).all();
}

/**
 * توليد تقرير تعلّم شامل
 */
export function generateLearningReport() {
  const db = getDB();
  const stats = {
    totalTrades: db.prepare("SELECT COUNT(*) as c FROM trades WHERE result != 'open'").get().c,
    totalSessions: db.prepare('SELECT COUNT(*) as c FROM postmortems').get().c,
  };

  if (stats.totalTrades < 5) {
    return {
      status: 'insufficient_data',
      message: `لا يوجد بيانات كافية (${stats.totalTrades} صفقة). يحتاج ≥ 5 صفقات لتحليل مفيد.`,
      currentRules: db.prepare('SELECT rule_number, title FROM lessons WHERE is_active=1').all(),
    };
  }

  return {
    status: 'ok',
    generatedAt: new Date().toISOString(),
    summary: stats,
    dayOfWeekAnalysis: analyzeByDayOfWeek(),
    volumeZoneAnalysis: analyzeVolumeZones(),
    winRateTrend: analyzeWinRateTrend(),
    bestSymbols: analyzeBySymbol(),
    bestSetups: getBestSetups(),
    actionableInsights: generateInsights(),
  };
}

/**
 * توليد توصيات قابلة للتطبيق من البيانات
 */
function generateInsights() {
  const db = getDB();
  const insights = [];

  // هل يوجد يوم أفضل للتداول؟
  const dayStats = db.prepare(`
    SELECT strftime('%w', entry_date) as dow,
           ROUND(100.0 * SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) / COUNT(*), 1) as wr,
           COUNT(*) as total
    FROM trades WHERE result != 'open' AND entry_date IS NOT NULL
    GROUP BY dow HAVING total >= 3
    ORDER BY wr DESC LIMIT 1
  `).get();

  if (dayStats) {
    insights.push({
      type: 'best_day',
      insight: `أفضل يوم للتداول هو ${DAY_NAMES[+dayStats.dow]} بنسبة نجاح ${dayStats.wr}%`,
      action: `ركّز الدخولات في ${DAY_NAMES[+dayStats.dow]}`,
    });
  }

  // هل نتحسّن؟
  const trend = analyzeWinRateTrend();
  if (trend.sessions >= 3) {
    insights.push({
      type: 'trend',
      insight: `Win Rate: ${trend.latestWR}% (متوسط: ${trend.avgWR}%) | الاتجاه: ${trend.trend}`,
      action: trend.trend.includes('تحسّن') ? 'استمر على نفس المنهجية' : 'راجع الفلاتر',
    });
  }

  return insights;
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  DISCOVERY ENGINE — اكتشاف أنماط جديدة
// ═══════════════════════════════════════════════════════════════════════════

/**
 * بناء Correlation Matrix بين الأسهم
 * يساعد على تفادي التركيز في أسهم تتحرك معاً
 */
export function buildCorrelationMatrix(symbols = null, limit = 200) {
  const db = getDB();
  const targets = symbols ?? [
    'MOSC','UTOP','TORA','ADRI','COMI','HDBK','PHDC','TMGH','SWDY','IRON',
  ];

  const priceData = {};
  for (const sym of targets) {
    const rows = db.prepare(`
      SELECT close FROM ohlcv_history WHERE symbol = ?
      ORDER BY bar_time DESC LIMIT ?
    `).all(sym, limit).map(r => r.close).reverse();
    if (rows.length >= 30) priceData[sym] = rows;
  }

  const valid = Object.keys(priceData);
  const matrix = {};

  for (const a of valid) {
    matrix[a] = {};
    for (const b of valid) {
      if (a === b) { matrix[a][b] = 1.0; continue; }
      try {
        const n = Math.min(priceData[a].length, priceData[b].length);
        matrix[a][b] = +ss.sampleCorrelation(
          priceData[a].slice(-n), priceData[b].slice(-n)
        ).toFixed(2);
      } catch { matrix[a][b] = null; }
    }
  }

  const pairs = [];
  for (const a of valid) {
    for (const b of valid) {
      if (a >= b) continue;
      const corr = matrix[a]?.[b];
      if (corr !== null) pairs.push({ a, b, corr });
    }
  }

  const highCorr = pairs.filter(p => p.corr >= 0.7).sort((a, b) => b.corr - a.corr);
  const negCorr  = pairs.filter(p => p.corr <= -0.4).sort((a, b) => a.corr - b.corr);

  return {
    matrix,
    highCorrelation:    highCorr.slice(0, 10),
    negativeCorrelation: negCorr.slice(0, 5),
    warnings: highCorr.slice(0, 3).map(p =>
      `⚠️  ${p.a}+${p.b} corr=${p.corr} — تجنب شراء الاثنين في نفس الوقت`
    ),
  };
}

/**
 * Walk-Forward Validation — تقسيم البيانات للتحقق من عدم overfitting
 */
export function walkForwardValidation(symbol) {
  const db = getDB();
  const allBars = db.prepare(`
    SELECT bar_time as time, open, high, low, close, volume
    FROM ohlcv_history WHERE symbol = ?
    ORDER BY bar_time ASC
  `).all(symbol);

  if (allBars.length < 100) {
    return { error: `بيانات غير كافية: ${allBars.length} شمعة (يحتاج ≥ 100)` };
  }

  const split    = Math.floor(allBars.length * 0.7);
  const inSample  = allBars.slice(0, split);
  const outSample = allBars.slice(split);
  const fmt = ts => ts ? new Date(ts * 1000).toISOString().split('T')[0] : '?';

  return {
    symbol,
    totalBars: allBars.length,
    splitAt:   split,
    inSample: {
      bars: inSample.length,
      from: fmt(inSample[0]?.time),
      to:   fmt(inSample[inSample.length - 1]?.time),
      note: 'استخدم backtestSymbol() على هذا النطاق لتدريب الأوزان',
    },
    outSample: {
      bars: outSample.length,
      from: fmt(outSample[0]?.time),
      to:   fmt(outSample[outSample.length - 1]?.time),
      note: 'اختبر على هذا النطاق — لم يُرَ من قبل',
    },
    interpretation: 'إذا WR in-sample ≈ WR out-of-sample → القاعدة صلبة (لا overfitting)',
  };
}

/**
 * اكتشاف أنماط جديدة من بيانات الصفقات
 */
export function discoverNewPatterns() {
  const db = getDB();

  const dayAnalysis    = analyzeByDayOfWeek();
  const volumeAnalysis = analyzeVolumeZones();

  // هل أسهم الجودة v3 تؤدي أفضل؟
  const QUALITY = ['MOSC','UTOP','TORA','ADRI','COMI','HDBK','PHDC','TMGH'];
  let qualityComparison = { available: false };
  try {
    const qTrades = db.prepare(`
      SELECT pnl_pct, result FROM trades
      WHERE symbol IN (${QUALITY.map(() => '?').join(',')}) AND result IN ('win','loss')
    `).all(...QUALITY);
    const rTrades = db.prepare(`
      SELECT pnl_pct, result FROM trades
      WHERE symbol NOT IN (${QUALITY.map(() => '?').join(',')}) AND result IN ('win','loss')
    `).all(...QUALITY);

    if (qTrades.length >= 3 && rTrades.length >= 3) {
      const qWR = qTrades.filter(t => t.result === 'win').length / qTrades.length;
      const rWR = rTrades.filter(t => t.result === 'win').length / rTrades.length;
      qualityComparison = {
        available: true,
        qualityWR: +(qWR * 100).toFixed(1),
        restWR:    +(rWR * 100).toFixed(1),
        qualityTrades: qTrades.length,
        restTrades:    rTrades.length,
        conclusion: qWR > rWR
          ? `✅ أسهم الجودة v3 أفضل بـ ${((qWR - rWR) * 100).toFixed(1)}% WR`
          : `➡️  لا فرق واضح بعد — تحتاج مزيداً من الصفقات`,
      };
    }
  } catch { /* تجاهل */ }

  return {
    discoveredAt:    new Date().toISOString(),
    dayOfWeek:       dayAnalysis,
    volumeZones:     volumeAnalysis,
    qualityStocks:   qualityComparison,
    confirmedRules: [
      '✅ إغلاق في الثلث السفلي = 29.4% WR (backtest v3 — 18,604 إشارة)',
      '✅ حجم 2.5–3x هو الأمثل (20.2% WR)',
      '✅ Volume Accumulation = أعلى WR إعداد (24.7%)',
      '✅ isNearATH يحتاج 300 شمعة للدقة',
    ],
    recommendations: [
      '🔲 أضف ADX إلى جدول scans لتتبع تأثيره',
      '🔲 أضف close_position_pct إلى جدول scans',
      '🔲 أضف sector إلى stock_universe لتحليل قطاعي',
      '🔲 جلب بيانات EGX30 لتحليل Market Regime',
    ],
  };
}

/**
 * Percentile الحجم — "الحجم اليوم في الـ Nth percentile تاريخياً"
 */
export function getVolumePercentile(symbol, currentVolume = null) {
  const db = getDB();
  const volumes = db.prepare(`
    SELECT volume FROM ohlcv_history
    WHERE symbol = ? AND volume > 0
    ORDER BY bar_time DESC LIMIT 200
  `).all(symbol).map(r => r.volume);

  if (volumes.length < 10) return null;

  const latestVol = currentVolume ?? volumes[0];
  const sorted    = [...volumes].sort((a, b) => a - b);
  const pctile    = ss.quantileRankSorted(sorted, latestVol);
  const avg       = ss.mean(volumes);

  return {
    symbol,
    currentVolume: latestVol,
    avgVolume:     +avg.toFixed(0),
    volumeRatio:   +(latestVol / avg).toFixed(2),
    percentile:    +(pctile * 100).toFixed(1),
    label: pctile >= 0.90 ? '🔥 استثنائي (أعلى 10%)'
         : pctile >= 0.75 ? '✅ مرتفع جداً (75–90%)'
         : pctile >= 0.60 ? '👍 مرتفع (60–75%)'
         : pctile >= 0.40 ? '➡️  طبيعي'
         : '⚠️ منخفض',
  };
}

/**
 * Sharpe Ratio للنظام + Skewness
 */
export function calculateSystemSharpe() {
  const db = getDB();
  const returns = db.prepare(`
    SELECT pnl_pct FROM trades
    WHERE result IN ('win','loss','breakeven') AND pnl_pct IS NOT NULL
    ORDER BY created_at
  `).all().map(t => t.pnl_pct);

  if (returns.length < 10) return { error: 'يحتاج ≥ 10 صفقات مغلقة', trades: returns.length };

  const mean   = ss.mean(returns);
  const std    = ss.standardDeviation(returns);
  const sharpe = std > 0 ? mean / std : 0;
  const skew   = returns.length >= 4 ? ss.sampleSkewness(returns) : 0;

  return {
    trades:   returns.length,
    avgPnl:   +mean.toFixed(2),
    stdDev:   +std.toFixed(2),
    sharpe:   +sharpe.toFixed(2),
    skewness: +skew.toFixed(2),
    sharpeLabel: sharpe >= 1.0 ? '✅ ممتاز (≥1)'
               : sharpe >= 0.5 ? '👍 جيد (0.5–1)'
               : sharpe > 0    ? '⚠️ مقبول (0–0.5)'
               : '❌ سلبي',
    skewnessLabel: skew > 0.3  ? '✅ Positive skew — الأرباح أكبر من الخسائر'
                 : skew < -0.3 ? '⚠️ Negative skew — الخسائر أكبر من الأرباح'
                 : '➡️  متوازن',
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  REGRESSION-BASED ANALYSIS (ml-regression) ─────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
 * تحليل العلاقة بين RSI وعائد T+5 — هل RSI=30 أفضل من RSI=35؟
 * يستخدم polynomial regression لرسم المنحنى الحقيقي
 *
 * @param {string} symbol - null = كل الأسهم
 * @returns {Object} - معادلة الانحدار + الـ optimal RSI range
 */
export function analyzeRsiReturnCurve(symbol = null) {
  const db = getDB();
  let sql = `
    SELECT ic.rsi14 as rsi, o5.close as close5, o0.close as close0
    FROM indicators_cache ic
    JOIN ohlcv_history o0 ON o0.symbol = ic.symbol
      AND o0.bar_time = (
        SELECT bar_time FROM ohlcv_history WHERE symbol = ic.symbol
        AND date(bar_time,'unixepoch') = ic.bar_date LIMIT 1
      )
    JOIN ohlcv_history o5 ON o5.symbol = ic.symbol
      AND o5.bar_time = (
        SELECT MIN(bar_time) FROM ohlcv_history WHERE symbol = ic.symbol
        AND bar_time > o0.bar_time + (4 * 86400)
        AND bar_time <= o0.bar_time + (8 * 86400)
      )
    WHERE ic.rsi14 BETWEEN 20 AND 60
  `;
  if (symbol) sql += ` AND ic.symbol = '${symbol}'`;
  sql += ' LIMIT 5000';

  const rows = db.prepare(sql).all();
  if (rows.length < 30) {
    return { error: 'بيانات غير كافية للانحدار', rows: rows.length };
  }

  // حساب العائد T+5
  const x = rows.map(r => r.rsi);
  const y = rows.map(r => r.close0 > 0 ? (r.close5 - r.close0) / r.close0 * 100 : 0);

  // polynomial regression degree 2 (parabola)
  let reg;
  try {
    reg = new PolynomialRegression(x, y, 2);
  } catch { return { error: 'فشل الانحدار' }; }

  // إيجاد RSI الأمثل (minimum of parabola = max return)
  const rsiRange = Array.from({length: 41}, (_, i) => i + 20);
  const predicted = rsiRange.map(r => ({ rsi: r, predicted: +reg.predict(r).toFixed(3) }));
  const best = predicted.sort((a,b) => b.predicted - a.predicted)[0];

  // تجميع buckets
  const buckets = {};
  for (let i = 0; i < rows.length; i++) {
    const bucket = Math.floor(x[i] / 5) * 5;
    if (!buckets[bucket]) buckets[bucket] = { returns: [], count: 0 };
    buckets[bucket].returns.push(y[i]);
    buckets[bucket].count++;
  }

  return {
    symbol: symbol ?? 'all',
    dataPoints: rows.length,
    rSquared: +reg.score(x, y).toFixed(3),
    optimalRsi: best.rsi,
    optimalReturn: best.predicted,
    buckets: Object.entries(buckets)
      .sort((a,b) => +a[0] - +b[0])
      .map(([rsi, d]) => ({
        rsiRange: `${rsi}-${+rsi+4}`,
        avgReturn: +(d.returns.reduce((a,b)=>a+b,0)/d.returns.length).toFixed(2),
        count: d.count,
      })),
    note: `الـ Optimal RSI للدخول في EGX هو ${best.rsi} (عائد متوقع: ${best.predicted}%)`
  };
}

/**
 * Score-to-WinRate regression — هل الـ score فعلاً يتنبأ بالنجاح؟
 * يحلل العلاقة بين نقاط الـ scorer وWin Rate الفعلي
 */
export function analyzeScoreCalibration() {
  const db = getDB();
  const rows = db.prepare(`
    SELECT s.score, t.result, t.pnl_pct
    FROM trades t
    JOIN scans s ON s.id = t.scan_id
    WHERE t.result IN ('win','loss','breakeven')
      AND s.score IS NOT NULL AND s.score > 0
  `).all();

  if (rows.length < 20) {
    return { error: 'يحتاج ≥ 20 صفقة مغلقة', trades: rows.length };
  }

  // تجميع في buckets كل 10 نقاط
  const buckets = {};
  for (const r of rows) {
    const b = Math.floor(r.score / 10) * 10;
    if (!buckets[b]) buckets[b] = { wins: 0, total: 0, pnls: [] };
    buckets[b].total++;
    if (r.result === 'win') buckets[b].wins++;
    buckets[b].pnls.push(r.pnl_pct ?? 0);
  }

  const bucketList = Object.entries(buckets)
    .filter(([,v]) => v.total >= 3)
    .sort((a,b) => +a[0] - +b[0])
    .map(([score, d]) => ({
      scoreRange: `${score}-${+score+9}`,
      winRate:   +(d.wins / d.total * 100).toFixed(1),
      avgPnl:    +(d.pnls.reduce((a,b)=>a+b,0)/d.pnls.length).toFixed(2),
      trades:    d.total,
    }));

  // هل هناك correlation بين score وWR؟
  if (bucketList.length >= 3) {
    const xs = bucketList.map(b => +b.scoreRange.split('-')[0]);
    const ys = bucketList.map(b => b.winRate);
    try {
      const corr = ss.sampleCorrelation(xs, ys);
      return {
        buckets: bucketList,
        scoreWrCorrelation: +corr.toFixed(3),
        calibrated: Math.abs(corr) >= 0.5,
        note: Math.abs(corr) >= 0.5
          ? `✅ الـ Score يتنبأ بـ WR جيداً (r=${corr.toFixed(2)})`
          : `⚠️ الـ Score يحتاج معايرة (r=${corr.toFixed(2)}) — ضع في عين الاعتبار الـ findings الإحصائية`,
      };
    } catch {}
  }

  return { buckets: bucketList };
}

/**
 * استخدام indicators_cache لاكتشاف أسهم اليوم بسرعة (SQL فقط)
 * بديل سريع لـ scan_today --db-only لمن يريد RSI_OBV_COMBO فقط
 */
export function quickComboScan() {
  const signals = getSignalsFromCache({ signalType: 'RSI_OBV_COMBO', limit: 20 });
  const oversold = getSignalsFromCache({ signalType: 'OVERSOLD', maxRsi: 35, limit: 30 });
  const bbOversold = getSignalsFromCache({ signalType: 'BB_OVERSOLD', limit: 20 });

  return {
    rsiObvCombos: signals,
    oversoldRsi:  oversold.filter(s => !signals.some(c => c.symbol === s.symbol)),
    bbOversold:   bbOversold.filter(s => !oversold.some(o => o.symbol === s.symbol)),
    total:        signals.length + oversold.length + bbOversold.length,
    note: signals.length > 0
      ? `🔥 ${signals.length} سهم في RSI+OBV COMBO (WR=69% إحصائياً!)`
      : 'لا يوجد RSI+OBV combo اليوم — السوق ليس في حالة oversold',
  };
}

// ═══════════════════════════════════════════════════════════════════════════

export default {
  analyzeByDayOfWeek,
  analyzeVolumeZones,
  analyzeWinRateTrend,
  analyzeBySymbol,
  generateLearningReport,
  buildCorrelationMatrix,
  walkForwardValidation,
  discoverNewPatterns,
  getVolumePercentile,
  calculateSystemSharpe,
  analyzeRsiReturnCurve,
  analyzeScoreCalibration,
  quickComboScan,
};
