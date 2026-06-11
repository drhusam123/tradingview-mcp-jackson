#!/usr/bin/env node
/**
 * Pre-session readiness — upstream dates, cron, last verify, trading calendar.
 * Usage: node scripts/egx_session_ready.mjs [--date YYYY-MM-DD]
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { isTradingDay, cairoDateParts, tradingDayStaleness, nextTradingDay } from './lib/egx_calendar.mjs';
import { getUpstreamDates, latestOhlcvDate, countActionable, wasAlreadySent } from './lib/delivery_audit.mjs';
import { alertNotification } from './lib/notification_alert.mjs';
import { runDailyQualityGate } from './lib/data_quality_gate.mjs';
import { getProofLoopMetrics, formatProofLoopLine, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';

loadEnv();

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const useNext = process.argv.includes('--next');
const SKIP_VERIFY_CHECK = process.argv.includes('--skip-verify-check');
const target = dateArg
  || (useNext ? nextTradingDay(cairoDateParts().date).next_trading_day : null)
  || latestOhlcvDate()
  || cairoDateParts().date;

const checks = [];
function ok(name, pass, detail = '', { warn = false } = {}) {
  checks.push({ name, pass, detail, warn });
  const icon = pass ? '✅' : (warn ? '⏳' : '❌');
  console.log(`${icon} ${name}${detail ? `: ${detail}` : ''}`);
}

let cron = '';
try { cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' }); } catch { /* */ }

try {
  const cal = isTradingDay(target);
  ok('EGX trading day', cal.is_trading_day, cal.is_trading_day ? target : (cal.holiday_name || 'closed'));
} catch (e) {
  ok('EGX trading day', false, e.message);
}

const upstream = getUpstreamDates();
const latestOhlcv = upstream.ohlcv || latestOhlcvDate();
const futureSession = useNext && latestOhlcv && target > latestOhlcv;

ok('OHLCV date', Boolean(latestOhlcv), latestOhlcv || 'none');

if (futureSession) {
  ok('ML pred date', true, `pending until ${target} (latest ml=${upstream.ml_pred})`, { warn: true });
  ok('Scan date', true, `pending until ${target} (latest scan=${upstream.scan})`, { warn: true });
  ok('Meta date', true, `pending until ${target} (latest meta=${upstream.meta ?? 'none'})`, { warn: true });
  ok('Data freshness', true, `pre-session — last OHLCV ${latestOhlcv}`, { warn: true });
} else {
  ok('ML pred date', upstream.ml_pred && upstream.ml_pred >= target, `ml=${upstream.ml_pred} need=${target}`);
  ok('Scan date', upstream.scan && upstream.scan >= target, `scan=${upstream.scan}`);
  ok('Meta date', !upstream.meta || upstream.meta >= target, `meta=${upstream.meta ?? 'none'}`);
  const stale = tradingDayStaleness(latestOhlcv || target, target);
  ok('Data freshness', stale.staleness_trading_days === 0, `${stale.staleness_trading_days} sessions stale`);
}

const act = countActionable(target);
ok('Actionable signals', true, `${act.db} db / ${act.deliverable} deliverable ${act.symbols.join(',') || 'none'}`);

const dup = wasAlreadySent(target);
if (dup.duplicate) {
  ok('Delivery status', true, `sent (${dup.reason})`);
} else {
  ok('Delivery status', true, useNext ? 'pending next session' : 'not sent yet — run prepare-send');
}

ok('Cron TV sync', /egx-tv-sync.*egx_tv_auto_update/.test(cron));
ok('Cron Telegram', /egx-telegram.*egx_telegram_cron/.test(cron));
ok('Cron post-session', /EGX-POST-SESSION-DAILY/.test(cron));

try {
  const gate = runDailyQualityGate();
  ok(
    'Data trust (L2)',
    !gate.blocked,
    gate.blocked
      ? `BLOCKED ${gate.reason}`
      : `${gate.latest_date} trust=${gate.trust_score} (${gate.trust_status})`,
    { warn: futureSession },
  );
} catch (e) {
  ok('Data trust (L2)', false, e.message?.slice(0, 80), { warn: futureSession });
}

const proof = getProofLoopMetrics();
const proofDel = getProofLoopMetrics({ deliveredOnly: true });
ok(
  'Proof loop P6',
  proof.gate_pass || proof.samples_needed > 0,
  formatProofLoopLine(proof).replace(/^[^\s]+\s/, ''),
  { warn: !proof.gate_pass && proof.n_completed < PROOF_MIN_N },
);
const p6Blocker = proof.gate_pass
  ? 'PASS'
  : proof.samples_needed > 0
    ? `need ${proof.samples_needed} more ULTRA samples`
    : `WR ${proof.win_rate ?? '—'}% < ${PROOF_MIN_WR}% (live winning sessions)`;
ok(
  'P6 delivered track',
  true,
  `${proofDel.n_completed} delivered ULTRA filled≥5 @ ${proofDel.win_rate ?? '—'}% | gate: ${p6Blocker}`,
  { warn: proofDel.n_completed === 0 },
);

ok('Telegram configured', Boolean(process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_CHAT_ID));

try {
  const sync = syncDeliveredOutcomes({ lookbackDays: 120 });
  ok('client_delivered sync', sync.ok, `${sync.ultra_delivered ?? 0}/${sync.ultra_total_filled ?? 0} ULTRA marked`);
} catch (e) {
  ok('client_delivered sync', false, e.message?.slice(0, 60));
}

const verifyPath = join(PROJECT_ROOT, 'data/full_verify_last.json');
if (SKIP_VERIFY_CHECK) {
  ok('Last full verify', true, 'skipped (verify running)', { warn: true });
} else if (existsSync(verifyPath)) {
  const v = JSON.parse(readFileSync(verifyPath, 'utf8'));
  const ageH = (Date.now() - new Date(v.at).getTime()) / 3_600_000;
  ok('Last full verify', v.pass, `${v.at?.slice(0, 16)} (${ageH.toFixed(0)}h ago)`);
} else {
  ok('Last full verify', false, 'never run — npm run egx:verify:all');
}

const fail = checks.filter(c => !c.pass && !c.warn).length;
const report = {
  date: target,
  at: new Date().toISOString(),
  next_mode: useNext,
  pass: fail === 0,
  passed: checks.length - fail,
  total: checks.length,
  checks,
};
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/session_ready_last.json'), JSON.stringify(report, null, 2));

console.log(`\n=== Session Ready (${target}): ${checks.length - fail}/${checks.length} ===\n`);
if (fail) {
  alertNotification('SESSION_READY_FAIL', {
    date: target,
    next_mode: useNext,
    failed: checks.filter(c => !c.pass && !c.warn).map(c => c.name),
  });
}
process.exit(fail ? 1 : 0);
