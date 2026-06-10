#!/usr/bin/env node
/**
 * TV Universe Sync — watchlist_get → stock_universe
 * Merges TradingView watchlist with local EGX_UNIVERSE.
 */
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { getDB, upsertStockUniverse, EGX_UNIVERSE } from '../src/egx/index.js';
import { fromTvSymbol } from '../src/egx/tv_symbols.js';

const today = new Date().toISOString().slice(0, 10);

async function main() {
  const db = getDB();
  db.exec(`
    CREATE TABLE IF NOT EXISTS stock_universe (
      symbol TEXT PRIMARY KEY,
      name TEXT, sector TEXT,
      last_fetch TEXT, total_bars INTEGER,
      earliest_bar TEXT, latest_bar TEXT,
      status TEXT DEFAULT 'pending'
    );
  `);

  const local = new Set(EGX_UNIVERSE);
  let tvSymbols = [];

  try {
    const health = await callMCPTool('tv_health_check', {});
    if (!health?.success) throw new Error('TV not connected');
    const wl = await callMCPTool('watchlist_get', {});
    const rows = wl?.symbols ?? wl?.result?.symbols ?? [];
    for (const r of rows) {
      const raw = r.symbol ?? r;
      const sym = fromTvSymbol(String(raw));
      if (sym && /^[A-Z0-9]{2,8}$/.test(sym)) tvSymbols.push(sym);
    }
  } catch (e) {
    console.warn(JSON.stringify({ warning: 'watchlist_get failed', error: e.message }));
  }

  const merged = new Set([...local, ...tvSymbols]);
  let upserted = 0;
  for (const sym of merged) {
    const stats = db.prepare(`
      SELECT COUNT(*) n, MIN(date(bar_time,'unixepoch')) earliest,
             MAX(date(bar_time,'unixepoch')) latest
      FROM ohlcv_history WHERE symbol=?
    `).get(sym);
    upsertStockUniverse(sym, {
      last_fetch: today,
      total_bars: stats?.n ?? 0,
      earliest_bar: stats?.earliest ?? null,
      latest_bar: stats?.latest ?? null,
      status: (stats?.n ?? 0) > 50 ? 'active' : 'pending',
    });
    upserted++;
  }

  console.log(JSON.stringify({
    success: true,
    date: today,
    local_count: local.size,
    tv_watchlist: tvSymbols.length,
    merged: upserted,
  }, null, 2));
}

// CDP websocket keeps the event loop alive — force exit after completion
main().then(() => process.exit(0)).catch(e => {
  console.error(JSON.stringify({ success: false, error: e.message }));
  process.exit(1);
});
