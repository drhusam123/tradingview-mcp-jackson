/**
 * EGX Backtesting Engine
 * =======================
 * يختبر الفلاتر المكتسبة على البيانات التاريخية المحفوظة محلياً
 * لا يحتاج TradingView — يعمل بشكل كامل من data/egx_trading.db
 *
 * المالك: Dr. Husam | آخر تحديث: مايو 2026
 * Realistic Cost Model: commission + slippage حسب السيولة + gap risk
 */

import * as ss from 'simple-statistics';
import { scoreSetup } from './scorer.js';
import { getDB, getOHLCV } from './database.js';

// ─── إعدادات افتراضية ────────────────────────────────────────────────────
const DEFAULT_CONFIG = {
  minScore:        55,      // الحد الأدنى للنقاط للدخول
  commissionPct:   0.00175, // 0.175% لكل اتجاه = 0.35% إجمالي (EGX Broker fees)
  // Slippage model (يتغير حسب السيولة — راجع calcSlippage أدناه)
  slippageModel:   'dynamic', // 'dynamic' | 'fixed' | 'zero'
  slippageFixed:   0.001,   // 0.1% fixed (تُستخدم إذا slippageModel=fixed)
  maxHoldBars:     5,       // أقصى عدد شمعات للاحتفاظ بالصفقة
  positionSizePct: 0.10,    // 10% من المحفظة لكل صفقة
  initialCapital:  100000,  // رأس مال افتراضي بالجنيه
  gapRiskPct:      0.005,   // 0.5% خصم إضافي لـ overnight gap risk (EGX يفتح بـ gaps)
};

/**
 * نموذج الـ Slippage الديناميكي حسب حجم التداول
 * EGX فيه أسهم thin volume — slippage أعلى بكثير من الأسواق المتقدمة
 *
 * @param {Object} bar        - شمعة الدخول (open, volume)
 * @param {Object[]} history  - تاريخ الشمعات السابقة
 * @param {Object} cfg        - الإعدادات
 * @returns {number}          - slippage كنسبة مئوية (0.05 = 0.05%)
 */
function calcSlippage(bar, history, cfg) {
  if (cfg.slippageModel === 'zero')  return 0;
  if (cfg.slippageModel === 'fixed') return cfg.slippageFixed * 100;

  // Dynamic: استخدم نسبة الحجم vs. متوسط 20 يوم
  if (!history || history.length < 5) return 0.15; // افتراضي للأسهم الجديدة

  const last20   = history.slice(-20);
  const avgVol   = last20.reduce((s, b) => s + b.volume, 0) / last20.length;
  const volRatio = bar.volume / (avgVol || 1);

  // سيولة عالية (حجم > 1.5x المتوسط) → slippage منخفض
  if (volRatio > 1.5) return 0.05;   // 0.05%
  if (volRatio > 1.0) return 0.10;   // 0.10%
  if (volRatio > 0.5) return 0.20;   // 0.20%
  return 0.40;                        // 0.40% للأسهم الـ thin
}

/**
 * اختبار استراتيجية على سهم محدد — يعمل من قاعدة البيانات المحلية
 * (لا يحتاج TradingView أن يكون شغّالاً)
 * @param {string} symbol
 * @param {number} barCount - عدد الشمعات (افتراضي 300)
 * @param {Object} config
 * @returns {Object} نتائج الـ backtest
 */
export function backtestSymbol(symbol, barCount = 300, config = {}) {
  const cfg = { ...DEFAULT_CONFIG, ...config };

  // ── جلب البيانات من قاعدة البيانات المحلية ──────────────────────────
  const allBars = getOHLCV(symbol, barCount);

  if (!allBars || allBars.length < 20) {
    return {
      symbol,
      error: 'لا بيانات محلية. شغّل fetch_egx_history.mjs أولاً.',
      bars: allBars?.length ?? 0,
    };
  }

  const signals = [];
  const trades  = [];

  // ── تشغيل الفلتر على كل شمعة (نافذة متحركة) ────────────────────────
  const WINDOW = 5;
  for (let i = WINDOW; i < allBars.length - 1; i++) {
    const windowBars = allBars.slice(i - WINDOW, i);
    const last       = windowBars[windowBars.length - 1];

    // بناء stockData مع all_bars للـ ATH الصحيح (بدون lookahead)
    const stockData = {
      symbol,
      quote: {
        close:  last.close,
        open:   last.open,
        high:   last.high,
        low:    last.low,
        volume: last.volume,
      },
      last_5_bars: windowBars,
      all_bars:    allBars.slice(0, i),   // تاريخ كامل حتى هذه اللحظة
    };

    const result = scoreSetup(stockData);

    // ── دخول إذا النقاط ≥ الحد الأدنى وغير مرفوض ────────────────────
    if (!result.rejected && result.score >= cfg.minScore) {
      // فتح الشمعة التالية = سعر الدخول الواقعي
      const nextBar    = allBars[i];
      const entryPrice = nextBar?.open ?? last.close;
      const sl = result.levels?.sl ?? entryPrice * 0.96;
      const t1 = result.levels?.t1 ?? entryPrice * 1.04;

      signals.push({
        barIndex:  i,
        score:     result.score,
        setupType: result.setupType,
        entryPrice,
        sl,
        t1,
      });

      // ── محاكاة الصفقة ────────────────────────────────────────────
      let exitPrice  = null;
      let exitReason = 'max_hold';
      let exitBar    = i + 1;

      for (let j = i + 1; j <= Math.min(i + cfg.maxHoldBars, allBars.length - 1); j++) {
        const bar = allBars[j];
        if (bar.low <= sl) {
          exitPrice  = sl;
          exitReason = 'stop_loss';
          exitBar    = j;
          break;
        }
        if (bar.high >= t1) {
          exitPrice  = t1;
          exitReason = 'target_1';
          exitBar    = j;
          break;
        }
      }

      if (!exitPrice) {
        exitPrice = allBars[Math.min(i + cfg.maxHoldBars, allBars.length - 1)]?.close ?? entryPrice;
      }

      // ── Realistic Cost Model (EGX-specific) ────────────────────────
      const commission   = cfg.commissionPct * 2 * 100;  // 0.35% round-trip
      const slippage     = calcSlippage(nextBar ?? last, allBars.slice(0, i), cfg);  // dynamic
      const gapRisk      = cfg.gapRiskPct * 100;         // overnight gap (0.5%)
      const totalCosts   = commission + slippage + gapRisk;
      const grossPnlPct  = (exitPrice - entryPrice) / entryPrice * 100;
      const pnlPct       = grossPnlPct - totalCosts;
      const won          = exitReason === 'target_1';

      trades.push({
        barIndex:   i,
        entryBar:   allBars[i + 1]?.time,
        exitBar:    allBars[exitBar]?.time,
        symbol,
        setupType:  result.setupType,
        score:      result.score,
        entryPrice: +entryPrice.toFixed(3),
        exitPrice:  +exitPrice.toFixed(3),
        sl:         +sl.toFixed(3),
        t1:         +t1.toFixed(3),
        grossPnlPct:+grossPnlPct.toFixed(2),
        commission: +commission.toFixed(3),
        slippage:   +slippage.toFixed(3),
        gapRisk:    +gapRisk.toFixed(3),
        totalCosts: +totalCosts.toFixed(3),
        pnlPct:     +pnlPct.toFixed(2),
        exitReason,
        won,
        holdBars:   exitBar - (i + 1),
      });
    }
  }

  return calculateBacktestStats(symbol, trades, signals.length, allBars.length, cfg);
}

/**
 * اختبار على قائمة أسهم EGX (synchronous loop)
 */
export function backtestPortfolio(symbols, barCount = 300, config = {}) {
  const results = [];

  for (const sym of symbols) {
    try {
      const r = backtestSymbol(sym, barCount, config);
      results.push(r);
    } catch (e) {
      results.push({ symbol: sym, error: e.message });
    }
  }

  const valid = results.filter(r => !r.error && r.totalTrades >= 3);
  if (valid.length === 0) return { error: 'لا بيانات كافية', symbols: results };

  return {
    portfolioSummary: {
      symbols:         valid.length,
      totalTrades:     valid.reduce((s, r) => s + r.totalTrades, 0),
      avgWinRate:      +(ss.mean(valid.map(r => r.winRate))).toFixed(1),
      avgProfitFactor: +(ss.mean(valid.map(r => r.profitFactor ?? 0))).toFixed(2),
      bestSymbol:      [...valid].sort((a, b) => b.winRate - a.winRate)[0]?.symbol,
      bestByPnl:       [...valid].sort((a, b) => b.avgPnl - a.avgPnl)[0]?.symbol,
    },
    bySymbol: results,
  };
}

// ─── حساب إحصائيات الـ Backtest ──────────────────────────────────────────
function calculateBacktestStats(symbol, trades, totalSignals, totalBars, cfg) {
  if (trades.length === 0) {
    return { symbol, totalTrades: 0, winRate: 0, totalSignals, message: 'لا توجد إشارات كافية' };
  }

  const wins   = trades.filter(t => t.won);
  const losses = trades.filter(t => !t.won);
  const pnls   = trades.map(t => t.pnlPct);

  const grossProfit  = wins.reduce((s, t) => s + t.pnlPct, 0);
  const grossLoss    = Math.abs(losses.reduce((s, t) => s + t.pnlPct, 0));
  const profitFactor = grossLoss > 0
    ? +(grossProfit / grossLoss).toFixed(2)
    : (grossProfit > 0 ? 999 : 0);

  // Max Drawdown
  let capital = cfg.initialCapital;
  let peak    = capital;
  let maxDD   = 0;
  for (const t of trades) {
    capital *= (1 + t.pnlPct / 100 * cfg.positionSizePct);
    if (capital > peak) peak = capital;
    const dd = (peak - capital) / peak * 100;
    if (dd > maxDD) maxDD = dd;
  }

  // حفظ في قاعدة البيانات
  try {
    const db = getDB();
    db.prepare(`
      INSERT INTO backtests
        (run_date, symbol, from_date, to_date, setup_filter,
         total_signals, wins, losses, win_rate, avg_pnl, max_drawdown, profit_factor, params)
      VALUES (date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      symbol,
      trades[0]?.entryBar    ? new Date(trades[0].entryBar    * 1000).toISOString().split('T')[0] : null,
      trades.at(-1)?.entryBar ? new Date(trades.at(-1).entryBar * 1000).toISOString().split('T')[0] : null,
      `score>=${cfg.minScore}`,
      totalSignals,
      wins.length,
      losses.length,
      +(wins.length / trades.length * 100).toFixed(1),
      +(ss.mean(pnls)).toFixed(2),
      +maxDD.toFixed(2),
      profitFactor,
      JSON.stringify({ ...cfg, source: 'local_db' })
    );
  } catch { /* تجاهل أخطاء الحفظ */ }

  // ── Cost breakdown ────────────────────────────────────────────────────────
  const avgCommission = +(ss.mean(trades.map(t => t.commission ?? 0))).toFixed(3);
  const avgSlippage   = +(ss.mean(trades.map(t => t.slippage   ?? 0))).toFixed(3);
  const avgGapRisk    = +(ss.mean(trades.map(t => t.gapRisk    ?? 0))).toFixed(3);
  const avgTotalCosts = +(ss.mean(trades.map(t => t.totalCosts ?? 0))).toFixed(3);
  const avgGrossPnl   = +(ss.mean(trades.map(t => t.grossPnlPct ?? t.pnlPct))).toFixed(2);

  // ── Advanced Risk-Adjusted Metrics ───────────────────────────────────────
  const avgPnl     = ss.mean(pnls);
  const stdPnl     = ss.standardDeviation(pnls);

  // Sortino Ratio: avgPnl / downside_std  (penalises only losses, not gains)
  const downsidePnls = pnls.filter(p => p < 0);
  const downsideStd  = downsidePnls.length > 1 ? ss.standardDeviation(downsidePnls) : stdPnl || 1;
  const sortinoRatio = downsideStd > 0 ? +(avgPnl / downsideStd).toFixed(3) : 0;

  // Sharpe Ratio (per-trade, risk-free = 0 for simplicity)
  const sharpeRatio  = stdPnl > 0 ? +(avgPnl / stdPnl).toFixed(3) : 0;

  // Calmar Ratio: total_return / max_drawdown  (higher = better)
  const totalReturn  = pnls.reduce((a, b) => a + b, 0);
  const calmarRatio  = maxDD > 0 ? +(totalReturn / maxDD).toFixed(3) : 0;

  // Max Drawdown Duration (bars in consecutive drawdown)
  let ddDuration = 0, curDDStart = null, maxDDDuration = 0;
  let capDD = cfg.initialCapital, peakDD = capDD;
  for (let i = 0; i < trades.length; i++) {
    capDD *= (1 + trades[i].pnlPct / 100 * cfg.positionSizePct);
    if (capDD > peakDD) { peakDD = capDD; curDDStart = null; ddDuration = 0; }
    else {
      if (curDDStart === null) curDDStart = i;
      ddDuration = i - curDDStart + 1;
      maxDDDuration = Math.max(maxDDDuration, ddDuration);
    }
  }

  // Recovery Factor: total_return / max_drawdown
  const recoveryFactor = maxDD > 0 ? +(totalReturn / maxDD).toFixed(2) : 0;

  // Expectancy per trade (in %)
  const expectancy = +((wins.length / trades.length) * ss.mean(wins.map(t => t.pnlPct) || [0])
                     + (losses.length / trades.length) * ss.mean(losses.map(t => t.pnlPct) || [0])).toFixed(2);

  return {
    symbol,
    totalBars,
    totalSignals,
    totalTrades:    trades.length,
    wins:           wins.length,
    losses:         losses.length,
    winRate:        +(wins.length / trades.length * 100).toFixed(1),
    avgWin:         wins.length   > 0 ? +(ss.mean(wins.map(t   => t.pnlPct))).toFixed(2) : 0,
    avgLoss:        losses.length > 0 ? +(ss.mean(losses.map(t => t.pnlPct))).toFixed(2) : 0,
    avgPnl:         +avgPnl.toFixed(2),
    avgGrossPnl,
    totalReturn:    +totalReturn.toFixed(2),
    profitFactor,
    maxDrawdown:    +maxDD.toFixed(2),
    maxDDDuration,
    avgHoldBars:    +(ss.mean(trades.map(t => t.holdBars))).toFixed(1),
    // ── Risk-Adjusted ─────────────────────────────────────────────────
    riskAdjusted: {
      sharpeRatio,
      sortinoRatio,    // أدق — يعاقب الخسائر فقط
      calmarRatio,     // total_return / max_drawdown
      recoveryFactor,  // أسرع من calmar للتقييم السريع
      expectancy,      // متوسط ربح/خسارة كل صفقة
      note: sortinoRatio > 0.5 ? '✅ Sortino جيد (>0.5)' :
            sortinoRatio > 0   ? '⚠️ Sortino ضعيف — راجع الاستراتيجية' :
                                 '❌ Sortino سالب — الاستراتيجية تخسر',
    },
    costBreakdown: {
      avgCommission,
      avgSlippage,
      avgGapRisk,
      avgTotalCosts,
      costDragPct: +((+avgGrossPnl) - (+avgPnl.toFixed(2))).toFixed(3),
      note: `commission(${avgCommission}%) + slippage(${avgSlippage}%) + gap(${avgGapRisk}%) = ${avgTotalCosts}% per trade`,
    },
    config:       cfg,
    sampleTrades: trades.slice(0, 5),
  };
}

export default { backtestSymbol, backtestPortfolio };
