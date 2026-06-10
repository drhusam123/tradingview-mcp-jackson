#!/usr/bin/env node
/**
 * Phase 56 — Market Breadth Fetcher
 * Uses batch_run + quote_get to get real-time prices for breadth calculation.
 * For historical breadth from existing daily data, use: npm run egx:breadth:history
 *
 * Options:
 *   --date 2026-05-15   compute breadth for specific date (uses DB data)
 *   --history 90        compute last N days from DB
 *   --live              fetch live quotes via TradingView (requires TV open)
 */
import { pythonBreadthCompute, pythonBreadthHistory, pythonBreadthBuildFull }
  from '../src/egx/index.js';
import { getDB } from '../src/egx/index.js';
import { fromTvSymbol, toTvSymbol } from '../src/egx/tv_symbols.js';

const args    = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const date    = getArg('--date', null);
const history = parseInt(getArg('--history', '0'));
const isLive  = args.includes('--live');

function log(msg) { console.log(`[breadth] ${msg}`); }

async function loadTVClient() {
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
      return {
        callTool: (tool, params = {}) => mod.callMCPTool(tool, params),
        disconnect: async () => {},
      };
    }
    if (mod?.Client) {
      const client = new mod.Client();
      await client.connect?.();
      return client;
    }
  }

  throw new Error('No TradingView MCP bridge or core API available');
}

function pushQuote(quotes, symbol, rawQuote) {
  const q = rawQuote?.result ?? rawQuote?.quote ?? rawQuote;
  const price = q?.close ?? q?.last ?? q?.price;
  if (price == null) return;
  quotes.push({
    symbol: fromTvSymbol(symbol ?? q?.symbol ?? ''),
    price,
    change_pct: q?.change_percent ?? q?.change_pct ?? 0,
    volume: q?.volume ?? 0,
  });
}

function extractBatchQuotes(batchResult) {
  const quotes = [];
  const results = batchResult?.results ?? batchResult;

  if (Array.isArray(results)) {
    for (const item of results) {
      if (item?.success === false) continue;
      pushQuote(quotes, item?.symbol, item);
    }
    return quotes;
  }

  if (results && typeof results === 'object') {
    for (const [symbol, item] of Object.entries(results)) {
      if (item?.success === false) continue;
      pushQuote(quotes, symbol, item);
    }
  }

  return quotes;
}

// ── Historical breadth from DB ──────────────────────────────────────────────
if (history > 0) {
  log(`Computing breadth for last ${history} days from DB...`);
  const r = await pythonBreadthHistory({ days: history });
  if (r?.computed?.length) {
    log(`✅ Computed ${r.computed.length} days | ${r.skipped ?? 0} skipped (cached)`);
    const last = r.computed[r.computed.length - 1];
    log(`Latest: ${last?.date}  score=${last?.breadth_score?.toFixed(1)}  signal=${last?.signal}`);
  } else {
    console.log(JSON.stringify(r, null, 2));
  }
  process.exit(0);
}

// ── Single date from DB (default: today/latest) ─────────────────────────────
if (!isLive) {
  log(`Computing breadth for ${date ?? 'latest trading day'}...`);
  const r = await pythonBreadthBuildFull(date ? { date } : {});
  if (r?.breadth?.breadth_score !== undefined) {
    log(`✅ ${r.date}  Score=${r.breadth.breadth_score.toFixed(1)}  Signal=${r.breadth.signal}`);
    log(`   A/D: ${r.breadth.n_advances}↑ / ${r.breadth.n_declines}↓  |  MA50: ${r.breadth.pct_above_ma50?.toFixed(1)}%`);
    log(`   52w Highs: ${r.breadth.n_new_highs_52w}  |  McClellan: ${r.breadth.mcclellan_oscillator?.toFixed(2)}`);
  } else {
    console.log(JSON.stringify(r, null, 2));
  }
  process.exit(0);
}

// ── Live: fetch real-time quotes via TradingView MCP ───────────────────────
log('Fetching live quotes for all EGX symbols...');

let tvClient;
try {
  tvClient = await loadTVClient();
} catch (e) {
  log(`⚠️  TradingView not connected: ${e.message}`);
  log('    Running from DB data instead...');
  const r = await pythonBreadthBuildFull({});
  console.log(JSON.stringify(r?.breadth ?? r, null, 2));
  process.exit(0);
}

// Get all EGX symbols from DB
const db = getDB();
const symbols = db.prepare("SELECT symbol FROM stock_universe WHERE status='fetched' ORDER BY symbol")
  .all().map(r => r.symbol);

log(`Fetching quotes for ${symbols.length} symbols via batch_run...`);

// batch_run in chunks of 50
const CHUNK = 50;
const allQuotes = [];
for (let i = 0; i < symbols.length; i += CHUNK) {
  const chunk = symbols.slice(i, i + CHUNK).map(toTvSymbol);
  try {
    const result = await tvClient.callTool('batch_run', {
      symbols: chunk,
      action: 'quote_get',
    });
    allQuotes.push(...extractBatchQuotes(result));
  } catch (e) {
    log(`⚠️  chunk ${i}-${i+CHUNK}: ${e.message}`);
  }
  if (i % 100 === 0 && i > 0) log(`  ${i}/${symbols.length} processed...`);
}

log(`Got ${allQuotes.length} live quotes. Computing breadth...`);

// Pass live quote data to Python for breadth computation
const r = await pythonBreadthCompute({
  date:        new Date().toISOString().split('T')[0],
  live_quotes: allQuotes,
});

if (r?.breadth_score !== undefined) {
  log(`\n✅ LIVE BREADTH SNAPSHOT`);
  log(`   Score: ${r.breadth_score.toFixed(1)}/100  |  Signal: ${r.signal}`);
  log(`   A/D: ${r.n_advances}↑ / ${r.n_declines}↓  (${(r.ad_ratio*100).toFixed(1)}%)`);
}

await tvClient.disconnect?.();
