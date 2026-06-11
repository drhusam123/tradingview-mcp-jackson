#!/usr/bin/env node
/**
 * Pine Analytics Rotation — 80 symbols/day from actionable + top scans.
 * Ensures full universe coverage every ~3 trading sessions.
 */
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { getDB } from '../src/egx/index.js';
import { FINAL_SIGNALS_MAX_DATE_SUBQUERY } from './lib/final_signals_query.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const PER_DAY = 80;

function pickRotationSymbols(db) {
  const actionable = db.prepare(`
    SELECT symbol FROM final_signals
    WHERE trade_date = ${FINAL_SIGNALS_MAX_DATE_SUBQUERY}
      AND actionable = 1
    ORDER BY score DESC LIMIT 20
  `).all().map(r => r.symbol);

  const topScan = db.prepare(`
    SELECT symbol FROM scans
    WHERE scan_date = (SELECT MAX(scan_date) FROM scans) AND rejected = 0
    ORDER BY score DESC LIMIT 40
  `).all().map(r => r.symbol);

  const stalePine = db.prepare(`
    SELECT DISTINCT ic.symbol
    FROM indicators_cache ic
    LEFT JOIN pine_analytics pa ON pa.symbol = ic.symbol
      AND pa.bar_date >= date('now', '-5 days')
      AND COALESCE(pa.source, '') NOT LIKE '%fallback%'
    WHERE pa.symbol IS NULL
    ORDER BY ic.symbol
    LIMIT ?
  `).all(Math.max(PER_DAY, 120)).map(r => r.symbol);

  const out = [];
  const seen = new Set();
  for (const s of [...actionable, ...topScan, ...stalePine]) {
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
    if (out.length >= PER_DAY) break;
  }
  return out;
}

function main() {
  const db = getDB();
  const symbols = pickRotationSymbols(db);
  if (!symbols.length) {
    console.log(JSON.stringify({ success: true, rotated: 0, note: 'no symbols' }));
    return;
  }

  const symArg = symbols.join(',');
  const cmd = `node scripts/fetch_pine_analytics.mjs all --symbols ${symArg} --local-fallback`;
  try {
    execSync(cmd, { cwd: ROOT, stdio: 'inherit', timeout: 600_000 });
  } catch (e) {
    console.warn(`[pine-rotation] TV fetch partial: ${e.message}`);
    execSync(
      `node scripts/fetch_pine_analytics.mjs all --local-only --symbols ${symArg}`,
      { cwd: ROOT, stdio: 'inherit', timeout: 300_000 },
    );
  }

  console.log(JSON.stringify({
    success: true,
    rotated: symbols.length,
    symbols: symbols.slice(0, 10),
  }, null, 2));
}

main();
