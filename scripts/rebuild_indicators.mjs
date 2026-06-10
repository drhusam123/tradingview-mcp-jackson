/**
 * EGX Indicators Cache Builder
 * ==============================
 * يحسب المؤشرات التقنية لكل سهم ويحفظها في indicators_cache
 * يُشغَّل مرة واحدة بعد daily_update (أو عند أول تشغيل)
 *
 * التشغيل:
 *   node scripts/rebuild_indicators.mjs              (كل الأسهم)
 *   node scripts/rebuild_indicators.mjs --symbol PHDC (سهم واحد)
 *   node scripts/rebuild_indicators.mjs --since 2026-05-01 (منذ تاريخ)
 *   node scripts/rebuild_indicators.mjs --stats      (إحصائيات الكاش فقط)
 *
 * الفائدة:
 *   - scan_today --cache-only: يقرأ من هنا بدل إعادة الحساب → سرعة 10x
 *   - getSignalsFromCache(): SQL فقط — لا حسابات في الميموري
 *   - active_signals_today VIEW: RSI_OBV_COMBO مكتشف بـ SELECT واحد
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import { getOHLCV, saveIndicatorsCache, getIndicatorsCacheStats, getDB,
         calculateIndicators, EGX_UNIVERSE } from '../src/egx/index.js';

const SINGLE_SYM = (() => { const i = process.argv.indexOf('--symbol'); return i >= 0 ? process.argv[i+1] : null; })();
const SINCE_DATE = (() => { const i = process.argv.indexOf('--since');  return i >= 0 ? process.argv[i+1] : null; })();
const STATS_ONLY = process.argv.includes('--stats');
const BARS_LIMIT = 500;  // كافٍ لـ EMA200 + ATR14 + OBV divergence

function formatDate(unixSec) {
  return new Date(unixSec * 1000).toISOString().split('T')[0];
}

function progressBar(pct, w = 28) {
  const f = Math.round(pct / 100 * w);
  return '[' + '█'.repeat(f) + '░'.repeat(w - f) + ']';
}

async function main() {
  if (STATS_ONLY) { printStats(); return; }

  const targets = SINGLE_SYM ? [SINGLE_SYM] : [...new Set(EGX_UNIVERSE)];
  const startTime = Date.now();
  const results = { ok: 0, skip: 0, err: 0 };

  process.stdout.write(`
╔═══════════════════════════════════════════════════════════════╗
║         EGX Indicators Cache Builder — Dr. Husam              ║
╠═══════════════════════════════════════════════════════════════╣
║  الأسهم : ${String(targets.length).padEnd(4)} سهم | حد الشمعات: ${BARS_LIMIT}               ║
╚═══════════════════════════════════════════════════════════════╝\n`);

  for (let i = 0; i < targets.length; i++) {
    const sym = targets[i];
    const pct = Math.round((i / targets.length) * 100);
    process.stdout.write(`\r${progressBar(pct)} ${String(i+1).padStart(3)}/${targets.length}  ${sym.padEnd(7)} `);

    try {
      const bars = getOHLCV(sym, BARS_LIMIT);
      if (!bars || bars.length < 30) { results.skip++; continue; }

      const ind = calculateIndicators(bars);
      if (!ind) { results.skip++; continue; }

      // نحفظ ليوم آخر شمعة متاحة
      const lastBar  = bars[bars.length - 1];
      const barDate  = formatDate(lastBar.time);

      // إضافة خصائص مساعدة للـ save
      ind.lastClose     = lastBar.close;
      ind.rsi           = ind.rsi;          // simple-statistics RSI already a number
      ind.closePosition = (lastBar.close - lastBar.low) / (lastBar.high - lastBar.low || 1);

      // Fix: map ema20val/ema50val/ema200val → ema20/ema50/ema200 for saveIndicatorsCache
      // saveIndicatorsCache reads ind.ema20 (not ind.ema20val) to compute above_ema20
      ind.ema10  = ind.ema10val  ?? null;
      ind.ema20  = ind.ema20val  ?? null;
      ind.ema50  = ind.ema50val  ?? null;
      ind.ema200 = ind.ema200val ?? null;

      // RSI slope 3d: (RSI_today - RSI_3_bars_ago) / 3 — momentum direction feature
      // Used in get_technical_score() RSI slope component (±10 pts)
      const rsiArr = ind.arrays?.rsi;
      if (rsiArr && rsiArr.length >= 4) {
        const rsiNow  = rsiArr[rsiArr.length - 1];
        const rsi3ago = rsiArr[rsiArr.length - 4];
        ind.rsiSlope3d = (rsiNow != null && rsi3ago != null)
          ? +((rsiNow - rsi3ago) / 3).toFixed(3)
          : null;
      } else {
        ind.rsiSlope3d = null;
      }

      // momentum — using price-continuity-adjusted closes to handle data errors
      // EGX circuit breakers cap daily moves at ±20%; clip to ±25% to remove unit
      // mismatches (e.g. ZMID: 0.39→6.09 on 2026-05-18 = +1461% data error).
      // Build adjCloses: forward-propagate, clipping any single-bar jump > ±25%.
      // (Added 2026-05-23 — parallel to the ±25% clip in egx_ml_trainer._compute_indicators)
      {
        const adjCloses = [bars[0].close];
        for (let i = 1; i < bars.length; i++) {
          const prev = adjCloses[adjCloses.length - 1];
          if (prev > 0) {
            const rawRet = (bars[i].close - prev) / prev;
            const clipped = Math.max(-0.25, Math.min(0.25, rawRet));
            adjCloses.push(+(prev * (1 + clipped)).toFixed(4));
          } else {
            adjCloses.push(bars[i].close);
          }
        }
        const lastAdj = adjCloses[adjCloses.length - 1];
        if (bars.length >= 6)  ind.momentum5d  = +((lastAdj / adjCloses[adjCloses.length-6]  - 1) * 100).toFixed(2);
        if (bars.length >= 11) ind.momentum10d = +((lastAdj / adjCloses[adjCloses.length-11] - 1) * 100).toFixed(2);
        if (bars.length >= 21) ind.momentum20d = +((lastAdj / adjCloses[adjCloses.length-21] - 1) * 100).toFixed(2);
      }

      // vol_ratio_20 — median بدل mean (مقاوم لتضخيم أيام الضخّ السابقة)
      const volArr = bars.slice(-21, -1).map(b => b.volume).filter(v => v > 0);
      if (volArr.length > 0) {
        const sorted = [...volArr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        const medVol20 = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
        ind.volumeRatio20 = medVol20 > 0 ? +(lastBar.volume / medVol20).toFixed(2) : null;
      }

      // ATH proximity (last BARS_LIMIT bars)
      const athPrice = Math.max(...bars.map(b => b.high));
      ind.athProximity = athPrice > 0 ? +((athPrice - lastBar.close) / athPrice).toFixed(4) : null;

      // BB position: position within band
      if (ind.bollingerBands) {
        const range = ind.bollingerBands.upper - ind.bollingerBands.lower;
        ind.bollingerBands.position = range > 0
          ? +((lastBar.close - ind.bollingerBands.lower) / range).toFixed(3)
          : 0.5;
        ind.bollingerBands.width = range > 0
          ? +(range / ind.bollingerBands.middle).toFixed(4)
          : null;
      }

      saveIndicatorsCache(sym, barDate, ind);
      results.ok++;

    } catch (err) {
      results.err++;
      // process.stderr.write(`\n⚠️ ${sym}: ${err.message?.slice(0,50)}\n`);
    }
  }

  const elapsed = Math.round((Date.now() - startTime) / 1000);
  process.stdout.write(`\n\n`);
  process.stdout.write(`╔═══════════════════════════════════════╗\n`);
  process.stdout.write(`║  ✅ ناجح  : ${String(results.ok).padEnd(4)} سهم               ║\n`);
  process.stdout.write(`║  ⏭️  تخطي  : ${String(results.skip).padEnd(4)} (بيانات ناقصة)  ║\n`);
  process.stdout.write(`║  ❌ خطأ   : ${String(results.err).padEnd(4)} سهم               ║\n`);
  process.stdout.write(`║  ⏱️  الوقت : ${String(elapsed).padEnd(4)} ثانية             ║\n`);
  process.stdout.write(`╚═══════════════════════════════════════╝\n\n`);

  printStats();
}

function printStats() {
  const s = getIndicatorsCacheStats();
  if (!s || !s.symbols_count) {
    process.stdout.write('📦 الكاش فارغ — شغّل rebuild_indicators.mjs أولاً\n');
    return;
  }
  process.stdout.write(`📊 Indicators Cache:\n`);
  process.stdout.write(`   أسهم محسوبة   : ${s.symbols_count}\n`);
  process.stdout.write(`   إجمالي صفوف   : ${s.total_rows}\n`);
  process.stdout.write(`   آخر تحديث     : ${s.latest_date}\n`);
  process.stdout.write(`   RSI≤35        : ${s.oversold_rsi} سهم\n`);
  process.stdout.write(`   OBV Bullish   : ${s.bullish_obv} سهم\n`);
  process.stdout.write(`   🔥 RSI+OBV COMBO: ${s.rsi_obv_combo} سهم ← أقوى إشارة (WR=69%)\n\n`);
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
