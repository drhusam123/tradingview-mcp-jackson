#!/usr/bin/env node
/**
 * EGX Daily Automation Runner (compat wrapper)
 * ============================================
 * المسار الرسمي للإنتاج: scripts/egx_tv_auto_update.mjs (npm run egx:daily)
 *
 * run_daily.mjs يُفوّض افتراضياً إلى المسار الرسمي. استخدم --legacy للسلسلة القديمة الكاملة.
 *
 * التشغيل:
 *   node scripts/run_daily.mjs                  → egx_tv_auto_update.mjs --launch
 *   node scripts/run_daily.mjs --notify         → نفس المسار + إرسال Telegram
 *   node scripts/run_daily.mjs --legacy           → السلسلة القديمة (30+ خطوة)
 *   node scripts/run_daily.mjs --weekly-deep      → night_lab weekly_deep فقط
 *
 * Cron الرسمي: install_cron.mjs → egx_tv_auto_update.mjs
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { execSync }           from 'child_process';
import { mkdirSync }          from 'fs';
import { join }               from 'path';
import { ensureTradingView }  from './lib/ensure_tv.mjs';

const ROOT     = new URL('..', import.meta.url).pathname;
const DRY_RUN     = process.argv.includes('--dry-run');
const SKIP_UPD    = process.argv.includes('--skip-update');
const FORCE       = process.argv.includes('--force');
const LEGACY      = process.argv.includes('--legacy');
const NOTIFY      = process.argv.includes('--notify');
const WEEKLY_DEEP = process.argv.includes('--weekly-deep');  // أحد: تدريب ML كامل
const LOG_DIR  = join(ROOT, 'logs');

// ── استخدام المسار الكامل للـ node binary لضمان عمل cron ──────────────
// process.execPath = /usr/local/bin/node (أو المسار الفعلي)
// بدون هذا يفشل cron لأن PATH المقيّد لا يتضمن node
const NODE = process.execPath;

mkdirSync(LOG_DIR, { recursive: true });

const log = (msg) => {
  const ts = new Date().toISOString();
  process.stdout.write(`[${ts}] ${msg}\n`);
};

const PYTHON3 = (() => {
  for (const p of ['/usr/bin/python3', '/usr/local/bin/python3', 'python3']) {
    try { execSync(`"${p}" --version`, { stdio: 'ignore' }); return p; } catch {}
  }
  return '/usr/bin/python3';
})();

function cairoDateISO() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Cairo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const byType = Object.fromEntries(parts.map(p => [p.type, p.value]));
  return `${byType.year}-${byType.month}-${byType.day}`;
}

function getMarketStatus() {
  try {
    const raw = execSync(
      `"${PYTHON3}" scripts/python/event_calendar.py is_trading_day '{"date":"${cairoDateISO()}"}'`,
      { cwd: ROOT, timeout: 10_000 }
    ).toString();
    return JSON.parse(raw.trim());
  } catch (e) {
    return { is_trading_day: true, warning: `calendar bridge failed: ${e.message}` };
  }
}

const run = (cmd, desc, { critical = false } = {}) => {
  // استبدل "node " و "python3 " بمسارات كاملة لضمان عمل cron
  const fullCmd = cmd
    .replace(/^node\s+/, `"${NODE}" `)
    .replace(/^python3\s+/, `"${PYTHON3}" `);
  log(`▶  ${desc}...`);
  if (DRY_RUN) {
    log(`   (dry-run) ${fullCmd}`);
    return;
  }
  try {
    execSync(fullCmd, { cwd: ROOT, stdio: 'inherit', timeout: 300_000 });
    log(`   ✅  ${desc} — اكتمل`);
  } catch (err) {
    log(`   ❌  ${desc} — فشل: ${err.message}`);
    if (critical) {
      throw new Error(`Critical step failed: ${desc}`);
    }
    // non-critical enrichment steps warn and continue
  }
};

async function spawnWeeklyDeep() {
  log('🧠 [Weekly Deep] بدء التدريب العميق الأسبوعي (night_lab weekly_deep)...');
  if (DRY_RUN) {
    log('   (dry-run) python3 scripts/python/night_lab.py weekly_deep');
    return;
  }
  const { spawn } = await import('child_process');
  const { createWriteStream } = await import('fs');
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const logPath = `${ROOT}/logs/weekly_deep_${ts}.log`;
  const child = spawn(PYTHON3, ['scripts/python/night_lab.py', 'weekly_deep'], {
    cwd: ROOT, detached: true, stdio: ['ignore', 'pipe', 'pipe'],
  });
  const logStream = createWriteStream(logPath, { flags: 'a' });
  child.stdout.pipe(logStream);
  child.stderr.pipe(logStream);
  child.unref();
  log(`   🚀 Weekly Deep started (PID ${child.pid}) → ${logPath}`);
}

async function runOfficialDaily() {
  const flags = ['--launch'];
  if (FORCE) flags.push('--force');
  if (NOTIFY) flags.push('--notify');
  if (DRY_RUN) flags.push('--dry-run');
  const cmd = `"${NODE}" scripts/egx_tv_auto_update.mjs ${flags.join(' ')}`;
  log(`▶  المسار الرسمي: egx_tv_auto_update.mjs ${flags.join(' ')}`);
  if (DRY_RUN) {
    log(`   (dry-run) ${cmd}`);
    return;
  }
  execSync(cmd, { cwd: ROOT, stdio: 'inherit', timeout: 1000 * 60 * 180 });
  const dayOfWeek = new Date().getDay();
  if (dayOfWeek === 0) await spawnWeeklyDeep();
}

async function main() {
  const today = new Date().toLocaleString('ar-EG', { timeZone: 'Africa/Cairo' });
  log(`═══ EGX Daily Run — ${today} ═══`);

  if (WEEKLY_DEEP && !LEGACY) {
    await spawnWeeklyDeep();
    return;
  }

  if (!LEGACY) {
    await runOfficialDaily();
    log('═══ تم الانتهاء (المسار الرسمي) ═══');
    return;
  }

  if (DRY_RUN) log('⚠️  DRY RUN — لا شيء سيُحفظ أو يُرسل');

  const market = getMarketStatus();
  if (market.warning) log(`⚠️  ${market.warning}`);
  if (!market.is_trading_day && !FORCE && !WEEKLY_DEEP) {
    const reason = market.holiday_name ? ` — ${market.holiday_name}` : '';
    log(`⛔ MARKET_CLOSED${reason} — لا fetch ولا إشارات إنتاج اليوم. استخدم --force للتجاوز اليدوي.`);
    run('node scripts/egx_status.mjs', 'ملخص حالة النظام');
    log(`═══ تم إيقاف التشغيل اليومي لأن السوق مغلق ═══`);
    return;
  }

  // 0. التحقق من TradingView وفتحه تلقائياً (shared helper)
  await ensureTradingView({ log, dryRun: DRY_RUN });

  // 1. تحديث البيانات من TradingView
  if (!SKIP_UPD) {
    run('node scripts/daily_update.mjs',        'جلب بيانات OHLCV من TradingView', { critical: true });
  }

  // 2. إعادة بناء المؤشرات
  run('node scripts/rebuild_indicators.mjs',    'إعادة حساب المؤشرات التقنية', { critical: true });

  // 3. مسح اليوم — حفظ في DB فقط
  run('node scripts/scan_today.mjs --db-only',  'مسح السوق وحفظ الإشارات', { critical: true });

  // 4. جلب البيانات المالية (P/E, P/B, ROE...) من TradingView Scanner
  //    يومياً لضمان تحديث بيانات الاستثمار
  run('node scripts/fetch_fundamentals.mjs',    'جلب البيانات المالية الأساسية');

  // 5. جلب بيانات ECONOMICS الكاملة (22 مؤشر: تضخم + فائدة + نمو + تجارة + احتياطيات...)
  run('node scripts/fetch_economics.mjs --bars 24',
      'جلب كامل 22 مؤشر اقتصادي مصري من TradingView');

  // 6. تحديث تنبؤات ML للانفجار السعري (Phase 63 — explosion_ml)
  //    يُنفَّذ بعد تحديث البيانات مباشرةً لضمان توقعات يومية حديثة
  run('node scripts/egx_explosion_ml.mjs predict',
      'تحديث توقعات ML للانفجار السعري (نموذج مفرد)');

  // 6a2. تحديث تنبؤات Ensemble — LGBM+XGB+RF+ET+Meta (egx_ml_trainer Phase 8)
  //     يكتب فوق explosion_predictions بنتائج أدق (AUC_OOS=0.904)
  run(`python3 scripts/python/egx_ml_trainer.py predict_ensemble`,
      'تحديث توقعات Ensemble (4 نماذج + Meta-Stack AUC=0.904)');

  // 6b. مسح Anti-Laws اليومي لجميع الأسهم
  run('node scripts/egx_anti_laws.mjs daily',
      'مسح Anti-Laws اليومي (تحديد الأسهم المحظورة)');

  // 6c. تحديث اتساع السوق (Market Breadth)
  run('node scripts/egx_market_breadth.mjs signal',
      'حساب اتساع السوق اليومي وحفظه');

  // 6d. كشف الـ Regime الحالي (HMM)
  run('node scripts/egx_hidden_regime.mjs detect',
      'كشف النظام السوقي الحالي (HMM)');

  // 6e. توقعات Regime-Specific ML
  run('node scripts/egx_regime_ml.mjs predict',
      'توقعات ML المخصصة لكل نظام سوقي');

  // 6f. تحديث Feature Store اليومي (Phase 84)
  //     يحسب 16 مميزاً لكل سهم ويحفظها مع lineage
  run('node scripts/egx_feature_store.mjs refresh',
      'تحديث Feature Store اليومي (247 سهم × 16 مميز)');

  // 6g. إنذار مبكر لتغيير النظام السوقي (Phase 83)
  run('node scripts/egx_regime_transition.mjs warning',
      'إنذار مبكر لتغيير النظام السوقي');

  // 6g2. Ph 21 — Spectral Cycle Intelligence يجب قبل signal_integration
  //       يحسب 15 feature طيفية لكل سهم ويكتب spectral_regime + cycle_bottom_prox
  run(`python3 scripts/python/egx_ml_trainer.py phase21`,
      'Spectral Intelligence — FFT cycle features (247 syms × 15 feats)');

  // 6h. Ph 75 — حساب درجات UES مع DNA + دورات لكل الأسهم (Ph27+28+29+75)
  run(`python3 scripts/python/signal_integration.py score_all`,
      'حساب UES + DNA + Behavioral + Pine + Cycle لكل الأسهم (Ph 27-29+75)', { critical: true });

  // 6h2. Ph 32 — تتبع نتائج التوصيات (سريع ~10ms)
  run(`python3 scripts/python/signal_integration.py track_outcomes`,
      'تتبع نتائج التوصيات وملء العوائد الفعلية (Ph 32)');

  // 6h3. Ph 33 — مراقب انجراف النموذج (سريع ~5ms)
  //      ينبّه إذا انخفض WR عن 45% أو فقد النموذج قوته التمييزية
  //      Ph 34: عند اكتشاف Drift → يُشغّل إعادة التدريب في الخلفية تلقائياً
  {
    const driftRaw = (() => {
      try {
        return execSync(
          `"${PYTHON3}" scripts/python/signal_integration.py model_drift '{"window_days":30,"min_filled":10,"alert_threshold_wr":45.0}'`,
          { cwd: ROOT, timeout: 15_000 }
        ).toString();
      } catch { return null; }
    })();
    if (driftRaw) {
      try {
        const drift = JSON.parse(driftRaw.trim());
        if (drift.drift_detected) {
          log(`⚠️  [Ph33] DRIFT: ${drift.drift_reason} — بدء إعادة التدريب تلقائياً (Ph34)`);
          if (!DRY_RUN) {
            // شغّل phase2 في الخلفية (non-blocking)
            const { spawn } = await import('child_process');
            const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
            const logPath = `${ROOT}/logs/auto_retrain_${ts}.log`;
            const child = spawn(
              PYTHON3,
              ['scripts/python/egx_ml_trainer.py', 'phase2'],
              { cwd: ROOT, detached: true, stdio: ['ignore', 'pipe', 'pipe'] }
            );
            const { createWriteStream } = await import('fs');
            const logStream = createWriteStream(logPath, { flags: 'a' });
            child.stdout.pipe(logStream);
            child.stderr.pipe(logStream);
            child.unref();
            log(`   🚀 Auto-retrain started (PID ${child.pid}) → ${logPath}`);
          }
        } else if (drift.n_filled >= 10) {
          log(`   ✅ [Ph33] Model OK: WR=${drift.win_rate}% | gated=${drift.gated_win_rate ?? 'N/A'}%`);
        } else {
          log(`   ℹ️  [Ph33] ${drift.message || `${drift.n_filled} outcomes filled`}`);
        }
      } catch { /* parse error — skip */ }
    }
  }

  // 6h4. Ph 36 — Signal Freshness Check (سريع ~50ms)
  //      يصنّف الإشارات: fresh / extended / chased / stopped
  //      يُلخّص كم إشارة لا تزال قابلة للتداول
  {
    const freshRaw = (() => {
      try {
        return execSync(
          `"${PYTHON3}" scripts/python/signal_integration.py signal_freshness '{}'`,
          { cwd: ROOT, timeout: 20_000 }
        ).toString();
      } catch { return null; }
    })();
    if (freshRaw) {
      try {
        const fr = JSON.parse(freshRaw.trim());
        log(`   📍 [Ph36] Freshness: ${fr.fresh_count} fresh | ${fr.extended_count} extended | ${fr.chased_count} chased | ${fr.stopped_count} stopped`);
      } catch { /* skip */ }
    }
  }

  // 6h5. Ph 40 — Signal Age Check (~5ms)
  //      يُظهر الإشارات التي مضى عليها ≥3 أيام في النظام — مؤشر إعادة تقييم
  {
    const ageRaw = (() => {
      try {
        return execSync(
          `"${PYTHON3}" scripts/python/signal_integration.py signal_age '{"min_age":2}'`,
          { cwd: ROOT, timeout: 10_000 }
        ).toString();
      } catch { return null; }
    })();
    if (ageRaw) {
      try {
        const ag = JSON.parse(ageRaw.trim());
        const oldSigs = (ag.aged_signals || []).filter(s => s.age_days >= 3);
        if (oldSigs.length > 0) {
          const oldest = oldSigs.slice(0, 3).map(s => `${s.symbol}(${s.age_days}d)`).join(', ');
          log(`   ⏳ [Ph40] ${oldSigs.length} إشارة عمرها ≥3 أيام: ${oldest}`);
        } else {
          log(`   ✅ [Ph40] جميع الإشارات حديثة (${ag.n_gated} إشارة)`);
        }
      } catch { /* skip */ }
    }
  }

  // 6h6. Ph 44 — Entry Trigger Tracker (~20ms)
  //      يكتشف الإشارات التي لامس سعرها منطقة الدخول — يُعلّمها entry_triggered=1
  {
    const trigRaw = (() => {
      try {
        return execSync(
          `"${PYTHON3}" scripts/python/signal_integration.py check_entry_triggers '{"lookback_days":10}'`,
          { cwd: ROOT, timeout: 30_000 }
        ).toString();
      } catch { return null; }
    })();
    if (trigRaw) {
      try {
        const tr = JSON.parse(trigRaw.trim());
        if (tr.n_triggered > 0) {
          const sym3 = (tr.triggered || []).slice(0, 3).map(t => `${t.symbol}(${t.trigger_date})`).join(', ');
          log(`   🎯 [Ph44] ${tr.n_triggered} إشارة مُفعَّلة من ${tr.n_checked}: ${sym3}`);
        } else {
          log(`   ℹ️  [Ph44] لا إشارات جديدة مُفعَّلة (فُحص ${tr.n_checked})`);
        }
      } catch { /* skip */ }
    }
  }

  // 6i. Pine Analytics — يُشغَّل في night_lab فقط (268 سهم × 2s = بطيء جداً للـ daily)
  //   يمكن تشغيله يدوياً: node scripts/fetch_pine_analytics.mjs all

  // 6j. تحديث Posture بعد كل المحللين (cognitive_orchestrator)
  //     يقرأ جميع المدخلات ويُصدر قرار الوضعية النهائي
  run(`python3 scripts/python/cognitive_orchestrator.py orchestrate_full`,
      'تحديث Posture والثقة (orchestrator)');

  // 6k2. Phase 24 — Spectral Pine Overlay (TradingView MCP)
  //      يولّد Pine Script يعرض cycle_bottom_prox + regime colors على الشارت
  run('node scripts/load_spectral_indicator.mjs --save-only',
      'توليد Spectral Cycle Pine Overlay (Ph24)');

  // 6k. Conformal Prediction Intervals — uncertainty bounds لكل إشارة
  run(`python3 scripts/python/egx_ml_trainer.py phase15`,
      'Conformal Intervals — تحديد الإشارات الواثقة');

  // 6k3. Phase 22 — Shadow Validator: ملء نتائج التنبؤات السابقة (5+ أيام)
  run(`python3 scripts/python/signal_integration.py shadow_fill_outcomes`,
      'Shadow Validator — ملء نتائج التنبؤات الطيفية السابقة (Ph22)');

  // 6k4. Phase 26 — Spectral Alpha Dashboard (تفعّل تلقائياً بعد ≥10 observations)
  run(`python3 scripts/python/signal_integration.py spectral_alpha_dashboard`,
      'Spectral Alpha Dashboard — مقارنة cyclical vs noisy فعلياً (Ph26)');

  // 6l. Cox PH Survival — hazard score: من الأقرب للانفجار؟
  run(`python3 scripts/python/egx_ml_trainer.py phase18`,
      'Survival Analysis — hazard score per stock');

  // 6m. Kelly Optimizer — حجم التداول الأمثل لكل إشارة
  run(`python3 scripts/python/egx_ml_trainer.py phase19`,
      'Kelly Portfolio Optimizer — position sizing');

  // 6n. Pine ML Indicator — توليد Pine Script تلقائياً للوحة ML
  run(`python3 scripts/python/egx_ml_trainer.py phase20`,
      'Pine ML Dashboard — auto-generate Pine Script');

  // 6n2. Phase 23 — Historical Spectral Attribution (~10ث)
  //      backtest FFT على explosive_moves — يُحدَّث يومياً لاستيعاب الأحداث الجديدة
  run(`python3 scripts/python/egx_ml_trainer.py phase23`,
      'Spectral Attribution Backtest — قياس predictive power للـ FFT (Ph23)');

  // 6w. Weekly Deep Training — التدريب العميق الأسبوعي (الأحد فقط أو --weekly-deep)
  //     يُعيد بناء كامل ML pipeline: Ph1→Ph2→Ph3→Ph4→Ph5→Ph6→Ph7 + predict + score_all
  //     المدة: ~90 دقيقة. يُشغَّل ليلة السبت/صباح الأحد قبل أول جلسة للأسبوع.
  {
    const dayOfWeek = new Date().getDay(); // 0=الأحد في JS
    const isSunday  = dayOfWeek === 0;
    if (WEEKLY_DEEP || isSunday) {
      log(`🧠 [Weekly Deep] بدء التدريب العميق الأسبوعي (Ph1→Ph7)...`);
      if (!DRY_RUN) {
        try {
          const { spawn } = await import('child_process');
          const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
          const logPath = `${ROOT}/logs/weekly_deep_${ts}.log`;
          const child = spawn(
            PYTHON3,
            ['scripts/python/night_lab.py', 'weekly_deep'],
            { cwd: ROOT, detached: true, stdio: ['ignore', 'pipe', 'pipe'] }
          );
          const { createWriteStream } = await import('fs');
          const logStream = createWriteStream(logPath, { flags: 'a' });
          child.stdout.pipe(logStream);
          child.stderr.pipe(logStream);
          child.unref();
          log(`   🚀 Weekly Deep Training started (PID ${child.pid}) → ${logPath}`);
          log(`   ⏱  المدة التقديرية ~90 دقيقة. تابع: tail -f ${logPath}`);
        } catch (e) {
          log(`   ❌ فشل تشغيل Weekly Deep: ${e.message}`);
        }
      } else {
        log(`   (dry-run) python3 scripts/python/night_lab.py weekly_deep`);
      }
    }
  }

  // 7. التقرير اليومي + ماكرو + إرسال Telegram
  // التقرير اليومي: يحفظ في DB + ماكرو بدون إرسال Telegram مباشر
  // الإرسال يتم حصراً عبر egx_telegram_daily.mjs (cron 15:00 UTC)
  const reportCmd = DRY_RUN
    ? 'node scripts/daily_report.mjs --macro'
    : 'node scripts/daily_report.mjs --save --macro';
  run(reportCmd,                                'توليد التقرير اليومي وحفظه');

  // 8. حالة النظام
  run('node scripts/egx_status.mjs',           'ملخص حالة النظام');

  log(`═══ تم الانتهاء من التشغيل اليومي ═══`);
}

main().catch(err => {
  log(`💥 خطأ فادح: ${err.message}`);
  process.exit(1);
});
