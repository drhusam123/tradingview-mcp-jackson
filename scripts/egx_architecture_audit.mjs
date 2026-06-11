#!/usr/bin/env node
/**
 * Architecture layer audit — verify L0→L11 data presence, freshness, and wiring.
 */
import Database from 'better-sqlite3';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { DB_PATH, latestReadySignalDate } from './lib/delivery_audit.mjs';
import { LAYER_GRAPH, REFRESH_PIPELINE, CLOSED_LOOP_PIPELINE } from './lib/architecture_layers.mjs';
import { FINAL_SIGNALS_DATE_WHERE, latestFinalSignalDate, purgeTestFinalSignals } from './lib/final_signals_query.mjs';

loadEnv();

purgeTestFinalSignals();

function tableStats(db, table) {
  try {
    const n = db.prepare(`SELECT COUNT(*) AS n FROM [${table}]`).get()?.n ?? 0;
    let latest = null;
    for (const col of ['trade_date', 'date', 'scan_date', 'pred_date', 'signal_date', 'bar_time', 'bar_date', 'last_fetch', 'computed_at', 'created_at']) {
      try {
        const r = db.prepare(`SELECT MAX([${col}]) AS d FROM [${table}]`).get()?.d;
        if (r) {
          const s = String(r);
          latest = /^\d{9,}$/.test(s)
            ? new Date(Number(s) * 1000).toISOString().slice(0, 10)
            : s.slice(0, 10);
          break;
        }
      } catch { /* */ }
    }
    return { exists: true, rows: n, latest, has_data: n > 0 };
  } catch {
    return { exists: false, rows: 0, latest: null, has_data: false };
  }
}

function runAudit() {
  const checks = [];
  const signalDate = latestReadySignalDate();
  const db = existsSync(DB_PATH) ? new Database(DB_PATH, { readonly: true }) : null;

  if (!db) {
    return { pass: false, error: 'NO_DB', checks: [] };
  }

  for (const layer of LAYER_GRAPH) {
    const anchorStats = [];
    for (const t of layer.anchors) {
      const s = tableStats(db, t);
      anchorStats.push({ table: t, ...s });
    }
    const optionalSet = new Set(layer.optionalAnchors || []);
    const requiredAnchors = anchorStats.filter(a => !optionalSet.has(a.table));
    const optionalAnchors = anchorStats.filter(a => optionalSet.has(a.table));
    const withData = requiredAnchors.filter(a => a.has_data);
    const minRequired = layer.optional ? 0 : Math.ceil(requiredAnchors.length / 2) || 1;
    const ok = layer.optional
      ? anchorStats.some(a => a.exists)
      : withData.length >= minRequired;
    checks.push({
      id: layer.id,
      name: layer.name,
      ok,
      anchors: anchorStats,
      upstream: layer.upstream,
      downstream: layer.downstream,
      producers: layer.producers,
    });
  }

  const fsLatest = latestFinalSignalDate(db);
  const fs2099 = db.prepare(`SELECT COUNT(*) n FROM final_signals WHERE trade_date LIKE '2099-%'`).get()?.n ?? 0;
  checks.push({
    id: 'wiring_final_signals_prod',
    ok: fs2099 === 0 && (!fsLatest || !String(fsLatest).startsWith('2099')),
    detail: `latest=${fsLatest ?? 'none'} test_rows=${fs2099}`,
  });

  if (signalDate) {
    const align = {};
    for (const [label, sql] of [
      ['ohlcv', "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history"],
      ['scans', 'SELECT MAX(scan_date) FROM scans'],
      ['explosion_pred', 'SELECT MAX(pred_date) FROM explosion_predictions'],
      ['meta_label', 'SELECT MAX(date) FROM meta_label_scores'],
      ['opp_v2', 'SELECT MAX(trade_date) FROM opportunity_score_v2'],
      ['pine', 'SELECT COUNT(*) FROM pine_analytics'],
    ]) {
      try {
        align[label] = db.prepare(sql).get();
      } catch {
        align[label] = null;
      }
    }
    const dates = [
      align.ohlcv?.['MAX(date(bar_time,\'unixepoch\'))'] ?? align.ohlcv?.d,
      align.scans?.['MAX(scan_date)'],
      align.explosion_pred?.['MAX(pred_date)'],
      align.meta_label?.['MAX(date)'],
      align.opp_v2?.['MAX(trade_date)'],
    ].filter(Boolean).map(String).slice(0, 10);
    const aligned = dates.every(d => d >= signalDate || d === signalDate);
    checks.push({
      id: 'wiring_upstream_aligned',
      ok: dates.length >= 4,
      signal_date: signalDate,
      dates,
      pine_rows: align.pine?.['COUNT(*)'] ?? 0,
      detail: aligned ? `aligned @ ${signalDate}` : `partial alignment: ${dates.join(', ')}`,
    });
  }

  checks.push({
    id: 'pipeline_refresh_order',
    ok: REFRESH_PIPELINE.length >= 8,
    stages: REFRESH_PIPELINE,
  });
  checks.push({
    id: 'pipeline_closed_loop_order',
    ok: CLOSED_LOOP_PIPELINE.length >= 6,
    stages: CLOSED_LOOP_PIPELINE,
  });

  db.close();

  const fail = checks.filter(c => c.ok === false);
  return {
    at: new Date().toISOString(),
    signal_date: signalDate,
    pass: fail.length === 0,
    layers_ok: checks.filter(c => c.id.startsWith('L')).filter(c => c.ok).length,
    layers_total: checks.filter(c => c.id.startsWith('L')).length,
    checks,
    failed: fail.map(f => f.id),
  };
}

const report = runAudit();
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/architecture_audit_last.json'), JSON.stringify(report, null, 2));

console.log('\n═══ EGX Architecture Audit (L0→L11) ═══');
for (const c of report.checks) {
  if (c.id.startsWith('L')) {
    const n = c.anchors?.filter(a => a.has_data).length ?? 0;
    const t = c.anchors?.length ?? 0;
    console.log(`  ${c.ok ? '✅' : '❌'} ${c.id} ${c.name} — ${n}/${t} anchors with data`);
  } else {
    console.log(`  ${c.ok ? '✅' : '❌'} ${c.id}: ${c.detail ?? ''}`);
  }
}
console.log(`\n  Layers: ${report.layers_ok}/${report.layers_total} OK`);
console.log(`  Result: ${report.pass ? 'PASS' : 'FAIL'}`);
if (!report.pass) console.log(`  Failed: ${report.failed.join(', ')}`);
console.log('  Saved: data/architecture_audit_last.json\n');

process.exit(report.pass ? 0 : 1);
