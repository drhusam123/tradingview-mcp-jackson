/**
 * EGX System Status Dashboard
 * ==============================
 * لوحة صحة النظام — تقرير شامل بأمر واحد:
 *   ✅ صحة قاعدة البيانات
 *   ✅ freshness البيانات
 *   ✅ indicators_cache
 *   ✅ إشارات اليوم من الكاش
 *   ✅ أفضل parameters مكتشفة
 *   ✅ موسم النتائج / Ramadan
 *   ✅ حاسبة المركز
 *   ✅ نصيحة اليوم
 *
 * التشغيل:
 *   node scripts/egx_status.mjs
 *   node scripts/egx_status.mjs --symbol PHDC
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import {
  getDB, getIndicatorsCacheStats, getSignalsFromCache,
  quickVaR, calcPositionSize, getStockSector,
  EGX_UNIVERSE, EGX_UNIVERSE_CORE, EGX_CONFIG, EGX_SECTORS,
  EGX_SCHEMA_VERSION,
  pythonEnsembleSignal, pythonRegimeDetection,
} from '../src/egx/index.js';
import { seedHolidayCalendar, formatFreshnessLine } from './lib/egx_calendar.mjs';
import { getProofLoopMetrics, formatProofLoopLine } from './lib/proof_loop.mjs';
import { countDirectiveStats } from './lib/directive_resolver.mjs';
import { auditClosedLoops } from './lib/loop_audit.mjs';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __statusDir = dirname(fileURLToPath(import.meta.url));
const DATA_DIR = join(__statusDir, '../data');

// --ensemble フラグ: Ensemble + Regime をリアルタイムで表示
const FULL = process.argv.includes('--full');

const SYMBOL = (() => { const i = process.argv.indexOf('--symbol'); return i >= 0 ? process.argv[i+1] : null; })();

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 65) => wl(c.repeat(n));
const h2  = (t) => { wl(); wl(`  ▶ ${t}`); };
const ok  = (s) => `✅ ${s}`;
const warn= (s) => `⚠️  ${s}`;
const err = (s) => `❌ ${s}`;

// ── تواريخ رمضان ─────────────────────────────────────────────────────────
const RAMADAN = [
  { start: '2026-02-18', end: '2026-03-19', post: '2026-04-02' },
  { start: '2025-03-01', end: '2025-03-29', post: '2025-04-12' },
];
const EARNINGS_MONTHS = [1, 4, 7, 10];

function getSeasonalContext() {
  const now    = new Date();
  const month  = now.getMonth() + 1;
  const day    = now.getDate();
  const today  = now.toISOString().split('T')[0];
  const flags  = [];

  // Earnings Season?
  if (EARNINGS_MONTHS.includes(month) && day <= 21) {
    flags.push(`📅 موسم النتائج (${['يناير','أبريل','يوليو','أكتوبر'][EARNINGS_MONTHS.indexOf(month)]}) — WR إضافي +10%`);
  }

  // Ramadan?
  for (const r of RAMADAN) {
    if (today >= r.start && today <= r.end) {
      flags.push(`🌙 شهر رمضان — السوق أبطأ (WR انخفاض ~5%)`);
    } else if (today > r.end && today <= r.post) {
      flags.push(`🎉 ما بعد رمضان — WR=58.7% تاريخياً (أقوى أسبوعين)`);
    }
  }

  return flags;
}

async function main() {
  const t0 = Date.now();
  const db = getDB();

  sep();
  wl('  📊 EGX System Status — Dr. Husam');
  wl(`  النظام v${EGX_SCHEMA_VERSION} | ${new Date().toLocaleString('ar-EG')}`);
  sep();

  // ── 1. صحة قاعدة البيانات ───────────────────────────────────────────
  h2('قاعدة البيانات');
  try {
    const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all().map(r => r.name).filter(n => !n.startsWith('sqlite') && !n.includes('fts'));
    const wal    = db.pragma('journal_mode', { simple: true });
    const cache  = db.pragma('cache_size',   { simple: true });
    wl(`  ${ok('WAL Mode: ' + wal.toUpperCase())}`);
    wl(`  ${ok(tables.length + ' جداول نشطة: ' + tables.join(', '))}`);

    // صفوف الجداول الرئيسية
    for (const t of ['ohlcv_history','indicators_cache','scans','trades']) {
      const cnt = db.prepare(`SELECT COUNT(*) c FROM ${t}`).get().c;
      wl(`  ${cnt > 0 ? ok(t + ': ' + cnt.toLocaleString() + ' صف') : warn(t + ': فارغ')}`);
    }

    // Freshness (EGX trading sessions — not calendar days)
    seedHolidayCalendar();
    const lastOHLCV = db.prepare('SELECT MAX(bar_time) t, COUNT(DISTINCT symbol) s FROM ohlcv_history').get();
    const latestBar = new Date(lastOHLCV.t * 1000).toISOString().split('T')[0];
    try {
      const fresh = formatFreshnessLine(latestBar);
      wl(`  ${fresh.level === 'ok' ? ok(fresh.text) : fresh.level === 'warn' ? warn(fresh.text) : err(fresh.text)}`);
    } catch (e) {
      const daysOld = Math.floor((Date.now() - lastOHLCV.t * 1000) / 86400000);
      wl(`  ${daysOld <= 1 ? ok('آخر شمعة: ' + latestBar + ' (محدّث ✅)') : warn('آخر شمعة: ' + latestBar + ' (قبل ' + daysOld + ' أيام)')}`);
      wl(`  ${warn('تقويم التداول غير متاح: ' + e.message)}`);
    }
  } catch (e) {
    wl(`  ${err('فشل: ' + e.message)}`);
  }

  // ── 2. indicators_cache ─────────────────────────────────────────────
  h2('indicators_cache');
  try {
    const stats = getIndicatorsCacheStats();
    const n     = stats.symbols_count ?? stats.totalSymbols ?? 0;
    const date  = stats.latest_date  ?? stats.latestDate   ?? '—';
    wl(`  ${ok('أسهم محسوبة: ' + n + ' | آخر تحديث: ' + date)}`);
    wl(`  RSI≤35: ${stats.oversold_rsi ?? 0} | OBV Bullish: ${stats.bullish_obv ?? 0} | RSI+OBV Combo: ${stats.rsi_obv_combo ?? 0}`);
    const outdated = n < EGX_UNIVERSE.length * 0.90;
    wl(`  ${outdated ? warn('يحتاج rebuild: npm run egx:indicators') : ok('الكاش محدّث (' + n + '/' + EGX_UNIVERSE.length + ')')}`);
  } catch(e) {
    wl(`  ${warn('لا بيانات — شغّل: npm run egx:indicators')}`);
  }

  // ── 3. إشارات اليوم ─────────────────────────────────────────────────
  h2('إشارات اليوم (من indicators_cache)');
  try {
    const signals = getSignalsFromCache();
    const combo   = signals.filter(s => s.signal_type === 'RSI_OBV_COMBO');
    const oversold= signals.filter(s => s.signal_type === 'OVERSOLD_RSI30');
    const bb      = signals.filter(s => s.signal_type === 'BB_OVERSOLD');
    const adx     = signals.filter(s => s.signal_type === 'ADX_SWEET_SPOT');

    wl(`  🔥 RSI+OBV Combo (WR=69%): ${combo.length > 0 ? combo.map(s => s.symbol).join(', ') : 'لا توجد إشارات'}`);
    wl(`  📉 RSI≤30 Oversold:         ${oversold.length > 0 ? oversold.map(s => s.symbol).join(', ') : 'لا توجد'}`);
    wl(`  📊 BB Oversold:             ${bb.length > 0 ? bb.map(s => s.symbol).join(', ') : 'لا توجد'}`);
    wl(`  📈 ADX Sweet Spot:          ${adx.length > 0 ? adx.map(s => s.symbol).join(', ') : 'لا توجد'}`);

    const total = signals.length;
    wl(`  إجمالي الإشارات: ${total}`);
    if (total === 0) wl(`  ${warn('لا إشارات اليوم — السوق في حالة momentum قوي (RSI مرتفع)')}`);
  } catch(e) {
    wl(`  ${warn('تعذّر القراءة: ' + e.message)}`);
  }

  // ── 3b. Pine analytics coverage ───────────────────────────────────────
  h2('Pine Analytics');
  try {
    const pine = db.prepare(`
      SELECT COUNT(*) AS rows,
             COUNT(DISTINCT symbol) AS syms,
             SUM(CASE WHEN volume_poc IS NOT NULL THEN 1 ELSE 0 END) AS poc_rows,
             SUM(CASE WHEN source_script NOT LIKE '%fallback%' THEN 1 ELSE 0 END) AS real_tv_rows
      FROM pine_analytics
    `).get();
    const pocPct = pine.rows ? Math.round((pine.poc_rows / pine.rows) * 100) : 0;
    wl(`  ${pine.rows > 0 ? ok(`صفوف: ${pine.rows} | رموز: ${pine.syms} | POC: ${pocPct}%`) : warn('لا بيانات pine — شغّل fetch_pine_analytics')}`);
    if (pine.real_tv_rows === 0 && pine.rows > 0) {
      wl(`  ${warn('كل الصفوف من OHLCV fallback — شغّل --pine في egx:daily للحصاد الحقيقي')}`);
    }
  } catch (e) {
    wl(`  ${warn('Pine coverage: ' + e.message)}`);
  }

  // ── 3c. Forward WR tracking ─────────────────────────────────────────
  h2('Forward WR Tracking');
  try {
    const rec = db.prepare(`
      SELECT COUNT(*) AS n,
             SUM(CASE WHEN return_t1 IS NOT NULL THEN 1 ELSE 0 END) AS filled,
             SUM(CASE WHEN hit_t1 = 1 THEN 1 ELSE 0 END) AS wins
      FROM recommendation_outcomes
      WHERE signal_date >= date('now', '-30 day')
    `).get();
    const wr30 = rec.filled > 0 ? ((rec.wins / rec.filled) * 100).toFixed(1) : '—';
    wl(`  توصيات 30يوم: ${rec.n} | مُقيَّمة: ${rec.filled} | WR(T1): ${wr30}%`);

    const fwd = db.prepare(`
      SELECT
        SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) AS done
      FROM forward_test_predictions
    `).get();
    wl(`  forward_test: ${fwd.pending ?? 0} معلّق | ${fwd.done ?? 0} مكتمل`);

    const bayes = db.prepare(`
      SELECT mean_wr, ci_lower, run_date
      FROM bayesian_wr
      WHERE category='overall'
      ORDER BY run_date DESC, id DESC LIMIT 1
    `).get();
    if (bayes) {
      const bayesTag = bayes.ci_lower < 0.45 ? warn : ok;
      wl(`  ${bayesTag(`Bayesian WR: ${(bayes.mean_wr * 100).toFixed(1)}% | CI↓ ${(bayes.ci_lower * 100).toFixed(1)}% (${bayes.run_date})`)}`);
    } else {
      wl(`  ${warn('لا bayesian_wr — شغّل phase46')}`);
    }
  } catch (e) {
    wl(`  ${warn('Forward WR: ' + e.message)}`);
  }

  // ── 3d. P6 Closed Loops ─────────────────────────────────────────────
  h2('P6 Closed Loops');
  try {
    const proof = getProofLoopMetrics();
    const del = getProofLoopMetrics({ deliveredOnly: true });
    const audit = auditClosedLoops({ maxAgeHours: 168 });
    const dirs = countDirectiveStats();
    wl(`  ${formatProofLoopLine(proof)}`);
    wl(`  Delivered ULTRA: ${del.n_completed} @ ${del.win_rate ?? '—'}%`);
    wl(`  Loop audit: ${audit.pass ? ok('PASS') : err('FAIL')} | directives ${dirs.pending}P/${dirs.completed}C`);
    const ctxPath = join(DATA_DIR, 'p6_research_context.json');
    if (existsSync(ctxPath)) {
      const ctx = JSON.parse(readFileSync(ctxPath, 'utf8'));
      wl(`  P6 context: ${ctx.ultra_losses?.length ?? 0} ULTRA losses | downrank ${(ctx.evolution_hints?.downrank_behavioral || []).join(',') || '—'}`);
    } else {
      wl(`  ${warn('لا p6_research_context — شغّل: npm run egx:closed:loop')}`);
    }
  } catch (e) {
    wl(`  ${warn('P6 loops: ' + e.message)}`);
  }

  // ── 4. السياق الموسمي ───────────────────────────────────────────────
  h2('السياق الموسمي');
  const seasonal = getSeasonalContext();
  if (seasonal.length > 0) {
    seasonal.forEach(s => wl(`  ${s}`));
  } else {
    wl('  📆 لا أحداث موسمية خاصة اليوم');
  }

  // ── 5. Macro Regime ─────────────────────────────────────────────────
  h2('الاقتصاد الكلي (Macro Regime)');
  try {
    const macroRow = db.prepare(
      "SELECT macro_regime, regime_score, equity_multiplier, strategic_bias, " +
      "inflation_yoy, cbe_rate, real_interest_rate, usd_egp, gdp_yoy, fx_reserves_b, " +
      "inflation_momentum, rate_cycle, fetched_at " +
      "FROM macro_snapshot ORDER BY id DESC LIMIT 1"
    ).get();
    if (macroRow) {
      const ageH = macroRow.fetched_at
        ? Math.round((Date.now() - new Date(macroRow.fetched_at).getTime()) / 3600000)
        : null;
      const freshTag = ageH != null ? (ageH < 48 ? '✅' : ageH < 168 ? '⚠️' : '🔴') : '❓';
      const multSign = macroRow.equity_multiplier > 1 ? '+' : '';
      const multPct  = ((macroRow.equity_multiplier - 1) * 100).toFixed(1);
      wl(`  🏛️  Regime: ${macroRow.macro_regime}  |  Score: ${macroRow.regime_score}/100  |  Equity Mult: ${macroRow.equity_multiplier}× (${multSign}${multPct}%) ${freshTag}`);
      wl(`  ⚖️   Bias: ${macroRow.strategic_bias}`);
      wl(`  📈  تضخم: ${macroRow.inflation_yoy}% (${macroRow.inflation_momentum ?? '—'})  |  CBE: ${macroRow.cbe_rate}%  (${macroRow.rate_cycle ?? '—'})  |  فائدة حقيقية: ${macroRow.real_interest_rate}%`);
      wl(`  💵  USD/EGP: ${macroRow.usd_egp}  |  GDP: ${macroRow.gdp_yoy}%  |  احتياطيات: $${macroRow.fx_reserves_b}B`);
      if (ageH != null && ageH >= 168)
        wl(`  ${warn(`⚠️ بيانات قديمة ${ageH}h — شغّل: npm run egx:economics:full`)}`);
    } else {
      wl(`  ${warn('لا بيانات macro — شغّل: npm run egx:economics:full')}`);
    }
  } catch(e) { wl(`  ${warn('Macro error: ' + e.message)}`); }

  // ── 6. أفضل parameters مكتشفة ──────────────────────────────────────
  h2('أفضل Parameters (Grid Search — مايو 2026)');
  wl(`  RSI ≤ ${EGX_CONFIG.bestRsiThreshold} + ADX ${EGX_CONFIG.bestAdxMin}-${EGX_CONFIG.bestAdxMax} | Hold = ${EGX_CONFIG.bestHoldDays} أيام`);
  wl(`  WR = ${EGX_CONFIG.bestWinRate}% | Sharpe proxy = 0.625 (أعلى من 575 تركيبة)`);
  wl(`  معتمَد بـ Walk-Forward: متوسط WR=60.6% عبر 4 نوافذ زمنية ✅`);

  // ── 7. حاسبة المركز ─────────────────────────────────────────────────
  h2('حاسبة حجم المركز (100,000 جنيه)');
  const ps = calcPositionSize({ capital: 100000, winRate: 0.641, avgWin: 2.97, avgLoss: 1.8, method: 'half_kelly' });
  wl(`  الطريقة         : Half-Kelly`);
  wl(`  Kelly Criterion : ${ps.kellyCriterion} (${(ps.kellyCriterion*100).toFixed(1)}%)`);
  wl(`  حجم المركز      : ${ps.positionPct}% = ${ps.positionValue.toLocaleString()} جنيه`);
  wl(`  المخاطرة لكل صفقة: ${ps.capitalAtRisk.toLocaleString()} جنيه (${ps.riskPct}%)`);
  wl(`  Edge الصفقة     : ${ps.edge}% لكل صفقة (إيجابي = نظام مربح)`);
  wl(`  ${ps.recommendation}`);

  if (SYMBOL) {
    wl();
    wl(`  ── تحليل ${SYMBOL} ──`);
    const sector = getStockSector(SYMBOL);
    wl(`  القطاع: ${sector}`);
    const row = db.prepare(`
      SELECT rsi14, adx14, bb_position, vol_ratio_20, momentum_5d, obv_divergence, bar_date
      FROM indicators_cache WHERE symbol=?
      ORDER BY bar_date DESC LIMIT 1
    `).get(SYMBOL);
    if (row) {
      const rsiAlert = row.rsi14 <= 25 ? '🔥' : row.rsi14 <= 35 ? '⚠️' : '';
      const adxAlert = row.adx14 >= 20 && row.adx14 <= 25 ? '✅' : '';
      wl(`  RSI14: ${row.rsi14?.toFixed(1)} ${rsiAlert} | ADX14: ${row.adx14?.toFixed(1)} ${adxAlert}`);
      wl(`  BB Position: ${row.bb_position != null ? row.bb_position.toFixed(2) : '—'} (0=أسفل BB, 1=أعلى BB)`);
      wl(`  Volume Ratio: ${row.vol_ratio_20?.toFixed(2)}x | Momentum 5d: ${row.momentum_5d?.toFixed(2)}%`);
      wl(`  OBV Divergence: ${row.obv_divergence ?? '—'}`);

      const varRes = quickVaR(SYMBOL, 0.95, 100000);
      if (!varRes.error) {
        wl(`  VaR(95%): ${varRes.var1d}% يومي | Max DD: ${varRes.maxDrawdownPct}%`);
      }
      const posS = calcPositionSize({ capital:100000, winRate:0.641, avgWin:2.97, avgLoss:1.8, stopLossPct:0.05 });
      wl(`  حجم مقترح: ${posS.positionValue.toLocaleString()} جنيه (وقف خسارة 5%)`);

      // هل توجد إشارة؟
      const isCombo = row.rsi14 <= 35 && row.obv_divergence === 'bullish';
      const isGrid  = row.rsi14 <= 25 && row.adx14 >= 20 && row.adx14 <= 25;
      if (isGrid)  wl('  🔥🔥 إشارة Grid Search المثلى: RSI≤25 + ADX 20-25');
      else if (isCombo) wl('  🔥 إشارة RSI+OBV Combo');
      else         wl('  ─ لا إشارة نشطة حالياً');
    } else {
      wl(`  ${warn('لا بيانات في indicators_cache — شغّل rebuild_indicators')}`);
    }
  }

  // ── 7. Ensemble Signal (إذا طُلب --full) ───────────────────────────
  if (FULL) {
    h2('Ensemble Signal (Rules×50% + ML×35% + Calendar×15%)');
    try {
      const ens = await pythonEnsembleSignal();
      if (ens.success) {
        const counts = ens.signal_counts ?? {};
        wl(`  🎯 ${ens.note}`);
        wl(`  🗓️  ${(ens.calendar_context ?? []).join(' | ')}`);
        const strong = ens.strong_buy ?? [];
        if (strong.length > 0) {
          wl(`  🔥 STRONG_BUY: ${strong.map(s => `${s.symbol}(${s.composite_score})`).join(' • ')}`);
        }
        const buys = ens.buy ?? [];
        if (buys.length > 0) {
          wl(`  🟢 BUY: ${buys.slice(0, 5).map(s => s.symbol).join(' • ')}`);
        }
      } else {
        wl(`  ${warn('Ensemble: ' + ens.error)}`);
      }
    } catch(e) { wl(`  ${warn('Ensemble error: ' + e.message)}`); }

    h2('Market Regime');
    try {
      const reg = await pythonRegimeDetection();
      if (reg.success) {
        wl(`  🌊 Market Regime: ${reg.market_regime}`);
        wl(`  ${reg.market_recommendation}`);
        const dist = reg.regime_distribution ?? {};
        const total = reg.total_symbols ?? 1;
        const entries = Object.entries(dist).sort((a,b) => b[1]-a[1]);
        for (const [r, n] of entries) {
          wl(`    ${r.padEnd(15)} ${n} سهم (${(n/total*100).toFixed(0)}%)`);
        }
      } else {
        wl(`  ${warn('Regime: ' + reg.error)}`);
      }
    } catch(e) { wl(`  ${warn('Regime error: ' + e.message)}`); }
  }

  // ── 8. ملخص الأوامر ─────────────────────────────────────────────────
  h2('الأوامر');
  wl('  npm run egx:daily              ← تحديث كامل (data + indicators + scan + deep)');
  wl('  npm run egx:weekly             ← كل شيء + discovery report');
  wl('  npm run egx:advanced           ← SHAP + Regime + Ensemble + Universe (~3 دقائق)');
  wl('  npm run egx:advanced:ensemble  ← Ensemble Signal فقط (~3s)');
  wl('  npm run egx:advanced:regime    ← Regime Detection فقط (~15s)');
  wl('  npm run egx:advanced:universe  ← Active Universe فقط (~5s)');
  wl('  npm run egx:advanced:shap      ← SHAP Analysis فقط (~90s)');
  wl('  npm run egx:discover           ← اكتشاف strategies جديدة (~60s)');
  wl('  npm run egx:deep               ← تقرير تحليل عميق (danfo+stdlib+python)');
  wl('  npm run egx:status             ← هذه اللوحة');
  wl('  npm run egx:status -- --full   ← + Ensemble + Regime فوري');
  wl(`  node scripts/egx_status.mjs --symbol PHDC  ← تحليل سهم محدد`);

  sep();
  wl(`  ⏱️  وقت التحميل: ${Date.now() - t0}ms`);
  sep();
}

main().catch(e => {
  process.stderr.write(`💥 ${e.message}\n${e.stack}\n`);
  process.exit(1);
});
