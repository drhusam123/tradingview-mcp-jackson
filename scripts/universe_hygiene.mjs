#!/usr/bin/env node
/**
 * Universe hygiene — report and apply delisted/renamed ghost symbols.
 *
 * Usage:
 *   node scripts/universe_hygiene.mjs           # report only
 *   node scripts/universe_hygiene.mjs --apply   # archive ghosts + map renames
 *   node scripts/universe_hygiene.mjs --dry-run   # preview apply
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { DB_PATH } from './lib/delivery_audit.mjs';
import { buildHygieneReport, applyHygiene } from './lib/universe_hygiene.mjs';

loadEnv();

const args = process.argv.slice(2);
const APPLY = args.includes('--apply');
const DRY_RUN = args.includes('--dry-run');

const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL');

const report = buildHygieneReport(db);
const outPath = join(PROJECT_ROOT, 'data/universe_hygiene_report.json');
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(outPath, JSON.stringify(report, null, 2));

console.log('\n═══ Universe Hygiene Report ═══');
console.log(`  Universe total:     ${report.universe_total}`);
console.log(`  OHLCV symbols:      ${report.ohlcv_symbols}`);
console.log(`  Ghosts (no OHLCV):  ${report.ghosts_no_ohlcv}`);
console.log(`  Unarchived ghosts:  ${report.unarchived_ghosts}`);
console.log(`  By category:        ${JSON.stringify(report.by_category)}`);
console.log(`  Weekly gap:         ${report.weekly_gap.gap_count} (${report.weekly_gap.daily_symbols} daily vs ${report.weekly_gap.weekly_symbols} weekly)`);
if (report.weekly_gap.missing_symbols.length) {
  console.log(`  Missing weekly:     ${report.weekly_gap.missing_symbols.join(', ')}`);
}
console.log(`  Saved: ${outPath}`);

if (APPLY || DRY_RUN) {
  const result = applyHygiene(db, { dryRun: DRY_RUN });
  const applyPath = join(PROJECT_ROOT, 'data/universe_hygiene_apply.json');
  writeFileSync(applyPath, JSON.stringify(result, null, 2));
  console.log(`\n═══ Hygiene ${DRY_RUN ? 'Dry Run' : 'Apply'} ═══`);
  console.log(`  Updated: ${result.applied.length} symbols`);
  for (const a of result.applied.slice(0, 10)) {
    console.log(`    ${a.symbol} → ${a.status} ${a.successor_symbol ? `(→${a.successor_symbol})` : ''} [${a.hygiene_reason}]`);
  }
  if (result.applied.length > 10) console.log(`    ... +${result.applied.length - 10} more`);
  console.log(`  Saved: ${applyPath}`);

  const after = buildHygieneReport(db);
  console.log(`  Unarchived ghosts after: ${after.unarchived_ghosts}`);
}

db.close();
console.log('');
