/**
 * Indicator cache coverage gate — ensures deliverable symbols have cache rows
 * for the signal session date before client safety veto runs.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH, countActionable } from './delivery_audit.mjs';

const DEFAULT_MIN_UNIVERSE = parseInt(process.env.EGX_CACHE_MIN_SYMBOLS || '180', 10);

function dbReadonly() {
  if (!existsSync(DB_PATH)) return null;
  const d = new Database(DB_PATH, { readonly: true });
  d.pragma('busy_timeout = 5000');
  return d;
}

/** Universe coverage for a session date (post rebuild_indicators). */
export function checkIndicatorCacheCoverage(signalDate) {
  const d = dbReadonly();
  if (!d) return { ok: false, error: 'NO_DB', signal_date: signalDate };

  const row = d.prepare(`
    SELECT COUNT(DISTINCT symbol) AS n, MAX(bar_date) AS latest
    FROM indicators_cache WHERE bar_date=?
  `).get(signalDate);

  const universe = d.prepare(`
    SELECT COUNT(DISTINCT symbol) AS n FROM indicators_cache
  `).get()?.n ?? 0;

  d.close();

  const n = row?.n ?? 0;
  const minNeeded = DEFAULT_MIN_UNIVERSE;
  return {
    ok: n >= minNeeded,
    signal_date: signalDate,
    symbols_on_date: n,
    latest_cache_date: row?.latest ?? null,
    universe_symbols: universe,
    min_required: minNeeded,
    reason: n >= minNeeded ? null : `only ${n}/${minNeeded} symbols cached for ${signalDate}`,
  };
}

/** Per-symbol check for actionable deliverable names. */
export function verifyActionableIndicatorCache(signalDate) {
  const act = countActionable(signalDate);
  if (!act.symbols.length) {
    return { ok: true, signal_date: signalDate, actionable: [], missing: [] };
  }

  const d = dbReadonly();
  if (!d) return { ok: false, error: 'NO_DB', actionable: act.symbols, missing: act.symbols };

  const missing = [];
  const stmt = d.prepare(`
    SELECT 1 FROM indicators_cache WHERE symbol=? AND bar_date=? LIMIT 1
  `);
  for (const sym of act.symbols) {
    if (!stmt.get(sym, signalDate)) missing.push(sym);
  }
  d.close();

  return {
    ok: missing.length === 0,
    signal_date: signalDate,
    actionable: act.symbols,
    missing,
    coverage: act.symbols.length - missing.length,
    total: act.symbols.length,
  };
}

export function enforceActionableIndicatorCache(signalDate, { exitOnFail = false } = {}) {
  const cov = checkIndicatorCacheCoverage(signalDate);
  const act = verifyActionableIndicatorCache(signalDate);
  const ok = cov.ok && act.ok;
  const result = { ...cov, actionable_check: act, ok };
  if (!ok && exitOnFail) {
    const err = new Error(
      act.missing.length
        ? `indicator_cache missing for: ${act.missing.join(', ')}`
        : cov.reason || 'indicator_cache coverage low',
    );
    err.gate = result;
    throw err;
  }
  return result;
}
