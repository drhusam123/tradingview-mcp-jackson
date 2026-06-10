/**
 * EGX Daily Data Update
 * ======================
 * يجلب آخر شمعة يومية لكل سهم في الكون المصري ويحفظها في قاعدة البيانات
 * يُشغَّل يومياً بعد إغلاق السوق (3PM Egypt Time)
 *
 * التشغيل:
 *   node scripts/daily_update.mjs
 *   node scripts/daily_update.mjs --force       (يُعيد جلب حتى الأسهم المحدّثة)
 *   node scripts/daily_update.mjs --symbol PHDC (سهم واحد)
 *   node scripts/daily_update.mjs --stats       (يطبع إحصائيات فقط)
 *   node scripts/daily_update.mjs --historical  (يجلب 500 شمعة لكل سهم — بناء قاعدة تاريخية)
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { getOhlcv }               from '../src/core/data.js';
import { saveOHLCV, upsertStockUniverse,
         getHistoryStats, getDB, getStaleSymbols,
         EGX_UNIVERSE }  from '../src/egx/index.js';
import { toTvSymbol }             from '../src/egx/tv_symbols.js';
import { waitForChartReady }      from '../src/wait.js';
import { cairoDateParts, isTradingDay, seedHolidayCalendar } from './lib/egx_calendar.mjs';

const DELAY_MS    = process.env.DELAY_MS ? +process.env.DELAY_MS : 2500;
const READY_TIMEOUT_MS = process.env.READY_TIMEOUT_MS ? +process.env.READY_TIMEOUT_MS : 10000;
const TIMEFRAME   = 'D';
const TODAY       = new Date().toISOString().split('T')[0];
const FORCE_MODE  = process.argv.includes('--force');
const STATS_ONLY  = process.argv.includes('--stats');
const HISTORICAL  = process.argv.includes('--historical');
const SELF_TEST_QUALITY = process.argv.includes('--self-test-quality');
const BAR_COUNT   = HISTORICAL ? 500 : 5;
const SINGLE_SYM = (() => {
  const i = process.argv.indexOf('--symbol');
  return i >= 0 ? process.argv[i + 1] : null;
})();
const MAX_SYMBOLS = (() => {
  const i = process.argv.indexOf('--max-symbols');
  return i >= 0 ? Math.max(1, parseInt(process.argv[i + 1] || '0')) : null;
})();

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function formatTime(unixSec) {
  if (!unixSec) return '—';
  return new Date(unixSec * 1000).toISOString().split('T')[0];
}

const KNOWN_BAD_OHLCV_FINGERPRINTS = new Set([
  '41.61|42.18|41.61|41.70|83579',
]);

function ohlcvFingerprint(bar) {
  return [
    Number(bar.open).toFixed(2),
    Number(bar.high).toFixed(2),
    Number(bar.low).toFixed(2),
    Number(bar.close).toFixed(2),
    Number(bar.volume).toFixed(0),
  ].join('|');
}

/** تحقق من صحة الشمعة — يحذف البيانات التالفة */
function validateBar(bar) {
  if (!bar || !bar.time) return false;
  const time = Number(bar.time);
  const open = Number(bar.open);
  const high = Number(bar.high);
  const low = Number(bar.low);
  const close = Number(bar.close);
  const volume = Number(bar.volume ?? 0);
  if (![time, open, high, low, close, volume].every(Number.isFinite)) return false;
  if (volume < 0) return false;
  if (high < low) return false;
  if (close <= 0 || open <= 0) return false;
  if (high < close || high < open) return false;
  if (low > close || low > open) return false;
  // يوم تعطل: حجم صفري + سعر ثابت تماماً
  if (volume === 0 && open === close && high === close && low === close) return false;
  return true;
}

function getNewBarsForQuality(db, sym, validBars) {
  const prev = db.prepare(
    'SELECT bar_time, close, volume FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 1'
  ).get(sym);

  const newBars = validBars
    .filter(b => !prev?.bar_time || Number(b.time) > Number(prev.bar_time))
    .sort((a, b) => Number(a.time) - Number(b.time));

  return { prev, newBars };
}

function rejectSuspiciousNewBars(prev, sym, newBars) {
  if (!prev?.close || !newBars.length) return null;
  let lastClose = Number(prev.close);

  for (const bar of newBars) {
    const close = Number(bar.close);
    if (!close || !lastClose) continue;
    const pct = Math.abs(close - lastClose) / lastClose;
    if (pct > 0.5) {
      return `suspicious new close jump ${sym}: ${lastClose} -> ${close} (${(pct * 100).toFixed(1)}%)`;
    }
    lastClose = close;
  }

  return null;
}

function analyzeBatchQuality(candidates, { minDuplicateSymbols = 4 } = {}) {
  const knownBad = [];
  const byFingerprint = new Map();

  for (const candidate of candidates) {
    for (const bar of candidate.newBars) {
      const fingerprint = ohlcvFingerprint(bar);
      const item = {
        sym: candidate.sym,
        time: Number(bar.time),
        date: formatTime(Number(bar.time)),
        fingerprint,
      };

      if (KNOWN_BAD_OHLCV_FINGERPRINTS.has(fingerprint)) {
        knownBad.push(item);
      }

      const group = byFingerprint.get(fingerprint) ?? [];
      group.push(item);
      byFingerprint.set(fingerprint, group);
    }
  }

  const duplicateFingerprints = [];
  for (const [fingerprint, rows] of byFingerprint.entries()) {
    const symbols = [...new Set(rows.map(r => r.sym))];
    if (symbols.length >= minDuplicateSymbols) {
      duplicateFingerprints.push({
        fingerprint,
        symbols,
        rows,
      });
    }
  }

  return {
    ok: knownBad.length === 0 && duplicateFingerprints.length === 0,
    knownBad,
    duplicateFingerprints,
  };
}

function findKnownBadBars(newBars) {
  return newBars
    .map(bar => ({ bar, fingerprint: ohlcvFingerprint(bar) }))
    .filter(item => KNOWN_BAD_OHLCV_FINGERPRINTS.has(item.fingerprint));
}

function printBatchQualityRejection(quality) {
  process.stdout.write(`\n⛔ رفض حارس جودة OHLCV الدفعة قبل الحفظ — لم تُكتب أي شمعة جديدة.\n`);

  if (quality.knownBad.length > 0) {
    process.stdout.write(`\nبصمة TradingView/Chrome فاسدة معروفة:\n`);
    for (const row of quality.knownBad.slice(0, 20)) {
      process.stdout.write(`  ${row.sym.padEnd(8)} ${row.date}: ${row.fingerprint}\n`);
    }
    if (quality.knownBad.length > 20) {
      process.stdout.write(`  ... +${quality.knownBad.length - 20} صفوف أخرى\n`);
    }
  }

  if (quality.duplicateFingerprints.length > 0) {
    process.stdout.write(`\nبصمات OHLCV+volume مكررة عبر عدة رموز:\n`);
    for (const group of quality.duplicateFingerprints.slice(0, 10)) {
      process.stdout.write(`  ${group.fingerprint} ← ${group.symbols.join(', ')}\n`);
      const sample = group.rows.slice(0, 6).map(r => `${r.sym}@${r.date}`).join(' | ');
      process.stdout.write(`    عينة: ${sample}\n`);
    }
    if (quality.duplicateFingerprints.length > 10) {
      process.stdout.write(`  ... +${quality.duplicateFingerprints.length - 10} بصمات أخرى\n`);
    }
  }

  process.stdout.write(`\nالإجراء: أصلح اتصال TradingView/Chrome أو أعد تشغيله، ثم أعد تشغيل التحديث. قاعدة البيانات لم تُعدّل من هذه الدفعة.\n\n`);
}

function runQualitySelfTest() {
  const unique = analyzeBatchQuality([
    { sym: 'COMI', newBars: [{ time: 1, open: 1, high: 2, low: 1, close: 1.5, volume: 100 }] },
    { sym: 'PHDC', newBars: [{ time: 1, open: 2, high: 3, low: 2, close: 2.5, volume: 200 }] },
  ]);
  if (!unique.ok) throw new Error('quality self-test failed: unique bars rejected');

  const duplicate = analyzeBatchQuality([
    { sym: 'COMI', newBars: [{ time: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 12345 }] },
    { sym: 'PHDC', newBars: [{ time: 1, open: 10, high: 11, low: 9, close: 10.5, volume: 12345 }] },
  ], { minDuplicateSymbols: 2 });
  if (duplicate.ok || duplicate.duplicateFingerprints.length !== 1) {
    throw new Error('quality self-test failed: duplicate fingerprint not rejected');
  }

  const knownBad = analyzeBatchQuality([
    { sym: 'ABUK', newBars: [{ time: 1, open: 41.61, high: 42.18, low: 41.61, close: 41.70, volume: 83579 }] },
  ]);
  if (knownBad.ok || knownBad.knownBad.length !== 1) {
    throw new Error('quality self-test failed: known bad fingerprint not rejected');
  }

  process.stdout.write('✅ OHLCV batch quality self-test passed.\n');
}

function progressBar(pct, width = 28) {
  const filled = Math.round(pct / 100 * width);
  return '[' + '█'.repeat(filled) + '░'.repeat(width - filled) + ']';
}

async function main() {
  if (SELF_TEST_QUALITY) {
    runQualitySelfTest();
    return;
  }

  const db = getDB();

  if (STATS_ONLY) {
    printStats();
    return;
  }

  // ── حارس العطلات الرسمية (لا جلب تلقائي في أيام الإغلاق) ─────────────
  if (!FORCE_MODE && !HISTORICAL && !SINGLE_SYM) {
    seedHolidayCalendar();
    try {
      const todayCairo = cairoDateParts().date;
      const market = isTradingDay(todayCairo);
      if (!market.is_trading_day) {
        const why = market.holiday_name || 'عطلة رسمية / نهاية أسبوع';
        process.stdout.write(
          `\n🏖️  السوق مغلق اليوم (${todayCairo}) — ${why}\n` +
          `   تم تخطي التحديث اليومي. استخدم --force لإجبار الجلب أو --symbol لسهم واحد.\n\n`
        );
        printStats();
        return;
      }
    } catch (e) {
      process.stdout.write(`⚠️  تعذّر التحقق من تقويم العطلات: ${e.message} — نكمل بحذر.\n`);
    }
  }

  // ── تحديد الأسهم المستهدفة ────────────────────────────────────────────
  let targets;
  if (SINGLE_SYM) {
    targets = [SINGLE_SYM];
  } else if (FORCE_MODE) {
    targets = [...new Set([...EGX_UNIVERSE, 'EGX30'])];
  } else {
    // فقط الأسهم التي لم تُحدَّث منذ أمس
    const stale = new Set(getStaleSymbols(1));
    targets = [...new Set([...EGX_UNIVERSE, 'EGX30'])].filter(s => stale.has(s) || s === 'EGX30');
  }

  if (targets.length === 0) {
    process.stdout.write(`✅ كل الأسهم محدّثة (${TODAY}). استخدم --force لإجبار الإعادة.\n`);
    printStats();
    return;
  }

  if (MAX_SYMBOLS && !SINGLE_SYM) {
    targets = targets.slice(0, MAX_SYMBOLS);
  }

  process.stdout.write(`
╔═══════════════════════════════════════════════════════════════╗
║              EGX Daily Update — Dr. Husam                     ║
╠═══════════════════════════════════════════════════════════════╣
║  الأسهم المستهدفة : ${String(targets.length).padEnd(4)} سهم                              ║
║  التاريخ          : ${TODAY}                              ║
║  الوضع            : ${HISTORICAL ? 'تاريخي (Historical)  ' : FORCE_MODE ? 'إجباري (Force)       ' : 'تدريجي (Incremental)'} ║
║  التأخير          : ${HISTORICAL ? 3000 : DELAY_MS}ms بين كل سهم                     ║
║  عدد الشمعات      : ${String(BAR_COUNT).padEnd(3)} شمعة لكل سهم                        ║
╚═══════════════════════════════════════════════════════════════╝
`);

  if (HISTORICAL) {
    process.stdout.write(
      `\n⚠️  الوضع التاريخي: يجلب ${BAR_COUNT} شمعة لكل سهم — سيستغرق ~${Math.round(targets.length * 3 / 60)} دقيقة\n` +
      `   يستخدم INSERT OR IGNORE — البيانات الموجودة لن تُعاد كتابتها\n\n`
    );
  }

  const results    = { success: [], failed: [], unchanged: [] };
  const candidates = [];
  let newBarsTotal = 0;
  const startTime  = Date.now();

  try {
    await setTimeframe({ timeframe: TIMEFRAME });
  } catch (err) {
    process.stdout.write(`\n⚠️  تعذّر ضبط الفريم ${TIMEFRAME} قبل التحديث: ${err.message}\n`);
  }

  for (let i = 0; i < targets.length; i++) {
    const sym = targets[i];
    const pct = Math.round((i / targets.length) * 100);
    const eta = i > 0
      ? Math.round(((Date.now() - startTime) / i) * (targets.length - i) / 1000)
      : '?';

    process.stdout.write(
      `\r${progressBar(pct)} ${String(i + 1).padStart(3)}/${targets.length}  ${sym.padEnd(6)}  ETA: ${String(eta).padStart(4)}s   `
    );

    try {
      const tvSymbol = toTvSymbol(sym);
      const symbolResult = await setSymbol({ symbol: tvSymbol });
      const ready = symbolResult?.chart_ready || await waitForChartReady(tvSymbol, null, READY_TIMEOUT_MS);
      if (!ready) await sleep(HISTORICAL ? 3000 : DELAY_MS); // fallback

      // جلب الشمعات: 500 في الوضع التاريخي، 5 في الوضع اليومي
      const result = await getOhlcv({ count: BAR_COUNT });

      if (!result.success || !result.bars?.length) {
        results.failed.push({ sym, reason: result.error ?? 'no bars' });
        continue;
      }

      const validBars = result.bars.filter(validateBar);

      if (validBars.length === 0) {
        results.unchanged.push(sym);
        continue;
      }

      const { prev, newBars } = getNewBarsForQuality(db, sym, validBars);

      if (newBars.length === 0) {
        results.unchanged.push(sym);
        continue;
      }

      const suspicious = rejectSuspiciousNewBars(prev, sym, newBars);
      if (suspicious) {
        results.failed.push({ sym, reason: suspicious.slice(0, 60) });
        continue;
      }

      const knownBadBars = findKnownBadBars(newBars);
      if (knownBadBars.length > 0) {
        const sample = knownBadBars[0];
        results.failed.push({
          sym,
          reason: `known bad TradingView fingerprint ${formatTime(Number(sample.bar.time))}: ${sample.fingerprint}`,
        });
        continue;
      }

      candidates.push({ sym, validBars, newBars });

    } catch (err) {
      results.failed.push({
        sym,
        reason: err.message?.slice(0, 60) ?? 'error',
      });
    }
  }

  const quality = analyzeBatchQuality(candidates);
  if (!quality.ok) {
    printBatchQualityRejection(quality);
    results.failed.push({
      sym: 'BATCH',
      reason: `OHLCV quality rejected ${quality.knownBad.length} bad + ${quality.duplicateFingerprints.length} duplicate fingerprints`,
    });
    printUpdateReport(results, newBarsTotal, startTime);
    printStats();
    process.exitCode = 2;
    return;
  }

  for (const candidate of candidates) {
    try {
      const { sym, validBars } = candidate;
      const saved = saveOHLCV(sym, validBars);
      newBarsTotal += saved;

      const times = validBars.map(b => b.time).filter(Boolean);
      const totalInDB = db.prepare(
        'SELECT COUNT(*) as c FROM ohlcv_history WHERE symbol=?'
      ).get(sym).c;

      upsertStockUniverse(sym, {
        last_fetch:  TODAY,
        total_bars:  totalInDB,
        latest_bar:  times.length > 0 ? Math.max(...times) : null,
        status:      'fetched',
      });

      results.success.push({ sym, saved, total: totalInDB });

    } catch (err) {
      results.failed.push({
        sym: candidate?.sym ?? 'UNKNOWN',
        reason: err.message?.slice(0, 60) ?? 'error',
      });
    }
  }

  // ─── التقرير النهائي ───────────────────────────────────────────────────
  printUpdateReport(results, newBarsTotal, startTime);
  printStats();
}

function printUpdateReport(results, newBarsTotal, startTime) {
  const elapsed = Math.round((Date.now() - startTime) / 1000);

  process.stdout.write(`\n\n`);
  process.stdout.write(`╔══════════════════════════════════════════════════════════════╗\n`);
  process.stdout.write(`║              نتيجة التحديث اليومي                           ║\n`);
  process.stdout.write(`╠══════════════════════════════════════════════════════════════╣\n`);
  process.stdout.write(`║  ✅ ناجح       : ${String(results.success.length).padEnd(4)} سهم                                  ║\n`);
  process.stdout.write(`║  ➡️  بدون تغيير : ${String(results.unchanged.length).padEnd(4)} سهم                                  ║\n`);
  process.stdout.write(`║  ❌ فاشل        : ${String(results.failed.length).padEnd(4)} سهم                                  ║\n`);
  process.stdout.write(`║  📊 شمعات جديدة : ${String(newBarsTotal).padEnd(5)} ${HISTORICAL ? '(تاريخية — INSERT OR IGNORE)' : '                        '}║\n`);
  process.stdout.write(`║  ⏱️  الوقت       : ${String(elapsed).padEnd(4)} ثانية                                ║\n`);
  process.stdout.write(`╚══════════════════════════════════════════════════════════════╝\n\n`);

  if (results.failed.length > 0) {
    process.stdout.write(`❌ الأسهم الفاشلة:\n`);
    for (const f of results.failed) {
      process.stdout.write(`  ${f.sym}: ${f.reason}\n`);
    }
    process.stdout.write('\n');
  }
}

function printStats() {
  const stats = getHistoryStats();
  const s     = stats.summary;
  if (!s || s.total_symbols === 0) {
    process.stdout.write('📦 قاعدة البيانات فارغة.\n');
    return;
  }

  process.stdout.write(`\n📦 قاعدة البيانات الآن:\n`);
  process.stdout.write(`   الأسهم       : ${s.total_symbols}\n`);
  process.stdout.write(`   إجمالي شمعات : ${(s.total_bars || 0).toLocaleString()}\n`);
  process.stdout.write(`   متوسط/سهم    : ${s.avg_bars_per_symbol}\n`);
  process.stdout.write(`   النطاق        : ${formatTime(s.earliest_bar)} → ${formatTime(s.latest_bar)}\n`);
  process.stdout.write('\n   الحالات:\n');
  for (const st of stats.statusCount) {
    process.stdout.write(`   ${(st.status || 'unknown').padEnd(12)}: ${st.cnt}\n`);
  }
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
