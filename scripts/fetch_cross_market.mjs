#!/usr/bin/env node
/**
 * Phase 51 — Cross-Market Data Fetcher
 * ======================================
 * يجلب 15 أصل عالمي من TradingView ويحفظها في cross_market_daily
 * اليومي: 5-10 دقائق فقط — أعلى عائد/وقت في النظام كله
 *
 * Usage:
 *   node scripts/fetch_cross_market.mjs          -- جلب الكل
 *   node scripts/fetch_cross_market.mjs --full   -- 500 bar تاريخ كامل
 *   node scripts/fetch_cross_market.mjs --daily  -- 30 bar آخر شهر فقط (سريع)
 */

import { setSymbol, setTimeframe } from '../src/core/chart.js';
import { getOhlcv }                from '../src/core/data.js';
import { getDB, saveCrossMarket, getCrossMarketCoverage } from '../src/egx/index.js';
import { toTvSymbol }              from '../src/egx/tv_symbols.js';

const args    = process.argv.slice(2);
const FULL    = args.includes('--full');   // 500 bars (default)
const DAILY   = args.includes('--daily');  // 30 bars only (fast daily update)
const MAX_BARS = DAILY ? 30 : 500;
const DELAY_MS = process.env.DELAY_MS ? +process.env.DELAY_MS : 2000;

// ─── Assets Registry ──────────────────────────────────────────────────────────
// TradingView symbols + category metadata
const ASSETS = [
  // FX — أهم asset للـ EGX
  { asset: 'USDEGP',   tv: 'FX_IDC:USDEGP',  cat: 'FX',        arabic: 'دولار/جنيه',     impact: 'CRITICAL' },
  { asset: 'EURUSD',   tv: 'FX:EURUSD',       cat: 'FX',        arabic: 'يورو/دولار',     impact: 'MEDIUM'   },
  { asset: 'DXY',      tv: 'TVC:DXY',         cat: 'FX_INDEX',  arabic: 'مؤشر الدولار',   impact: 'HIGH'     },

  // Commodities
  { asset: 'XAUUSD',   tv: 'OANDA:XAUUSD',    cat: 'COMMODITY', arabic: 'ذهب',            impact: 'HIGH'     },
  { asset: 'UKOIL',    tv: 'TVC:UKOIL',       cat: 'COMMODITY', arabic: 'نفط برنت',       impact: 'HIGH'     },

  // Global Indices
  { asset: 'SPY',      tv: 'SP:SPX',          cat: 'INDEX',     arabic: 'S&P 500',        impact: 'MEDIUM'   },
  { asset: 'EEM',      tv: 'AMEX:EEM',        cat: 'INDEX',     arabic: 'أسواق ناشئة',    impact: 'HIGH'     },
  { asset: 'VIX',      tv: 'TVC:VIX',         cat: 'VOLATILITY',arabic: 'تقلب عالمي',    impact: 'HIGH'     },

  // Bonds
  { asset: 'US10Y',    tv: 'TVC:US10Y',       cat: 'BOND',      arabic: 'عائد أمريكي 10س', impact: 'MEDIUM'  },

  // Regional Markets
  { asset: 'TASI',     tv: 'TADAWUL:TASI',    cat: 'REGIONAL',  arabic: 'تداول السعودية', impact: 'MEDIUM'   },
  { asset: 'DFMGI',    tv: 'DFM:DFMGI',       cat: 'REGIONAL',  arabic: 'سوق دبي',        impact: 'MEDIUM'   },

  // Additional EGX context
  { asset: 'EGX30',    tv: toTvSymbol('EGX30'), cat: 'EGX_INDEX', arabic: 'مؤشر EGX30',     impact: 'DIRECT'   },
  { asset: 'EGX70',    tv: 'EGX:EGX70EWI',       cat: 'EGX_INDEX', arabic: 'مؤشر EGX70',     impact: 'DIRECT'   },
];

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function pp(o)     { return JSON.stringify(o, null, 2); }

async function fetchAsset(asset) {
  await setSymbol({ symbol: asset.tv });
  await sleep(700);
  await setTimeframe({ timeframe: 'D' });
  await sleep(500);
  const data = await getOhlcv({ count: MAX_BARS });
  if (!data?.bars?.length) throw new Error(`No bars returned for ${asset.tv}`);
  return data.bars;
}

async function main() {
  // Ensure tables exist
  try {
    const { initPhase49to55Schema } = await import('../src/egx/index.js');
    initPhase49to55Schema();
  } catch { /* already initialized */ }

  const db = getDB();

  process.stdout.write(`
╔════════════════════════════════════════════════════════════════╗
║         Cross-Market Data Fetcher — Phase 51                   ║
╠════════════════════════════════════════════════════════════════╣
║  Assets  : ${String(ASSETS.length).padEnd(3)}                                              ║
║  Bars    : ${String(MAX_BARS).padEnd(3)} per asset  (${DAILY ? 'daily mode — fast' : 'full history mode'})             ║
╚════════════════════════════════════════════════════════════════╝
\n`);

  const results = { ok: [], err: [], total_bars: 0 };
  const startTime = Date.now();

  for (const asset of ASSETS) {
    process.stdout.write(`  ⏳ ${String(asset.arabic).padEnd(18)} (${asset.tv}) ... `);
    try {
      const bars = await fetchAsset(asset);
      const saved = saveCrossMarket(asset.asset, bars);
      results.ok.push(asset.asset);
      results.total_bars += saved;
      const oldest = new Date(bars[0].time * 1000).toISOString().split('T')[0];
      const newest = new Date(bars[bars.length-1].time * 1000).toISOString().split('T')[0];
      process.stdout.write(`✅  ${bars.length} bars (${oldest} → ${newest})  +${saved} new\n`);
    } catch (e) {
      results.err.push({ asset: asset.asset, error: e.message });
      process.stdout.write(`❌  ${e.message}\n`);
    }
    await sleep(DELAY_MS);
  }

  // Restore EGX chart
  await setSymbol({ symbol: toTvSymbol('COMI') }).catch(() => {});
  await setTimeframe({ timeframe: 'D' }).catch(() => {});

  // Summary
  const coverage = getCrossMarketCoverage();
  const elapsed  = Math.round((Date.now() - startTime) / 1000);

  process.stdout.write(`
╔════════════════════════════════════════════════════════════════╗
║  ✅ Done: ${String(results.ok.length).padEnd(2)} assets  ❌ Errors: ${String(results.err.length).padEnd(2)}  +${results.total_bars} rows  ${elapsed}s   ║
╠════════════════════════════════════════════════════════════════╣\n`);

  coverage.slice(0,13).forEach(r =>
    process.stdout.write(`║  ${String(r.asset).padEnd(10)} ${String(r.bars).padStart(5)} bars  ${r.oldest} → ${r.newest}       ║\n`));

  process.stdout.write(`╚════════════════════════════════════════════════════════════════╝\n`);

  if (results.err.length) {
    process.stdout.write('\nErrors:\n');
    results.err.forEach(e => process.stdout.write(`  ❌ ${e.asset}: ${e.error}\n`));
  }

  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
