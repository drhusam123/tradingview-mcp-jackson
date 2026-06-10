/**
 * Load .env into process.env (cron-safe — crontab has minimal env).
 */
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');

export function loadEnv() {
  const p = join(ROOT, '.env');
  if (!existsSync(p)) return false;
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    const key = k.trim();
    const val = rest.join('=').trim().replace(/^["']|["']$/g, '');
    if (key && process.env[key] === undefined) process.env[key] = val;
  }
  if (!process.env.PYTHON_BIN && !process.env.PYTHON3) {
    process.env.PYTHON_BIN = '/usr/bin/python3';
  }
  return true;
}

export const PROJECT_ROOT = ROOT;
