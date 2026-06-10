#!/usr/bin/env node
/**
 * DOM snapshots for actionable final_signals only.
 */
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { getDB, saveDOMSnapshot } from '../src/egx/index.js';
import { toTvSymbol } from '../src/egx/tv_symbols.js';
import { pythonMonitorSaveDom } from '../src/egx/index.js';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function main() {
  const health = await callMCPTool('tv_health_check', {});
  if (!health?.success) {
    console.log(JSON.stringify({ success: false, error: 'TV not connected' }));
    return;
  }

  const db = getDB();
  const date = db.prepare('SELECT MAX(trade_date) d FROM final_signals').get()?.d;
  const rows = db.prepare(`
    SELECT symbol FROM final_signals
    WHERE trade_date=? AND actionable=1
    ORDER BY score DESC LIMIT 10
  `).all(date);

  let saved = 0;
  for (const { symbol } of rows) {
    try {
      await callMCPTool('chart_set_symbol', { symbol: toTvSymbol(symbol) });
      await sleep(900);
      const dom = await callMCPTool('depth_get', {});
      if (dom?.bids || dom?.asks) {
        const r = await pythonMonitorSaveDom({ symbol, dom_data: dom });
        saveDOMSnapshot(symbol, dom.bids ?? [], dom.asks ?? [], (r.spread_bps ?? 0) / 10000);
        saved++;
      }
    } catch (e) {
      console.warn(`[actionable-dom] ${symbol}: ${e.message}`);
    }
  }

  console.log(JSON.stringify({ success: true, date, saved, attempted: rows.length }));
}

// CDP websocket keeps the event loop alive — force exit after completion
main().then(() => process.exit(0)).catch(e => {
  console.error(JSON.stringify({ success: false, error: e.message }));
  process.exit(1);
});
