#!/usr/bin/env node
/**
 * EGX Production Operations — تفعيل / مراقبة / تشغيل العملاء
 *
 *   npm run egx:prod:status     — لوحة الإنتاج الكاملة
 *   npm run egx:prod:activate   — تثبيت cron + فحص deps + TV CDP
 *   npm run egx:prod:daily      — تحديث يومي كامل (--launch --notify)
 *   npm run egx:prod:go-live    — preflight + telegram dry-run
 *   npm run egx:prod:send       — إرسال Telegram للعملاء
 *   npm run egx:prod:health     — health_monitor.py
 *   npm run egx:prod:funnel     — تقرير قمع الإشارات
 */
import { execSync, spawn } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { loadFreshnessKpis, formatFreshnessLines } from './lib/freshness_kpis.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const NODE = process.execPath;
const PY = process.env.PYTHON3 || 'python3';
const cmd = process.argv[2] || 'status';
const extra = process.argv.slice(3);

function run(cmdStr, { inherit = false, optional = false } = {}) {
  try {
    const out = execSync(cmdStr, {
      cwd: ROOT,
      encoding: 'utf8',
      stdio: inherit ? 'inherit' : 'pipe',
      timeout: 14_400_000, // 4h — daily TV sync alone can take ~2h

      env: { ...process.env, TV_CDP_BROWSER: process.env.TV_CDP_BROWSER || 'chrome' },
    });
    return { ok: true, out: out || '' };
  } catch (e) {
    if (!optional) console.error(`❌  ${cmdStr.split(' ').slice(0, 4).join(' ')}… → ${e.message}`);
    return { ok: false, err: e.message, out: e.stdout?.toString() || '' };
  }
}

function loadEnv() {
  const p = join(ROOT, '.env');
  if (!existsSync(p)) return;
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    const key = k.trim();
    const val = rest.join('=').trim().replace(/^["']|["']$/g, '');
    if (key && process.env[key] === undefined) process.env[key] = val;
  }
}

function dbStats() {
  const dbPath = join(ROOT, 'data/egx_trading.db');
  if (!existsSync(dbPath)) return null;
  const db = new Database(dbPath, { readonly: true });
  const q = (sql) => db.prepare(sql).get();
  const stats = {
    ohlcv: q("SELECT COUNT(DISTINCT symbol) sym, MAX(date) latest FROM ohlcv"),
    scans: q('SELECT COUNT(*) n, MAX(scan_date) latest FROM scans'),
    explosion: q('SELECT MAX(pred_date) latest, ROUND(AVG(prob_pct),1) avg_ml FROM explosion_predictions WHERE pred_date=(SELECT MAX(pred_date) FROM explosion_predictions)'),
    actionable: q("SELECT COUNT(*) n, MAX(trade_date) latest FROM final_signals WHERE actionable=1 AND trade_date NOT LIKE '2099-%'"),
    quant: q('SELECT COUNT(*) n, MAX(discovered_at) latest FROM quant_discovery_rules'),
    opportunity: q('SELECT COUNT(*) n, MAX(trade_date) latest FROM opportunity_score_v2'),
    mladv_meta: q('SELECT COUNT(*) n, MAX(date) latest FROM meta_label_scores'),
    mladv_shadow: q('SELECT COUNT(*) n, SUM(CASE WHEN win IS NOT NULL THEN 1 ELSE 0 END) resolved FROM gate_shadow_book'),
    mladv_vetted: q('SELECT SUM(vetted) vetted, COUNT(*) total FROM quant_discovery_rules'),
    purged_auc: q("SELECT feature_value v FROM feature_store WHERE symbol='MARKET' AND feature_name='purged_auc_triple_barrier' ORDER BY feature_date DESC LIMIT 1"),
    drift: q("SELECT feature_value v FROM feature_store WHERE symbol='MARKET' AND feature_name='mladv_drift_throttle' ORDER BY feature_date DESC LIMIT 1"),
  };
  db.close();
  return stats;
}

function trainingRunning() {
  try {
    return execSync('pgrep -fl "egx_full_train|egx_ml_trainer" 2>/dev/null || true', { encoding: 'utf8' }).trim();
  } catch { return ''; }
}

function cronCount() {
  try {
    const c = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
    return (c.match(/# EGX/g) || []).length;
  } catch { return 0; }
}

function cdpOk() {
  try {
    execSync('curl -sf --max-time 2 http://127.0.0.1:9222/json/version', { stdio: 'ignore' });
    return true;
  } catch { return false; }
}

function banner(t) {
  console.log(`\n${'═'.repeat(64)}\n  ${t}\n${'═'.repeat(64)}`);
}

async function status() {
  loadEnv();
  banner('EGX Production Dashboard');
  console.log(`  الوقت: ${new Date().toLocaleString('ar-EG')}`);

  console.log('\n▶  البنية التحتية');
  console.log(`  ${cdpOk() ? '✅' : '❌'}  TV CDP (9222)  TV_CDP_BROWSER=${process.env.TV_CDP_BROWSER || 'chrome'}`);
  console.log(`  ${cronCount() >= 40 ? '✅' : '⚠️'}  Cron EGX jobs: ${cronCount()}`);
  const train = trainingRunning();
  console.log(`  ${train ? '⏳' : '✅'}  ML Training: ${train ? 'جاري' : 'لا يوجد'}`);
  if (train) console.log(`       ${train.split('\n').join('\n       ')}`);

  const s = dbStats();
  if (s) {
    console.log('\n▶  البيانات والمحركات');
    console.log(`  OHLCV:      ${s.ohlcv?.sym} سهم | آخر ${s.ohlcv?.latest}`);
    console.log(`  Scans:      ${s.scans?.n} | آخر ${s.scans?.latest}`);
    console.log(`  ML pred:    avg ${s.explosion?.avg_ml}% | آخر ${s.explosion?.latest}`);
    console.log(`  Quant:      ${s.quant?.n} قاعدة | آخر ${s.quant?.latest}`);
    console.log(`  Opportunity:${s.opportunity?.n} | آخر ${s.opportunity?.latest}`);
    console.log(`  Actionable: ${s.actionable?.n} | آخر ${s.actionable?.latest}`);
    console.log(`  ML-Advanced: meta ${s.mladv_meta?.n} | shadow ${s.mladv_shadow?.resolved}/${s.mladv_shadow?.n} | quant vetted ${s.mladv_vetted?.vetted}/${s.mladv_vetted?.total}`);
    if (s.purged_auc?.v != null) console.log(`  Purged AUC: ${s.purged_auc.v} (honest)`);
    if (s.drift?.v >= 1) console.log(`  ⚠️  Drift throttle ON — ML floor +4`);
  }

  console.log('\n▶  Freshness KPIs (L0→L1)');
  const freshness = loadFreshnessKpis(ROOT, join(ROOT, 'data/egx_trading.db'));
  for (const line of formatFreshnessLines(freshness)) console.log(line);

  console.log('\n▶  أوامر الإنتاج');
  console.log('  npm run egx:prod:daily         — تحديث + scoring + dry-run telegram');
  console.log('  npm run egx:prod:prepare-send  — score + ML + health + dry-run');
  console.log('  npm run egx:prod:send          — إرسال للعملاء (يتطلب prepare-send)');
  console.log('  npm run egx:notify:daily-ops   — reconcile + safety + health + dry-run + audit');
  console.log('  npm run egx:verify:all         — تحقق كامل (TV + cron + tests)');
  console.log('  npm run egx:verify:fast        — تحقق سريع (بدون CDP/tests)');

  const verifyPath = join(ROOT, 'data/full_verify_last.json');
  if (existsSync(verifyPath)) {
    try {
      const v = JSON.parse(readFileSync(verifyPath, 'utf8'));
      console.log(`\n▶  آخر تحقق: ${v.at?.slice(0, 19)} → ${v.pass ? '✅ PASS' : '❌ FAIL'} (${v.total - v.failed}/${v.total})`);
    } catch { /* */ }
  }
  const readyPath = join(ROOT, 'data/prod_ready_last.json');
  if (existsSync(readyPath)) {
    try {
      const r = JSON.parse(readFileSync(readyPath, 'utf8'));
      const n = r.steps?.length ?? 0;
      const p = r.steps?.filter(s => s.ok).length ?? 0;
      console.log(`▶  Production ready: ${r.at?.slice(0, 19)} → ${r.pass ? '✅ PASS' : '❌ FAIL'} (${p}/${n}) | next ${r.next_session ?? '—'}`);
    } catch { /* */ }
  }
  console.log('  npm run egx:notify:reconcile   — فجوات التسليم actionable vs audit');
  console.log('  npm run egx:notify:recovery    — استعادة الإرسالات المعلقة');
  console.log('  npm run egx:post:session       — reconcile + ml_refresh + closed_loop بعد الجلسة');
  console.log('  npm run egx:pre:session        — audit + funnel + gate_simulate قبل الجلسة');
  console.log('  npm run egx:ml:gate:verify       — تحقق أتمتة ML+Gates');
  console.log('  npm run egx:alert:test         — اختبار تنبيهات الأعطال');
  console.log('  npm run egx:session:ready      — جاهزية الجلسة (upstream+cron)');
  console.log('  npm run egx:session:next       — جاهزية الجلسة القادمة');
  console.log('  npm run egx:runbook            — دليل التشغيل اليومي');
  console.log('  npm run egx:runbook:next       — دليل الجلسة القادمة');
  console.log('  npm run egx:prod:ready           — بوابة جاهزية شاملة (7 خطوات)');
  console.log('  npm run egx:automation:status    — حالة الأتمتة الكاملة');
  console.log('  npm run egx:cron:log-check       — فحص سجلات cron للأعطال');
  console.log('  npm run egx:prod:go-live       — فحص قبل الإرسال');
  console.log('  npm run egx:prod:health     — مراقبة الصحة');
  console.log('  npm run egx:prod:funnel     — قمع الإشارات');
  console.log('  npm run egx:train:full      — تدريب ثقيل (أسبوعي)');
}

async function activate() {
  loadEnv();
  banner('تفعيل الإنتاج');
  run(`"${NODE}" scripts/install_cron.mjs`, { inherit: true });
  run('npm run egx:deps:check', { inherit: true, optional: true });
  if (!cdpOk()) {
    console.log('\n▶  تشغيل TV CDP...');
    run('npm run tv:launch', { inherit: true, optional: true });
  }
  run(`"${PY}" scripts/python/health_monitor.py check`, { inherit: true, optional: true });
  run('npm run egx:env:sync', { inherit: true, optional: true });
  run('npm run egx:verify:all', { inherit: true, optional: true });
  run('npm run egx:automation:status', { inherit: true, optional: true });
  console.log('\n✅  التفعيل اكتمل — شغّل: npm run egx:prod:daily');
  console.log('   أو: npm run egx:prod:ready  — فحص جاهزية كامل');
}

async function daily() {
  loadEnv();
  const notify = extra.includes('--notify') || process.argv.includes('--notify');
  banner(notify ? 'تحديث يومي + إرسال عملاء' : 'تحديث يومي (dry-run telegram)');
  const flags = '--launch --pine --tech' + (notify ? ' --notify' : '');
  run(`"${NODE}" scripts/egx_tv_auto_update.mjs ${flags}`, { inherit: true });
  run(`"${NODE}" scripts/egx_signal_funnel.mjs`, { inherit: true, optional: true });
}

async function goLive() {
  loadEnv();
  const send = cmd === 'send' || extra.includes('--send');
  run(`"${NODE}" scripts/egx_go_live.mjs ${send ? '--send --update' : '--update'}`, { inherit: true });
}

loadEnv();

switch (cmd) {
  case 'status':
    await status();
    break;
  case 'activate':
    await activate();
    break;
  case 'daily':
    await daily();
    break;
  case 'go-live':
    await goLive();
    break;
  case 'prepare-send':
    run(`"${NODE}" scripts/egx_prod_prepare_send.mjs ${extra.join(' ')}`, { inherit: true });
    break;
  case 'send':
    await goLive();
    break;
  case 'health':
    run(`"${PY}" scripts/python/health_monitor.py check`, { inherit: true });
    break;
  case 'funnel':
    run(`"${NODE}" scripts/egx_signal_funnel.mjs ${extra.join(' ')}`, { inherit: true });
    break;
  case 'watch-train':
    run('tail -f logs/full_train_*.log', { inherit: true });
    break;
  default:
    console.log('Usage: egx_production_ops.mjs [status|activate|daily|go-live|send|health|funnel]');
    process.exit(1);
}
