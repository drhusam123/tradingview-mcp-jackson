#!/usr/bin/env node
/**
 * Production handoff summary — one screen for Dr. Husam.
 * Usage: node scripts/egx_handoff.mjs
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { nextTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';

loadEnv();

function readJson(rel) {
  const p = join(PROJECT_ROOT, rel);
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; }
}

const cairo = cairoDateParts();
const digest = buildDeliveryDigest();
const nxt = nextTradingDay(cairo.date);
const ready = readJson('data/prod_ready_last.json');
const verify = readJson('data/full_verify_last.json');

let gitLine = 'git: unknown';
try {
  const sb = execSync('git status -sb', { cwd: PROJECT_ROOT, encoding: 'utf8' }).trim().split('\n')[0];
  gitLine = sb.replace('## ', '');
} catch { /* */ }

console.log(`
╔══════════════════════════════════════════════════════════════╗
║  EGX PRODUCTION — HANDOFF SUMMARY                            ║
╚══════════════════════════════════════════════════════════════╝

  Cairo:        ${cairo.date} ${String(cairo.hour).padStart(2, '0')}:${String(cairo.minute).padStart(2, '0')}
  Last OHLCV:   ${digest.ohlcv ?? '—'}
  Deliverable:  ${digest.symbols?.join(', ') || 'none'} (${digest.deliverable})
  Reconcile:    ${digest.reconcile} | pending ${digest.pending}
  Next session: ${nxt.next_trading_day}

  Prod ready:   ${ready?.pass ? '✅ PASS' : ready ? '❌ FAIL' : '—'} ${ready?.at?.slice(0, 19) ?? ''}
  Full verify:  ${verify?.pass ? '✅ PASS' : verify ? '❌ FAIL' : '—'} ${verify?.at?.slice(0, 19) ?? ''}
  Git:          ${gitLine}

── ONE COMMANDS ──
  npm run egx:prod:ready          # 7-step gate
  npm run egx:automation:status   # runbook + digest + logs
  npm run egx:runbook:next        # next session preview

── DOCS ──
  docs/PRODUCTION_AUTOMATION.md

── SUNDAY 2026-06-14 (automated) ──
  06:45 prod:ready → 16:30 TV → 17:20 Telegram → 17:45 post-session
  Ops alerts: EGX_ALERT_TELEGRAM=1 | EGX_OPS_SUCCESS_ALERT=1

  No manual action required.

  Push to GitHub (optional backup):
    npm run egx:git:sync -- --push
`);
