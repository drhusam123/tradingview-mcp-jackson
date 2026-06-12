/**
 * Universe hygiene — classify ghosts, renames, and delisted symbols.
 */
import Database from 'better-sqlite3';

/** Known EGX ticker renames (old → current active symbol). */
export const RENAME_MAP = {
  QNBA: 'QNBE',
  MNHD: 'CIRA',
  EKHW: 'ARVA',
  ESRS: 'TAQA',
  SIDI: 'ELWA',
  OBGI: 'VALU',
};

/** Symbols confirmed delisted / merged — no active successor in universe. */
export const DELISTED_KNOWN = new Set([
  'AIBL', 'ALXS', 'ARCO', 'BARI', 'BDCA', 'BFIN', 'BKCO', 'CLPH', 'DOMG',
  'EFTS', 'EMAR', 'EMIC', 'IBHG', 'IRCE', 'KIMA', 'MAHA', 'MFCO', 'MOPCO',
  'NASR', 'NBSK', 'NKCL', 'NSGB', 'OBOL', 'PHOS', 'SGBC', 'VENT',
]);

export function ensureHygieneColumns(db) {
  const cols = new Set(
    db.prepare('PRAGMA table_info(stock_universe)').all().map(r => r.name),
  );
  if (!cols.has('successor_symbol')) {
    db.exec('ALTER TABLE stock_universe ADD COLUMN successor_symbol TEXT');
  }
  if (!cols.has('archived_at')) {
    db.exec('ALTER TABLE stock_universe ADD COLUMN archived_at TEXT');
  }
  if (!cols.has('hygiene_reason')) {
    db.exec('ALTER TABLE stock_universe ADD COLUMN hygiene_reason TEXT');
  }
}

export function classifySymbol(db, symbol) {
  const row = db.prepare(
    'SELECT symbol, status, successor_symbol, archived_at FROM stock_universe WHERE symbol=?',
  ).get(symbol);
  if (!row) return { symbol, category: 'missing_from_universe' };

  const hasOhlcv = db.prepare(
    'SELECT 1 ok FROM ohlcv_history WHERE symbol=? LIMIT 1',
  ).get(symbol)?.ok === 1;

  if (hasOhlcv) {
    return {
      symbol,
      category: 'active_with_ohlcv',
      status: row.status,
      has_ohlcv: true,
    };
  }

  if (RENAME_MAP[symbol]) {
    const successor = RENAME_MAP[symbol];
    const successorHasOhlcv = db.prepare(
      'SELECT 1 ok FROM ohlcv_history WHERE symbol=? LIMIT 1',
    ).get(successor)?.ok === 1;
    return {
      symbol,
      category: 'renamed',
      status: row.status,
      successor_symbol: successor,
      successor_has_ohlcv: successorHasOhlcv,
      recommended_action: 'archive_with_successor',
    };
  }

  if (DELISTED_KNOWN.has(symbol) || row.status === 'invalid') {
    return {
      symbol,
      category: 'delisted_or_ghost',
      status: row.status,
      recommended_action: 'archive_delisted',
    };
  }

  return {
    symbol,
    category: 'fetchable_missing',
    status: row.status,
    recommended_action: 'investigate_fetch',
  };
}

export function buildHygieneReport(db) {
  ensureHygieneColumns(db);

  const universe = db.prepare('SELECT symbol, status FROM stock_universe ORDER BY symbol').all();
  const ohlcvSyms = new Set(
    db.prepare('SELECT DISTINCT symbol FROM ohlcv_history').all().map(r => r.symbol),
  );

  const ghosts = universe.filter(u => !ohlcvSyms.has(u.symbol));
  const classified = ghosts.map(g => classifySymbol(db, g.symbol));

  const byCategory = {};
  for (const c of classified) {
    byCategory[c.category] = (byCategory[c.category] ?? 0) + 1;
  }

  const unarchivedGhosts = db.prepare(`
    SELECT COUNT(*) n FROM stock_universe u
    WHERE NOT EXISTS (SELECT 1 FROM ohlcv_history h WHERE h.symbol = u.symbol)
      AND (u.archived_at IS NULL OR u.archived_at = '')
      AND u.status NOT IN ('archived')
  `).get()?.n ?? 0;

  const dailyN = db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history').get()?.n ?? 0;
  const weeklyN = db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_weekly').get()?.n ?? 0;
  const weeklyGap = db.prepare(`
    SELECT symbol FROM (SELECT DISTINCT symbol FROM ohlcv_history)
    WHERE symbol NOT IN (SELECT DISTINCT symbol FROM ohlcv_weekly)
    ORDER BY symbol
  `).all().map(r => r.symbol);

  return {
    at: new Date().toISOString(),
    universe_total: universe.length,
    ohlcv_symbols: ohlcvSyms.size,
    ghosts_no_ohlcv: ghosts.length,
    unarchived_ghosts: unarchivedGhosts,
    by_category: byCategory,
    classified,
    weekly_gap: {
      daily_symbols: dailyN,
      weekly_symbols: weeklyN,
      gap_count: weeklyGap.length,
      missing_symbols: weeklyGap,
    },
    rename_map: RENAME_MAP,
  };
}

export function applyHygiene(db, { dryRun = false } = {}) {
  ensureHygieneColumns(db);
  const report = buildHygieneReport(db);
  const now = new Date().toISOString();
  const applied = [];

  const backup = db.prepare('SELECT * FROM stock_universe').all();

  for (const item of report.classified) {
    if (item.category === 'active_with_ohlcv') continue;

    let status = 'archived';
    let successor = null;
    let reason = item.category;

    if (item.category === 'renamed') {
      successor = item.successor_symbol;
      reason = `renamed→${successor}`;
    } else if (item.category === 'delisted_or_ghost') {
      reason = 'delisted_or_invalid_ghost';
    } else if (item.category === 'fetchable_missing') {
      status = 'pending';
      reason = 'fetchable_missing_ohlcv';
    }

    if (dryRun) {
      applied.push({ symbol: item.symbol, status, successor_symbol: successor, hygiene_reason: reason, dry_run: true });
      continue;
    }

    db.prepare(`
      UPDATE stock_universe
      SET status = ?, successor_symbol = ?, archived_at = ?, hygiene_reason = ?
      WHERE symbol = ?
    `).run(status, successor, status === 'archived' ? now : null, reason, item.symbol);

    applied.push({ symbol: item.symbol, status, successor_symbol: successor, hygiene_reason: reason });
  }

  return { applied, backup_count: backup.length, at: now };
}
