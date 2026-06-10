#!/usr/bin/env node
/**
 * Add missing keys from .env.template → .env (never overwrites existing values).
 */
import { existsSync, readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './lib/load_env.mjs';

const envPath = join(PROJECT_ROOT, '.env');
const tplPath = join(PROJECT_ROOT, '.env.template');
if (!existsSync(tplPath)) {
  console.error('Missing .env.template');
  process.exit(1);
}

const existing = existsSync(envPath) ? readFileSync(envPath, 'utf8') : '';
const keys = new Set(
  existing.split('\n')
    .filter(l => l.includes('=') && !l.startsWith('#'))
    .map(l => l.split('=')[0].trim()),
);

const toAdd = [];
for (const line of readFileSync(tplPath, 'utf8').split('\n')) {
  if (!line || line.startsWith('#') || !line.includes('=')) continue;
  const key = line.split('=')[0].trim();
  if (key && !keys.has(key)) toAdd.push(line);
}

if (!toAdd.length) {
  console.log('✅ .env already has all template keys');
  process.exit(0);
}

const block = `\n# --- added by sync_env_defaults ${new Date().toISOString().slice(0, 10)} ---\n${toAdd.join('\n')}\n`;
writeFileSync(envPath, existing.trimEnd() + block);
console.log(`✅ Added ${toAdd.length} key(s) to .env:`);
toAdd.forEach(l => console.log(`   ${l.split('=')[0]}`));
