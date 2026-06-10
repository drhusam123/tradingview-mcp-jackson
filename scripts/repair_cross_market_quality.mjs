#!/usr/bin/env node
/**
 * Repair deterministic cross-market quality issues without deleting raw tables.
 */
import { getDB } from '../src/egx/index.js';

function ensureAudit(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS cross_market_quality_repairs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      repaired_at TEXT DEFAULT (datetime('now')),
      repair_name TEXT,
      rows_changed INTEGER,
      notes TEXT
    );
  `);
}

function repairInvertedUsdEgp(db) {
  const rows = db.prepare(`
    SELECT rowid, open, high, low, close
    FROM cross_market_daily
    WHERE asset = 'USDEGP'
      AND close IS NOT NULL
      AND close > 0
      AND close < 1
  `).all();

  const upd = db.prepare(`
    UPDATE cross_market_daily
    SET open = ?, high = ?, low = ?, close = ?
    WHERE rowid = ?
  `);
  const tx = db.transaction(() => {
    for (const r of rows) {
      const invOpen = r.open ? 1 / r.open : null;
      const invHigh = r.low ? 1 / r.low : null;
      const invLow = r.high ? 1 / r.high : null;
      const invClose = r.close ? 1 / r.close : null;
      upd.run(invOpen, invHigh, invLow, invClose, r.rowid);
    }
  });
  tx();
  return rows.length;
}

function fillEgxIndex(db, symbol) {
  const insert = db.prepare(`
    INSERT OR REPLACE INTO cross_market_daily(asset, bar_time, open, high, low, close, volume)
    SELECT symbol, date(bar_time, 'unixepoch'), open, high, low, close, volume
    FROM ohlcv_history
    WHERE symbol = ?
      AND close IS NOT NULL
  `);
  return insert.run(symbol).changes;
}

function main() {
  const db = getDB();
  ensureAudit(db);
  const repairs = [];

  const inverted = repairInvertedUsdEgp(db);
  db.prepare('INSERT INTO cross_market_quality_repairs(repair_name, rows_changed, notes) VALUES (?, ?, ?)')
    .run('invert_usdegp_rows_below_one', inverted, 'Converted EGP/USD-like rows to USD/EGP scale.');
  repairs.push({ repair: 'invert_usdegp_rows_below_one', rows_changed: inverted });

  for (const symbol of ['EGX30', 'EGX70']) {
    const changed = fillEgxIndex(db, symbol);
    db.prepare('INSERT INTO cross_market_quality_repairs(repair_name, rows_changed, notes) VALUES (?, ?, ?)')
      .run(`fill_${symbol.toLowerCase()}_from_ohlcv_history`, changed, 'Local EGX index copy for macro/cross-market validation.');
    repairs.push({ repair: `fill_${symbol.toLowerCase()}_from_ohlcv_history`, rows_changed: changed });
  }

  console.log(JSON.stringify({ success: true, repairs }, null, 2));
  process.exit(0);
}

main();
