#!/usr/bin/env node
/**
 * Go-live completion — P6 countdown, Telegram, cron, git push.
 * Usage: node scripts/egx_go_live.mjs [--skip-push] [--skip-cron] [--telegram-dry-run]
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { getProofLoopMetrics, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { nextTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';
import { auditClosedLoops } from './lib/loop_audit.mjs';
import { countDirectiveStats } from './lib/directive_resolver.mjs';
import { pushCommand, resolvePushRemote } from './lib/git_remote.mjs';

loadEnv();

const NODE = process.execPath;
const SKIP_PUSH = process.argv.includes('--skip-push');
const SKIP_CRON = process.argv.includes('--skip-cron');
const TG_DRY = process.argv.includes('--telegram-dry-run') || !process.argv.includes('--skip-telegram');

const steps = [];
function step(name, fn, { optional = false } = {}) {
  console.log(`\n▶  ${name}`);
  try {
    const result = fn();
    steps.push({ name, ok: true, result });
    return result;
  } catch (e) {
    steps.push({ name, ok: false, error: e.message?.slice(0, 200), optional });
    if (!optional) throw e;
    console.log(`⚠️  ${name}: ${e.message?.slice(0, 120)}`);
    return null;
  }
}

console.log('\n═══ EGX Go-Live Completion ═══');
console.log(`  Cairo: ${cairoDateParts().date}\n`);

// ── 1. P6 gate countdown (next 4 ULTRA sessions) ─────────────────────────────
const p6All = getProofLoopMetrics();
const p6Del = getProofLoopMetrics({ deliveredOnly: true });
const sessionsNeeded = p6All.samples_needed ?? Math.max(0, PROOF_MIN_N - p6All.n_completed);

let sessionDate = cairoDateParts().date;
const upcomingSessions = [];
for (let i = 0; i < Math.max(sessionsNeeded, 4); i++) {
  const nxt = nextTradingDay(sessionDate);
  sessionDate = nxt.next_trading_day;
  const wd = new Date(`${sessionDate}T12:00:00`).toLocaleDateString('en-US', { weekday: 'short' });
  upcomingSessions.push({
    n: i + 1,
    date: sessionDate,
    weekday: wd,
  });
}

const p6Plan = {
  at: new Date().toISOString(),
  gate: {
    n_completed: p6All.n_completed,
    target_n: PROOF_MIN_N,
    samples_needed: sessionsNeeded,
    win_rate: p6All.win_rate,
    target_wr: PROOF_MIN_WR,
    gate_pass: p6All.gate_pass,
  },
  delivered_gate: {
    n_completed: p6Del.n_completed,
    win_rate: p6Del.win_rate,
    note: 'Counts ULTRA with client_delivered=1 after live Telegram',
  },
  upcoming_ultra_sessions: upcomingSessions.slice(0, sessionsNeeded || 4),
  checklist_per_session: [
    'npm run egx:tv:auto (or wait cron 16:30)',
    'npm run egx:prod:prepare-send',
    'npm run egx:telegram:cron (live — no --dry-run)',
    'npm run egx:post:session (reconcile + closed_loop + p6_sync:light)',
  ],
  note: 'P6 delivered gate counts ULTRA_CONVICTION with client_delivered=1 and outcome_filled≥5. HIGH-only days do not advance delivered P6.',
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/p6_session_plan.json'), JSON.stringify(p6Plan, null, 2));

console.log('  📊 P6 Gate');
console.log(`     All ULTRA:     ${p6All.n_completed}/${PROOF_MIN_N} @ ${p6All.win_rate ?? '—'}% (need ${sessionsNeeded} more)`);
console.log(`     Delivered:     ${p6Del.n_completed}/${PROOF_MIN_N} @ ${p6Del.win_rate ?? '—'}%`);
console.log('     Next sessions for gate:');
for (const s of p6Plan.upcoming_ultra_sessions) {
  console.log(`       ${s.n}. ${s.date} (${s.weekday})`);
}
steps.push({ name: 'p6_session_plan', ok: true, result: p6Plan });

// ── 2. Telegram + client_delivered bridge ────────────────────────────────────
const hasToken = Boolean(process.env.TELEGRAM_BOT_TOKEN);
const hasChat = Boolean(process.env.TELEGRAM_CHAT_ID);
step('Telegram env', () => {
  if (!hasToken || !hasChat) throw new Error('TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env');
  return { token: true, chat: true };
});

step('Delivered outcomes sync', () => syncDeliveredOutcomes());

if (TG_DRY) {
  step('Telegram pipeline dry-run', () => {
    execSync(`"${NODE}" scripts/egx_telegram_cron.mjs --dry-run`, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: 600_000,
      env: { ...process.env },
    });
    return { dry_run: true };
  }, { optional: true });
}

// ── 3. Closed-loop health ────────────────────────────────────────────────────
step('Loop audit', () => auditClosedLoops({ maxAgeHours: 168 }));
step('Directive stats', () => countDirectiveStats());

// ── 4. Cron install ──────────────────────────────────────────────────────────
if (!SKIP_CRON) {
  step('Cron install', () => {
    execSync(`"${NODE}" scripts/install_cron.mjs`, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: 120_000,
    });
    const cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
    const markers = [
      'EGX-TELEGRAM-DAILY',
      'EGX-POST-SESSION-DAILY',
      'EGX-DAILY-AUTOMATION',
      'EGX-LEARNING-LOOP-WEEKLY',
      'EGX-DISCOVERY-FABRIC-D',
      'EGX-TV-MICRO-D',
      'EGX-DISCOVERY-PERPETUAL-W',
    ];
    const found = markers.filter(m => cron.includes(m));
    return { markers_found: found, total_lines: cron.split('\n').filter(l => l.includes('EGX-')).length };
  });
} else {
  console.log('\n⏭  Cron install skipped (--skip-cron)');
}

// ── 5. Git push ──────────────────────────────────────────────────────────────
let pushResult = { skipped: true };
if (!SKIP_PUSH) {
  try {
    const remote = resolvePushRemote();
    const cmd = pushCommand('main');
    console.log(`\n▶  Git push (${cmd})`);
    const status = execSync('git status -sb', { cwd: PROJECT_ROOT, encoding: 'utf8' }).trim();
    const ahead = status.match(/ahead (\d+)/)?.[1] ?? '?';
    console.log(`   Branch: ${status.split('\n')[0]} (${ahead} commits ahead)`);
    execSync(cmd, {
      cwd: PROJECT_ROOT,
      stdio: 'inherit',
      timeout: 300_000,
    });
    pushResult = { ok: true, ahead, remote };
    steps.push({ name: 'git_push', ok: true, result: pushResult });
  } catch (e) {
    const cmd = pushCommand('main');
    pushResult = {
      ok: false,
      error: e.message?.slice(0, 200),
      fix: `Run: gh auth login  (account: drhusam123)  then: ${cmd}`,
    };
    steps.push({ name: 'git_push', ok: false, error: pushResult.error, optional: true });
    console.log(`\n⚠️  Git push failed — manual step required:`);
    console.log('   gh auth login');
    console.log(`   ${cmd}`);
  }
}

const report = {
  at: new Date().toISOString(),
  steps,
  p6_plan: p6Plan,
  push: pushResult,
  telegram_configured: hasToken && hasChat,
};
writeFileSync(join(PROJECT_ROOT, 'data/go_live_last.json'), JSON.stringify(report, null, 2));

const fail = steps.filter(s => !s.ok && !s.optional).length;
console.log('\n═══ Go-Live Summary ═══');
for (const s of steps) {
  console.log(`  ${s.ok ? '✅' : s.optional ? '⚠️' : '❌'} ${s.name}`);
}
console.log(`\n  Saved: data/go_live_last.json + data/p6_session_plan.json`);
console.log(`  Result: ${fail ? 'PARTIAL — fix items above' : 'READY'}\n`);

process.exit(fail ? 1 : 0);
