#!/usr/bin/env node
/**
 * Phase 58 — Technical Indicator Fetcher
 * Loads each top-scan symbol in TradingView, reads RSI/MACD/BB/EMA values,
 * saves to technical_indicators_cache.
 *
 * Options:
 *   --date 2026-05-15     use scan results from this date
 *   --min-score 65        minimum scan score
 *   --max-symbols 30      max symbols to fetch indicators for
 *   --symbol COMI         fetch single symbol
 *   --timeframe D         timeframe (default daily)
 */
import { pythonTechSaveIndicators, pythonTechReport } from '../src/egx/index.js';
import { getDB } from '../src/egx/index.js';
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const args      = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
let date        = getArg('--date', null);
const minScore  = parseFloat(getArg('--min-score', '60'));
const maxSym    = parseInt(getArg('--max-symbols', '30'));
const singleSym = getArg('--symbol', null);
const tf        = getArg('--timeframe', 'D');
const LOCAL_ONLY = args.includes('--local-only');
const TV_CALL_MS = parseInt(getArg('--tv-timeout-ms', '45000'), 10);

function log(msg) { console.log(`[tech] ${msg}`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function loadMCPTools() {
  const candidates = [
    '../src/egx/tv_bridge.js',
    '../src/egx/mcp_tools.js',
    '../src/client.js',
  ];

  for (const path of candidates) {
    const mod = await import(path).catch(() => null);
    if (mod?.callMCPTool) {
      const health = await mod.callMCPTool('tv_health_check', {});
      if (!health?.success) continue;
      return (tool, params = {}) => mod.callMCPTool(tool, params);
    }
    if (mod?.Client) {
      const client = new mod.Client();
      await client.connect?.();
      return (tool, params = {}) => client.callTool(tool, params);
    }
  }

  return null;
}

// ── Get symbols to process ───────────────────────────────────────────────────
const db = getDB();
let symbols = [];

if (!date) {
  const row = db.prepare(`
    SELECT MAX(scan_date) AS d
    FROM scans
    WHERE rejected = 0
  `).get();
  date = row?.d || new Date().toISOString().split('T')[0];
}

if (singleSym) {
  symbols = [singleSym];
} else {
  // Top scan picks for date
  const rows = db.prepare(`
    SELECT DISTINCT symbol, MAX(score) as score
    FROM scans
    WHERE scan_date = ? AND rejected = 0 AND score >= ?
    GROUP BY symbol
    ORDER BY score DESC
    LIMIT ?
  `).all(date, minScore, maxSym);
  symbols = rows.map(r => r.symbol);
}

if (!symbols.length) {
  log(`No scan picks found for ${date} with score >= ${minScore}`);
  log('Run egx:scan first, or use --symbol XXXX');
  process.exit(0);
}

log(`Fetching technical indicators for ${symbols.length} symbols (timeframe: ${tf})...`);

// ── TradingView MCP integration ──────────────────────────────────────────────
let callTV = null;
if (!LOCAL_ONLY) {
  try {
    callTV = await loadMCPTools();
  } catch { /* offline */ }
}

async function tv(tool, params = {}) {
  if (!callTV) return null;
  return Promise.race([
    callTV(tool, params),
    new Promise((_, reject) => setTimeout(
      () => reject(new Error(`TV ${tool} timeout after ${TV_CALL_MS}ms`)),
      TV_CALL_MS,
    )),
  ]);
}

// Indicators to load
const INDICATORS = [
  'Relative Strength Index',
  'MACD',
  'Bollinger Bands',
  'Moving Average Exponential',  // EMA 20
  'Volume',
];

let fetched = 0, errors = 0;

for (const symbol of symbols) {
  try {
    log(`  [${fetched+1}/${symbols.length}] ${symbol}...`);

    let indicators = {};

    if (callTV) {
      // Live TV fetch (per-call timeout — avoids CDP hangs blocking the pipeline)
      await tv('chart_set_symbol', { symbol: toTvSymbol(symbol) });
      await tv('chart_set_timeframe', { timeframe: tf });
      await sleep(1500); // wait for chart to load

      // Ensure indicators are on chart
      for (const ind of ['Relative Strength Index', 'MACD', 'Bollinger Bands']) {
        await tv('chart_manage_indicator', { action: 'add', indicator: ind }).catch(() => {});
      }
      await sleep(1000);

      const vals = await tv('data_get_study_values', {});
      if (vals?.studies) {
        for (const [name, data] of Object.entries(vals.studies)) {
          const nm = name.toLowerCase();
          if (nm.includes('rsi')) {
            indicators.rsi_14 = data?.RSI ?? data?.value ?? data;
          } else if (nm.includes('macd')) {
            indicators.macd_value       = data?.MACD ?? data?.macd;
            indicators.macd_signal_line = data?.Signal ?? data?.signal;
            indicators.macd_histogram   = data?.Histogram ?? data?.hist;
          } else if (nm.includes('boll')) {
            indicators.bb_upper  = data?.Upper ?? data?.upper;
            indicators.bb_middle = data?.Basis ?? data?.mid;
            indicators.bb_lower  = data?.Lower ?? data?.lower;
          } else if (nm.includes('ema') || nm.includes('moving average')) {
            if (!indicators.ema_20) indicators.ema_20 = data?.EMA ?? data?.value;
            else if (!indicators.ema_50) indicators.ema_50 = data?.EMA ?? data?.value;
            else indicators.ema_200 = data?.EMA ?? data?.value;
          } else if (nm.includes('volume')) {
            indicators.volume     = data?.Volume ?? data?.volume;
            indicators.volume_ma20 = data?.Volume_MA ?? data?.vol_ma;
          }
        }
      }
    } else {
      // Offline: compute from ohlcv_history in DB
      const rows = db.prepare(`
        SELECT bar_time, open, high, low, close, volume
        FROM ohlcv_history
        WHERE symbol = ?
        ORDER BY bar_time DESC
        LIMIT 210
      `).all(symbol);

      if (rows.length < 20) {
        log(`    ⚠️  Not enough data for ${symbol} (${rows.length} bars)`);
        errors++;
        continue;
      }

      const closes  = rows.map(r => r.close).reverse();
      const volumes = rows.map(r => r.volume).reverse();

      // Compute EMA
      function ema(data, period) {
        const k = 2 / (period + 1);
        let e = data.slice(0, period).reduce((a, b) => a + b, 0) / period;
        for (let i = period; i < data.length; i++) e = data[i] * k + e * (1 - k);
        return e;
      }
      // Compute RSI
      function rsi(data, period = 14) {
        let gains = 0, losses = 0;
        for (let i = data.length - period; i < data.length; i++) {
          const diff = data[i] - data[i-1];
          if (diff > 0) gains += diff; else losses -= diff;
        }
        const rs = gains / (losses || 0.0001);
        return 100 - 100 / (1 + rs);
      }
      // Simple SMA
      function sma(data, period) {
        return data.slice(-period).reduce((a, b) => a + b, 0) / period;
      }
      // Bollinger Bands
      function bollinger(data, period = 20, mult = 2) {
        const mid  = sma(data, period);
        const slice = data.slice(-period);
        const std  = Math.sqrt(slice.reduce((s, x) => s + (x-mid)**2, 0) / period);
        return { upper: mid + mult*std, middle: mid, lower: mid - mult*std };
      }

      const bb = bollinger(closes);
      const volMa = sma(volumes, 20);

      indicators = {
        rsi_14:         rsi(closes),
        macd_value:     ema(closes, 12) - ema(closes, 26),
        macd_signal_line: 0,
        macd_histogram: 0,
        bb_upper:       bb.upper,
        bb_middle:      bb.middle,
        bb_lower:       bb.lower,
        ema_20:         ema(closes, 20),
        ema_50:         ema(closes, 50),
        ema_200:        ema(closes, 200),
        close_price:    closes[closes.length - 1],
        volume:         volumes[volumes.length - 1],
        volume_ma20:    volMa,
      };
    }

    if (!indicators.close_price) {
      const row = db.prepare('SELECT close FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 1').get(symbol);
      indicators.close_price = row?.close;
    }

    // Save to DB via Python
    const saved = await pythonTechSaveIndicators({
      symbol,
      fetch_date: date,
      indicators,
    });

    if (saved?.tech_score !== undefined) {
      log(`    ✅ Score=${saved.tech_score.toFixed(1)}  ${saved.tech_signal}  EMA=${saved.ema_alignment}`);
    }

    fetched++;
    await sleep(300); // brief pause between symbols
  } catch (e) {
    log(`    ❌ ${symbol}: ${e.message}`);
    errors++;
  }
}

log(`\n✅ Done: ${fetched} fetched, ${errors} errors`);

// Show confluence report
if (fetched > 0) {
  const report = await pythonTechReport({ scan_date: date, min_score: 0 });
  if (report?.strongly_confirmed?.length) {
    log(`\n🔥 Strongly confirmed picks (high scan + tech score):`);
    report.strongly_confirmed.slice(0, 8).forEach(p =>
      log(`   ${String(p.symbol).padEnd(8)} scan=${p.scan_score?.toFixed(0)} tech=${p.tech_score?.toFixed(0)} combined=${p.combined_score?.toFixed(1)}`));
  }
  if (report?.contradicted?.length) {
    log(`\n⚠️  Contradicted picks (scan bullish, tech bearish):`);
    report.contradicted.slice(0, 5).forEach(p =>
      log(`   ${String(p.symbol).padEnd(8)} scan=${p.scan_score?.toFixed(0)} tech=${p.tech_score?.toFixed(0)}`));
  }
}
