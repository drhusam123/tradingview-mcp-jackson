#!/usr/bin/env node
/**
 * Institutional E2E — health → full-cycle → audit → telegram dry-run → health
 *
 * Usage:
 *   npm run egx:audit:e2e
 *   npm run egx:audit:e2e -- --skip-cdp --fast
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const SKIP_CDP = process.argv.includes('--skip-cdp');
const FAST = process.argv.includes('--fast') || !process.argv.includes('--no-fast');
const AS_JSON = process.argv.includes('--json');

function cdpHttpUp() {
  const url = process.env.TV_CDP_URL || 'http://127.0.0.1:9222';
  try {
    const code = execSync(`curl -s -o /dev/null -w "%{http_code}" "${url}/json/version"`, {
      encoding: 'utf8', timeout: 4000,
    }).trim();
    return code === '200';
  } catch {
    return false;
  }
}

const CDP_UP = cdpHttpUp();
const USE_SKIP_CDP = SKIP_CDP || (!CDP_UP && !process.argv.includes('--require-cdp'));

const logPath = join(PROJECT_ROOT, 'logs', `audit_e2e_${cairoDateParts().date}.log`);
mkdirSync(join(PROJECT_ROOT, 'logs'), { recursive: true });

const steps = [];

function run(name, cmd, { optional = false, timeout = 900_000 } = {}) {
  const t0 = Date.now();
  try {
    execSync(cmd, { cwd: PROJECT_ROOT, stdio: AS_JSON ? 'pipe' : 'inherit', timeout });
    const row = { name, ok: true, ms: Date.now() - t0, optional };
    steps.push(row);
    return row;
  } catch (e) {
    const row = {
      name, ok: false, ms: Date.now() - t0, optional,
      error: e.message?.slice(0, 300),
    };
    steps.push(row);
    if (!optional) throw new Error(`${name} failed: ${row.error}`);
    return row;
  }
}

function readJson(rel) {
  const p = join(PROJECT_ROOT, 'data', rel);
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; }
}

const t0 = Date.now();
const result = { at: new Date().toISOString(), pass: false, steps: [] };

try {
  run('health_pre', `${PYTHON} scripts/python/system_health_check.py --quick`);
  const fcFlags = [USE_SKIP_CDP && '--skip-cdp', FAST && '--fast'].filter(Boolean).join(' ');
  run('full_cycle', `npm run egx:full-cycle -- ${fcFlags}`.trim());
  run('audit_all', `npm run egx:audit:all`);
  run('telegram_dry', `npm run egx:cron:telegram:dry`, { optional: true, timeout: 120_000 });
  run('prepare_dry', `npm run egx:prod:prepare-send -- --dry-run`, { optional: true, timeout: 120_000 });
  run('health_post', `${PYTHON} scripts/python/system_health_check.py --quick`);

  const healthPost = readJson('system_health_last.json');
  const fullCycle = readJson('full_cycle_last.json');
  const dataLayer = readJson('data_layer_audit_last.json');

  result.pass = Boolean(
    healthPost?.status !== 'FAIL'
    && fullCycle?.pass !== false
  );
  result.health = healthPost?.status;
  result.full_cycle = fullCycle?.pass;
  result.cdp_up = CDP_UP;
  result.skip_cdp = USE_SKIP_CDP;
  result.steps = steps;
  result.ms = Date.now() - t0;

  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/audit_e2e_last.json'), JSON.stringify(result, null, 2));

  if (!AS_JSON) {
    console.log('\n═══ Institutional E2E ═══');
    for (const s of steps) console.log(`  ${s.ok ? '✅' : '❌'} ${s.name}${s.optional && !s.ok ? ' (optional)' : ''}`);
    console.log(`\n  HEALTH: ${result.health} | FULL_CYCLE: ${result.full_cycle ? 'PASS' : 'FAIL'}`);
    console.log(`  Result: ${result.pass ? 'PASS' : 'FAIL'}\n`);
  } else {
    console.log(JSON.stringify(result, null, 2));
  }
  process.exit(result.pass ? 0 : 1);
} catch (e) {
  result.error = e.message;
  result.steps = steps;
  writeFileSync(join(PROJECT_ROOT, 'data/audit_e2e_last.json'), JSON.stringify(result, null, 2));
  console.error(e.message);
  process.exit(1);
}
