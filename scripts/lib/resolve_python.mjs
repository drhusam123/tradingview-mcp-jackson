/**
 * Resolve Python binary with cognition deps (numpy) when possible.
 */
import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

const CANDIDATES = () => [
  process.env.PYTHON_BIN,
  process.env.PYTHON3,
  join(PROJECT_ROOT, 'venv/bin/python3'),
  join(PROJECT_ROOT, '.venv/bin/python3'),
  '/opt/homebrew/bin/python3',
  '/usr/local/bin/python3',
  '/usr/bin/python3',
  'python3',
].filter(Boolean);

export function pythonHasModule(bin, mod = 'numpy') {
  try {
    execSync(`"${bin}" -c "import ${mod}"`, { stdio: 'pipe', timeout: 5000 });
    return true;
  } catch {
    return false;
  }
}

export function resolvePythonBin({ requireNumpy = false } = {}) {
  const seen = new Set();
  for (const bin of CANDIDATES()) {
    if (seen.has(bin)) continue;
    seen.add(bin);
    if (!existsSync(bin) && !bin.includes('/') && bin !== 'python3') continue;
    if (requireNumpy && !pythonHasModule(bin)) continue;
    return bin;
  }
  return process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
}

export function ensurePythonEnv() {
  const bin = resolvePythonBin({ requireNumpy: true });
  process.env.PYTHON_BIN = bin;
  process.env.PYTHON3 = bin;
  return bin;
}
