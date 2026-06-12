/**
 * Single entry for quant_discovery.py — always uses unified P6 discovery context.
 */
import { execFileSync } from 'child_process';
import { join } from 'path';
import { buildDiscoveryParams } from './discovery_context.mjs';
import { PROJECT_ROOT } from './load_env.mjs';
import { parsePythonJson } from './parse_python_json.mjs';

const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';

/**
 * @param {{ signalDate?: string, includeDirectives?: boolean }} [opts]
 */
export function runQuantDiscovery(opts = {}) {
  const { signalDate = null, includeDirectives = true } = opts;
  const ctx = buildDiscoveryParams({ signalDate, includeDirectives });
  const params = JSON.stringify(ctx.params);
  const out = execFileSync(PYTHON3, [
    join(PROJECT_ROOT, 'scripts/python/quant_discovery.py'),
    'run',
    params,
  ], {
    cwd: PROJECT_ROOT,
    encoding: 'utf8',
    timeout: 1_200_000,
  });
  const result = parsePythonJson(out);
  return { result, context: ctx };
}
