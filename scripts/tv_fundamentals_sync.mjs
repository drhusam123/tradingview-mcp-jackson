#!/usr/bin/env node
/**
 * TV Fundamentals Sync — symbol_info → financial_data
 * Rotates through liquid symbols (max 40/session by default).
 */
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { getDB, saveFinancialData } from '../src/egx/index.js';
import { toTvSymbol, fromTvSymbol } from '../src/egx/tv_symbols.js';

const args = process.argv.slice(2);
const maxN = (() => {
  const i = args.indexOf('--max');
  return i >= 0 ? Math.max(1, +args[i + 1] || 40) : 40;
})();

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function pickSymbols(db, limit) {
  const rows = db.prepare(`
    SELECT s.symbol FROM stock_universe s
    LEFT JOIN financial_data f ON f.symbol = s.symbol
    WHERE s.status = 'active' OR s.latest_bar IS NOT NULL
    ORDER BY CASE WHEN f.sector IS NULL OR f.sector = '' THEN 0 ELSE 1 END,
             s.total_bars DESC
    LIMIT ?
  `).all(limit);
  if (rows.length) return rows.map(r => r.symbol);
  return db.prepare(`
    SELECT DISTINCT symbol FROM ohlcv_history
    ORDER BY symbol LIMIT ?
  `).all(limit).map(r => r.symbol);
}

async function main() {
  const health = await callMCPTool('tv_health_check', {});
  if (!health?.success) {
    console.log(JSON.stringify({ success: false, error: 'TV not connected' }));
    process.exit(1);
  }

  const db = getDB();
  const symbols = pickSymbols(db, maxN);
  let saved = 0;
  const errors = [];

  for (const sym of symbols) {
    try {
      await callMCPTool('chart_set_symbol', { symbol: toTvSymbol(sym) });
      await sleep(1200);
      const info = await callMCPTool('symbol_info', { symbol: toTvSymbol(sym) });
      const d = info?.result ?? info ?? {};
      const sector = d.sector ?? d.industry ?? d.type ?? null;
      saveFinancialData(sym, {
        pe_ratio: d.pe ?? d.pe_ratio ?? null,
        pb_ratio: d.pb ?? d.pb_ratio ?? null,
        dividend_yield: d.dividend_yield ?? null,
        market_cap: d.market_cap ?? null,
        sector: sector ? String(sector).slice(0, 80) : null,
        source: 'tv_mcp_symbol_info',
      });
      saved++;
    } catch (e) {
      errors.push({ symbol: sym, error: e.message });
    }
    await sleep(800);
  }

  console.log(JSON.stringify({
    success: true,
    attempted: symbols.length,
    saved,
    errors: errors.slice(0, 5),
  }, null, 2));
}

// CDP websocket keeps the event loop alive — force exit after completion
main().then(() => process.exit(0)).catch(e => {
  console.error(JSON.stringify({ success: false, error: e.message }));
  process.exit(1);
});
