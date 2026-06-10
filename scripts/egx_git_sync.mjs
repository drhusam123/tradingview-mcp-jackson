#!/usr/bin/env node
/**
 * Git sync status + push helper for production commits.
 * Usage: node scripts/egx_git_sync.mjs [--push]
 */
import { execSync } from 'child_process';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';

loadEnv();

const PUSH = process.argv.includes('--push');

function run(cmd) {
  return execSync(cmd, { cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe' }).trim();
}

console.log('\n═══ EGX Git Sync ═══\n');

try {
  const branch = run('git branch --show-current');
  const status = run('git status -sb');
  const aheadBehind = status.match(/\[([^\]]+)\]/);
  console.log(`Branch: ${branch}`);
  console.log(`Status: ${status.split('\n')[0]}`);
  if (aheadBehind) console.log(`Remote:   ${aheadBehind[1]}`);
} catch (e) {
  console.log(`Git status error: ${e.message}`);
}

try {
  const stash = run('git stash list');
  if (stash) console.log(`\nStash:\n${stash.split('\n').map(l => `  ${l}`).join('\n')}`);
} catch { /* */ }

console.log('\n── To push (run in your Terminal) ──');
console.log('  brew install gh && gh auth login');
console.log('  git push origin main');
console.log('  # or: npm run egx:git:sync -- --push');

if (PUSH) {
  console.log('\n▶  Attempting push...');
  try {
    execSync('git push origin main', { cwd: PROJECT_ROOT, stdio: 'inherit' });
    console.log('\n✅ Push succeeded\n');
  } catch (e) {
    console.error('\n❌ Push failed — authenticate in Terminal (see above)\n');
    process.exit(1);
  }
} else {
  console.log('');
}
