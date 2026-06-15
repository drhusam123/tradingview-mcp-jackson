#!/usr/bin/env node
/**
 * CDP smoke test — proves TradingView Desktop is reachable without full EOD sync.
 *
 * Usage:
 *   npm run egx:cdp:smoke
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { healthCheck } from '../src/core/health.js';

loadEnv();

const NODE = process.execPath;
const CDP_URL = process.env.TV_CDP_URL || 'http://127.0.0.1:9222';
const TIMEOUT_MS = 25_000;

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timeout ${ms}ms`)), ms)),
  ]);
}

const steps = [];

async function main() {
  // 1. CDP HTTP endpoint
  try {
    const code = execSync(`curl -s -o /dev/null -w "%{http_code}" "${CDP_URL}/json/version"`, {
      encoding: 'utf8', timeout: 5000,
    }).trim();
    const ok = code === '200';
    steps.push({ name: 'cdp_http', ok, detail: `HTTP ${code}` });
    if (!ok) throw new Error(`CDP HTTP ${code}`);
  } catch (e) {
    steps.push({ name: 'cdp_http', ok: false, error: e.message?.slice(0, 120) });
    throw e;
  }

  // 2. CDP chart API
  try {
    const health = await withTimeout(healthCheck(), TIMEOUT_MS, 'healthCheck');
    const ok = health.cdp_connected && health.api_available !== false;
    steps.push({
      name: 'cdp_chart_api',
      ok,
      detail: `${health.chart_symbol} @ ${health.chart_resolution}`,
      symbol: health.chart_symbol,
    });
    if (!ok) throw new Error('chart API unavailable');
  } catch (e) {
    steps.push({ name: 'cdp_chart_api', ok: false, error: e.message?.slice(0, 120) });
    throw e;
  }

  // 3. Quick validation gate
  try {
    execSync(`"${NODE}" scripts/egx_validate.mjs --quick`, {
      cwd: PROJECT_ROOT, stdio: 'pipe', timeout: 60_000,
    });
    steps.push({ name: 'validate_quick', ok: true });
  } catch (e) {
    steps.push({ name: 'validate_quick', ok: false, error: e.message?.slice(0, 120) });
    throw e;
  }

  const report = {
    at: new Date().toISOString(),
    pass: true,
    cdp_url: CDP_URL,
    steps,
  };
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/cdp_smoke_last.json'), JSON.stringify(report, null, 2));
  console.log('\n═══ CDP Smoke: PASS ═══');
  for (const s of steps) console.log(`  ✅ ${s.name}${s.detail ? `: ${s.detail}` : ''}`);
  console.log('  Saved: data/cdp_smoke_last.json\n');
  process.exit(0);
}

main().catch((e) => {
  const report = { at: new Date().toISOString(), pass: false, steps, error: e.message };
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/cdp_smoke_last.json'), JSON.stringify(report, null, 2));
  console.error('\n═══ CDP Smoke: FAIL ═══');
  for (const s of steps) console.log(`  ${s.ok ? '✅' : '❌'} ${s.name}${s.error ? `: ${s.error}` : ''}`);
  process.exit(1);
});
