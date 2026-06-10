/**
 * EGX Historical Data Fetcher
 * ============================
 * يجلب كامل الشمعات المتاحة (max 500) لكل سهم في الكون المصري
 * ويحفظها في data/egx_trading.db
 *
 * التشغيل:
 *   node scripts/fetch_egx_history.mjs
 *   node scripts/fetch_egx_history.mjs --resume   (يكمل من آخر نقطة)
 *   node scripts/fetch_egx_history.mjs --symbol PHDC  (سهم واحد)
 */

import { setSymbol, setTimeframe }  from '../src/core/chart.js';
import { getOhlcv }                 from '../src/core/data.js';
import { saveOHLCV, upsertStockUniverse,
         getHistoryStats, getDB }   from '../src/egx/index.js';
import { EGX_UNIVERSE }             from '../src/egx/index.js';
import { waitForChartReady }        from '../src/wait.js';
import { toTvSymbol }               from '../src/egx/tv_symbols.js';

const DELAY_MS    = process.env.DELAY_MS ? +process.env.DELAY_MS : 2000;   // وقت الانتظار — override: DELAY_MS=5000
const MAX_BARS    = 500;    // أقصى عدد شمعات
const TIMEFRAME   = 'D';    // يومي — إلزامي للـ swing trading analysis
const TODAY       = new Date().toISOString().split('T')[0];
const RESUME_MODE = process.argv.includes('--resume');
const SINGLE_SYM  = (() => { const i = process.argv.indexOf('--symbol'); return i>=0 ? process.argv[i+1] : null; })();

// ─── إزالة التكرار من القائمة ──────────────────────────────────────────────
const ALL_SYMBOLS = SINGLE_SYM
  ? [SINGLE_SYM]
  : [...new Set(EGX_UNIVERSE)];

// ── مساعدات ──────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function formatTime(unixSec) {
  if (!unixSec) return '—';
  return new Date(unixSec * 1000).toISOString().split('T')[0];
}

function bar(pct, width=30) {
  const filled = Math.round(pct / 100 * width);
  return '[' + '█'.repeat(filled) + '░'.repeat(width-filled) + ']';
}

// ─── الـ Main ──────────────────────────────────────────────────────────────
async function main() {
  const db = getDB();

  // في حالة resume: تخطّ كل سهم مكتمل (status='fetched') بغض النظر عن التاريخ
  const alreadyFetched = new Set(
    RESUME_MODE
      ? db.prepare("SELECT symbol FROM stock_universe WHERE status='fetched'")
           .all().map(r => r.symbol)
      : []
  );

  const targets = ALL_SYMBOLS.filter(s => !alreadyFetched.has(s));

  process.stdout.write(`
╔═══════════════════════════════════════════════════════════════╗
║          EGX Historical Data Fetcher — Dr. Husam              ║
╠═══════════════════════════════════════════════════════════════╣
║  الأسهم المستهدفة : ${String(targets.length).padEnd(4)} | الكلي: ${String(ALL_SYMBOLS.length).padEnd(4)}              ║
║  الشمعات المطلوبة : ${MAX_BARS} لكل سهم                              ║
║  وقت التأخير      : ${DELAY_MS}ms بين كل سهم                         ║
║  الوضع            : ${RESUME_MODE ? 'استكمال (Resume)         ' : 'تشغيل جديد (Fresh)       '} ║
╚═══════════════════════════════════════════════════════════════╝
`);

  if (targets.length === 0) {
    process.stdout.write('✅ كل الأسهم مجلوبة اليوم. استخدم --resume=false لإعادة الجلب.\n');
    printFinalStats();
    return;
  }

  const results = { success: [], failed: [], skipped: [] };
  let totalBars = 0;
  const startTime = Date.now();

  for (let i = 0; i < targets.length; i++) {
    const sym = targets[i];
    const pct  = Math.round((i / targets.length) * 100);
    const eta  = i > 0 ? Math.round(((Date.now() - startTime) / i) * (targets.length - i) / 1000) : '?';

    process.stdout.write(`\r${bar(pct)} ${String(i+1).padStart(3)}/${targets.length}  ${sym.padEnd(6)}  ETA: ${eta}s   `);

    try {
      // تغيير الرمز والـ timeframe إلى Daily — ثم انتظار تحميل البيانات فعلياً
      const tvSymbol = toTvSymbol(sym);
      await setSymbol({ symbol: tvSymbol });
      await setTimeframe({ timeframe: TIMEFRAME });   // ← إلزامي: Daily
      const ready = await waitForChartReady(tvSymbol, null, 8000);
      if (!ready) await sleep(DELAY_MS); // fallback if legend check times out

      // جلب الشمعات اليومية
      const result = await getOhlcv({ count: MAX_BARS });

      if (!result.success || !result.bars || result.bars.length === 0) {
        results.failed.push({ sym, reason: result.error ?? 'no bars' });
        upsertStockUniverse(sym, { status: 'failed', last_fetch: TODAY, total_bars: 0 });
        continue;
      }

      const bars = result.bars;

      // حفظ في قاعدة البيانات
      const saved = saveOHLCV(sym, bars);
      totalBars += saved;

      // تحديث حالة السهم
      const times = bars.map(b => b.time).filter(Boolean);
      upsertStockUniverse(sym, {
        last_fetch:   TODAY,
        total_bars:   bars.length,
        earliest_bar: times.length > 0 ? Math.min(...times) : null,
        latest_bar:   times.length > 0 ? Math.max(...times) : null,
        status:       'fetched',
      });

      results.success.push({
        sym,
        bars: bars.length,
        saved,
        from: formatTime(times.length > 0 ? Math.min(...times) : null),
        to:   formatTime(times.length > 0 ? Math.max(...times) : null),
      });

    } catch (err) {
      results.failed.push({ sym, reason: err.message });
      upsertStockUniverse(sym, { status: 'failed', last_fetch: TODAY, total_bars: 0 });
    }
  }

  // ─── التقرير النهائي ───────────────────────────────────────────────────
  const elapsed = Math.round((Date.now() - startTime) / 1000);

  process.stdout.write(`\n\n`);
  process.stdout.write(`╔══════════════════════════════════════════════════════════════╗\n`);
  process.stdout.write(`║           النتيجة النهائية — EGX History Fetch              ║\n`);
  process.stdout.write(`╠══════════════════════════════════════════════════════════════╣\n`);
  process.stdout.write(`║  ✅ ناجح     : ${String(results.success.length).padEnd(4)} سهم                                    ║\n`);
  process.stdout.write(`║  ❌ فاشل     : ${String(results.failed.length).padEnd(4)} سهم                                    ║\n`);
  process.stdout.write(`║  📊 شمعات   : ${String(totalBars).padEnd(7)} شمعة جديدة في قاعدة البيانات            ║\n`);
  process.stdout.write(`║  ⏱️  الوقت   : ${String(elapsed).padEnd(4)} ثانية                                    ║\n`);
  process.stdout.write(`╚══════════════════════════════════════════════════════════════╝\n\n`);

  if (results.success.length > 0) {
    process.stdout.write(`✅ الأسهم الناجحة:\n`);
    process.stdout.write(`${'Symbol'.padEnd(8)} ${'Bars'.padEnd(6)} ${'New'.padEnd(6)} ${'From'.padEnd(12)} ${'To'.padEnd(12)}\n`);
    process.stdout.write(`${'─'.repeat(50)}\n`);
    for (const r of results.success) {
      process.stdout.write(`${r.sym.padEnd(8)} ${String(r.bars).padEnd(6)} ${String(r.saved).padEnd(6)} ${r.from.padEnd(12)} ${r.to}\n`);
    }
  }

  if (results.failed.length > 0) {
    process.stdout.write(`\n❌ الأسهم الفاشلة (${results.failed.length}):\n`);
    for (const f of results.failed) {
      process.stdout.write(`  ${f.sym}: ${f.reason}\n`);
    }
  }

  printFinalStats();
}

function printFinalStats() {
  const stats = getHistoryStats();
  const s     = stats.summary;
  if (!s || s.total_symbols === 0) return;

  const earliest = formatTime(s.earliest_bar);
  const latest   = formatTime(s.latest_bar);

  process.stdout.write(`\n📦 قاعدة البيانات الآن:\n`);
  process.stdout.write(`   الأسهم المحفوظة : ${s.total_symbols}\n`);
  process.stdout.write(`   إجمالي الشمعات  : ${s.total_bars.toLocaleString()}\n`);
  process.stdout.write(`   متوسط شمعات/سهم : ${s.avg_bars_per_symbol}\n`);
  process.stdout.write(`   النطاق الزمني   : ${earliest} → ${latest}\n`);
  process.stdout.write(`\n💾 محفوظ في: data/egx_trading.db\n`);
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
