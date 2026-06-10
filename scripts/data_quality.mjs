/**
 * EGX Data Quality Checker & Cleaner
 * =====================================
 * يفحص جودة البيانات في قاعدة البيانات ويُصلح المشاكل:
 *   - شمعات حجم صفري (zero-volume)
 *   - OHLC anomalies (High < Low, سعر سالب)
 *   - فجوات زمنية كبيرة (gaps) في الأسهم الرقيقة
 *   - شمعات مكررة (duplicate timestamps)
 *   - flat candles مشبوهة
 *
 * التشغيل:
 *   node scripts/data_quality.mjs           (تقرير فقط — لا حذف)
 *   node scripts/data_quality.mjs --fix     (يحذف الشمعات الفاسدة)
 *   node scripts/data_quality.mjs --symbol PHDC
 *   node scripts/data_quality.mjs --gaps    (تقرير الفجوات فقط)
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import { getDB, EGX_UNIVERSE } from '../src/egx/index.js';

const FIX_MODE    = process.argv.includes('--fix');
const GAPS_ONLY   = process.argv.includes('--gaps');
const SINGLE_SYM  = (() => { const i = process.argv.indexOf('--symbol'); return i >= 0 ? process.argv[i+1] : null; })();

// أيام التداول في الأسبوع (0=Sun, 6=Sat) — EGX يعمل أحد–خميس
const TRADING_DAYS  = new Set([0, 1, 2, 3, 4]); // Sun-Thu
const GAP_THRESHOLD = 14; // فجوة > 14 يوم مشبوهة

function daysBetween(ts1, ts2) {
  return Math.abs(ts2 - ts1) / (60 * 60 * 24);
}

function formatDate(ts) {
  return new Date(ts * 1000).toISOString().split('T')[0];
}

async function main() {
  const db      = getDB();
  const targets = SINGLE_SYM ? [SINGLE_SYM] : [...new Set(EGX_UNIVERSE)];
  const report  = {
    zeroVolume:    [],
    ohlcAnomaly:   [],
    flatCandles:   [],
    duplicates:    [],
    largeGaps:     [],
    thinStocks:    [],
  };
  let totalBarsScanned = 0;

  console.log(`\n🔍 فحص جودة البيانات — ${targets.length} سهم...\n`);

  for (const sym of targets) {
    const bars = db.prepare(`
      SELECT id, bar_time as time, open, high, low, close, volume
      FROM ohlcv_history WHERE symbol = ?
      ORDER BY bar_time ASC
    `).all(sym);

    if (!bars.length) continue;
    totalBarsScanned += bars.length;

    // ── فحص 1: شمعات حجم صفري ───────────────────────────────────────
    const zeroVol = bars.filter(b =>
      b.volume === 0 && !(b.open === b.close && b.high === b.close && b.low === b.close)
    );
    if (zeroVol.length > 0) {
      report.zeroVolume.push({ sym, count: zeroVol.length, ids: zeroVol.map(b => b.id) });
    }

    // ── فحص 2: شمعات flat تماماً (zero volume + flat) ───────────────
    const flat = bars.filter(b =>
      b.volume === 0 && b.open === b.close && b.high === b.close && b.low === b.close
    );
    if (flat.length > 0) {
      report.flatCandles.push({ sym, count: flat.length, ids: flat.map(b => b.id) });
    }

    // ── فحص 3: OHLC anomalies ───────────────────────────────────────
    const anomalies = bars.filter(b =>
      b.high < b.low || b.close <= 0 || b.open <= 0 ||
      b.high < b.close || b.high < b.open ||
      b.low  > b.close || b.low  > b.open
    );
    if (anomalies.length > 0) {
      report.ohlcAnomaly.push({ sym, count: anomalies.length, ids: anomalies.map(b => b.id) });
    }

    // ── فحص 4: شمعات مكررة ─────────────────────────────────────────
    const seen = new Set();
    const dups = bars.filter(b => {
      if (seen.has(b.time)) return true;
      seen.add(b.time); return false;
    });
    if (dups.length > 0) {
      report.duplicates.push({ sym, count: dups.length, ids: dups.map(b => b.id) });
    }

    // ── فحص 5: فجوات زمنية كبيرة ───────────────────────────────────
    const gaps = [];
    for (let i = 1; i < bars.length; i++) {
      const days = daysBetween(bars[i-1].time, bars[i].time);
      if (days > GAP_THRESHOLD) {
        gaps.push({
          from: formatDate(bars[i-1].time),
          to:   formatDate(bars[i].time),
          days: Math.round(days),
        });
      }
    }
    if (gaps.length > 0) {
      report.largeGaps.push({ sym, gaps });
    }

    // ── فحص 6: أسهم رقيقة (< 100 شمعة) ─────────────────────────────
    if (bars.length < 100) {
      report.thinStocks.push({ sym, bars: bars.length });
    }
  }

  // ── طباعة التقرير ───────────────────────────────────────────────────
  console.log(`═══════════════════════════════════════════════════════`);
  console.log(`         تقرير جودة البيانات — EGX`);
  console.log(`═══════════════════════════════════════════════════════`);
  console.log(`الشمعات المفحوصة : ${totalBarsScanned.toLocaleString()}`);
  console.log(`الأسهم المفحوصة  : ${targets.length}\n`);

  let totalBadBars = 0;

  // Zero Volume (بدون flat)
  if (report.zeroVolume.length > 0) {
    const n = report.zeroVolume.reduce((a,b) => a + b.count, 0);
    totalBadBars += n;
    console.log(`\n📊 شمعات حجم صفري (غير flat): ${n} في ${report.zeroVolume.length} سهم`);
    report.zeroVolume.sort((a,b) => b.count - a.count).slice(0, 10)
      .forEach(r => console.log(`   ${r.sym.padEnd(8)}: ${r.count} شمعة`));
  }

  // Flat candles
  if (report.flatCandles.length > 0) {
    const n = report.flatCandles.reduce((a,b) => a + b.count, 0);
    totalBadBars += n;
    console.log(`\n🟰 شمعات flat تماماً (حجم صفري + سعر ثابت): ${n} في ${report.flatCandles.length} سهم`);
    report.flatCandles.sort((a,b) => b.count - a.count).slice(0, 10)
      .forEach(r => console.log(`   ${r.sym.padEnd(8)}: ${r.count} شمعة`));
  }

  // OHLC anomalies
  if (report.ohlcAnomaly.length > 0) {
    const n = report.ohlcAnomaly.reduce((a,b) => a + b.count, 0);
    totalBadBars += n;
    console.log(`\n⚠️ OHLC anomalies (High<Low أو سعر سالب): ${n} في ${report.ohlcAnomaly.length} سهم`);
    report.ohlcAnomaly.forEach(r => console.log(`   ${r.sym.padEnd(8)}: ${r.count} شمعة`));
  }

  // Duplicates
  if (report.duplicates.length > 0) {
    const n = report.duplicates.reduce((a,b) => a + b.count, 0);
    totalBadBars += n;
    console.log(`\n🔁 شمعات مكررة: ${n} في ${report.duplicates.length} سهم`);
    report.duplicates.forEach(r => console.log(`   ${r.sym.padEnd(8)}: ${r.count}`));
  }

  // Large gaps
  if (!GAPS_ONLY) {
    if (report.largeGaps.length > 0) {
      console.log(`\n⏩ فجوات زمنية > ${GAP_THRESHOLD} يوم: ${report.largeGaps.length} سهم`);
      report.largeGaps.sort((a,b) => b.gaps.reduce((s,g)=>s+g.days,0) - a.gaps.reduce((s,g)=>s+g.days,0))
        .slice(0, 15)
        .forEach(r => {
          const biggest = r.gaps.sort((a,b) => b.days - a.days)[0];
          console.log(`   ${r.sym.padEnd(8)}: ${r.gaps.length} فجوة (أكبرها ${biggest.days}d: ${biggest.from}→${biggest.to})`);
        });
    }
  } else {
    // تقرير مفصّل للفجوات
    console.log(`\n⏩ تقرير الفجوات الزمنية:\n`);
    report.largeGaps.sort((a,b) =>
      Math.max(...b.gaps.map(g=>g.days)) - Math.max(...a.gaps.map(g=>g.days))
    ).forEach(r => {
      console.log(`${r.sym}:`);
      r.gaps.forEach(g => console.log(`  ${g.from} → ${g.to} (${g.days} يوم)`));
    });
  }

  // Thin stocks
  if (report.thinStocks.length > 0) {
    console.log(`\n📉 أسهم بيانات ناقصة (< 100 شمعة): ${report.thinStocks.length} سهم`);
    report.thinStocks.sort((a,b) => a.bars - b.bars).slice(0, 10)
      .forEach(r => console.log(`   ${r.sym.padEnd(8)}: ${r.bars} شمعة فقط`));
  }

  console.log(`\n═══════════════════════════════════════════════════════`);
  console.log(`📊 إجمالي شمعات مشكوك فيها: ${totalBadBars.toLocaleString()} (${(totalBadBars/totalBarsScanned*100).toFixed(2)}%)`);
  console.log(`═══════════════════════════════════════════════════════\n`);

  // ── الحذف إذا --fix ──────────────────────────────────────────────────
  if (FIX_MODE && totalBadBars > 0) {
    console.log(`🔧 وضع الإصلاح — يحذف الشمعات الفاسدة...`);
    const del = db.prepare('DELETE FROM ohlcv_history WHERE id = ?');
    const deleteAll = db.transaction(ids => ids.forEach(id => del.run(id)));

    let deleted = 0;

    // حذف flat candles (أكثرها بيانات زائفة)
    for (const r of report.flatCandles) {
      deleteAll(r.ids);
      deleted += r.ids.length;
      console.log(`   🗑️  ${r.sym}: حُذفت ${r.ids.length} flat candles`);
    }

    // حذف OHLC anomalies
    for (const r of report.ohlcAnomaly) {
      deleteAll(r.ids);
      deleted += r.ids.length;
      console.log(`   🗑️  ${r.sym}: حُذفت ${r.ids.length} OHLC anomalies`);
    }

    // حذف المكررات (الأحدث يُحذف)
    for (const r of report.duplicates) {
      deleteAll(r.ids);
      deleted += r.ids.length;
      console.log(`   🗑️  ${r.sym}: حُذفت ${r.ids.length} مكررات`);
    }

    console.log(`\n✅ تم حذف ${deleted} شمعة فاسدة`);
    console.log(`💡 يُنصح بتشغيل rebuild_indicators.mjs بعد الإصلاح`);
  } else if (!FIX_MODE && totalBadBars > 0) {
    console.log(`💡 استخدم --fix لحذف الشمعات الفاسدة`);
  }
}

main().catch(err => {
  process.stderr.write(`\n💥 ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
