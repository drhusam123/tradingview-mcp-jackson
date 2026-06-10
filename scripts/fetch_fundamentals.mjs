#!/usr/bin/env node
/**
 * EGX Fundamentals Fetcher — جلب البيانات المالية الأساسية
 * ==========================================================
 * يجلب P/E, P/B, ROE, Debt/Equity, Dividend Yield, Market Cap
 * لكامل الـ 268 سهم في EGX_UNIVERSE من TradingView Scanner API
 * ويحفظها في جدول financial_data بالـ SQLite
 *
 * التشغيل:
 *   node scripts/fetch_fundamentals.mjs                    (كل EGX_UNIVERSE)
 *   node scripts/fetch_fundamentals.mjs --symbols MOSC,COMI (أسهم محددة)
 *   node scripts/fetch_fundamentals.mjs --dry-run           (بدون حفظ)
 *   node scripts/fetch_fundamentals.mjs --show              (عرض ما في DB)
 *
 * المصدر: TradingView Scanner API (مجاني، لا يحتاج مفتاح)
 *   https://scanner.tradingview.com/egypt/scan
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { getDB, saveFinancialData, EGX_UNIVERSE } from '../src/egx/index.js';

const DRY_RUN  = process.argv.includes('--dry-run');
const SHOW     = process.argv.includes('--show');
const SYM_ARG  = (() => {
  const i = process.argv.indexOf('--symbols');
  return i >= 0 ? process.argv[i + 1]?.split(',') : null;
})();

const TV_SCAN_URL = 'https://scanner.tradingview.com/egypt/scan';

// ── الحقول المالية المطلوبة ──────────────────────────────────────────────────
const FUNDAMENTAL_COLUMNS = [
  'name',                    // رمز السهم
  'close',                   // آخر سعر
  'price_earnings_ttm',      // P/E (TTM)
  'price_book_fq',           // P/B (آخر ربع)
  'dividend_yield_recent',   // عائد توزيعات الأرباح %
  'market_cap_basic',        // القيمة السوقية (EGP)
  'return_on_equity',        // ROE %
  'debt_to_equity',          // نسبة الديون/حقوق المساهمين
  'net_margin',              // هامش الربح الصافي %
  'book_value_per_share_fq', // القيمة الدفترية للسهم
  'net_income',              // صافي الربح
  'total_revenue',           // الإيرادات
];

// ── جلب من TradingView Scanner ────────────────────────────────────────────────
async function fetchAllFundamentals() {
  const payload = JSON.stringify({
    columns: FUNDAMENTAL_COLUMNS,
    range:   [0, 500],      // كل السوق المصري
  });

  const res = await fetch(TV_SCAN_URL, {
    method:  'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent':   'EGX-System/1.0',
      'Origin':       'https://www.tradingview.com',
      'Referer':      'https://www.tradingview.com/',
    },
    body: payload,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Scanner API Error ${res.status}: ${text.slice(0, 200)}`);
  }

  const data = await res.json();
  return data.data ?? [];
}

// ── تحويل صف Scanner إلى Object ──────────────────────────────────────────────
function parseRow(row) {
  const d = row.d ?? [];
  const obj = {};
  FUNDAMENTAL_COLUMNS.forEach((col, i) => { obj[col] = d[i]; });
  return obj;
}

// ── عرض ما في قاعدة البيانات ─────────────────────────────────────────────────
function showDB() {
  const db = getDB();
  const rows = db.prepare(`
    SELECT symbol, pe_ratio, pb_ratio, dividend_yield, market_cap,
           return_on_equity, debt_to_equity, net_margin, fetch_date
    FROM financial_data
    ORDER BY market_cap DESC NULLS LAST
    LIMIT 30
  `).all();

  if (!rows.length) {
    console.log('❌  financial_data فارغة — شغّل: node scripts/fetch_fundamentals.mjs');
    return;
  }

  console.log(`\n${'═'.repeat(90)}`);
  console.log(`  EGX Fundamentals — ${rows.length} أسهم في قاعدة البيانات`);
  console.log('═'.repeat(90));
  console.log('Symbol   P/E      P/B     DivYld%  ROE%    D/E     NetMarg%  MarketCap(B)  Date');
  console.log('─'.repeat(90));

  for (const r of rows) {
    const pe  = r.pe_ratio        != null ? r.pe_ratio.toFixed(1).padEnd(7)  : 'N/A    ';
    const pb  = r.pb_ratio        != null ? r.pb_ratio.toFixed(2).padEnd(7)  : 'N/A    ';
    const div = r.dividend_yield  != null ? r.dividend_yield.toFixed(1).padEnd(8) : 'N/A     ';
    const roe = r.return_on_equity!= null ? r.return_on_equity.toFixed(1).padEnd(7) : 'N/A    ';
    const de  = r.debt_to_equity  != null ? r.debt_to_equity.toFixed(2).padEnd(7)  : 'N/A    ';
    const nm  = r.net_margin      != null ? r.net_margin.toFixed(1).padEnd(9) : 'N/A      ';
    const mc  = r.market_cap      != null ? (r.market_cap / 1e9).toFixed(1).padEnd(13) : 'N/A          ';
    console.log(`${r.symbol.padEnd(8)} ${pe} ${pb} ${div} ${roe} ${de} ${nm} ${mc} ${r.fetch_date}`);
  }
  console.log('═'.repeat(90));
}

// ── Main ─────────────────────────────────────────────────────────────────────
async function main() {
  if (SHOW) { showDB(); return; }

  console.log('\n' + '═'.repeat(60));
  console.log('  EGX Fundamentals Fetcher — TradingView Scanner');
  console.log('═'.repeat(60));
  if (DRY_RUN) console.log('⚠️  DRY RUN — لا شيء سيُحفظ\n');

  const target = new Set(SYM_ARG ?? EGX_UNIVERSE);
  console.log(`📋  الأسهم المستهدفة: ${target.size}`);

  // جلب كل بيانات السوق المصري
  console.log('⬇️   جلب البيانات من TradingView Scanner...');
  const startTime = Date.now();

  let rows;
  try {
    rows = await fetchAllFundamentals();
  } catch (err) {
    console.error(`❌  فشل الجلب: ${err.message}`);
    process.exit(1);
  }

  console.log(`✅  وصلت ${rows.length} صف من Scanner (${Date.now() - startTime}ms)`);

  // فلترة + معالجة
  let saved = 0, skipped = 0, nodata = 0;
  const results = [];

  for (const row of rows) {
    const parsed = parseRow(row);
    const symbol = parsed.name;
    if (!symbol) continue;
    if (!target.has(symbol)) { skipped++; continue; }

    const record = {
      symbol,
      pe_ratio:        parsed.price_earnings_ttm  ?? null,
      pb_ratio:        parsed.price_book_fq        ?? null,
      dividend_yield:  parsed.dividend_yield_recent != null
                         ? parsed.dividend_yield_recent / 100
                         : null,                           // تحويل من % إلى decimal
      market_cap:      parsed.market_cap_basic     ?? null,
      return_on_equity:parsed.return_on_equity     ?? null,
      debt_to_equity:  parsed.debt_to_equity       ?? null,
      net_margin:      parsed.net_margin           ?? null,
      earnings_growth: null,                               // لا يوفرها Scanner
      free_cashflow:   null,
      revenue_growth:  null,
      roe:             parsed.return_on_equity     ?? null,
      source:          'tradingview_scanner',
    };

    const hasData = record.pe_ratio != null || record.pb_ratio != null ||
                    record.return_on_equity != null || record.market_cap != null;
    if (!hasData) { nodata++; }

    results.push(record);

    if (!DRY_RUN) {
      saveFinancialData(symbol, record);
      saved++;
    } else {
      saved++;
    }
  }

  // ملخص
  console.log(`\n${'─'.repeat(50)}`);
  console.log(`✅  محفوظ:  ${saved}`);
  console.log(`⏭️   خارج universe: ${skipped}`);
  console.log(`⚪  بدون بيانات مالية: ${nodata}`);

  // عرض أفضل 10 حسب P/E
  const withPE = results
    .filter(r => r.pe_ratio != null && r.pe_ratio > 0 && r.pe_ratio < 50)
    .sort((a, b) => a.pe_ratio - b.pe_ratio)
    .slice(0, 10);

  if (withPE.length) {
    console.log('\n📊  أرخص 10 أسهم حسب P/E:');
    for (const r of withPE) {
      const pb  = r.pb_ratio != null ? `P/B ${r.pb_ratio.toFixed(2)}` : '';
      const roe = r.roe != null ? `ROE ${r.roe.toFixed(1)}%` : '';
      console.log(`   ${r.symbol.padEnd(8)} P/E ${r.pe_ratio.toFixed(1).padEnd(6)} ${pb.padEnd(10)} ${roe}`);
    }
  }

  // أسهم undervalued حقيقية
  const undervalued = results.filter(r =>
    r.pe_ratio != null && r.pe_ratio > 0 && r.pe_ratio < 10 &&
    r.pb_ratio != null && r.pb_ratio < 1.5 &&
    r.return_on_equity != null && r.return_on_equity > 10
  );

  if (undervalued.length) {
    console.log(`\n💎  Undervalued Candidates (P/E<10, P/B<1.5, ROE>10%):`);
    for (const r of undervalued) {
      console.log(`   ${r.symbol}: P/E ${r.pe_ratio.toFixed(1)} | P/B ${r.pb_ratio.toFixed(2)} | ROE ${r.roe.toFixed(1)}%`);
    }
  }

  console.log(`\n⏰  تم في ${((Date.now() - startTime) / 1000).toFixed(1)}s`);
  if (!DRY_RUN) console.log(`💾  البيانات محفوظة في financial_data`);
  console.log('═'.repeat(60) + '\n');
}

main().catch(err => {
  console.error('💥', err.message);
  process.exit(1);
});
