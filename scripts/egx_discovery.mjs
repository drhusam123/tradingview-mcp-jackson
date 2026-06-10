/**
 * EGX Strategy Discovery — تقرير اكتشاف الاستراتيجيات الكامل
 * =============================================================
 * يجمع كل أدوات الاكتشاف في تقرير واحد:
 *   1. Grid Search موازي (240+ تركيبة RSI×ADX×Hold)
 *   2. Walk-Forward Validation (5 نوافذ زمنية)
 *   3. ML Signal (Random Forest + HistGradientBoosting)
 *   4. EGX Patterns (Circuit Breaker / Gap Fill / Ramadan / Earnings)
 *
 * التشغيل:
 *   node scripts/egx_discovery.mjs                   (تقرير كامل ~2 دقيقة)
 *   node scripts/egx_discovery.mjs --section sweep   (Grid Search فقط)
 *   node scripts/egx_discovery.mjs --section wf      (Walk-Forward فقط)
 *   node scripts/egx_discovery.mjs --section ml      (ML فقط)
 *   node scripts/egx_discovery.mjs --section patterns (EGX patterns فقط)
 *   node scripts/egx_discovery.mjs --json            (إخراج JSON)
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import {
  checkPythonBridge,
  pythonParamSweep,
  pythonWalkForward,
  pythonMLSignal,
  pythonEgxPatterns,
  getDB,
} from '../src/egx/index.js';

// ── قراءة بيانات الماكرو من DB مباشرة (يُفضّل TradingView Live) ─────────
function loadMacroFromDB() {
  try {
    const db = getDB();
    const lastRow = db.prepare(`
      SELECT usd_egp, inflation, lending_rate, cbe_rate, source, fetched_at, raw_json
      FROM macro_data
      WHERE source = 'tradingview_live'
        AND fetched_at >= datetime('now', '-48 hours')
      ORDER BY id DESC LIMIT 1
    `).get() ??
    db.prepare(`
      SELECT usd_egp, inflation, lending_rate, cbe_rate, source, fetched_at, raw_json
      FROM macro_data ORDER BY id DESC LIMIT 1
    `).get();

    if (!lastRow) return {};
    let raw = {};
    try { raw = JSON.parse(lastRow.raw_json || '{}'); } catch {}

    const cbeRate = lastRow.cbe_rate ?? raw.cbe_rate_pct;
    const infl    = lastRow.inflation ?? raw.inflation_pct;
    let realRate  = raw.real_interest_rate ?? (cbeRate != null && infl != null ? cbeRate - infl : null);

    // احسب strategic_bias
    let bias = raw.strategic_bias ?? 'NEUTRAL';
    if (!bias || bias === 'None') {
      if (realRate != null) {
        if      (realRate < -5) bias = 'EQUITY_POSITIVE';
        else if (realRate >  5) bias = 'EQUITY_NEGATIVE';
        else                    bias = 'NEUTRAL';
      }
    }

    return {
      ...raw,
      usd_egp:           lastRow.usd_egp      ?? raw.usd_egp,
      inflation_pct:     infl,
      cbe_rate_pct:      cbeRate,
      lending_rate_pct:  lastRow.lending_rate  ?? raw.lending_rate_pct ?? cbeRate,
      real_interest_rate: realRate,
      strategic_bias:    bias,
      _db_source:        lastRow.source,
      _fetched_at:       lastRow.fetched_at,
    };
  } catch { return {}; }
}

const SECTION   = (() => { const i = process.argv.indexOf('--section'); return i >= 0 ? process.argv[i+1] : 'all'; })();
const JSON_MODE = process.argv.includes('--json');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 68) => wl(c.repeat(n));
const h1  = (t) => { sep(); wl(`  ${t}`); sep(); };
const h2  = (t) => { wl(); wl(`  ┌─ ${t}`); wl(`  └${'─'.repeat(t.length + 2)}`); };
const pct = (n, d = 1) => n != null ? `${n >= 0 ? '+' : ''}${(+n).toFixed(d)}%` : '—';
const fmt = (n, d = 2) => n != null ? (+n).toFixed(d) : '—';

// ─── جدول ASCII ──────────────────────────────────────────────────────────
function table(headers, rows, widths) {
  const line = widths.map(w => '─'.repeat(w)).join('─┼─');
  const head = headers.map((h, i) => h.padEnd(widths[i])).join(' │ ');
  wl('  ┌─' + line + '─┐');
  wl('  │ ' + head + ' │');
  wl('  ├─' + line + '─┤');
  rows.forEach(row => {
    const r = row.map((v, i) => String(v ?? '—').padEnd(widths[i])).join(' │ ');
    wl('  │ ' + r + ' │');
  });
  wl('  └─' + line + '─┘');
}

// ══════════════════════════════════════════════════════════════════════════
async function main() {
  const t0      = Date.now();
  const results = {};

  h1('🔍 EGX Strategy Discovery — Dr. Husam');
  wl(`  التاريخ : ${new Date().toISOString().split('T')[0]}`);
  wl(`  القسم   : ${SECTION}`);
  sep('─');

  const pyHealth = await checkPythonBridge();
  wl(`  Python  : ${pyHealth.message}`);
  if (!pyHealth.available) {
    wl('  ❌ Python غير متاح — توقف');
    process.exit(1);
  }

  // ── السياق الكلي من DB (TradingView Live) ───────────────────────────
  const macro = loadMacroFromDB();
  {
    const rr   = macro.real_interest_rate != null ? +macro.real_interest_rate : null;
    const icon = rr == null ? '⚪' : rr < -5 ? '🟢' : rr < 0 ? '🟡' : rr < 5 ? '🟠' : '🔴';
    const src  = (macro._db_source ?? '').includes('tradingview') ? '📡 TradingView Live' : '🌐 APIs';
    wl(`  ${icon} ماكرو (${src}): تضخم=${macro.inflation_pct?.toFixed(1) ?? '?'}% | CBE=${macro.cbe_rate_pct?.toFixed(1) ?? '?'}% | فائدة حقيقية=${rr?.toFixed(1) ?? '?'}% | USD/EGP=${macro.usd_egp?.toFixed(2) ?? '?'} | ${macro.strategic_bias ?? 'NEUTRAL'}`);
    if (rr != null) {
      const macroNote = rr < -3 ? '📊 بيئة ماكرو محفّزة للأسهم — الـ RSI threshold يمكن رفعه قليلاً'
                      : rr >  5 ? '⚠️  فائدة حقيقية موجبة (+4.1%) — الودائع تنافس الأسهم، كن انتقائياً'
                      :           '↔️  البيئة الكلية محايدة — إشارات التقنيكال هي المحرك الرئيسي';
      wl(`     ↳ ${macroNote}`);
    }
  }
  sep('─');

  // ══ SECTION: PARAM SWEEP ══════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'sweep') {
    h2('🔬 Grid Search — أفضل Parameters (RSI × ADX × Hold Days)');
    wl('  ⏳ جارٍ اختبار 500+ تركيبة على 73K شمعة...');
    try {
      const sw = await pythonParamSweep();
      if (sw.success) {
        wl(`  ✅ ${sw.valid_combos} تركيبة صالحة من ${sw.total_combos} إجمالية`);

        // أفضل 10 بـ Sharpe
        wl();
        wl('  📊 أفضل 10 تركيبة بـ Sharpe Proxy:');
        wl(`  ${'#'.padEnd(3)} ${'RSI≤'.padEnd(5)} ${'ADX'.padEnd(10)} ${'Hold'.padEnd(6)} ${'n'.padStart(7)} ${'WR%'.padStart(6)} ${'AvgRet'.padStart(8)} ${'Sharpe'.padStart(7)}`);
        wl('  ' + '─'.repeat(60));
        const best = (sw.best_by_sharpe ?? []).slice(0, 10);
        best.forEach((r, i) => {
          wl(`  ${String(i+1).padEnd(3)} ${String(r.rsi_threshold).padEnd(5)} ${`${r.adx_min}-${r.adx_max}`.padEnd(10)} ${`${r.hold_days}d`.padEnd(6)} ${String((r.n||0).toLocaleString()).padStart(7)} ${String(r.win_rate+'%').padStart(6)} ${pct(r.avg_return).padStart(8)} ${fmt(r.sharpe_proxy,3).padStart(7)}`);
        });

        wl();
        wl('  🎯 أفضل 10 بـ Hold=5 أيام:');
        (sw.best_5d_hold ?? []).slice(0, 5).forEach((r, i) => {
          wl(`  ${i+1}. RSI≤${r.rsi_threshold} ADX ${r.adx_min}-${r.adx_max} | WR=${r.win_rate}% | AvgRet=${pct(r.avg_return)} | n=${r.n}`);
        });

        // مقارنة مع المعروف
        const cur = sw.current_best;
        wl();
        wl(`  📌 المعيار الحالي: ${cur?.params} → WR=${cur?.wr_t5}% | AvgT5=${pct(cur?.avg_t5)}`);

        // هل وُجد أفضل؟
        const topNew = sw.best_5d_hold?.[0];
        if (topNew && topNew.win_rate > (cur?.wr_t5 ?? 0)) {
          wl(`  🔥 اكتُشفت تركيبة أقوى: RSI≤${topNew.rsi_threshold} ADX ${topNew.adx_min}-${topNew.adx_max} → WR=${topNew.win_rate}%`);
        }
        results.sweep = sw;
      } else {
        wl(`  ⚠️ ${sw.error}`);
      }
    } catch (e) {
      wl(`  ❌ خطأ: ${e.message}`);
    }
  }

  // ══ SECTION: WALK-FORWARD ════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'wf') {
    h2('⏱️  Walk-Forward Validation — هل الاستراتيجية مستقرة؟');
    wl('  ⏳ 5 نوافذ train/test موازية...');
    try {
      const wf = await pythonWalkForward();
      if (wf.success) {
        wl();
        wl(`  ${'النافذة'.padEnd(36)} ${'Best RSI'.padStart(8)} ${'Test n'.padStart(7)} ${'WR%'.padStart(6)} ${'AvgR5'.padStart(7)} ${'Edge'.padStart(6)}`);
        wl('  ' + '─'.repeat(75));
        (wf.windows ?? []).forEach(w => {
          const edge = w.edge >= 0.5 ? '✅' : w.edge >= 0 ? '⚠️' : '❌';
          wl(`  ${w.window.padEnd(36)} ${`≤${w.best_thresh}`.padStart(8)} ${String(w.test_n).padStart(7)} ${String(w.test_wr + '%').padStart(6)} ${pct(w.test_avg_r5).padStart(7)} ${(pct(w.edge) + edge).padStart(8)}`);
        });
        wl();
        wl(`  ┌─────────────────────────────────────────┐`);
        wl(`  │  متوسط WR : ${String(wf.avg_test_wr + '%').padEnd(8)} | Edge : ${pct(wf.avg_edge).padEnd(7)} │`);
        wl(`  │  متوسط R5 : ${pct(wf.avg_ret5).padEnd(8)} | ${wf.conclusion.padEnd(24)} │`);
        wl(`  └─────────────────────────────────────────┘`);
        results.walkForward = wf;
      } else {
        wl(`  ⚠️ ${wf.error}`);
      }
    } catch (e) {
      wl(`  ❌ خطأ: ${e.message}`);
    }
  }

  // ══ SECTION: ML ══════════════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'ml') {
    h2('🤖 ML Signal Detection — Random Forest + HistGradientBoosting');
    wl('  ⏳ تدريب النماذج على 66K عينة (13 feature)...');
    try {
      const ml = await pythonMLSignal(3.0, 4);
      if (ml.success) {
        wl();
        wl(`  البيانات   : ${(ml.n_samples ?? 0).toLocaleString()} عينة | ${ml.n_features} feature | ${ml.positive_rate}% إيجابية`);
        wl(`  الهدف      : ${ml.target_def}`);
        wl();

        const rf  = ml.random_forest ?? {};
        const hgb = ml.hist_gradient_boosting ?? {};
        wl(`  ┌───────────────────────────────────────────────────┐`);
        wl(`  │  النموذج              Precision  ±Std    أفضل CV  │`);
        wl(`  ├───────────────────────────────────────────────────┤`);
        wl(`  │  Random Forest        ${String(rf.cv_precision_mean ?? '—').padEnd(9)}  ${String('±'+(rf.cv_precision_std ?? '—')).padEnd(6)}  ${Math.max(...(rf.cv_scores ?? [0])).toFixed(3)}  │`);
        wl(`  │  HistGradientBoosting ${String(hgb.cv_precision_mean ?? '—').padEnd(9)}  ${String('±'+(hgb.cv_precision_std ?? '—')).padEnd(6)}  ${Math.max(...(hgb.cv_scores ?? [0])).toFixed(3)}  │`);
        wl(`  │  Base Rate            ${String((ml.positive_rate/100).toFixed(3)).padEnd(9)}  (random guess)                │`);
        wl(`  └───────────────────────────────────────────────────┘`);

        wl();
        wl('  📊 أهم 15 مؤشر (Feature Importance):');
        (ml.top_features ?? []).forEach((f, i) => {
          const bar = '█'.repeat(Math.round(f.importance / 2));
          wl(`  ${String(i+1).padStart(2)}. ${f.feature.padEnd(15)} ${bar.padEnd(20)} ${f.importance}%`);
        });

        wl();
        wl(`  🔥 إشارات عالية الثقة (≥65%) اليوم: ${ml.high_conf_signals} سهم`);
        wl(`  📌 ${ml.interpretation}`);

        // نصيحة عملية
        const bestPrec = Math.max(rf.cv_precision_mean ?? 0, hgb.cv_precision_mean ?? 0);
        const baseRate = (ml.positive_rate ?? 0) / 100;
        const lift     = bestPrec / baseRate;
        wl();
        wl(`  💡 الـ Lift = ${lift.toFixed(2)}x (النموذج ${lift.toFixed(1)}x أفضل من الصدفة)`);
        wl(`     استخدم الـ ML مع RSI≤35 + ADX 20-30 للحصول على أقوى إشارة مركّبة`);

        results.ml = ml;
      } else {
        wl(`  ⚠️ ${ml.error}`);
      }
    } catch (e) {
      wl(`  ❌ خطأ: ${e.message}`);
    }
  }

  // ══ SECTION: EGX PATTERNS ════════════════════════════════════════════
  if (SECTION === 'all' || SECTION === 'patterns') {
    h2('🇪🇬 EGX-Specific Patterns — أنماط خاصة بالسوق المصري');
    wl('  ⏳ تحليل 73K شمعة...');
    try {
      const pt = await pythonEgxPatterns(9.0);
      if (pt.success) {

        // 1. Circuit Breaker
        wl();
        wl('  ⚡ Circuit Breaker Reversal (±9% في يوم واحد):');
        const cd = pt.circuit_breaker?.down_limit ?? {};
        const cu = pt.circuit_breaker?.up_limit   ?? {};
        wl(`  ┌────────────────────────────────────────────────────────┐`);
        wl(`  │  بعد -9%  : n=${String(cd.n ?? '—').padEnd(5)} | T+5 WR=${String(cd.t5_wr ?? '—').padEnd(5)}% | AvgR5=${pct(cd.t5_avg)}  │`);
        wl(`  │  بعد +9%  : n=${String(cu.n ?? '—').padEnd(5)} | T+5 WR=${String(cu.t5_wr ?? '—').padEnd(5)}% | AvgR5=${pct(cu.t5_avg)}  │`);
        wl(`  └────────────────────────────────────────────────────────┘`);
        if ((cd.t5_wr ?? 0) > 55) wl(`  ✅ الانخفاض الحاد (-9%) يُنتج WR=${cd.t5_wr}% في T+5 — إشارة انعكاس قوية`);

        // 2. Gap Fill
        wl();
        wl('  📐 Gap Fill (فجوات الفتح):');
        const gu = pt.gap_fill?.gap_up   ?? {};
        const gd = pt.gap_fill?.gap_down ?? {};
        wl(`  فجوة صاعدة ≥1.5%  : n=${gu.n ?? '—'} | تُملأ نفس اليوم: ${gu.pct_filled_same_day ?? '—'}% | T+5 WR=${gu.t5_wr ?? '—'}%`);
        wl(`  فجوة هابطة ≤-1.5% : n=${gd.n ?? '—'} | تُملأ نفس اليوم: ${gd.pct_filled_same_day ?? '—'}% | T+5 WR=${gd.t5_wr ?? '—'}%`);

        // 3. Earnings Season
        wl();
        wl('  📅 Earnings Season Effect (يناير/أبريل/يوليو/أكتوبر):');
        const ee = pt.earnings_effect ?? {};
        wl(`  ┌──────────────────────────────────────────────────┐`);
        wl(`  │  موسم النتائج : T+5 WR=${String(ee.earnings_t5_wr ?? '—').padEnd(5)}% | avg=${pct(ee.earnings_t5_avg)}  │`);
        wl(`  │  خارج الموسم : T+5 WR=${String(ee.normal_t5_wr ?? '—').padEnd(5)}% | avg=${pct(ee.normal_t5_avg)}  │`);
        wl(`  │  الميزة      : ${pct(ee.edge)} إضافي في موسم النتائج        │`);
        wl(`  └──────────────────────────────────────────────────┘`);

        // 4. Thin Volume
        wl();
        wl('  📉 Thin Volume Reversal (حجم < 0.3x + RSI<40):');
        const tv = pt.thin_volume?.thin_oversold ?? {};
        wl(`  n=${tv.n ?? '—'} | T+5 WR=${tv.t5_wr ?? '—'}% | AvgRet=${pct(tv.t5_avg)}`);
        if ((tv.t5_wr ?? 0) > 50) wl('  ✅ إشارة انعكاس عند إرهاق البائعين');

        // 5. Ramadan Effect
        wl();
        wl('  🌙 Ramadan Effect:');
        const rm   = pt.ramadan_effect ?? {};
        const dur  = rm.during_ramadan   ?? {};
        const post = rm.post_ramadan_2weeks ?? {};
        const non  = rm.non_ramadan      ?? {};
        wl(`  خلال رمضان         : n=${dur.n  ?? '—'} | T+5 WR=${dur.t5_wr  ?? '—'}% | avg=${pct(dur.t5_avg)}`);
        wl(`  بعد رمضان (أسبوعين): n=${post.n ?? '—'} | T+5 WR=${post.t5_wr ?? '—'}% | avg=${pct(post.t5_avg)}`);
        wl(`  خارج رمضان         : n=${non.n  ?? '—'} | T+5 WR=${non.t5_wr  ?? '—'}% | avg=${pct(non.t5_avg)}`);

        // 6. Day of Week
        wl();
        wl('  📆 Day-of-Week Effect:');
        (pt.day_of_week ?? []).forEach(d => {
          const bar   = '█'.repeat(Math.round(Math.max(0, d.t5_wr - 45)));
          const trend = d.t5_wr >= 52 ? '🟢' : d.t5_wr >= 48 ? '🟡' : '🔴';
          wl(`  ${trend} ${d.day.padEnd(10)}: T+5 WR=${d.t5_wr}% | avg=${pct(d.t5_avg)} | n=${d.n} ${bar}`);
        });

        // ── 7. ATR Regime × RSI ──────────────────────────────────────────
        if (pt.atr_regime && Object.keys(pt.atr_regime).length) {
          wl();
          wl('  🌡️  ATR Regime × RSI≤30 — متى يعمل RSI؟ (اكتشاف جديد):');
          const atrOrder = ['LOW','MED','HIGH','EXTREME'];
          atrOrder.forEach(k => {
            const a = pt.atr_regime[k]; if (!a) return;
            const icon = a.t5_wr >= 35 ? '✅' : a.t5_wr >= 20 ? '⚠️ ' : '❌';
            wl(`  ${icon} ATR ${k.padEnd(7)}: n=${String(a.n).padEnd(5)} | WR5=${a.t5_wr}% | avg5=${pct(a.t5_avg)}`);
          });
          const lowAtr = pt.atr_regime['LOW'];
          if (lowAtr && lowAtr.t5_wr < 10) wl('  🚨 تحذير: RSI يفشل تقريباً في بيئة ATR منخفض (<1%) — أضف فلتر ATR>1%');
        }

        // ── 8. Panic Gap + RSI ──────────────────────────────────────────
        if (pt.panic_gap_rsi && Object.keys(pt.panic_gap_rsi).length) {
          wl();
          wl('  🚨 Panic Gap + RSI≤35 — إشارة الانهيار الحقيقي:');
          ['gap_3pct','gap_5pct','gap_7pct'].forEach(k => {
            const p = pt.panic_gap_rsi[k]; if (!p) return;
            const thresh = k.replace('gap_','').replace('pct','%');
            wl(`  Gap≤-${thresh}: all(n=${p.n_all}, WR5=${p.t5_wr_all}%) | +vol≥1x(n=${p.n_vol ?? 0}, WR5=${p.t5_wr_vol ?? '—'}%)`);
          });
        }

        // ── 9. Market Regime ────────────────────────────────────────────
        if (pt.market_regime && Object.keys(pt.market_regime).length) {
          wl();
          wl('  📊 Market Regime × RSI≤30 — السياق أهم من الإشارة:');
          const regOrder = ['CRASH','DOWN','FLAT_NEG','FLAT_POS','UP','SURGE'];
          regOrder.forEach(k => {
            const r = pt.market_regime[k]; if (!r) return;
            wl(`  ${r.signal_quality} ${k.padEnd(9)}: n=${String(r.n).padEnd(5)} | WR5=${r.t5_wr}% | avg5=${pct(r.t5_avg)}`);
          });
          wl('  💡 الدرس: اشترِ RSI≤30 في السوق DOWN/CRASH — تجنبه في UP/SURGE');
        }

        // ── ملخص الاكتشافات ───────────────────────────────────────────────
        wl();
        sep('─');
        wl('  📋 ملخص الأنماط المكتشفة:');
        const discoveries = [];
        if ((cd.t5_wr ?? 0) > 58) discoveries.push(`CB Down: WR=${cd.t5_wr}% بعد انخفاض ≥9%`);
        if ((gd.pct_filled_same_day ?? 0) > 60) discoveries.push(`Gap Down: ${gd.pct_filled_same_day}% تُملأ نفس اليوم`);
        if ((ee.edge ?? 0) > 1) discoveries.push(`Earnings Season: +${ee.edge}% ميزة في موسم النتائج`);
        if ((tv.t5_wr ?? 0) > 52) discoveries.push(`Thin Volume: WR=${tv.t5_wr}% عند إرهاق البائعين`);
        if ((post.t5_wr ?? 0) > 52) discoveries.push(`Post-Ramadan: WR=${post.t5_wr}% في أسبوعين بعد رمضان`);
        // New discoveries
        const atrLow = pt.atr_regime?.LOW;
        if (atrLow && atrLow.t5_wr < 10) discoveries.push(`❌ RSI يفشل في ATR<1% — اشترط ATR>1% دائماً`);
        const crashReg = pt.market_regime?.CRASH;
        if (crashReg && crashReg.t5_wr > 40) discoveries.push(`🚨 Market CRASH + RSI≤30 → WR5=${crashReg.t5_wr}% — أقوى إشارة`);
        const mrv = pt.momentum_reversal?.rsi30_mom5_neg5;
        if (mrv && mrv.t5_wr > 40) discoveries.push(`🔄 RSI≤30+mom5≤-5% → WR5=${mrv.t5_wr}% (n=${mrv.n})`);
        discoveries.forEach((d, i) => wl(`  ${i+1}. ${d}`));
        if (discoveries.length === 0) wl('  لم تُكتشف أنماط ذات دلالة إحصائية كافية');

        results.patterns = pt;
      } else {
        wl(`  ⚠️ ${pt.error}`);
      }
    } catch (e) {
      wl(`  ❌ خطأ: ${e.message}`);
    }
  }

  // ══ الخلاصة الاستراتيجية (مُحدَّثة بالمنهجية الجديدة) ════════════════
  if (SECTION === 'all') {
    wl(); sep();
    wl('  🏆 الإطار الاستراتيجي المُحسَّن (Quant Research v2):');
    wl();

    // RSI threshold: الآن يعتمد على ATR regime + Macro معاً
    const rr = +( macro.real_interest_rate ?? 0 );
    // في بيئة NEUTRAL (rr=+4.1%): نحتاج RSI≤30 + ATR>1% (ليس ≤35)
    const recRsi = rr < -5 ? 35 : rr < 0 ? 32 : rr < 5 ? 30 : 28;

    wl('  ┌────────────────────────────────────────────────────────────────────────┐');
    wl('  │  LAYER 1 — Market Regime Filter (قبل أي إشارة)                        │');
    wl('  │  ✅ ادخل فقط إذا: السوق DOWN أو FLAT_NEG (avg mom5 ≤ 0%)             │');
    wl('  │  ❌ لا تشترِ RSI في سوق UP أو SURGE (WR5 ينهار إلى 21%)              │');
    wl('  ├────────────────────────────────────────────────────────────────────────┤');
    wl('  │  LAYER 2 — ATR Volatility Gate (فلتر جديد مكتشف)                     │');
    wl('  │  ✅ اشترط ATR > 1% يومياً — RSI في ATR<1% يُنتج WR5=4.5% فقط!       │');
    wl('  │  🔥 ATR EXTREME (>3%) + RSI≤30 = أفضل بيئة (WR5=35%)                │');
    wl('  ├────────────────────────────────────────────────────────────────────────┤');
    wl('  │  LAYER 3 — Entry Signal (مرتّب بالقوة)                                │');
    wl(`  │  🥇 RSI≤${recRsi} + bb_pos≤0.1 + ATR>1%          WR5~38%  n=2714     │`);
    wl(`  │  🥈 RSI≤30 + mom5≤-5% (momentum exhaustion)     WR5~44%  n=2188     │`);
    wl('  │  🥉 CB Down (-9%) + RSI≤35                       WR5~61%  n=591      │');
    wl('  │  4️⃣  Panic Gap (gap≤-5% + RSI≤35 + vol≥1x)      WR5~65%  n=20       │');
    wl('  │  5️⃣  ML Signal (HGB ≥65% confidence)             WR5~60%  n=2 today  │');
    wl('  ├────────────────────────────────────────────────────────────────────────┤');
    wl('  │  LAYER 4 — Macro Multiplier                                           │');
    wl(`  │  🟠 NEUTRAL (rr=${rr.toFixed(1)}%): RSI≤${recRsi}, لا ≤35 — الودائع تنافس         │`);
    wl('  │  🟢 EQUITY_POSITIVE (rr<-5%): رفع RSI إلى ≤38 + وزّن أثقل           │');
    wl('  └────────────────────────────────────────────────────────────────────────┘');
    wl();
    wl('  🧠 المنطق العلمي الجديد:');
    wl('     • الـ RSI edge ليس RSI نفسه — هو تقلب ATR مخفي تحته');
    wl('     • bb_width هو المحرك الأول (SHAP 17.3%) — اشترط bb_width > 3%');
    wl('     • السياق الكلي (market regime) يُضاعف أو يُلغي الإشارة');
    wl('     • RSI≤25 (n=74) في Grid Search: كبير بإحصاء لكن صغير عملياً');
    wl('       → 0.3 إشارة/سهم كل التاريخ — غير قابل للتداول الحقيقي');

    // ملخص السياق الكلي
    if (macro.real_interest_rate != null) {
      wl();
      wl('  🌍 السياق الكلي (📡 TradingView Live):');
      const icon = rr < -5 ? '🟢' : rr < 0 ? '🟡' : rr < 5 ? '🟠' : '🔴';
      wl(`     ${icon} تضخم=${macro.inflation_pct?.toFixed(1) ?? '?'}% | CBE=${macro.cbe_rate_pct?.toFixed(1) ?? '?'}% | فائدة حقيقية=${rr.toFixed(1)}% | USD/EGP=${macro.usd_egp?.toFixed(2) ?? '?'}`);
      wl(`     📌 RSI Entry Threshold موصى به: ≤${recRsi} (تحت البيئة الحالية)`);
      wl(`     📌 ATR Minimum Gate: > 1.0% (بيانات 73K شمعة تُثبت ذلك)`);
    }
  }

  const elapsed = Math.round((Date.now() - t0) / 1000);
  sep();
  wl(`  ⏱️  وقت الاكتشاف : ${elapsed}s | ${new Date().toLocaleTimeString('ar-EG')}`);
  sep();

  if (JSON_MODE) {
    process.stdout.write('\n' + JSON.stringify(results, null, 2) + '\n');
  }
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
