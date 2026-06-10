#!/usr/bin/env node
/**
 * EGX Weekly Performance Report Sender — Phase 32
 * =================================================
 * يُشغَّل كل أحد 16:00 القاهرة (13:00 UTC) عبر cron.
 * يُولِّد تقرير الأداء الأسبوعي من recommendation_outcomes
 * ويُرسله مباشرةً إلى Telegram.
 *
 * Usage:
 *   node scripts/egx_weekly_perf.mjs            # live send
 *   node scripts/egx_weekly_perf.mjs --dry-run  # print only, no send
 *
 * Cron (الأحد 16:00 القاهرة = 13:00 UTC):
 *   0 13 * * 0  cd /path && node scripts/egx_weekly_perf.mjs >> logs/weekly_perf.log 2>&1
 */

import { execFileSync }     from 'child_process';
import { sendTelegram }     from '../src/egx/notify.js';
import { join, dirname }    from 'path';
import { fileURLToPath }    from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT      = join(__dirname, '..');
const DRY_RUN   = process.argv.includes('--dry-run');

const log = (m) => process.stdout.write(`[${new Date().toISOString()}] ${m}\n`);

const PYTHON3 = (() => {
  for (const p of ['/usr/bin/python3', '/usr/local/bin/python3', 'python3']) {
    try { execFileSync(p, ['--version'], { stdio: 'ignore' }); return p; } catch {}
  }
  return '/usr/bin/python3';
})();

log('═══ EGX Weekly Performance Report ═══');
if (DRY_RUN) log('⚠️  DRY RUN — لن يُرسَل شيء');

// 1. Generate the report via signal_integration.py
let result;
try {
  const raw = execFileSync(
    PYTHON3,
    ['scripts/python/signal_integration.py', 'weekly_performance_report',
     JSON.stringify({ send: true, weeks_back: 4, min_outcomes: 5 })],
    { cwd: ROOT, timeout: 30_000 }
  ).toString();
  result = JSON.parse(raw.trim());
} catch (e) {
  log(`❌ فشل توليد التقرير: ${e.message}`);
  process.exit(1);
}

if (!result.success) {
  log(`❌ خطأ: ${result.error || 'unknown'}`);
  process.exit(1);
}

// 2. Check if we have enough outcomes
if (result.message) {
  log(`ℹ️  ${result.message}`);
  // Still exit cleanly — not enough data yet
  process.exit(0);
}

const msg = result.telegram_message;
if (!msg) {
  log('⚠️  لا توجد رسالة في النتيجة');
  process.exit(0);
}

log(`📊 التقرير: ${result.n_outcomes} صفقة | أحدث ${result.period}`);
log(`   WR: ${result.win_rate_5d}% | Avg ret: ${result.avg_return_5d}%`);

if (DRY_RUN) {
  log('\n──── معاينة الرسالة ────');
  msg.split('\n').forEach(l => log('  ' + l));
  log(`\n✅ DRY RUN — تم`);
  process.exit(0);
}

// 3. Send to Telegram
try {
  const r = await sendTelegram(msg, { parseMode: 'HTML' });
  if (r?.ok === false) {
    log(`❌ Telegram error: ${r.error}`);
    process.exit(1);
  }
  log('✅ تم إرسال التقرير الأسبوعي إلى Telegram');
} catch (e) {
  log(`❌ فشل الإرسال: ${e.message}`);
  process.exit(1);
}

log('═══ اكتمل ═══');
