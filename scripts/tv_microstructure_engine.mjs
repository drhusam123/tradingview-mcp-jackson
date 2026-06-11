#!/usr/bin/env node
/**
 * TV Microstructure Engine — Phase 2 sensing layer.
 *
 * 1. Select watchlist (top opp + scans, max 30)
 * 2. Fetch pine analytics (TV live or local OHLCV fallback)
 * 3. Derive atoms → tv_discovery_features table
 *
 * Usage:
 *   npm run egx:discovery:tv:micro
 *   node scripts/tv_microstructure_engine.mjs --local-only --max-symbols 30
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

function selectSymbols(limit) {
  if (!existsSync(DB_PATH)) return [];
  const db = new Database(DB_PATH, { readonly: true });
  const out = [];
  const opp = db.prepare(`
    SELECT symbol FROM opportunity_score_v2
    WHERE trade_date=? AND opportunity_score >= 62
    ORDER BY opportunity_score DESC LIMIT ?
  `).all(signalDate, limit);
  for (const r of opp) out.push(r.symbol);
  if (out.length < limit) {
    const scans = db.prepare(`
      SELECT symbol FROM scans
      WHERE scan_date=? AND rejected=0
      GROUP BY symbol ORDER BY MAX(score) DESC LIMIT ?
    `).all(signalDate, limit);
    for (const r of scans) {
      if (!out.includes(r.symbol)) out.push(r.symbol);
    }
  }
  db.close();
  return out.slice(0, limit);
}

const symbols = selectSymbols(maxArg);
const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  symbols_targeted: symbols.length,
  stages: [],
};

console.log(`\n═══ TV Microstructure Engine ═══`);
console.log(`  Date: ${signalDate} | symbols: ${symbols.length} | local: ${LOCAL_ONLY || LOCAL_FALLBACK}\n`);

// Stage 1 — pine analytics fetch
const pineFlags = [
  'all',
  ...(symbols.length ? ['--symbols', symbols.join(',')] : ['--max-symbols', String(maxArg)]),
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
  const params = JSON.stringify({ date: signalDate, max_symbols: maxArg, symbols });
  const feat = parsePythonJson(execFileSync(PYTHON3, [
    join(ROOT, 'scripts/python/tv_discovery_features.py'),
    'compute',
    params,
  ], { cwd: ROOT, encoding: 'utf8', timeout: 120_000 }));
  report.stages.push({ name: 'tv_discovery_features', ok: true, ms: Date.now() - t0, result: feat });
  report.features_written = feat.features_written ?? 0;
  report.atom_counts = feat.atom_counts ?? {};
  console.log(`  ✅ Features: ${feat.features_written} symbols | atoms: ${JSON.stringify(feat.atom_counts || {})}`);
} catch (e) {
  report.stages.push({ name: 'tv_discovery_features', ok: false, error: e.message?.slice(0, 120) });
  console.error(`  ❌ Feature compute failed: ${e.message}`);
}

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/tv_microstructure_last.json'), JSON.stringify(report, null, 2));

const failed = report.stages.filter(s => !s.ok).length;
console.log(failed ? '\n⚠️  TV microstructure completed with errors\n' : '\n═══ TV Microstructure OK ═══\n');
process.exit(failed && !report.features_written ? 1 : 0);
