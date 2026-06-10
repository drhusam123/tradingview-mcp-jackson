/**
 * EGX Morning Scan — الـ Scan الصباحي
 * ======================================
 * يمسح كل أسهم EGX ويعطي أفضل إعدادات لليوم
 * يُشغَّل الساعة 10:00 AM (بعد فتح السوق بـ 30 دقيقة)
 *
 * التشغيل:
 *   node scripts/scan_today.mjs
 *   node scripts/scan_today.mjs --top 20
 *   node scripts/scan_today.mjs --min-score 65
 *   node scripts/scan_today.mjs --setup volume_accumulation
 *   node scripts/scan_today.mjs --db-only      (من قاعدة البيانات فقط — لا TV)
 *   node scripts/scan_today.mjs --cache-only   (مؤشرات من indicators_cache — أسرع 10x)
 *   node scripts/scan_today.mjs --no-save      (بدون حفظ في قاعدة البيانات)
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { getOhlcv }                from '../src/core/data.js';
import { scoreSetup, saveScan, getOHLCV, getDB, getLatestIndicators,
         calculateIndicators, quickScan,
         EGX_UNIVERSE, EGX_CONFIG }  from '../src/egx/index.js';
import { toTvSymbol }              from '../src/egx/tv_symbols.js';
import { isTradingDay, tradingDayStaleness } from './lib/egx_calendar.mjs';

// ── الأسهم ذات الجودة التاريخية العالية v3 ────────────────────────────────
const QUALITY_V3 = [
  'MOSC','UTOP','TORA','ADRI','COMI','HDBK','PHDC','TMGH','SWDY','IRON',
  'OCDI','AMOC','EFID','ORWE','ACGC','CLHO','HELI','POUL','KNGC','VALU',
];

// ── خيارات CLI ────────────────────────────────────────────────────────────
const DELAY_MS    = process.env.DELAY_MS ? +process.env.DELAY_MS : 2000;
const TOP_N       = (() => { const i = process.argv.indexOf('--top');       return i >= 0 ? +process.argv[i+1] : 15; })();
const MIN_SCORE   = (() => { const i = process.argv.indexOf('--min-score'); return i >= 0 ? +process.argv[i+1] : EGX_CONFIG.minScore; })();
const SETUP_FILTER= (() => { const i = process.argv.indexOf('--setup');     return i >= 0 ? process.argv[i+1] : null; })();
const DATE_ARG    = (() => { const i = process.argv.indexOf('--date');      return i >= 0 ? process.argv[i+1] : null; })();
const DB_ONLY     = process.argv.includes('--db-only');
const CACHE_ONLY  = process.argv.includes('--cache-only');
const NO_SAVE     = process.argv.includes('--no-save');
const START_TIME  = Date.now();

function latestMarketDate() {
  try {
    const db = getDB();
    const row = db.prepare(`
      SELECT MAX(date(bar_time,'unixepoch')) AS d
      FROM ohlcv_history_execution
    `).get();
    if (row?.d) return row.d;
  } catch {}
  try {
    const db = getDB();
    const row = db.prepare(`
      SELECT MAX(date(bar_time,'unixepoch')) AS d
      FROM ohlcv_history
    `).get();
    if (row?.d) return row.d;
  } catch {}
  return new Date().toISOString().split('T')[0];
}

const TODAY       = DATE_ARG || latestMarketDate();

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function indicatorsFromCache(sym) {
  const cached = getLatestIndicators(sym);
  if (!cached) return null;
  return {
    cached,
    early: {
      rsi: cached.rsi14 ?? null,
      adx: cached.adx14 != null ? { adx: cached.adx14 } : null,
      ema20: cached.ema20 ?? null,
    },
    full: {
      obvDivergence: cached.obv_divergence ?? null,
      isHammer: !!cached.is_hammer,
      isBullishEngulfing: !!cached.is_engulfing,
    },
    forScorer: {
      rsi: cached.rsi14 ?? null,
      adx: cached.adx14 != null ? { adx: cached.adx14 } : null,
      obvDivergence: cached.obv_divergence ?? null,
    },
  };
}

function pBar(pct, w = 28) {
  const f = Math.round(pct / 100 * w);
  return '[' + '█'.repeat(f) + '░'.repeat(w - f) + ']';
}

// ─── Main ─────────────────────────────────────────────────────────────────
async function main() {
  const db          = getDB();
  const allSymbols  = [...new Set(EGX_UNIVERSE)];

  if (!isTradingDay(TODAY)) {
    process.stdout.write(`\n⚠️  ${TODAY} ليس يوم تداول EGX — النتائج تعتمد على fallback.\n`);
  }

  // الجودة أولاً ثم الباقي
  const ordered = [
    ...QUALITY_V3.filter(s => allSymbols.includes(s)),
    ...allSymbols.filter(s => !QUALITY_V3.includes(s)),
  ];

  process.stdout.write(`
╔═══════════════════════════════════════════════════════════════╗
║              EGX Morning Scan — Dr. Husam                     ║
╠═══════════════════════════════════════════════════════════════╣
║  الأسهم      : ${String(ordered.length).padEnd(4)} | الحد: ${MIN_SCORE} نقطة | أفضل: ${TOP_N}           ║
║  التاريخ     : ${TODAY}                               ║
║  المصدر      : ${CACHE_ONLY ? 'indicators_cache      ' : DB_ONLY ? 'قاعدة البيانات فقط      ' : 'TradingView + DB      '} ║
╚═══════════════════════════════════════════════════════════════╝
`);

  const allScored = [];
  let processed   = 0;

  for (let i = 0; i < ordered.length; i++) {
    const sym = ordered[i];
    const pct     = Math.round((i / ordered.length) * 100);
    const elapsed = Math.round((Date.now() - START_TIME) / 1000);
    process.stdout.write(`\r${pBar(pct)} ${String(i+1).padStart(3)}/${ordered.length}  ${sym.padEnd(6)}  ${elapsed}s   `);

    try {
      // ── جلب البيانات ──────────────────────────────────────────────────
      let bars = getOHLCV(sym, 300);

      if (!DB_ONLY && (!bars || bars.length < 10)) {
        // fallback: جلب من TradingView
        await setSymbol({ symbol: toTvSymbol(sym) });
        await setTimeframe({ timeframe: 'D' });
        await sleep(DELAY_MS);
        const r = await getOhlcv({ count: 20 });
        if (r.success && r.bars?.length > 0) bars = r.bars;
      }

      if (!bars || bars.length < 5) continue;

      const last5   = bars.slice(-5);
      const lastBar = last5[last5.length - 1];

      // ── المؤشرات: indicators_cache أولاً (--cache-only) ثم fallback للحساب ──
      let earlyIndicators = null;
      let fullIndicators = null;
      let indicatorsForScorer = { rsi: null, adx: null, obvDivergence: null };
      let usedCache = false;

      if (CACHE_ONLY || DB_ONLY) {
        const fromCache = indicatorsFromCache(sym);
        if (fromCache) {
          usedCache = true;
          earlyIndicators = fromCache.early;
          fullIndicators = fromCache.full;
          indicatorsForScorer = fromCache.forScorer;
        }
      }

      if (!usedCache || !CACHE_ONLY) {
        if (!earlyIndicators && bars.length >= 20) {
          try { earlyIndicators = quickScan(bars); } catch { /* تجاهل */ }
        }
        if (!fullIndicators && bars.length >= 30) {
          try { fullIndicators = calculateIndicators(bars); } catch { /* تجاهل */ }
        }
        if (!usedCache) {
          indicatorsForScorer = {
            rsi:           earlyIndicators?.rsi ?? null,
            adx:           earlyIndicators?.adx ?? null,
            obvDivergence: fullIndicators?.obvDivergence ?? null,
          };
        }
      }

      const stockData = {
        symbol: sym,
        quote: {
          close:  lastBar.close,
          open:   lastBar.open,
          high:   lastBar.high,
          low:    lastBar.low,
          volume: lastBar.volume,
        },
        last_5_bars:  last5,
        all_bars:     bars,
        indicators:   indicatorsForScorer,  // ← جديد: يُفعّل RSI_OBV_COMBO
      };

      const scored = scoreSetup(stockData);

      // ── إضافة قيم المؤشرات للعرض (من الحسابات المبكرة أعلاه) ─────────
      if (earlyIndicators) {
        scored.rsiVal     = earlyIndicators.rsi != null ? +Number(earlyIndicators.rsi).toFixed(1) : null;
        scored.adxVal     = earlyIndicators.adx?.adx != null
          ? +Number(earlyIndicators.adx.adx).toFixed(1)
          : (earlyIndicators.adx != null ? +Number(earlyIndicators.adx).toFixed(1) : null);
        scored.ema20Val   = earlyIndicators.ema20;
        scored.aboveEma20 = earlyIndicators.ema20 ? lastBar.close > earlyIndicators.ema20 : null;
      }
      if (fullIndicators) {
        scored.obvDivergence      = fullIndicators.obvDivergence;
        scored.isHammer           = fullIndicators.isHammer;
        scored.isBullishEngulfing = fullIndicators.isBullishEngulfing;
      }

      // ── فلتر نوع الإعداد ──────────────────────────────────────────
      if (SETUP_FILTER && !scored.setupId?.includes(SETUP_FILTER)) continue;

      allScored.push(scored);
      processed++;

    } catch (err) {
      // لا نوقف الـ scan بسبب سهم واحد
    }
  }

  process.stdout.write(`\n\n`);

  // ── ترتيب وفلترة ───────────────────────────────────────────────────
  const qualified = allScored.filter(r => !r.rejected && r.score >= MIN_SCORE);
  const ranked = qualified
    .sort((a, b) => {
      const qa = QUALITY_V3.includes(a.symbol) ? 5 : 0;
      const qb = QUALITY_V3.includes(b.symbol) ? 5 : 0;
      return (b.score + qb) - (a.score + qa);
    })
    .slice(0, TOP_N);

  // ── حفظ في قاعدة البيانات ──────────────────────────────────────────
  if (!NO_SAVE && allScored.length > 0) {
    try {
      const toSave = allScored.filter(r => r.score >= 40);
      saveScan(TODAY, toSave);
      process.stdout.write(`💾 حُفظ ${toSave.length} نتيجة (score ≥ 40) في قاعدة البيانات\n\n`);
    } catch (e) {
      process.stdout.write(`⚠️ خطأ في الحفظ: ${e.message}\n`);
    }
  }

  // ── طباعة التقرير ──────────────────────────────────────────────────
  printReport(ranked, qualified);

  const elapsed = Math.round((Date.now() - START_TIME) / 1000);
  process.stdout.write(`\n⏱️  وقت الـ scan: ${elapsed}s | معالجة: ${processed} سهم | مؤهل: ${qualified.length}\n`);

  try {
    const latestScan = db.prepare('SELECT MAX(scan_date) d FROM scans').get()?.d;
    if (latestScan && latestScan < TODAY) {
      const stale = tradingDayStaleness(latestScan, TODAY);
      process.stdout.write(
        `⚠️  SCAN_STALE: آخر scan=${latestScan} | جلسات متأخرة=${stale.staleness_trading_days ?? '?'}\n`,
      );
    }
  } catch { /* optional */ }
}

// ─── طباعة التقرير ────────────────────────────────────────────────────────
function printReport(ranked, allQualified) {
  // ملخص حسب نوع الإعداد
  const setupCounts = {};
  for (const r of allQualified) {
    const key = r.setupId || 'unknown';
    setupCounts[key] = (setupCounts[key] || 0) + 1;
  }

  process.stdout.write(`═══════════════════════════════════════════════════════════════════════\n`);
  process.stdout.write(`                📊 نتائج الـ Scan — ${TODAY}\n`);
  process.stdout.write(`═══════════════════════════════════════════════════════════════════════\n`);
  process.stdout.write(`إجمالي مؤهل (score ≥ ${MIN_SCORE}): ${allQualified.length} سهم\n`);
  for (const [setup, cnt] of Object.entries(setupCounts).sort((a,b) => b[1]-a[1])) {
    process.stdout.write(`  ${setup.padEnd(32)}: ${cnt}\n`);
  }

  // جدول الأفضل
  process.stdout.write(`\n`);
  const h = `${'#'.padEnd(3)} ${'★'.padEnd(2)} ${'Sym'.padEnd(7)} ${'Sc'.padEnd(4)} ${'Grade'.padEnd(8)} ${'Setup'.padEnd(30)} ${'Entry'.padEnd(8)} ${'SL'.padEnd(8)} ${'T1'.padEnd(8)} ${'R:R'.padEnd(5)} ${'RSI'.padEnd(5)} ${'ADX'.padEnd(5)} ${'Vol'.padEnd(5)} Extras`;
  process.stdout.write(h + '\n');
  process.stdout.write('─'.repeat(h.length) + '\n');

  for (let i = 0; i < ranked.length; i++) {
    const r  = ranked[i];
    const q  = QUALITY_V3.includes(r.symbol) ? '★' : ' ';
    const rsi  = r.rsiVal  != null ? String(r.rsiVal).padEnd(5) : '?    ';
    const adx  = r.adxVal  != null ? String(r.adxVal).padEnd(5) : '?    ';
    const vol  = r.volumeRatio != null ? `${r.volumeRatio}x`.padEnd(5) : '?    ';
    const extras = [
      r.obvDivergence === 'bullish'  ? '↑OBV'  : '',
      r.isHammer                     ? '🔨'     : '',
      r.isBullishEngulfing           ? '🕯️'     : '',
      r.aboveEma20 === false         ? '▼EMA20' : '',
    ].filter(Boolean).join(' ');

    process.stdout.write(
      `${String(i+1).padStart(3)} ${q}  ` +
      `${r.symbol.padEnd(7)} ` +
      `${String(r.score).padEnd(4)} ` +
      `${(r.grade || '').padEnd(8)} ` +
      `${(r.setupType || '').slice(0, 28).padEnd(30)} ` +
      `${String(r.levels?.entryLow ?? '?').padEnd(8)} ` +
      `${String(r.levels?.sl ?? '?').padEnd(8)} ` +
      `${String(r.levels?.t1 ?? '?').padEnd(8)} ` +
      `${String(r.levels?.rr1 ?? '?').padEnd(5)} ` +
      `${rsi} ${adx} ${vol} ${extras}\n`
    );
  }

  // تفاصيل أفضل 5
  if (ranked.length > 0) {
    process.stdout.write(`\n${'═'.repeat(65)}\n`);
    process.stdout.write(`         🔥 أفضل ${Math.min(5, ranked.length)} إعدادات — تفاصيل كاملة\n`);
    process.stdout.write(`${'═'.repeat(65)}\n`);

    for (const r of ranked.slice(0, 5)) {
      const isQ = QUALITY_V3.includes(r.symbol) ? ' ★ جودة عالية v3' : '';
      process.stdout.write(`\n📌 ${r.symbol}${isQ}\n`);
      process.stdout.write(`   الإعداد    : ${r.setupType}\n`);
      process.stdout.write(`   النقاط     : ${r.score}/100 (${r.grade})\n`);
      process.stdout.write(`   منطقة دخول : ${r.levels?.entryLow ?? '?'} — ${r.levels?.entryHigh ?? '?'}\n`);
      process.stdout.write(`   وقف الخسارة : ${r.levels?.sl ?? '?'} (هيكلي)\n`);
      process.stdout.write(`   الهدف 1    : ${r.levels?.t1 ?? '?'} (R:R ${r.levels?.rr1}:1)\n`);
      process.stdout.write(`   الهدف 2    : ${r.levels?.t2 ?? '?'} (R:R ${r.levels?.rr2}:1)\n`);
      process.stdout.write(`   الحجم      : ${r.volumeRatio}x المتوسط (avg: ${r.avgVolume?.toLocaleString()})\n`);
      if (r.rsiVal != null) process.stdout.write(`   RSI(14)    : ${r.rsiVal}${r.rsiVal < 35 ? ' ← oversold' : r.rsiVal > 65 ? ' ← overbought' : ''}\n`);
      if (r.adxVal != null) process.stdout.write(`   ADX(14)    : ${r.adxVal}${r.adxVal >= 25 ? ' ✅ trend قوي' : r.adxVal >= 18 ? ' ⚠️ trend متوسط' : ' ❌ trend ضعيف'}\n`);
      if (r.obvDivergence === 'bullish')  process.stdout.write(`   OBV        : ↑ Bullish Divergence\n`);
      if (r.isHammer)          process.stdout.write(`   الشمعة     : 🔨 Hammer\n`);
      if (r.isBullishEngulfing) process.stdout.write(`   الشمعة     : 🕯️ Bullish Engulfing\n`);
      if (r.bonuses?.length > 0) process.stdout.write(`   مكافآت     : ${r.bonuses.slice(0, 3).join(' | ')}\n`);
      if (r.warnings?.length > 0) process.stdout.write(`   تحذيرات    : ${r.warnings.join(' | ')}\n`);
    }
  }

  process.stdout.write(`\n★ = جودة تاريخية عالية v3 | ↑OBV = Bullish OBV Divergence | ▼EMA20 = تحت EMA20\n`);
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
