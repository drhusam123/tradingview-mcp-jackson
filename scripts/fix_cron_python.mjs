#!/usr/bin/env node
/**
 * Patch crontab: replace pyenv PYTHON_BIN with /usr/bin/python3 (cron-stable).
 * Usage: node scripts/fix_cron_python.mjs [--dry-run]
 */
import { execSync } from 'child_process';

const SYSTEM_PY = '/usr/bin/python3';
const DRY = process.argv.includes('--dry-run');

function getCron() {
  try {
    return execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
  } catch {
    return '';
  }
}

const current = getCron();
if (!current.trim()) {
  console.log('لا توجد مهام cron');
  process.exit(0);
}

const pyenvPatterns = [
  /PYTHON_BIN=\/Users\/[^/]+\/\.pyenv\/shims\/python3/g,
  /PYTHON3=\/Users\/[^/]+\/\.pyenv\/shims\/python3/g,
];

let patched = current;
let changes = 0;
for (const re of pyenvPatterns) {
  const before = patched;
  patched = patched.replace(re, (m) => {
    changes += 1;
    return m.startsWith('PYTHON_BIN=')
      ? `PYTHON_BIN=${SYSTEM_PY}`
      : `PYTHON3=${SYSTEM_PY}`;
  });
  if (patched !== before) changes += 0; // counted per replacement
}

const pyenvHits = (current.match(/\.pyenv\/shims\/python/g) || []).length;
if (pyenvHits === 0) {
  console.log(`✅  Cron يستخدم بالفعل مساراً مستقراً — لا pyenv (${SYSTEM_PY} موصى به)`);
  process.exit(0);
}

if (DRY) {
  console.log(`[dry-run] سيُستبدل ${pyenvHits} مرجع pyenv → ${SYSTEM_PY}`);
  const sample = patched.split('\n').filter(l => l.includes('PYTHON_BIN')).slice(0, 5);
  sample.forEach(l => console.log('  ', l.trim().slice(0, 120)));
  process.exit(0);
}

execSync('crontab -', { input: `${patched.trim()}\n` });
console.log(`✅  تم تحديث cron: ${pyenvHits} مرجع pyenv → ${SYSTEM_PY}`);
console.log('   تحقق: crontab -l | grep PYTHON_BIN | head');
