/**
 * EGX Historical Pattern Analyzer
 * =================================
 * يحلل كامل البيانات التاريخية المحفوظة ويُجري:
 *   1. Walkforward Backtest — تطبيق فلاتر scorer على كل 5 شمعات متتالية
 *   2. Pattern Discovery — أي volume ratio / setup type يعمل فعلياً؟
 *   3. Threshold Optimization — ما هو أفضل حجم للدخول؟
 *   4. تحديث TRADING_LESSONS.md بالأرقام الحقيقية
 *
 * التشغيل:
 *   node scripts/analyze_egx_history.mjs
 */

import * as ss from 'simple-statistics';
import { getOHLCV, getHistoryStats, getDB,
         scoreSetup, rankStocks }           from '../src/egx/index.js';
import { readFileSync, writeFileSync }       from 'fs';

const TODAY = new Date().toISOString().split('T')[0];
const WINDOW = 5;   // حجم نافذة الشمعات للتحليل
const HOLD   = 5;   // عدد الشمعات للانتظار بعد الإشارة

// ─── جلب قائمة الأسهم المتاحة ──────────────────────────────────────────────
function getAvailableSymbols() {
  const db = getDB();
  return db.prepare(
    "SELECT symbol, total_bars FROM stock_universe WHERE status='fetched' AND total_bars >= 20 ORDER BY total_bars DESC"
  ).all();
}

// ─── محاكاة النتيجة بعد إشارة ──────────────────────────────────────────────
// بعد الدخول، نتتبع لـ HOLD شمعات ونحسب:
// - هل وصل T1؟
// - هل ضرب SL؟
// - ما الـ PnL عند النهاية؟
function simulateTrade(bars, signalIdx, levels) {
  const sl = levels?.sl ?? levels?.stopLoss;
  const t1 = levels?.t1;
  if (!levels || !sl || !t1) return null;
  const entry   = levels.entryHigh ?? bars[signalIdx].close;
  const maxHold = Math.min(signalIdx + HOLD, bars.length - 1);

  for (let i = signalIdx + 1; i <= maxHold; i++) {
    const bar = bars[i];
    if (bar.low  <= sl) return { result: 'loss', exit: sl, pnl: (sl - entry) / entry * 100 };
    if (bar.high >= t1) return { result: 'win',  exit: t1, pnl: (t1 - entry) / entry * 100 };
  }
  // انتهت فترة الـ hold دون T1 أو SL
  const exitClose = bars[maxHold].close;
  const pnl       = (exitClose - entry) / entry * 100;
  return { result: pnl >= 0 ? 'breakeven' : 'loss', exit: exitClose, pnl };
}

// ─── Walkforward على سهم واحد ─────────────────────────────────────────────
function walkthroughSymbol(symbol) {
  const allBars = getOHLCV(symbol, 500);
  if (allBars.length < WINDOW + HOLD + 5) return null;

  const signals = [];

  // تمرير نافذة متحركة على كل التاريخ
  for (let i = WINDOW; i <= allBars.length - HOLD - 1; i++) {
    const windowBars = allBars.slice(i - WINDOW, i);   // آخر WINDOW شمعات
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
      all_bars:    allBars.slice(0, i),  // تاريخ كامل حتى هذه اللحظة فقط
    };

    const score = scoreSetup(stockData);
    if (score.rejected) continue;          // مرفوض بالفلاتر
    if (score.score < 55)  continue;       // نقاط منخفضة جداً

    // محاكاة الصفقة
    const trade = simulateTrade(allBars, i - 1, score.levels);
    if (!trade) continue;

    signals.push({
      symbol,
      bar_idx:    i,
      bar_date:   new Date((last.time || 0) * 1000).toISOString().split('T')[0],
      setup_type: score.setupType,
      score:      score.score,
      volume_ratio:   score.volumeRatio,
      close_position: score.closePosition,
      pnl:         trade.pnl,
      result:      trade.result,
      entry:       score.levels?.entryHigh,
      sl:          score.levels?.sl,       // Bug fix: كان stopLoss (undefined دائماً)
      t1:          score.levels?.t1,
    });
  }

  return signals;
}

// ─── Main Analyzer ─────────────────────────────────────────────────────────
async function main() {
  const symbols = getAvailableSymbols();
  if (symbols.length === 0) {
    console.log('❌ لا توجد بيانات تاريخية. شغّل fetch_egx_history.mjs أولاً.');
    process.exit(1);
  }

  process.stdout.write(`
╔══════════════════════════════════════════════════════════════╗
║       EGX Walkforward Backtest + Pattern Discovery           ║
╠══════════════════════════════════════════════════════════════╣
║  الأسهم: ${String(symbols.length).padEnd(4)} | نافذة: ${WINDOW} شمعات | Hold: ${HOLD} شمعات           ║
╚══════════════════════════════════════════════════════════════╝\n`);

  let allSignals = [];
  let processed  = 0;

  for (const { symbol, total_bars } of symbols) {
    process.stdout.write(`  تحليل ${symbol.padEnd(8)} (${total_bars} شمعة)...\r`);
    const sigs = walkthroughSymbol(symbol);
    if (sigs && sigs.length > 0) {
      allSignals = allSignals.concat(sigs);
      process.stdout.write(`  ✓ ${symbol.padEnd(8)}: ${sigs.length} إشارة\n`);
    }
    processed++;
  }

  if (allSignals.length === 0) {
    console.log('\n❌ لا توجد إشارات — تحقق من البيانات.');
    return;
  }

  // ─── إحصائيات شاملة ──────────────────────────────────────────────────
  const wins      = allSignals.filter(s => s.result === 'win');
  const losses    = allSignals.filter(s => s.result === 'loss');
  const breakeven = allSignals.filter(s => s.result === 'breakeven');
  const winRate   = wins.length / allSignals.length * 100;
  const avgWin    = wins.length    > 0 ? ss.mean(wins.map(s=>s.pnl))    : 0;
  const avgLoss   = losses.length  > 0 ? ss.mean(losses.map(s=>s.pnl))  : 0;
  const profitFactor = avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : Infinity;

  // ─── تحليل حسب نوع الإعداد ────────────────────────────────────────────
  const bySetup = {};
  for (const s of allSignals) {
    const k = s.setup_type;
    if (!bySetup[k]) bySetup[k] = [];
    bySetup[k].push(s);
  }

  const setupStats = Object.entries(bySetup).map(([setup, sigs]) => {
    const w = sigs.filter(s=>s.result==='win');
    const l = sigs.filter(s=>s.result==='loss');
    return {
      setup,
      count: sigs.length,
      win_rate: (w.length / sigs.length * 100).toFixed(1),
      avg_pnl:  ss.mean(sigs.map(s=>s.pnl)).toFixed(2),
      avg_win:  w.length > 0 ? ss.mean(w.map(s=>s.pnl)).toFixed(2)  : '—',
      avg_loss: l.length > 0 ? ss.mean(l.map(s=>s.pnl)).toFixed(2)  : '—',
    };
  }).sort((a,b) => parseFloat(b.win_rate) - parseFloat(a.win_rate));

  // ─── تحليل بناءً على Volume Ratio ─────────────────────────────────────
  const volBuckets = [
    { label: '< 1.5x',  min: 0,   max: 1.5 },
    { label: '1.5–2x',  min: 1.5, max: 2.0 },
    { label: '2–2.5x',  min: 2.0, max: 2.5 },
    { label: '2.5–3x',  min: 2.5, max: 3.0 },
    { label: '≥ 3x',    min: 3.0, max: 999 },
  ];

  const volStats = volBuckets.map(bucket => {
    const sigs = allSignals.filter(s =>
      (s.volume_ratio ?? 0) >= bucket.min && (s.volume_ratio ?? 0) < bucket.max
    );
    if (sigs.length === 0) return null;
    const w = sigs.filter(s=>s.result==='win');
    return {
      label:    bucket.label,
      count:    sigs.length,
      win_rate: (w.length / sigs.length * 100).toFixed(1),
      avg_pnl:  ss.mean(sigs.map(s=>s.pnl)).toFixed(2),
    };
  }).filter(Boolean);

  // ─── أفضل الأسهم ──────────────────────────────────────────────────────
  const bySymbol = {};
  for (const s of allSignals) {
    if (!bySymbol[s.symbol]) bySymbol[s.symbol] = [];
    bySymbol[s.symbol].push(s);
  }
  const symbolStats = Object.entries(bySymbol).map(([sym, sigs]) => {
    const w = sigs.filter(s=>s.result==='win');
    return { sym, count: sigs.length, win_rate: (w.length/sigs.length*100).toFixed(0), avg_pnl: ss.mean(sigs.map(s=>s.pnl)).toFixed(2) };
  }).filter(s => s.count >= 3).sort((a,b) => parseFloat(b.win_rate) - parseFloat(a.win_rate));

  // ─── تحليل Close Position ──────────────────────────────────────────────
  const cpBuckets = [
    { label: 'ثلث سفلي (< 0.33)',  min: 0,    max: 0.33 },
    { label: 'وسط (0.33–0.66)',     min: 0.33, max: 0.66 },
    { label: 'ثلث علوي (> 0.66)',  min: 0.66, max: 1.0  },
  ];

  const cpStats = cpBuckets.map(b => {
    const sigs = allSignals.filter(s => (s.close_position??0.5) >= b.min && (s.close_position??0.5) < b.max);
    if (sigs.length === 0) return null;
    const w = sigs.filter(s=>s.result==='win');
    return { label: b.label, count: sigs.length, win_rate: (w.length/sigs.length*100).toFixed(1) };
  }).filter(Boolean);

  // ─── طباعة التقرير ────────────────────────────────────────────────────
  console.log(`\n╔══════════════════════════════════════════════════════════════╗`);
  console.log(`║            WALKFORWARD BACKTEST RESULTS                      ║`);
  console.log(`╠══════════════════════════════════════════════════════════════╣`);
  console.log(`║  إجمالي الإشارات: ${String(allSignals.length).padEnd(6)} | Win Rate: ${winRate.toFixed(1)}%           ║`);
  console.log(`║  🏆 فائزون: ${String(wins.length).padEnd(6)} | 💔 خاسرون: ${String(losses.length).padEnd(6)} | 🟡 متعادل: ${String(breakeven.length).padEnd(5)} ║`);
  console.log(`║  متوسط ربح: +${String(avgWin.toFixed(1)).padEnd(6)}% | متوسط خسارة: ${String(avgLoss.toFixed(1)).padEnd(7)}%        ║`);
  console.log(`║  Profit Factor: ${String(profitFactor.toFixed(2)).padEnd(6)}                                   ║`);
  console.log(`╚══════════════════════════════════════════════════════════════╝\n`);

  console.log('📊 الأداء حسب نوع الإعداد:');
  console.log(`${'Setup Type'.padEnd(38)} ${'n'.padEnd(5)} ${'WR%'.padEnd(7)} ${'avg PnL'.padEnd(9)} ${'avg Win'.padEnd(9)} avg Loss`);
  console.log('─'.repeat(80));
  for (const s of setupStats) {
    const icon = parseFloat(s.win_rate) >= 60 ? '✅' : (parseFloat(s.win_rate) >= 45 ? '🟡' : '❌');
    console.log(`${icon} ${s.setup.padEnd(36)} ${String(s.count).padEnd(5)} ${String(s.win_rate+'%').padEnd(7)} ${String(s.avg_pnl+'%').padEnd(9)} ${String(s.avg_win+'%').padEnd(9)} ${s.avg_loss}%`);
  }

  console.log('\n📈 الأداء حسب Volume Ratio (مهم لضبط الفلاتر):');
  console.log(`${'Volume Range'.padEnd(15)} ${'n'.padEnd(6)} ${'Win Rate'.padEnd(10)} avg PnL`);
  console.log('─'.repeat(45));
  for (const v of volStats) {
    const icon = parseFloat(v.win_rate) >= 60 ? '✅' : (parseFloat(v.win_rate) >= 45 ? '🟡' : '❌');
    console.log(`${icon} ${v.label.padEnd(14)} ${String(v.count).padEnd(6)} ${String(v.win_rate+'%').padEnd(10)} ${v.avg_pnl}%`);
  }

  console.log('\n📐 الأداء حسب موضع الإغلاق في الشمعة:');
  for (const c of cpStats) {
    const icon = parseFloat(c.win_rate) >= 60 ? '✅' : '❌';
    console.log(`  ${icon} ${c.label.padEnd(25)} n=${c.count} | WR: ${c.win_rate}%`);
  }

  console.log('\n🏆 أفضل الأسهم تاريخياً (≥ 3 إشارات):');
  console.log(`${'Symbol'.padEnd(8)} ${'n'.padEnd(5)} ${'Win Rate'.padEnd(10)} avg PnL`);
  console.log('─'.repeat(35));
  for (const s of symbolStats.slice(0, 15)) {
    const icon = parseFloat(s.win_rate) >= 65 ? '🌟' : (parseFloat(s.win_rate) >= 50 ? '✅' : '🟡');
    console.log(`${icon} ${s.sym.padEnd(8)} ${String(s.count).padEnd(5)} ${String(s.win_rate+'%').padEnd(10)} ${s.avg_pnl}%`);
  }

  // ─── حفظ نتائج الـ backtest في قاعدة البيانات ────────────────────────
  const db = getDB();
  const insertBT = db.prepare(`
    INSERT INTO backtests (run_date, symbol, setup_filter, total_signals, wins, losses, win_rate, avg_pnl, profit_factor, params)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  insertBT.run(TODAY, 'ALL_EGX', 'score>=55', allSignals.length, wins.length, losses.length,
    +winRate.toFixed(1), +ss.mean(allSignals.map(s=>s.pnl)).toFixed(2), +profitFactor.toFixed(2),
    JSON.stringify({ window: WINDOW, hold: HOLD, min_score: 55 }));

  // حفظ per-setup مع حساب profit_factor الصحيح
  for (const s of setupStats) {
    const sWins   = bySetup[s.setup]?.filter(x => x.result === 'win')  ?? [];
    const sLosses = bySetup[s.setup]?.filter(x => x.result !== 'win')  ?? [];
    const grossP  = sWins.reduce((acc, x) => acc + x.pnl, 0);
    const grossL  = Math.abs(sLosses.reduce((acc, x) => acc + x.pnl, 0));
    const pf      = grossL > 0 ? +(grossP / grossL).toFixed(2) : (grossP > 0 ? 99 : 0);

    insertBT.run(TODAY, s.setup, s.setup,
      s.count,
      Math.round(s.count * parseFloat(s.win_rate) / 100),
      Math.round(s.count * (1 - parseFloat(s.win_rate) / 100)),
      parseFloat(s.win_rate), parseFloat(s.avg_pnl),
      pf,                                // Bug fix: profit_factor محسوب
      JSON.stringify({ setup_type: s.setup, window: WINDOW, hold: HOLD }));
  }

  console.log(`\n✅ نتائج الـ backtest حُفظت في data/egx_trading.db`);
  console.log(`\n💡 توصيات تحسين القواعد (من ${allSignals.length} إشارة):`);

  // إيجاد أفضل volume ratio
  const bestVol = volStats.reduce((a,b) => parseFloat(a.win_rate) > parseFloat(b.win_rate) ? a : b);
  console.log(`  ⚡ أفضل Volume Range: ${bestVol.label} → Win Rate ${bestVol.win_rate}%`);

  // أفضل setup type
  if (setupStats.length > 0) {
    console.log(`  🏆 أفضل Setup: ${setupStats[0].setup} → WR ${setupStats[0].win_rate}%`);
  }

  // هل يجب رفع أو خفض الـ score threshold؟
  const above65  = allSignals.filter(s => s.score >= 65);
  const wr65     = above65.length > 0 ? (above65.filter(s=>s.result==='win').length / above65.length * 100).toFixed(1) : 0;
  console.log(`  📊 الإشارات ≥ 65 نقطة (${above65.length}): Win Rate ${wr65}%`);

  const above75  = allSignals.filter(s => s.score >= 75);
  const wr75     = above75.length > 0 ? (above75.filter(s=>s.result==='win').length / above75.length * 100).toFixed(1) : 0;
  console.log(`  📊 الإشارات ≥ 75 نقطة (${above75.length}): Win Rate ${wr75}%`);

  console.log('\n📁 للتفاصيل الكاملة: data/egx_trading.db (جدول backtests)');
}

main().catch(err => {
  console.error('💥 خطأ:', err.message);
  process.exit(1);
});
