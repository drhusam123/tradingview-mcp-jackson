#!/usr/bin/env node
/** Run quant_discovery with full P6 discovery context (feedback + directives). */
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { buildDiscoveryParams } from './lib/discovery_context.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const params = JSON.stringify(buildDiscoveryParams().params);

const out = execFileSync(PYTHON3, [join(ROOT, 'scripts/python/quant_discovery.py'), 'run', params], {
  cwd: ROOT,
  encoding: 'utf8',
  stdio: ['pipe', 'pipe', 'inherit'],
});
console.log(out);
