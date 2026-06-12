#!/usr/bin/env node
/**
 * Audit all package.json npm scripts — verify targets exist.
 * Usage: npm run egx:scripts:audit
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'));
const scripts = pkg.scripts || {};

const SKIP = new Set(['start', 'test', 'test:offline', 'test:ci', 'test:python', 'test:live', 'tv:smoke']);

function extractTargets(cmd) {
  if (/\bnode\s+-e\b/.test(cmd) || /\bnode\s+--test\b/.test(cmd)) return [];
  const targets = [];
  const nodeRe = /(?:^|\s)node\s+([^\s&|;]+)/g;
  const pyRe = /(?:^|\s)(?:python3|\/usr\/bin\/python3)\s+([^\s&|;]+)/g;
  const bashRe = /bash\s+([^\s&|;]+)/g;
  let m;
  while ((m = nodeRe.exec(cmd))) targets.push(m[1]);
  while ((m = pyRe.exec(cmd))) targets.push(m[1]);
  while ((m = bashRe.exec(cmd))) targets.push(m[1]);
  return targets;
}

const broken = [];
const ok = [];
for (const [name, cmd] of Object.entries(scripts)) {
  if (SKIP.has(name) || cmd.includes('npm run') || cmd.includes('&&')) {
    ok.push({ name, cmd, note: 'composite/skip' });
    continue;
  }
  const targets = extractTargets(cmd);
  if (!targets.length) {
    ok.push({ name, cmd, note: 'no file target' });
    continue;
  }
  const missing = targets.filter(t => {
    const p = t.startsWith('/') ? t : join(ROOT, t);
    return !existsSync(p);
  });
  if (missing.length) broken.push({ name, cmd, missing });
  else ok.push({ name, targets });
}

console.log('\n═══ EGX npm Scripts Audit ═══');
console.log(`Total: ${Object.keys(scripts).length} | OK: ${ok.length} | Broken: ${broken.length}\n`);
for (const b of broken) {
  console.log(`❌ ${b.name}`);
  console.log(`   ${b.cmd.slice(0, 100)}`);
  console.log(`   missing: ${b.missing.join(', ')}`);
}
console.log(`\n=== Scripts Audit: ${Object.keys(scripts).length - broken.length}/${Object.keys(scripts).length} OK ===\n`);
process.exit(broken.length ? 1 : 0);
