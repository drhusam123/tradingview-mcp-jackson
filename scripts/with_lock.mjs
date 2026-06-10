#!/usr/bin/env node
/**
 * Lightweight cron lock wrapper.
 *
 * Usage:
 *   node scripts/with_lock.mjs egx-daily -- node script.mjs args...
 */

import { spawn } from 'child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { logDeliveryAttempt, latestOhlcvDate } from './lib/delivery_audit.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const LOCK_DIR = join(ROOT, 'logs', 'locks');
const DEFAULT_TTL_MS = 4 * 60 * 60 * 1000;
const DEFAULT_WAIT_MS = 90 * 60 * 1000;
const POLL_MS = 30 * 1000;

const sep = process.argv.indexOf('--');
const scope = process.argv[2];
const cmdParts = sep >= 0 ? process.argv.slice(sep + 1) : [];

if (!scope || sep < 0 || cmdParts.length === 0) {
  console.error('Usage: node scripts/with_lock.mjs <scope> -- <command> [args...]');
  process.exit(2);
}

mkdirSync(LOCK_DIR, { recursive: true });

const safeScope = String(scope).replace(/[^a-zA-Z0-9_.-]/g, '_');
const lockPath = join(LOCK_DIR, `${safeScope}.lock`);

function lockAgeMs() {
  try {
    const raw = JSON.parse(readFileSync(lockPath, 'utf8'));
    return Date.now() - Date.parse(raw.started_at || 0);
  } catch {
    return Infinity;
  }
}

function readLockHolder(path) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
}

function logLockSkip(lockScope, parts) {
  const cmd = (parts || []).join(' ');
  const notifyCmd = /egx_telegram_daily|notify_health|notify_dry_run|egx_go_live|egx_prod_prepare/.test(cmd);
  if (!notifyCmd) return;
  try {
    const signalDate = latestOhlcvDate() || new Date().toISOString().slice(0, 10);
    const held = readLockHolder(lockPath);
    const skippedAt = new Date().toISOString();
    logDeliveryAttempt({
      signal_date: signalDate,
      actionable: 0,
      message_generated: 0,
      send_attempted: 0,
      send_success: 0,
      skip_reason: `cron_lock_skip:scope=${lockScope}`,
      pipeline_stage: 'cron_lock_skip',
      cron_lock_status: 'SKIP_TIMEOUT',
      dedup_key: `lock_skip:${lockScope}:${signalDate}`,
      event_type: 'cron_lock_skip',
      held_by: held,
      meta_json: {
        event_type: 'cron_lock_skip',
        lock_name: lockScope,
        command: cmd.slice(0, 200),
        started_at: held?.started_at ?? null,
        skipped_at: skippedAt,
        held_by: held,
      },
    });
    console.error('[with-lock] Skip logged to notification_delivery_audit');
  } catch (e) {
    console.error(`[with-lock] audit log failed: ${e.message}`);
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

const waitStarted = Date.now();
let announcedWait = false;

while (existsSync(lockPath)) {
  const age = lockAgeMs();
  if (age >= DEFAULT_TTL_MS) {
    console.log(`[with-lock] removing stale lock for ${scope}`);
    rmSync(lockPath, { force: true, recursive: true });
    break;
  }
  if (Date.now() - waitStarted >= DEFAULT_WAIT_MS) {
    console.error(`[with-lock] SKIP ${scope}: lock did not clear within wait window`);
    logLockSkip(scope, cmdParts);
    process.exit(1);
  }
  if (!announcedWait) {
    console.log(`[with-lock] WAIT ${scope}: previous run is still active`);
    announcedWait = true;
  }
  await sleep(POLL_MS);
}

writeFileSync(lockPath, JSON.stringify({
  scope,
  started_at: new Date().toISOString(),
  pid: process.pid,
}) + '\n');

const [command, ...args] = cmdParts;
const child = spawn(command, args, { stdio: 'inherit' });

function cleanup() {
  rmSync(lockPath, { force: true, recursive: true });
}

child.on('exit', (code, signal) => {
  cleanup();
  if (signal) {
    console.error(`[with-lock] ${scope} terminated by ${signal}`);
    process.exit(1);
  }
  process.exit(code ?? 0);
});

child.on('error', (err) => {
  cleanup();
  console.error(`[with-lock] ${scope} failed: ${err.message}`);
  process.exit(1);
});
