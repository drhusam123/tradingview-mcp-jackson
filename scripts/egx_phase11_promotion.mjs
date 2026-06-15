#!/usr/bin/env node
/**
 * Phase 11 — Research→client promotion infrastructure.
 * Resolves env from graduation gates, runs MDE shadow bridge, persists snapshot.
 *
 * Usage: node scripts/egx_phase11_promotion.mjs [--json] [--skip-phase10] [--apply-env]
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { evaluateGraduationReadiness } from './lib/p6_graduation_gate.mjs';
import {
  resolveResearchClientEnv,
  writeResearchClientEnvSnapshot,
  RESEARCH_ENV_KEYS,
} from './lib/research_client_env.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const AS_JSON = process.argv.includes('--json');
const SKIP_PHASE10 = process.argv.includes('--skip-phase10');
const signalDate = latestOhlcvDate() || cairoDateParts().date;

console.log('\n═══ Phase 11 — Research Client Promotion ═══');
console.log(`  Signal date: ${signalDate}\n`);

if (!SKIP_PHASE10) {
  try {
    execSync(`"${NODE}" scripts/egx_phase10_graduation.mjs --skip-phase9`, {
      cwd: PROJECT_ROOT,
      stdio: AS_JSON ? 'pipe' : 'inherit',
      timeout: 120_000,
    });
  } catch (e) {
    if (!AS_JSON) console.log(`⚠️  Phase 10: ${e.message?.slice(0, 80)}`);
  }
}

let mdeBridge = null;
try {
  const raw = execSync(
    `"${PYTHON}" scripts/python/mde_promotion_bridge.py '${JSON.stringify({ trade_date: signalDate })}'`,
    { cwd: PROJECT_ROOT, encoding: 'utf8', timeout: 60_000 },
  );
  mdeBridge = JSON.parse(raw);
} catch (e) {
  mdeBridge = { success: false, error: e.message?.slice(0, 120) };
}

const readiness = evaluateGraduationReadiness();
const resolved = writeResearchClientEnvSnapshot(resolveResearchClientEnv({ readiness }));

const checks = [
  {
    id: 'env_snapshot',
    ok: Boolean(resolved?.env),
    detail: 'data/research_client_env.json',
  },
  {
    id: 'mde_bridge',
    ok: mdeBridge?.success === true,
    detail: `${mdeBridge?.pilot_count ?? 0} pilot hints`,
  },
  {
    id: 'med_client_gated',
    ok: resolved.env.MED_CLIENT_SIGNAL === '0' || readiness.gates.med_client_signal.recommended === '1' || resolved.force_override,
    detail: `MED_CLIENT_SIGNAL=${resolved.env.MED_CLIENT_SIGNAL}`,
  },
  {
    id: 'mde_opp_off',
    ok: resolved.env.EGX_MDE_OPP_BOOST === '0',
    detail: 'EGX_MDE_OPP_BOOST=0 (shadow)',
  },
  {
    id: 'auto_promote_configured',
    ok: true,
    detail: process.env.EGX_PHASE11_AUTO_PROMOTE === '1' ? 'auto ON' : 'auto OFF (manual .env until gates pass)',
  },
];

const report = {
  at: new Date().toISOString(),
  signal_date: signalDate,
  pass: checks.every(c => c.ok),
  checks,
  research_env: resolved,
  mde_bridge: mdeBridge,
  gates: readiness.gates,
  client_beta_ready: readiness.client_beta_ready,
  promotion_ready: {
    med_client_signal: readiness.gates.med_client_signal.recommended === '1',
    med_feed_boost: readiness.gates.med_feed_boost.recommended === '1',
    lre_feed_boost: readiness.gates.lre_feed_boost.recommended === '1',
    mde_shadow_pilot: Boolean(mdeBridge?.pilot_eligible),
  },
  operator_notes: [
    'Set EGX_PHASE11_AUTO_PROMOTE=1 to apply gate recommendations automatically when gates PASS',
    'Manual .env overrides require EGX_RESEARCH_ENV_FORCE=1 if gate not yet PASS',
    'MDE hints are shadow-only — never bypass safety or promotion gates',
  ],
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/phase11_promotion_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log('  Resolved env (effective):');
  for (const k of RESEARCH_ENV_KEYS) {
    const src = resolved.sources[k] ?? '—';
    console.log(`    ${k.padEnd(28)} ${String(resolved.env[k]).padEnd(3)}  (${src})`);
  }
  if (resolved.clamps.length) {
    console.log('\n  Clamps applied:');
    resolved.clamps.forEach(c => console.log(`    • ${c.key}: ${c.reason}`));
  }
  console.log(`\n  MDE shadow bridge: ${mdeBridge?.pilot_count ?? 0} hints | pilot_eligible=${mdeBridge?.pilot_eligible ?? false}`);
  console.log(`  Client beta ready: ${readiness.client_beta_ready ? '✅ YES' : '⏳ accumulating'}`);
  console.log(`  Auto-promote: ${process.env.EGX_PHASE11_AUTO_PROMOTE === '1' ? 'ON' : 'OFF'}`);
  console.log('  Saved: data/phase11_promotion_last.json');
  console.log('  Saved: data/research_client_env.json\n');
}

process.exit(report.pass ? 0 : 1);
