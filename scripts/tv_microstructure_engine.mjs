#!/usr/bin/env node
/**
 * TV Microstructure Engine — Phase 2 sensing layer.
 *
 * 1. Select watchlist (wide: actionable + opp + scans + volume leaders + universe)
 * 2. Fetch pine analytics (TV live or local OHLCV fallback)
 * 3. Derive atoms → tv_discovery_features table
 *
 * Usage:
 *   npm run egx:discovery:tv:micro
 *   node scripts/tv_microstructure_engine.mjs --local-only --max-symbols 30
 *   node scripts/tv_microstructure_engine.mjs --wide --max-symbols 50
 */
import { execFileSync, execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import Database from 'better-sqlite3';
import { PROJECT_ROOT } from './lib/load_env.mjs';
import { latestReadySignalDate, DB_PATH } from './lib/delivery_audit.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const NODE = process.execPath;

const LOCAL_ONLY = process.argv.includes('--local-only');
const LOCAL_FALLBACK = process.argv.includes('--local-fallback') || LOCAL_ONLY;
const WIDE = process.argv.includes('--wide');
const maxArg = (() => {
  const i = process.argv.indexOf('--max-symbols');
  return i >= 0 ? Math.max(1, parseInt(process.argv[i + 1], 10) || 30) : 30;
})();
const dateArg = (() => {
  const i = process.argv.indexOf('--date');
  return i >= 0 ? process.argv[i + 1] : null;
})();

const signalDate = dateArg || latestReadySignalDate();
if (!signalDate) {
  console.error(JSON.stringify({ success: false, error: 'NO_SIGNAL_DATE' }));
  process.exit(1);
}

const symbolLimit = WIDE ? Math.max(maxArg, 50) : maxArg;

function addUnique(out, sym) {
  const s = String(sym || '').trim().toUpperCase();
  if (!s || out.includes(s)) return;
  out.push(s);
}

function selectSymbols(limit, tradeDate) {
  if (!existsSync(DB_PATH)) return [];
  const db = new Database(DB_PATH, { readonly: true });
  const out = [];

  try {
    // 1) Client-actionable candidates (break chicken-and-egg with opp-only selection)
    try {
      const actionable = db.prepare(`
        SELECT symbol FROM final_signals
        WHERE trade_date=? AND actionable=1 AND veto_reason IS NULL
        ORDER BY score DESC LIMIT ?
      `).all(tradeDate, limit);
      for (const r of actionable) addUnique(out, r.symbol);
    } catch { /* */ }

    // 2) High opportunity scores (if prior run exists)
    try {
      const opp = db.prepare(`
        SELECT symbol FROM opportunity_score_v2
        WHERE trade_date=? AND opportunity_score >= 62
        ORDER BY opportunity_score DESC LIMIT ?
      `).all(tradeDate, limit);
      for (const r of opp) addUnique(out, r.symbol);
      if (out.length < limit) {
        const oppSoft = db.prepare(`
          SELECT symbol FROM opportunity_score_v2
          WHERE trade_date=? AND opportunity_score >= 55
          ORDER BY opportunity_score DESC LIMIT ?
        `).all(tradeDate, limit);
        for (const r of oppSoft) addUnique(out, r.symbol);
      }
    } catch { /* */ }

    // 3) Today's scans
    try {
      const scans = db.prepare(`
        SELECT symbol FROM scans
        WHERE scan_date=? AND rejected=0
        GROUP BY symbol ORDER BY MAX(score) DESC LIMIT ?
      `).all(tradeDate, limit);
      for (const r of scans) addUnique(out, r.symbol);
    } catch { /* */ }

    // 4) EGX-X Pro top scores (liquidity/RS discovery)
    try {
      const xpro = db.prepare(`
        SELECT symbol FROM egx_x_pro_daily
        WHERE trade_date=? AND x_score >= 65
        ORDER BY x_score DESC LIMIT ?
      `).all(tradeDate, limit);
      for (const r of xpro) addUnique(out, r.symbol);
    } catch { /* */ }

    // 5) Volume leaders (5-session) — catch early movers before opp ranks them
    if (WIDE || out.length < limit) {
      try {
        const volLeaders = db.prepare(`
          SELECT symbol, SUM(volume) AS vol
          FROM ohlcv_history
          WHERE date(bar_time, 'unixepoch') >= date(?, '-7 days')
            AND date(bar_time, 'unixepoch') <= ?
          GROUP BY symbol
          ORDER BY vol DESC
          LIMIT ?
        `).all(tradeDate, tradeDate, limit * 2);
        for (const r of volLeaders) addUnique(out, r.symbol);
      } catch { /* */ }
    }

    // 6) Active universe fill
    if (out.length < limit) {
      try {
        const uni = db.prepare(`
          SELECT symbol FROM stock_universe
          WHERE COALESCE(status, 'ACTIVE') IN ('ACTIVE', 'active', '')
          ORDER BY symbol
          LIMIT ?
        `).all(limit * 3);
        for (const r of uni) addUnique(out, r.symbol);
      } catch { /* */ }
    }
  } finally {
    db.close();
  }

  return out.slice(0, limit);
}

const symbols = selectSymbols(symbolLimit, signalDate);
const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  wide_mode: WIDE,
  symbols_targeted: symbols.length,
  symbol_limit: symbolLimit,
  stages: [],
};

console.log(`\n═══ TV Microstructure Engine ═══`);
console.log(`  Date: ${signalDate} | symbols: ${symbols.length}/${symbolLimit} | wide: ${WIDE} | local: ${LOCAL_ONLY || LOCAL_FALLBACK}\n`);

// Stage 1 — pine analytics fetch
const pineFlags = [
  'all',
  ...(symbols.length ? ['--symbols', symbols.join(',')] : ['--max-symbols', String(symbolLimit)]),
  ...(LOCAL_ONLY ? ['--local-only'] : LOCAL_FALLBACK ? ['--local-fallback'] : []),
];

try {
  const t0 = Date.now();
  execSync(`"${NODE}" scripts/fetch_pine_analytics.mjs ${pineFlags.join(' ')}`, {
    cwd: ROOT,
    stdio: 'inherit',
    timeout: 900_000,
    env: { ...process.env, DELAY_MS: LOCAL_ONLY ? '0' : (process.env.DELAY_MS || '800') },
  });
  report.stages.push({ name: 'fetch_pine_analytics', ok: true, ms: Date.now() - t0 });
} catch (e) {
  report.stages.push({ name: 'fetch_pine_analytics', ok: false, error: e.message?.slice(0, 120) });
  if (!LOCAL_FALLBACK) {
    console.log('  ⚠️  TV fetch failed — continuing with existing pine_analytics rows');
  }
}

// Stage 2 — derive tv_discovery_features
try {
  const t0 = Date.now();
  const params = JSON.stringify({ date: signalDate, max_symbols: symbolLimit, symbols });
  const feat = parsePythonJson(execFileSync(PYTHON3, [
    join(ROOT, 'scripts/python/tv_discovery_features.py'),
    'compute',
    params,
  ], { cwd: ROOT, encoding: 'utf8', timeout: 120_000 }));
  report.stages.push({ name: 'tv_discovery_features', ok: true, ms: Date.now() - t0, result: feat });
  console.log(`  ✅ tv_discovery_features: ${feat?.n_rows ?? feat?.symbols ?? '?'} symbols`);
} catch (e) {
  report.stages.push({ name: 'tv_discovery_features', ok: false, error: e.message?.slice(0, 120) });
  console.error(`  ❌ tv_discovery_features: ${e.message?.slice(0, 120)}`);
}

mkdirSync(join(ROOT, 'data'), { recursive: true });
writeFileSync(join(ROOT, 'data/tv_microstructure_last.json'), JSON.stringify(report, null, 2));

const failed = report.stages.filter(s => !s.ok).length;
process.exit(failed ? 1 : 0);
