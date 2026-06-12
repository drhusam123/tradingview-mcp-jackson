#!/usr/bin/env node
/**
 * Daily data quality exclusions summary (L0 governance).
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { DB_PATH } from './lib/delivery_audit.mjs';

loadEnv();

const db = new Database(DB_PATH, { readonly: true });
const rawN = db.prepare('SELECT COUNT(*) n FROM ohlcv_history').get()?.n ?? 0;
const execN = db.prepare('SELECT COUNT(*) n FROM ohlcv_history_execution').get()?.n ?? 0;
const activeN = db.prepare(
  "SELECT COUNT(*) n FROM data_quality_bar_exclusions WHERE status='ACTIVE'",
).get()?.n ?? 0;
const byReason = db.prepare(`
  SELECT reason, COUNT(*) n
  FROM data_quality_bar_exclusions
  WHERE status='ACTIVE'
  GROUP BY reason
  ORDER BY n DESC
  LIMIT 10
`).all();
const trust = db.prepare(`
  SELECT source, trust_score, status
  FROM data_trust_scores
  WHERE source='ohlcv_history'
  ORDER BY last_checked DESC LIMIT 1
`).get();
db.close();

const ratio = rawN > 0 ? Math.round((execN / rawN) * 1000) / 10 : 0;
const report = {
  at: new Date().toISOString(),
  raw_bars: rawN,
  execution_bars: execN,
  active_exclusions: activeN,
  execution_ratio_pct: ratio,
  by_reason: byReason,
  trust: trust ?? null,
};

const out = join(PROJECT_ROOT, 'data/exclusions_daily_report.json');
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(out, JSON.stringify(report, null, 2));

console.log('\n═══ Data Quality Exclusions Report ═══');
console.log(`  raw=${rawN} execution=${execN} ratio=${ratio}%`);
console.log(`  active_exclusions=${activeN}`);
if (trust) console.log(`  trust(ohlcv_history)=${trust.trust_score} (${trust.status})`);
for (const r of byReason.slice(0, 5)) {
  console.log(`    ${r.reason}: ${r.n}`);
}
console.log(`  Saved: ${out}\n`);
