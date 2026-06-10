#!/usr/bin/env node
/**
 * Phase 61 — Real-time Intraday Monitor + DOM Fetcher
 * Captures live quotes + DOM snapshots during EGX session.
 *
 * Options:
 *   --dom                 capture DOM snapshot for top liquid symbols
 *   --quotes              capture live quote batch for all symbols
 *   --status              show session status only
 *   --symbol COMI         target single symbol (for DOM)
 *   --interval 300        polling interval in seconds (default: 300)
 *   --once                run once, don't loop
 */
import { pythonMonitorSessionStatus, pythonMonitorSaveDom,
         pythonMonitorSaveQuotes, pythonMonitorLiveSnapshot,
         pythonMonitorBuildFull }
  from '../src/egx/index.js';
import { getDB, saveDOMSnapshot } from '../src/egx/index.js';
import { fromTvSymbol, toTvSymbol } from '../src/egx/tv_symbols.js';

const args      = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const doDom     = args.includes('--dom');
const doQuotes  = args.includes('--quotes');
const doStatus  = args.includes('--status');
const singleSym = getArg('--symbol', null);
const interval  = parseInt(getArg('--interval', '300')) * 1000;
const runOnce   = args.includes('--once');

function log(msg) { console.log(`[live] ${msg}`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function loadMCPCaller() {
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

function pushQuote(quotes, symbol, rawQuote) {
  const q = rawQuote?.result ?? rawQuote?.quote ?? rawQuote;
  const price = q?.close ?? q?.last ?? q?.price;
  if (price == null) return;
  quotes.push({
    symbol: fromTvSymbol(symbol ?? q?.symbol ?? ''),
    price,
    change_pct: q?.change_percent ?? q?.change_pct ?? 0,
    volume: q?.volume ?? 0,
    bid: q?.bid ?? price - 0.01,
    ask: q?.ask ?? price + 0.01,
  });
}

function quoteToProxyDom(symbol, rawQuote) {
  const q = rawQuote?.result ?? rawQuote?.quote ?? rawQuote;
  const price = Number(q?.close ?? q?.last ?? q?.price);
  if (!Number.isFinite(price) || price <= 0) return null;
  const bid = Number(q?.bid);
  const ask = Number(q?.ask);
  const volume = Math.max(1, Number(q?.volume ?? 0) || 1);
  const fallbackHalfSpread = Math.max(price * 0.0005, 0.01);
  const bestBid = Number.isFinite(bid) && bid > 0 ? bid : price - fallbackHalfSpread;
  const bestAsk = Number.isFinite(ask) && ask > 0 ? ask : price + fallbackHalfSpread;
  const depth = Math.max(1, Math.round(volume * 0.02));
  return {
    source: 'quote_proxy',
    proxy: true,
    price,
    volume,
    bids: [{ price: bestBid, volume: depth }],
    asks: [{ price: bestAsk, volume: depth }],
    note: 'TradingView depth_get unavailable; proxy generated from quote_get/batch quote.',
  };
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

// ── Status only ──────────────────────────────────────────────────────────────
if (doStatus) {
  const r = await pythonMonitorSessionStatus({});
  const phaseEmoji = {
    OPENING_AUCTION: '🔔', CONTINUOUS: '🟢', CLOSING_AUCTION: '🔶',
    PRE_MARKET: '🌅', CLOSED: '🌙',
  };
  console.log(`\n   ${phaseEmoji[r.session_phase] ?? '?'} EGX Session: ${r.session_phase}`);
  console.log(`   Cairo time: ${r.cairo_time}`);
  console.log(`   Trading day: ${r.is_trading_day ? 'Yes' : 'No'}`);
  if (r.minutes_to_open != null)  console.log(`   Opens in: ${r.minutes_to_open} min`);
  if (r.minutes_to_close != null) console.log(`   Closes in: ${r.minutes_to_close} min`);
  console.log(`   Optimal execution: ${r.optimal_execution}`);
  process.exit(0);
}

// Check if session is active
const session = await pythonMonitorSessionStatus({});
log(`Cairo time: ${session.cairo_time} | Phase: ${session.session_phase}`);

if (!session.is_trading_day && !doDom && !doQuotes) {
  log('Market is closed. Use --once --quotes to force a snapshot, or wait for trading hours.');
  if (runOnce) process.exit(0);
}

// ── TradingView tools ────────────────────────────────────────────────────────
let tvAvailable = false;
let callTV = async (tool, params) => { throw new Error('TV not connected'); };

try {
  const caller = await loadMCPCaller();
  if (caller) {
    callTV = caller;
    tvAvailable = true;
    log('✅ TradingView connected');
  }
} catch { /* offline */ }

// Top liquid symbols for DOM snapshots
const db = getDB();
const liquidSymbols = (singleSym ? [singleSym] :
  db.prepare(`
    SELECT symbol FROM liquidity_profile
    WHERE liquidity_tier IN ('TIER1','TIER2')
    ORDER BY advt_10d DESC
    LIMIT 20
  `).all().map(r => r.symbol));

log(`Monitoring ${liquidSymbols.length} symbols...`);

async function runSnapshot() {
  const now = new Date().toISOString();

  // DOM snapshots
  if (doDom || (!doQuotes && tvAvailable)) {
    for (const sym of liquidSymbols.slice(0, 10)) {
      try {
        await callTV('chart_set_symbol', { symbol: toTvSymbol(sym) });
        await sleep(800);
        const dom = await callTV('depth_get', {});
        if (dom?.bids || dom?.asks) {
          const saved = await pythonMonitorSaveDom({ symbol: sym, dom_data: dom });
          saveDOMSnapshot(sym, dom.bids ?? [], dom.asks ?? [], (saved.spread_bps ?? 0) / 10000);
          log(`  DOM ${sym}: spread=${saved.spread_bps?.toFixed(1)}bps  imbalance=${(saved.imbalance_ratio*100)?.toFixed(0)}%`);
        } else {
          const quote = await callTV('quote_get', {});
          const proxyDom = quoteToProxyDom(sym, quote);
          if (proxyDom) {
            const saved = await pythonMonitorSaveDom({ symbol: sym, dom_data: proxyDom });
            saveDOMSnapshot(sym, proxyDom.bids, proxyDom.asks, (saved.spread_bps ?? 0) / 10000);
            log(`  PROXY ${sym}: spread=${saved.spread_bps?.toFixed(1)}bps  source=quote`);
          }
        }
      } catch (e) {
        log(`  ⚠️ DOM ${sym}: ${e.message}`);
      }
      await sleep(500);
    }
  }

  // Live quote batch
  if (doQuotes || !doDom) {
    const allSymbols = db.prepare("SELECT symbol FROM stock_universe WHERE status='fetched'")
      .all().map(r => r.symbol);

    if (tvAvailable) {
      try {
        const CHUNK = 50;
        const quotes = [];
        for (let i = 0; i < allSymbols.length; i += CHUNK) {
          const chunk = allSymbols.slice(i, i + CHUNK).map(toTvSymbol);
          const result = await callTV('batch_run', { symbols: chunk, action: 'quote_get' });
          quotes.push(...extractBatchQuotes(result));
        }
        if (quotes.length) {
          const saved = await pythonMonitorSaveQuotes({ quotes });
          log(`✅ Saved ${saved.n_saved ?? quotes.length} live quotes at ${saved.fetched_at ?? now}`);
        }
      } catch (e) {
        log(`⚠️ Quote batch failed: ${e.message}`);
      }
    } else {
      log('[DRY-RUN] Would fetch live quotes for all symbols');
    }
  }

  // Live snapshot summary
  const snap = await pythonMonitorLiveSnapshot({ top_n: 20 });
  if (snap?.top_movers_up?.length) {
    log(`\n🟢 Top movers UP: ${snap.top_movers_up.slice(0,5).map(q => `${q.symbol}(+${q.change_pct?.toFixed(1)}%)`).join(' ')}`);
    log(`🔴 Top movers DN: ${snap.top_movers_down?.slice(0,5).map(q => `${q.symbol}(${q.change_pct?.toFixed(1)}%)`).join(' ')}`);
  }
}

// ── Run loop ─────────────────────────────────────────────────────────────────
if (runOnce) {
  await runSnapshot();
  process.exit(0);
} else {
  log(`Starting monitoring loop (interval: ${interval/1000}s). Ctrl+C to stop.`);
  while (true) {
    const s = await pythonMonitorSessionStatus({});
    if (s.is_trading_day && s.session_phase !== 'CLOSED') {
      await runSnapshot();
    } else {
      log(`Market ${s.session_phase} — waiting ${interval/1000}s...`);
    }
    await sleep(interval);
  }
}
