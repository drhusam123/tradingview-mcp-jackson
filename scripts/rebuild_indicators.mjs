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

import { getOHLCV, saveIndicatorsCache, getIndicatorsCacheStats, getDB, EGX_UNIVERSE } from '../src/egx/index.js';
import { buildIndicatorPayload } from './lib/indicator_snapshot.mjs';

const SINGLE_SYM = (() => { const i = process.argv.indexOf('--symbol'); return i >= 0 ? process.argv[i+1] : null; })();
const SINCE_DATE = (() => { const i = process.argv.indexOf('--since');  return i >= 0 ? process.argv[i+1] : null; })();
const STATS_ONLY = process.argv.includes('--stats');
const BARS_LIMIT = 500;  // كافٍ لـ EMA200 + ATR14 + OBV divergence

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
      const bars = getOHLCV(sym, BARS_LIMIT, { execution: true, sinceDate: SINCE_DATE || undefined });
      const payload = buildIndicatorPayload(bars);
      if (!payload) { results.skip++; continue; }

      saveIndicatorsCache(sym, payload.barDate, payload.ind);
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
