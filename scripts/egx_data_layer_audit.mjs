#!/usr/bin/env node
/**
 * L0/L1 Data Layer Audit — OHLCV, universe, indicators, parquet, MCP wiring.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync, statSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { DB_PATH, latestReadySignalDate } from './lib/delivery_audit.mjs';
import { getOHLCV, getHistoryStats } from '../src/egx/index.js';
import { checkIndicatorCacheCoverage } from './lib/indicator_cache_gate.mjs';

loadEnv();

const checks = [];
function ok(id, pass, detail = '') {
  checks.push({ id, ok: pass, detail });
}

function ageHours(path) {
  if (!existsSync(path)) return null;
  return Math.round((Date.now() - statSync(path).mtimeMs) / 36e5 * 10) / 10;
}

function runAudit() {
  const signalDate = latestReadySignalDate();
  if (!existsSync(DB_PATH)) {
    return { pass: false, error: 'NO_DB', checks: [] };
  }

  const db = new Database(DB_PATH, { readonly: true });

  const execView = db.prepare(
    "SELECT 1 ok FROM sqlite_master WHERE type='view' AND name='ohlcv_history_execution'",
  ).get()?.ok === 1;
  ok('l0_execution_view', execView, execView ? 'ohlcv_history_execution present' : 'MISSING — run gate_daily');

  const rawN = db.prepare('SELECT COUNT(*) n FROM ohlcv_history').get()?.n ?? 0;
  const execN = execView
    ? db.prepare('SELECT COUNT(*) n FROM ohlcv_history_execution').get()?.n ?? 0
    : rawN;
  const ohlcvLatest = db.prepare(
    "SELECT MAX(date(bar_time,'unixepoch')) d FROM ohlcv_history",
  ).get()?.d ?? null;
  ok('l0_ohlcv_rows', rawN > 50000, `raw=${rawN} execution=${execN} latest=${ohlcvLatest ?? 'none'}`);

  const universeN = db.prepare('SELECT COUNT(*) n FROM stock_universe').get()?.n ?? 0;
  const universeLatest = db.prepare('SELECT MAX(last_fetch) d FROM stock_universe').get()?.d ?? null;
  ok('l0_stock_universe', universeN >= 200, `${universeN} symbols | last_fetch=${universeLatest ?? 'none'}`);

  const icN = db.prepare('SELECT COUNT(*) n FROM indicators_cache').get()?.n ?? 0;
  const icTest = db.prepare(
    "SELECT COUNT(*) n FROM indicators_cache WHERE bar_date LIKE '2099-%'",
  ).get()?.n ?? 0;
  const icLatest = db.prepare(
    "SELECT MAX(bar_date) d FROM indicators_cache WHERE bar_date NOT LIKE '2099-%'",
  ).get()?.d ?? null;
  ok('l1_indicators_cache', icN > 1000 && icTest === 0,
    `${icN} rows | latest=${icLatest ?? 'none'} test_rows=${icTest}`);

  const intra60 = db.prepare('SELECT COUNT(*) n, COUNT(DISTINCT symbol) sym FROM ohlcv_60min').get();
  const intra15 = db.prepare('SELECT COUNT(*) n, COUNT(DISTINCT symbol) sym FROM ohlcv_15min').get();
  db.close();

  ok('l0_intraday_60min', (intra60?.sym ?? 0) >= 20,
    `bars=${intra60?.n ?? 0} symbols=${intra60?.sym ?? 0} (core target ≥20)`);
  ok('l0_intraday_15min', (intra15?.sym ?? 0) >= 20,
    `bars=${intra15?.n ?? 0} symbols=${intra15?.sym ?? 0}`);

  if (signalDate) {
    const cacheGate = checkIndicatorCacheCoverage(signalDate);
    ok('l1_cache_coverage', cacheGate.ok,
      `${cacheGate.symbols_on_date}/${cacheGate.min_required} on ${signalDate}`);
  }

  const sample = getOHLCV('COMI', 5, { execution: true });
  ok('l1_getOHLCV_execution', sample.length >= 1,
    `COMI bars=${sample.length} vol>0=${sample.every(b => b.volume > 0)}`);

  const stats = getHistoryStats();
  ok('l0_history_stats', (stats?.summary?.total_symbols ?? 0) >= 200,
    `${stats?.summary?.total_symbols ?? 0} symbols | ${stats?.summary?.total_bars ?? 0} bars`);

  const manifestPath = join(PROJECT_ROOT, 'data/parquet/_manifest.json');
  const manifest = existsSync(manifestPath)
    ? JSON.parse(readFileSync(manifestPath, 'utf8'))
    : null;
  const pqAge = ageHours(manifestPath);
  const pqRows = manifest?.ohlcv_history?.rows ?? manifest?.tables?.ohlcv_history?.rows ?? 0;
  ok('l0_parquet_snapshot', pqRows > 50000 && pqAge != null && pqAge < 168,
    `ohlcv parquet rows=${pqRows} age_h=${pqAge ?? 'missing'}`);

  const hydratePy = readFileSync(join(PROJECT_ROOT, 'scripts/python/discovery_data_hydrate.py'), 'utf8');
  ok('hydrate_l0_wired', hydratePy.includes('stock_universe') && hydratePy.includes('ohlcv_history'),
    'HYDRATE_CMDS includes L0 targets');
  ok('hydrate_exit_codes', hydratePy.includes('exit_code') && hydratePy.includes('proc.returncode'),
    'subprocess exit validated');

  const dbJs = readFileSync(join(PROJECT_ROOT, 'src/egx/database.js'), 'utf8');
  ok('getOHLCV_execution_view', dbJs.includes('ohlcv_history_execution'),
    'getOHLCV prefers execution view');

  const batchJs = readFileSync(join(PROJECT_ROOT, 'src/core/batch.js'), 'utf8');
  ok('batch_get_ohlcv_unified', batchJs.includes('getOhlcv'),
    'batch_run uses getOhlcv from data.js');

  const mcpTools = readFileSync(join(PROJECT_ROOT, 'src/tools/data.js'), 'utf8');
  ok('mcp_data_get_ohlcv', mcpTools.includes('data_get_ohlcv'),
    'MCP data_get_ohlcv registered');
  ok('mcp_quote_get', mcpTools.includes('quote_get'), 'MCP quote_get registered');

  const fail = checks.filter(c => !c.ok);
  return {
    at: new Date().toISOString(),
    signal_date: signalDate,
    pass: fail.length === 0,
    checks,
    failed: fail.map(f => f.id),
  };
}

const report = runAudit();
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/data_layer_audit_last.json'), JSON.stringify(report, null, 2));

console.log('\n═══ EGX Data Layer Audit (L0→L1) ═══');
for (const c of report.checks) {
  console.log(`  ${c.ok ? '✅' : '❌'} ${c.id}: ${c.detail}`);
}
console.log(`\n  Result: ${report.pass ? 'PASS' : 'FAIL'}`);
if (!report.pass) console.log(`  Failed: ${report.failed.join(', ')}`);
console.log('  Saved: data/data_layer_audit_last.json\n');

process.exit(report.pass ? 0 : 1);
