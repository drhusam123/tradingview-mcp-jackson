/**
 * Liquid tier symbol selection from liquidity_profile + EGX universe.
 */
import Database from 'better-sqlite3';
import { EGX_UNIVERSE, EGX_UNIVERSE_CORE } from '../../src/egx/index.js';

/** TV/CDP OHLCV extract fails repeatedly — excluded from liquid rotation. */
export const INTRADAY_TV_SKIP = new Set(['ANCC', 'DCCC', 'HDST', 'ANFI']);

/**
 * @param {Database} db
 * @param {{ limit?: number, offset?: number }} opts
 * @returns {string[]}
 */
export function loadLiquidTierSymbols(db, { limit = 80, offset = 0 } = {}) {
  const hasTable = db.prepare(
    "SELECT 1 ok FROM sqlite_master WHERE type='table' AND name='liquidity_profile'",
  ).get()?.ok === 1;

  if (!hasTable) {
    return [...new Set([...EGX_UNIVERSE_CORE, ...EGX_UNIVERSE])].slice(offset, offset + limit);
  }

  const rows = db.prepare(`
    SELECT lp.symbol
    FROM liquidity_profile lp
    INNER JOIN (
      SELECT symbol, MAX(computed_date) AS latest
      FROM liquidity_profile
      GROUP BY symbol
    ) ld ON lp.symbol = ld.symbol AND lp.computed_date = ld.latest
    WHERE lp.liquidity_tier IN ('TIER1', 'TIER2')
    ORDER BY lp.advt_10d DESC
  `).all();

  const liquid = rows.map(r => r.symbol).filter(s =>
    EGX_UNIVERSE.includes(s) && !INTRADAY_TV_SKIP.has(s),
  );
  const coreFirst = [
    ...EGX_UNIVERSE_CORE.filter(s => EGX_UNIVERSE.includes(s)),
    ...liquid.filter(s => !EGX_UNIVERSE_CORE.includes(s)),
  ];
  const unique = [...new Set(coreFirst)];
  return unique.slice(offset, offset + limit);
}
