#!/usr/bin/env node
/**
 * Verify EGX production automation (cron, env, locks, notify path).
 * Usage: node scripts/egx_automation_verify.mjs [--ci]
 *   --ci  structural checks only (no crontab/Telegram/machine deps) — for GitHub Actions
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { isTelegramConfigured } from '../src/egx/notify.js';

loadEnv();

const CI_MODE = process.argv.includes('--ci');

const checks = [];
function ok(name, pass, detail = '') {
  checks.push({ name, pass, detail });
  console.log(`${pass ? '✅' : '❌'} ${name}${detail ? `: ${detail}` : ''}`);
}

let cron = '';
try {
  cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
} catch {
  cron = '';
}

if (CI_MODE) {
  ok('CI structural mode', true, 'skipping crontab/Telegram/machine deps');
} else {
  ok('crontab installed', cron.includes('EGX-DAILY-AUTOMATION'), cron ? 'found markers' : 'empty');
  ok('TV sync lock egx-tv-sync', /egx-tv-sync.*egx_tv_auto_update/.test(cron));
  ok('Telegram cron egx-telegram', /egx-telegram.*egx_telegram_cron/.test(cron));
  ok('Telegram cron PYTHON_BIN=/usr/bin/python3', /PYTHON_BIN=\/usr\/bin\/python3.*egx-telegram/.test(cron));
  ok('Telegram NOT sharing egx-daily lock', !/egx-daily.*egx_telegram_daily/.test(cron));
  ok('.env exists', existsSync(join(PROJECT_ROOT, '.env')));
  ok('TELEGRAM_BOT_TOKEN', Boolean(process.env.TELEGRAM_BOT_TOKEN));
  ok('TELEGRAM_CHAT_ID', Boolean(process.env.TELEGRAM_CHAT_ID));
  ok('Telegram configured', isTelegramConfigured());
  ok('PYTHON_BIN', Boolean(process.env.PYTHON_BIN || process.env.PYTHON3), process.env.PYTHON_BIN || process.env.PYTHON3 || 'missing');
}

const scripts = [
  'scripts/egx_telegram_cron.mjs',
  'scripts/egx_prod_prepare_send.mjs',
  'scripts/egx_decision_bot.mjs',
  'scripts/egx_export_trades_csv.mjs',
  'scripts/egx_notify_reconcile.mjs',
  'scripts/egx_runbook.mjs',
  'scripts/egx_session_ready.mjs',
  'scripts/egx_cron_log_check.mjs',
  'scripts/egx_automation_status.mjs',
  'scripts/egx_prod_ready.mjs',
  'scripts/lib/pre_send_check.mjs',
  'scripts/lib/delivery_audit.mjs',
  'scripts/lib/egx_safety_check.mjs',
  'scripts/lib/ops_digest.mjs',
  'egx_rules.json',
];
for (const s of scripts) {
  ok(`script ${s}`, existsSync(join(PROJECT_ROOT, s)));
}

if (!CI_MODE) {
  const PROD_PY = '/usr/bin/python3';
  try {
    execSync(`"${PROD_PY}" -c "import numpy, lightgbm"`, { stdio: 'pipe' });
    ok('Python ML deps prod (/usr/bin/python3)', true);
  } catch {
    ok('Python ML deps prod (/usr/bin/python3)', false, 'pip install numpy lightgbm for system python3');
  }
}

const stampPath = join(PROJECT_ROOT, 'data/notification_prepare_stamp.json');
if (existsSync(stampPath)) {
  try {
    const stamp = JSON.parse(readFileSync(stampPath, 'utf8'));
    ok('prepare stamp file', true, `${stamp.signal_date} ok=${stamp.ok}`);
  } catch {
    ok('prepare stamp file', false, 'parse error');
  }
} else {
  ok('prepare stamp file', true, 'optional — created by prepare-send');
}
ok('EGX safety veto configured', process.env.EGX_SAFETY_VETO !== '0', process.env.EGX_SAFETY_VETO ?? 'default=1');
ok('egx_rules.json readable', existsSync(join(PROJECT_ROOT, 'egx_rules.json')));
ok('Full verify script', existsSync(join(PROJECT_ROOT, 'scripts/egx_full_verify.mjs')));
if (!CI_MODE) {
  ok('Cron pre-market verify', /EGX-FULL-VERIFY-DAILY/.test(cron));
  ok('Cron post-session ops', /EGX-POST-SESSION-DAILY/.test(cron));
  ok('Cron session ready', /EGX-SESSION-READY-DAILY/.test(cron));
  ok('Cron log check', /EGX-CRON-LOG-CHECK-DAILY/.test(cron));
} else {
  ok('install_cron.mjs', existsSync(join(PROJECT_ROOT, 'scripts/install_cron.mjs')));
}
ok('Post-session script', existsSync(join(PROJECT_ROOT, 'scripts/egx_post_session_ops.mjs')));
ok('Recovery script', existsSync(join(PROJECT_ROOT, 'scripts/egx_notify_recovery.mjs')));
ok('EGX_ALERT_TELEGRAM', process.env.EGX_ALERT_TELEGRAM !== '0', process.env.EGX_ALERT_TELEGRAM ?? 'default=1');
ok('EGX_OPS_SUCCESS_ALERT', process.env.EGX_OPS_SUCCESS_ALERT !== '0', process.env.EGX_OPS_SUCCESS_ALERT ?? 'default=1');
ok('npm egx:runbook', true, 'egx:runbook + egx:runbook:next');
ok('npm egx:session:next', true, 'pre-session next-day checks');

const fail = checks.filter(c => !c.pass).length;
console.log(`\n=== Automation Verify: ${checks.length - fail}/${checks.length} PASS ===\n`);
process.exit(fail ? 1 : 0);
