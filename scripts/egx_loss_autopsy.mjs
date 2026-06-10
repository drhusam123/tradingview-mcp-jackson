#!/usr/bin/env node
/**
 * ULTRA loss autopsy — setup-level diagnosis for residual losses.
 * Usage: node scripts/egx_loss_autopsy.mjs [--json]
 */
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { runLossAutopsy } from './lib/loss_autopsy.mjs';

loadEnv();

const AS_JSON = process.argv.includes('--json');
const report = runLossAutopsy();

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/loss_autopsy_last.json'), JSON.stringify({
  at: new Date().toISOString(),
  ...report,
}, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

console.log('\n═══ ULTRA Loss Autopsy ═══\n');
console.log(`  Residual (still pass filters): ${report.n_residual_losses}`);
console.log(`  Cases analyzed: ${report.n_cases_analyzed ?? report.n_residual_losses}`);
if (report.counterfactual_blocked_losses != null) {
  console.log(`  Counterfactual blocks: ${report.counterfactual_blocked_losses}/${report.n_all_losses_lookback} losses`);
}
console.log(`  All losses (120d): ${report.n_all_losses_lookback}\n`);

if (report.repeat_symbols?.length) {
  console.log('  Repeat losers:');
  report.repeat_symbols.forEach(s => console.log(`    ${s.symbol}: ${s.losses} ULTRA losses`));
  console.log('');
}

if (Object.keys(report.flag_counts || {}).length) {
  console.log('  Pattern flags:');
  Object.entries(report.flag_counts)
    .sort((a, b) => b[1] - a[1])
    .forEach(([f, n]) => console.log(`    ${f.padEnd(28)} ${n}`));
  console.log('');
}

if (report.proposed_rules?.length) {
  console.log('  Proposed rules:');
  report.proposed_rules.forEach(r => console.log(`    • [${r.evidence}x] ${r.rule}`));
  console.log('');
}

if (report.cases?.length) {
  console.log('  Cases:');
  for (const c of report.cases.slice(0, 12)) {
    const ret = c.return_t5 != null ? `${c.return_t5 >= 0 ? '+' : ''}${c.return_t5.toFixed(1)}%` : '—';
    console.log(`    ${c.symbol.padEnd(6)} ${c.signal_date}  ${ret}  ${(c.setup_type || '—').slice(0, 28)}`);
    if (c.flags.length) console.log(`           flags: ${c.flags.join(', ')}`);
  }
  console.log('');
}

console.log('  Saved: data/loss_autopsy_last.json\n');
