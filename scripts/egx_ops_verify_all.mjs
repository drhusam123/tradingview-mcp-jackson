#!/usr/bin/env node
/**
 * Run the full operational verification pyramid (no TV CDP, no live send).
 * Usage: npm run egx:ops:verify-all
 */
import { execSync } from 'child_process';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();
const NODE = process.execPath;

const steps = [
  ['scripts_audit', 'npm run egx:scripts:audit'],
  ['migrations', 'npm run egx:migrate -- --check'],
  ['automation_verify', 'npm run egx:automation:verify'],
  ['ml_gate_verify', 'npm run egx:ml:gate:verify'],
  ['architecture_audit', 'npm run egx:architecture:audit'],
  ['data_audit', 'npm run egx:data:audit'],
  ['discovery_verify', 'npm run egx:discovery:verify'],
  ['gate_simulate', 'npm run egx:gate:simulate'],
  ['signals_diagnose', 'npm run egx:signals:diagnose'],
  ['loop_audit', 'npm run egx:loop:audit'],
  ['verify_fast', 'npm run egx:verify:fast'],
];

const results = [];
let fail = 0;
console.log('\n═══ EGX Ops Verify All ═══\n');
for (const [name, cmd] of steps) {
  process.stdout.write(`▶  ${name}... `);
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: 'pipe', timeout: 600_000 });
    console.log('✅');
    results.push({ name, ok: true });
  } catch (e) {
    console.log('❌');
    results.push({ name, ok: false, error: e.message?.slice(0, 80) });
    fail += 1;
  }
}
console.log(`\n=== Ops Verify All: ${steps.length - fail}/${steps.length} PASS ===\n`);
process.exit(fail ? 1 : 0);
