#!/usr/bin/env node
/**
 * Validate TradingView macro/cross-market inputs before they influence signals.
 */
import { getDB } from '../src/egx/index.js';

const REQUIRED_ASSETS = [
  { asset: 'USDEGP', min: 20, max: 90, note: 'USD/EGP should be quoted as EGP per USD, not inverted.' },
  { asset: 'EURUSD', min: 0.5, max: 2 },
  { asset: 'DXY', min: 50, max: 200 },
  { asset: 'XAUUSD', min: 500, max: 10000 },
  { asset: 'UKOIL', min: 10, max: 250 },
  { asset: 'SPY', min: 100, max: 10000, note: 'Stored as S&P 500 index proxy for return direction, not the SPY ETF price.' },
  { asset: 'EEM', min: 10, max: 200 },
  { asset: 'VIX', min: 5, max: 150 },
  { asset: 'US10Y', min: 0, max: 20 },
  { asset: 'TASI', min: 1000, max: 30000 },
  { asset: 'DFMGI', min: 1000, max: 15000 },
  { asset: 'EGX30', min: 1000, max: 100000 },
  { asset: 'EGX70', min: 100, max: 50000 },
];

function ensureTable(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS tv_macro_reconcile_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_at TEXT DEFAULT (datetime('now')),
      status TEXT,
      checked_assets INTEGER,
      warnings INTEGER,
      notes TEXT
    );
  `);
}

function latestCross(db, asset) {
  return db.prepare(`
    SELECT asset, bar_time, close, volume
    FROM cross_market_daily
    WHERE asset = ?
    ORDER BY bar_time DESC
    LIMIT 1
  `).get(asset);
}

function tableColumns(db, table) {
  return new Set(db.prepare(`PRAGMA table_info(${table})`).all().map(c => c.name));
}

function dateValue(barTime) {
  if (barTime == null) return null;
  if (/^\d{4}-\d{2}-\d{2}/.test(String(barTime))) return String(barTime).slice(0, 10);
  return new Date(Number(barTime) * 1000).toISOString().slice(0, 10);
}

function main() {
  const db = getDB();
  ensureTable(db);
  const warnings = [];
  const assets = [];

  for (const spec of REQUIRED_ASSETS) {
    const row = latestCross(db, spec.asset);
    if (!row) {
      warnings.push({ asset: spec.asset, issue: 'MISSING_ASSET' });
      assets.push({ asset: spec.asset, status: 'MISSING' });
      continue;
    }
    const close = Number(row.close);
    const d = dateValue(row.bar_time);
    const status = close >= spec.min && close <= spec.max ? 'OK' : 'SUSPICIOUS_SCALE';
    const item = { asset: spec.asset, date: d, close, status };
    if (status !== 'OK') warnings.push({ ...item, issue: status, expected_range: [spec.min, spec.max], note: spec.note || null });
    assets.push(item);
  }

  const macro = db.prepare(`
    SELECT source, fetched_at, usd_egp, inflation_yoy, cbe_rate, macro_regime
    FROM macro_snapshot
    ORDER BY fetched_at DESC
    LIMIT 1
  `).get();
  if (!macro) {
    warnings.push({ issue: 'MISSING_MACRO_SNAPSHOT' });
  } else if (Number(macro.usd_egp) < 20 || Number(macro.usd_egp) > 90) {
    warnings.push({ issue: 'MACRO_USDEGP_SUSPICIOUS', value: macro.usd_egp });
  }

  const status = warnings.length ? 'WARN' : 'PASS';
  const runId = db.prepare(`
    INSERT INTO tv_macro_reconcile_runs(status, checked_assets, warnings, notes)
    VALUES (?, ?, ?, ?)
  `).run(status, REQUIRED_ASSETS.length, warnings.length, JSON.stringify({ assets, macro, warnings }).slice(0, 5000)).lastInsertRowid;

  console.log(JSON.stringify({
    success: true,
    status,
    run_id: runId,
    macro_snapshot: macro || null,
    assets,
    warnings,
  }, null, 2));
  if (warnings.length) process.exitCode = 2;
}

main();
