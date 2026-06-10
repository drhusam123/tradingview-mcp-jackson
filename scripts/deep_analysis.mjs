/**
 * EGX Deep Analysis Report
 * ==========================
 * تقرير تحليل عميق يجمع:
 *   1. danfo-js  — DataFrame analysis (distribution, momentum ranking)
 *   2. @stdlib   — Advanced stats (Hurst, VaR, Autocorrelation, Monte Carlo)
 *   3. Python    — Full signal backtest (68K شمعة) + KS test + market breadth
 *
 * التشغيل:
 *   node scripts/deep_analysis.mjs
 *   node scripts/deep_analysis.mjs --section stats     (إحصاء فقط)
 *   node scripts/deep_analysis.mjs --section backtest  (backtest فقط)
 *   node scripts/deep_analysis.mjs --section risk      (مخاطر فقط)
 *   node scripts/deep_analysis.mjs --section regime    (regime detection)
 *   node scripts/deep_analysis.mjs --symbol PHDC       (تحليل سهم واحد)
 *   node scripts/deep_analysis.mjs --json              (إخراج JSON)
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import {
  // danfo-js
  analyzeIndicatorsDistribution,
  topMomentumStocks,
  buildReturnCorrelationDF,
  // @stdlib + simple-statistics
  testNormality,
  quickVaR,
  calculateHurst,
  analyzeMarketRegime,
  analyzeAutocorrelation,
  quickMonteCarloFromDB,
  // Python bridge
  checkPythonBridge,
  pythonSignalBacktest,
  pythonReturnAnalysis,
  pythonSectorMomentum,
  pythonRollingStats,
  // Learning
  quickComboScan,
  analyzeScoreCalibration,
  EGX_UNIVERSE,
  getDB,
} from '../src/egx/index.js';

const SECTION   = (() => { const i = process.argv.indexOf('--section'); return i >= 0 ? process.argv[i+1] : 'all'; })();
const SYMBOL    = (() => { const i = process.argv.indexOf('--symbol');  return i >= 0 ? process.argv[i+1] : null;  })();
const JSON_MODE = process.argv.includes('--json');

const w  = (s) => process.stdout.write(s);
const wl = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 65) => wl(c.repeat(n));
const h1  = (t) => { sep(); wl(`  ${t}`); sep(); };
const h2  = (t) => { wl(); wl(`  ── ${t} ──`); };

// ─── تنسيق أعداد ─────────────────────────────────────────────────────────
const pct = (n, d = 2) => n != null ? `${n >= 0 ? '+' : ''}${(+n).toFixed(d)}%` : '?';
const fmt = (n, d = 2) => n != null ? (+n).toFixed(d) : '?';

// ══════════════════════════════════════════════════════════════════════════
async function main() {
  const t0      = Date.now();
  const results = {};

  h1('🔬 EGX Deep Analysis Report — Dr. Husam');
  wl(`  التاريخ : ${new Date().toISOString().split('T')[0]}`);
  wl(`  القسم   : ${SECTION}${SYMBOL ? ` | السهم: ${SYMBOL}` : ''}`);
  sep('─');

  // ── Python Health Check ─────────────────────────────────────────────
  const pyHealth = await checkPythonBridge();
  wl(`  Python  : ${pyHealth.message}`);

  // ══ SECTION: STATS ═══════════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'stats') {
    h2('📊 توزيع المؤشرات الحالية (danfo-js)');
    const dist = analyzeIndicatorsDistribution();
    if (!dist.error) {
      wl(`  الأسهم المحللة : ${dist.totalSymbols}`);
      const r = dist.summary.rsi14;
      if (r) wl(`  RSI14  : mean=${fmt(r.mean)} | median=${fmt(r.median)} | p25=${fmt(r.p25)} | p75=${fmt(r.p75)}`);
      const a = dist.summary.adx14;
      if (a) wl(`  ADX14  : mean=${fmt(a.mean)} | median=${fmt(a.median)} | p25=${fmt(a.p25)} | p75=${fmt(a.p75)}`);
      const v = dist.summary.vol_ratio_20;
      if (v) wl(`  Vol/20 : mean=${fmt(v.mean)}x | median=${fmt(v.median)}x | p75=${fmt(v.p75)}x`);

      wl();
      wl(`  RSI ≤ 35 اليوم  : ${dist.oversold.length} سهم`);
      wl(`  OBV Bullish      : ${dist.bullishOBV.length} سهم`);
      wl(`  🔥 RSI+OBV Combo : ${dist.comboSignals.length} سهم (WR=69% إحصائياً)`);
      if (dist.comboSignals.length > 0) {
        wl();
        wl('  أسهم الـ COMBO اليوم:');
        dist.comboSignals.forEach(s => wl(`    ★ ${s.symbol}: RSI=${s.rsi} ADX=${s.adx} Vol=${s.vol}x → ${s.note}`));
      }
    }
    results.dist = dist;

    h2('🚀 أفضل momentum 5d (danfo-js)');
    const top = topMomentumStocks('momentum_5d', 10);
    const hdr = `${'#'.padEnd(3)} ${'رمز'.padEnd(8)} ${'5d'.padEnd(8)} ${'10d'.padEnd(8)} ${'RSI'.padEnd(7)} ${'ADX'.padEnd(7)} ${'Vol'.padEnd(6)}`;
    wl('  ' + hdr);
    wl('  ' + '─'.repeat(hdr.length));
    top.forEach((s, i) => {
      wl(`  ${String(i+1).padEnd(3)} ${s.symbol.padEnd(8)} ${s.momentum.padEnd(8)} ${(s.momentum10 ?? '?').padEnd(8)} ${(s.rsi ?? '?').padEnd(7)} ${(s.adx ?? '?').padEnd(7)} ${s.volRatio}`);
    });
    results.momentum = top;
  }

  // ══ SECTION: RISK ════════════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'risk') {
    h2('📐 تحليل المخاطر (@stdlib)');

    // VaR على كل السوق
    const var95  = quickVaR(SYMBOL ?? null, 0.95, 100000);
    const var99  = quickVaR(SYMBOL ?? null, 0.99, 100000);
    wl(`  السهم   : ${SYMBOL ?? 'EGX (sample)'}`);
    wl(`  VaR(95%): ${var95.var1d}% → خسارة يومية بـ ${var95.varCapital?.toLocaleString()} جنيه على 100K`);
    wl(`  VaR(99%): ${var99.var1d}%`);
    wl(`  CVaR(95%): ${var95.cvar1d}% (متوسط أسوأ 5% الأيام)`);
    wl(`  Max Drawdown: ${var95.maxDrawdownPct}%`);
    if (var95.sortinoRatio) wl(`  Sortino Ratio: ${var95.sortinoRatio}`);
    results.risk = { var95, var99 };

    // KS Test
    h2('📏 Normality Test (@stdlib KS)');
    const norm = testNormality(SYMBOL ?? null, 3000);
    if (!norm.error) {
      wl(`  Kurtosis      : ${norm.kurtosis} (Normal=3, EGX~10-27)`);
      wl(`  Excess Kurt.  : ${norm.excessKurtosis}`);
      wl(`  Skewness      : ${norm.skewness}`);
      wl(`  Fat tail 3σ   : ${norm.fatTailPct}% (Normal يتوقع 0.3%)`);
      wl(`  KS p-value    : ${norm.ksPValue ?? 0} → ${norm.isNormalDist ? '✅ Normal' : '❌ ليس Normal'}`);
      norm.interpretation.forEach(l => wl(`  ${l}`));
    }
    results.normality = norm;

    // Monte Carlo
    h2('🎲 Monte Carlo Simulation (@stdlib + bootstrap)');
    const mc = quickMonteCarloFromDB(SYMBOL ?? null, 100000, 22);
    if (!mc.error) {
      wl(`  رأس المال 100,000 جنيه | 22 يوم | 1000 سيناريو`);
      wl(`  احتمال الربح : ${mc.probProfit}%`);
      wl(`  احتمال خسارة >5% : ${mc.probLoss5pct}%`);
      wl(`  الأسوأ 5%  : ${mc.results.worst5pct?.toLocaleString()} جنيه (P&L: ${mc.pnl.worst5pct?.toLocaleString()})`);
      wl(`  المتوسط    : ${mc.results.median?.toLocaleString()} جنيه (P&L: ${mc.pnl.median?.toLocaleString()})`);
      wl(`  الأفضل 5%  : ${mc.results.best5pct?.toLocaleString()} جنيه (P&L: ${mc.pnl.best5pct?.toLocaleString()})`);
    }
    results.monteCarlo = mc;
  }

  // ══ SECTION: REGIME ══════════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'regime') {
    h2('🔭 Regime Detection — Hurst Exponent');
    const sample = SYMBOL ? [SYMBOL] : EGX_UNIVERSE.slice(0, 50);
    const regime = analyzeMarketRegime(sample);
    if (regime.summary.totalAnalyzed > 0) {
      wl(`  أسهم محللة    : ${regime.summary.totalAnalyzed}`);
      wl(`  متوسط Hurst  : ${regime.summary.avgHurst}`);
      wl(`  Trending      : ${regime.summary.trendingCount} سهم`);
      wl(`  Mean-Reverting: ${regime.summary.meanRevertingCount} سهم`);
      wl(`  Random Walk   : ${regime.summary.randomCount} سهم`);
      wl(`  النظام السائد : ${regime.summary.dominantRegime}`);
      if (regime.meanReverting.length > 0) {
        wl(`  أفضل Mean-Reverting (RSI+OBV أقوى فيها):`);
        regime.meanReverting.slice(0, 5).forEach(s => wl(`    ${s.symbol}: H=${s.hurst}`));
      }
    }
    results.regime = regime.summary;

    if (SYMBOL) {
      h2(`📈 Autocorrelation — ${SYMBOL}`);
      const acf = analyzeAutocorrelation(SYMBOL, 10);
      if (!acf.error) {
        acf.interpretation.forEach(l => wl(`  ${l}`));
        wl(`  ACF: ${acf.acf?.slice(0,5).map(a => `lag${a.lag}=${a.acf}`).join(' | ')}`);
      }
    }
  }

  // ══ SECTION: BACKTEST (Python) ═══════════════════════════════════════
  if ((SECTION === 'all' || SECTION === 'backtest') && pyHealth.available) {
    h2('⚡ Signal Backtest على 68K شمعة (Python/pandas)');
    wl('  جارٍ الحساب...');
    try {
      const bt = await pythonSignalBacktest(35);
      if (bt.success && bt.signals) {
        wl(`  إجمالي الصفوف : ${bt.total_rows?.toLocaleString()} | الأسهم: ${bt.symbols}`);
        wl(`  الفترة        : ${bt.date_range}`);
        wl();
        const hdr2 = `${'Signal'.padEnd(38)} ${'n'.padStart(7)} ${'T+1'.padStart(8)} ${'T+3'.padStart(8)} ${'T+5'.padStart(8)} ${'WR%'.padStart(6)}`;
        wl('  ' + hdr2);
        wl('  ' + '─'.repeat(hdr2.length));
        bt.signals.forEach(s => {
          if (s.insufficient) return;
          const n    = String(s.count?.toLocaleString()).padStart(7);
          const t1   = pct(s.t1_avg).padStart(8);
          const t3   = pct(s.t3_avg).padStart(8);
          const t5   = pct(s.t5_avg).padStart(8);
          const wr   = String(s.t5_wr + '%').padStart(6);
          wl(`  ${s.name.slice(0,38).padEnd(38)} ${n} ${t1} ${t3} ${t5} ${wr}`);
        });
      } else {
        wl(`  ⚠️ ${bt.error}`);
      }
      results.backtest = bt;
    } catch (e) {
      wl(`  ❌ خطأ في backtest: ${e.message}`);
    }

    h2('🌍 Market Breadth (Python/pandas)');
    try {
      const mb = await pythonSectorMomentum();
      if (mb.success) {
        wl(`  نبضة السوق    : ${mb.market_breadth?.market_tone}`);
        if (mb.market_breadth?.above_ema200_pct != null)
          wl(`  % فوق EMA200  : ${mb.market_breadth.above_ema200_pct}%`);
        wl(`  % إيجابي 5d   : ${mb.market_breadth?.pct_positive_5d}%`);
        wl(`  متوسط momentum: ${pct(mb.market_breadth?.avg_momentum_5d)}`);
        wl();
        wl(`  التوزيع:`);
        for (const [k, v] of Object.entries(mb.regime_distribution ?? {})) {
          wl(`    ${k.padEnd(20)}: ${v}`);
        }
        if (mb.top10_momentum?.length) {
          wl(`\n  أقوى 5 أسهم momentum:`);
          mb.top10_momentum.slice(0,5).forEach((s,i) =>
            wl(`    ${i+1}. ${s.symbol}: 5d=${pct(s.momentum_5d,1)} | RSI=${fmt(s.rsi14,1)} | ADX=${fmt(s.adx14,1)}`));
        }
      }
      results.breadth = mb;
    } catch (e) {
      wl(`  ❌ خطأ: ${e.message}`);
    }
  } else if (SECTION === 'backtest' && !pyHealth.available) {
    wl('  ⚠️ Python غير متاح — تحقق من تثبيت pandas/numpy/scipy');
  }

  // ══ القسم الخاص بالسهم ═══════════════════════════════════════════════
  if (SYMBOL && pyHealth.available) {
    h2(`📌 تحليل مفصّل — ${SYMBOL} (Python rolling stats)`);
    try {
      const rs = await pythonRollingStats(SYMBOL, 20);
      if (rs.success && rs.current_rolling) {
        const r = rs.current_rolling;
        wl(`  Rolling Mean(20d) : ${pct(r.mean)}`);
        wl(`  Rolling Std(20d)  : ${fmt(r.std)}%`);
        wl(`  Rolling Sharpe    : ${fmt(r.sharpe)}`);
      }
    } catch (e) { /* تجاهل */ }
  }

  // ══ Quick Combo Scan ═════════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'stats') {
    h2('🔥 Quick Combo Scan (من الكاش — فوري)');
    const combo = quickComboScan();
    wl(`  ${combo.note}`);
    if (combo.oversoldRsi.length > 0) {
      wl(`  RSI≤35 اليوم:`);
      combo.oversoldRsi.slice(0,5).forEach(s =>
        wl(`    ${s.symbol}: RSI=${s.rsi14?.toFixed(1)} ADX=${s.adx14?.toFixed(1)}`));
    }
    if (combo.bbOversold.length > 0) {
      wl(`  BB Oversold:`);
      combo.bbOversold.slice(0,3).forEach(s => wl(`    ${s.symbol}: BB_pos=${s.bb_position?.toFixed(2)}`));
    }
  }

  // ══ الخلاصة ══════════════════════════════════════════════════════════
  const elapsed = Math.round((Date.now() - t0) / 1000);
  sep();
  wl(`  ⏱️  وقت التحليل : ${elapsed}s`);
  wl(`  📦 المكتبات    : danfo-js ${results.dist ? '✅' : '⚠️'} | @stdlib ✅ | Python ${pyHealth.available ? '✅' : '❌'}`);
  sep();

  if (JSON_MODE) {
    process.stdout.write('\n' + JSON.stringify(results, null, 2) + '\n');
  }
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
