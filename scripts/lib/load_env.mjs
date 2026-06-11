/**
 * Load .env into process.env (cron-safe — crontab has minimal env).
 */
import { existsSync, readFileSync } from 'fs';
import { execSync } from 'child_process';
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
  const candidates = [
    process.env.PYTHON_BIN,
    process.env.PYTHON3,
    join(ROOT, 'venv/bin/python3'),
    join(ROOT, '.venv/bin/python3'),
    '/usr/bin/python3',
    'python3',
  ].filter(Boolean);
  const seen = new Set();
  let resolved = null;
  for (const bin of candidates) {
    if (seen.has(bin)) continue;
    seen.add(bin);
    try {
      execSync(`"${bin}" -c "import numpy"`, { stdio: 'pipe', timeout: 5000 });
      resolved = bin;
      break;
    } catch { /* try next */ }
  }
  if (resolved) {
    process.env.PYTHON_BIN = resolved;
    process.env.PYTHON3 = resolved;
  } else if (!process.env.PYTHON_BIN && !process.env.PYTHON3) {
    process.env.PYTHON_BIN = '/usr/bin/python3';
  }
  // lightgbm on macOS needs libomp (brew install libomp)
  const libompPaths = [
    '/opt/homebrew/opt/libomp/lib',
    '/usr/local/opt/libomp/lib',
  ];
  for (const p of libompPaths) {
    if (existsSync(join(p, 'libomp.dylib'))) {
      const cur = process.env.DYLD_LIBRARY_PATH || '';
      if (!cur.includes(p)) {
        process.env.DYLD_LIBRARY_PATH = cur ? `${p}:${cur}` : p;
      }
      break;
    }
  }
  return true;
}

export const PROJECT_ROOT = ROOT;
