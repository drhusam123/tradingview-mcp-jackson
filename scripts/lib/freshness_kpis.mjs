/**
 * Data layer freshness KPIs — shared by production ops and audits.
 */
import { existsSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SKIP_PY = new Set([
  'data_quality_gate.py',
  'historical_integrity_engine.py',
]);

export function loadFreshnessKpis(projectRoot, dbPath) {
  const auditPath = join(projectRoot, 'data/data_layer_audit_last.json');
  const manifestPath = join(projectRoot, 'data/parquet/_manifest.json');
  const hygienePath = join(projectRoot, 'data/universe_hygiene_report.json');

  const audit = existsSync(auditPath)
    ? JSON.parse(readFileSync(auditPath, 'utf8'))
    : null;
  const manifest = existsSync(manifestPath)
    ? JSON.parse(readFileSync(manifestPath, 'utf8'))
    : null;
  const hygiene = existsSync(hygienePath)
    ? JSON.parse(readFileSync(hygienePath, 'utf8'))
    : null;

  let dbKpis = null;
  if (existsSync(dbPath)) {
    const db = new Database(dbPath, { readonly: true });
    const signalDate = db.prepare(
      "SELECT MAX(date(bar_time,'unixepoch')) d FROM ohlcv_history",
    ).get()?.d ?? null;

    const hasSource = db.prepare(
      "SELECT 1 ok FROM pragma_table_info('indicators_cache') WHERE name='source'",
    ).get()?.ok === 1;

    dbKpis = {
      signal_date: signalDate,
      ohlcv_symbols: db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history').get()?.n ?? 0,
      weekly_symbols: db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_weekly').get()?.n ?? 0,
      intraday_60: db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_60min').get()?.n ?? 0,
      indicators: db.prepare('SELECT COUNT(*) n FROM indicators_cache').get()?.n ?? 0,
      indicators_on_date: signalDate
        ? db.prepare('SELECT COUNT(DISTINCT symbol) n FROM indicators_cache WHERE bar_date=?').get(signalDate)?.n ?? 0
        : 0,
      explosion: signalDate
        ? db.prepare('SELECT COUNT(DISTINCT symbol) n FROM explosion_predictions WHERE pred_date=?').get(signalDate)?.n ?? 0
        : 0,
      meta_label: signalDate
        ? db.prepare('SELECT COUNT(DISTINCT symbol) n FROM meta_label_scores WHERE date=?').get(signalDate)?.n ?? 0
        : 0,
      unarchived_ghosts: db.prepare(`
        SELECT COUNT(*) n FROM stock_universe u
        WHERE NOT EXISTS (SELECT 1 FROM ohlcv_history h WHERE h.symbol = u.symbol)
          AND (u.archived_at IS NULL OR u.archived_at = '')
          AND u.status NOT IN ('archived')
      `).get()?.n ?? 0,
      indicators_by_source: hasSource
        ? db.prepare('SELECT source, COUNT(*) n FROM indicators_cache GROUP BY source').all()
        : [],
    };
    db.close();
  }

  const pqAgeH = existsSync(manifestPath)
    ? Math.round((Date.now() - statSync(manifestPath).mtimeMs) / 36e5 * 10) / 10
    : null;

  return {
    at: new Date().toISOString(),
    data_layer_audit: audit ? { pass: audit.pass, checks: audit.checks?.length ?? 0, failed: audit.failed ?? [] } : null,
    parquet: {
      age_h: pqAgeH,
      tables: manifest?.tables ? Object.keys(manifest.tables) : Object.keys(manifest || {}).filter(k => k !== 'exported_at'),
      ohlcv_rows: manifest?.ohlcv_history?.rows ?? manifest?.tables?.ohlcv_history?.rows ?? null,
    },
    hygiene: hygiene ? { unarchived_ghosts: hygiene.unarchived_ghosts, weekly_gap: hygiene.weekly_gap?.gap_count } : null,
    db: dbKpis,
  };
}

export function formatFreshnessLines(kpis) {
  const lines = [];
  if (kpis.data_layer_audit) {
    lines.push(`  Data audit:  ${kpis.data_layer_audit.pass ? '✅ PASS' : '❌ FAIL'} (${kpis.data_layer_audit.checks} checks)`);
  }
  if (kpis.db) {
    const d = kpis.db;
    lines.push(`  Signal date: ${d.signal_date ?? '—'}`);
    lines.push(`  OHLCV:       ${d.ohlcv_symbols} sym | weekly ${d.weekly_symbols} | intraday60 ${d.intraday_60}`);
    lines.push(`  Indicators:  ${d.indicators_on_date}/${d.indicators} on date | sources ${JSON.stringify(d.indicators_by_source)}`);
    lines.push(`  ML:          meta ${d.meta_label} | explosion ${d.explosion}`);
    lines.push(`  Ghosts:      ${d.unarchived_ghosts} unarchived`);
  }
  if (kpis.parquet) {
    lines.push(`  Parquet:     age ${kpis.parquet.age_h ?? '?'}h | tables ${(kpis.parquet.tables || []).length}`);
  }
  return lines;
}
