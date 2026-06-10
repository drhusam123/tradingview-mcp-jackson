#!/usr/bin/env node
/**
 * Closed-loop health audit.
 * Usage: node scripts/egx_loop_audit.mjs [--json] [--max-age-hours 168]
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { auditClosedLoops } from './lib/loop_audit.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');
const maxArg = process.argv.find((a, i) => process.argv[i - 1] === '--max-age-hours');
const maxAgeHours = maxArg ? Number(maxArg) : 168;

const report = auditClosedLoops({ maxAgeHours });

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/loop_audit_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(report.pass ? 0 : 1);
}

console.log('\n═══ EGX Closed-Loop Audit ═══\n');
for (const c of report.checks) {
  console.log(`  ${c.ok ? '✅' : '❌'} ${c.id}: ${c.detail}`);
}
console.log(`\n  Directives: pending ${report.directives.pending} | completed ${report.directives.completed}`);
console.log(`\n  Result: ${report.pass ? 'PASS' : 'FAIL'}`);
console.log('  Saved: data/loop_audit_last.json\n');

process.exit(report.pass ? 0 : 1);
