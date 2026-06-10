#!/usr/bin/env node
/**
 * Compare TradingView Desktop daily candles with the EGX local database.
 */
import { getDB } from '../src/egx/index.js';
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const args = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const count = Number(getArg('--count', '20'));
const symbolArg = getArg('--symbols', null);
const REPAIR = args.includes('--repair');
const symbols = symbolArg
  ? symbolArg.split(',').map(s => s.trim()).filter(Boolean)
  : ['COMI', 'EFIH', 'ORHD', 'SWDY', 'TMGH'];

function ensureTables(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS tv_data_reconcile_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_at TEXT DEFAULT (datetime('now')),
      status TEXT,
      symbols_checked INTEGER,
      mismatches INTEGER,
      tv_connected INTEGER,
      notes TEXT
    );
    CREATE TABLE IF NOT EXISTS tv_data_reconcile_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id INTEGER,
      symbol TEXT,
      tv_symbol TEXT,
      bar_date TEXT,
      field TEXT,
      local_value REAL,
      tv_value REAL,
      diff_pct REAL,
      status TEXT,
      note TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tv_reconcile_items_run ON tv_data_reconcile_items(run_id);
    CREATE TABLE IF NOT EXISTS tv_ohlcv_repair_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      repaired_at TEXT DEFAULT (datetime('now')),
      run_id INTEGER,
      symbol TEXT,
      bar_date TEXT,
      field TEXT,
      old_value REAL,
      new_value REAL,
      source TEXT
    );
  `);
}

function pctDiff(a, b) {
  if (a == null || b == null) return null;
  const den = Math.max(Math.abs(Number(a)), Math.abs(Number(b)), 1e-9);
  return Math.abs(Number(a) - Number(b)) / den;
}

function localRows(db, symbol, n) {
  return db.prepare(`
    SELECT date(bar_time, 'unixepoch') AS d, open, high, low, close, volume
    FROM ohlcv_history_execution
    WHERE symbol = ?
    ORDER BY bar_time DESC
    LIMIT ?
  `).all(symbol, n);
}

function rawRow(db, symbol, barDate) {
  return db.prepare(`
    SELECT date(bar_time, 'unixepoch') AS d, bar_time, open, high, low, close, volume
    FROM ohlcv_history
    WHERE symbol = ?
      AND date(bar_time, 'unixepoch') = ?
    LIMIT 1
  `).get(symbol, barDate);
}

function repairLocalField(db, runId, symbol, barDate, field, oldValue, newValue) {
  const allowed = new Set(['open', 'high', 'low', 'close', 'volume']);
  if (!allowed.has(field)) return 0;
  const changed = db.prepare(`
    UPDATE ohlcv_history
    SET ${field} = ?
    WHERE symbol = ?
      AND date(bar_time, 'unixepoch') = ?
  `).run(newValue, symbol, barDate).changes;
  if (changed) {
    db.prepare(`
      INSERT INTO tv_ohlcv_repair_log(run_id, symbol, bar_date, field, old_value, new_value, source)
      VALUES (?, ?, ?, ?, ?, ?, 'TradingView Desktop MCP reconcile')
    `).run(runId, symbol, barDate, field, oldValue, newValue);
  }
  return changed;
}

function resolveQualityExclusion(db, symbol, barDate) {
  return db.prepare(`
    UPDATE data_quality_bar_exclusions
    SET status = 'RESOLVED_TV_RECONCILE',
        resolved_at = datetime('now'),
        notes = COALESCE(notes || '; ', '') || 'TradingView MCP confirmed bar'
    WHERE source_table = 'ohlcv_history'
      AND symbol = ?
      AND trade_date = ?
      AND status = 'ACTIVE'
  `).run(symbol, barDate).changes;
}

function repairFullBar(db, runId, symbol, tvBar) {
  const existing = rawRow(db, symbol, tvBar.d);
  const ts = existing?.bar_time || tvBar.time || Math.floor(new Date(`${tvBar.d}T07:00:00Z`).getTime() / 1000);
  const old = existing || {};
  const changed = db.prepare(`
    INSERT INTO ohlcv_history(symbol, bar_time, open, high, low, close, volume)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(symbol, bar_time) DO UPDATE SET
      open = excluded.open,
      high = excluded.high,
      low = excluded.low,
      close = excluded.close,
      volume = excluded.volume
  `).run(symbol, ts, tvBar.open, tvBar.high, tvBar.low, tvBar.close, tvBar.volume).changes;

  for (const field of ['open', 'high', 'low', 'close', 'volume']) {
    db.prepare(`
      INSERT INTO tv_ohlcv_repair_log(run_id, symbol, bar_date, field, old_value, new_value, source)
      VALUES (?, ?, ?, ?, ?, ?, 'TradingView Desktop MCP reconcile')
    `).run(runId, symbol, tvBar.d, field, old[field] ?? null, tvBar[field]);
  }
  const resolved = resolveQualityExclusion(db, symbol, tvBar.d);
  return changed + resolved;
}

function normalizeBars(raw) {
  const bars = raw?.bars || raw?.data || [];
  return bars.map(b => ({
    time: b.time ? Number(b.time) : null,
    d: b.time ? new Date(Number(b.time) * 1000).toISOString().slice(0, 10) : b.date,
    open: Number(b.open),
    high: Number(b.high),
    low: Number(b.low),
    close: Number(b.close),
    volume: Number(b.volume ?? 0),
  })).filter(b => b.d);
}

async function main() {
  const db = getDB();
  ensureTables(db);

  const health = await callMCPTool('tv_health_check', {});
  if (!health?.success) {
    const info = {
      success: false,
      tv_connected: false,
      error: health?.error || 'TradingView Desktop is not connected',
      checked_locally: symbols.length,
      instruction: 'Run with TradingView Desktop CDP active or use npm run egx:tv:auto:launch.',
    };
    db.prepare(`
      INSERT INTO tv_data_reconcile_runs(status, symbols_checked, mismatches, tv_connected, notes)
      VALUES ('SKIPPED_NO_TV', ?, 0, 0, ?)
    `).run(symbols.length, info.error);
    console.log(JSON.stringify(info, null, 2));
    return;
  }

  const runId = db.prepare(`
    INSERT INTO tv_data_reconcile_runs(status, symbols_checked, mismatches, tv_connected, notes)
    VALUES ('RUNNING', ?, 0, 1, NULL)
  `).run(symbols.length).lastInsertRowid;

  const insert = db.prepare(`
    INSERT INTO tv_data_reconcile_items
    (run_id, symbol, tv_symbol, bar_date, field, local_value, tv_value, diff_pct, status, note)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  const mismatches = [];
  let repairs = 0;
  const checked = [];
  for (const symbol of symbols) {
    const tvSymbol = toTvSymbol(symbol);
    await callMCPTool('chart_set_symbol', { symbol: tvSymbol });
    await new Promise(r => setTimeout(r, 800));
    await callMCPTool('chart_set_timeframe', { timeframe: 'D' });
    await new Promise(r => setTimeout(r, 500));
    const info = await callMCPTool('symbol_info', {});
    const ohlcv = await callMCPTool('data_get_ohlcv', { count, summary: false });
    const tvBars = normalizeBars(ohlcv);
    const local = new Map(localRows(db, symbol, count).map(r => [r.d, r]));
    checked.push({ symbol, tv_symbol: tvSymbol, resolved: info?.full_name || info?.symbol || null, tv_bars: tvBars.length, local_bars: local.size });

    for (const tvBar of tvBars) {
      const localBar = local.get(tvBar.d);
      if (!localBar) {
        insert.run(runId, symbol, tvSymbol, tvBar.d, 'bar', null, null, null, 'MISSING_LOCAL', 'TV has a bar missing from local DB');
        if (REPAIR) repairs += repairFullBar(db, runId, symbol, tvBar);
        mismatches.push({ symbol, date: tvBar.d, field: 'bar', status: 'MISSING_LOCAL' });
        continue;
      }
      for (const field of ['open', 'high', 'low', 'close', 'volume']) {
        const diff = pctDiff(localBar[field], tvBar[field]);
        const tolerance = field === 'volume' ? 0.03 : 0.005;
        const status = diff == null || diff <= tolerance ? 'OK' : 'MISMATCH';
        insert.run(runId, symbol, tvSymbol, tvBar.d, field, localBar[field], tvBar[field], diff, status, null);
        if (status !== 'OK') {
          if (REPAIR) repairs += repairLocalField(db, runId, symbol, tvBar.d, field, localBar[field], tvBar[field]);
          mismatches.push({ symbol, date: tvBar.d, field, local: localBar[field], tv: tvBar[field], diff_pct: diff });
        }
      }
    }
  }

  const status = mismatches.length && !REPAIR ? 'WARN' : 'PASS';
  db.prepare('UPDATE tv_data_reconcile_runs SET status=?, mismatches=?, notes=? WHERE id=?')
    .run(status, mismatches.length, JSON.stringify({ checked, repair: REPAIR, repairs }).slice(0, 3000), runId);

  console.log(JSON.stringify({
    success: true,
    status,
    tv_connected: true,
    run_id: runId,
    checked,
    mismatches: mismatches.slice(0, 50),
    mismatch_count: mismatches.length,
    repair_enabled: REPAIR,
    repairs_applied: repairs,
  }, null, 2));
  process.exit(status === 'WARN' ? 2 : 0);
}

main().catch(err => {
  console.error(JSON.stringify({ success: false, error: err.message }, null, 2));
  process.exit(1);
});
