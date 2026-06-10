/**
 * EGX Daily Report — 5 آفاق زمنية
 * ==================================
 * يولّد تقرير يومي شامل من قاعدة البيانات المحلية
 * scalp + short swing + long swing + investment + undervalued
 *
 * التشغيل:
 *   node scripts/daily_report.mjs
 *   node scripts/daily_report.mjs --save          (يحفظ في قاعدة البيانات)
 *   node scripts/daily_report.mjs --json          (إخراج JSON)
 *   node scripts/daily_report.mjs --date 2026-05-04
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import * as ss  from 'simple-statistics';
import TI       from 'technicalindicators';
import { readFileSync } from 'fs';
import { join }         from 'path';
import { getDB, getOHLCV, saveDailyReport, getLastReport,
         scoreSetup, calculateIndicators, quickScan,
         EGX_UNIVERSE }                   from '../src/egx/index.js';
import { pythonMacroData }               from '../src/egx/python_bridge.js';
import { sendDailyReport, isTelegramConfigured } from '../src/egx/notify.js';

// ── CLI ────────────────────────────────────────────────────────────────────
const SAVE_MODE   = process.argv.includes('--save');
const JSON_MODE   = process.argv.includes('--json');
const NOTIFY_MODE = process.argv.includes('--notify');       // إرسال Telegram
const MACRO_MODE  = process.argv.includes('--macro');        // جلب بيانات ماكرو
const DATE_ARG    = (() => { const i = process.argv.indexOf('--date'); return i >= 0 ? process.argv[i+1] : null; })();
const TODAY       = DATE_ARG ?? new Date().toISOString().split('T')[0];

// ── أسهم الجودة العالية v3 ────────────────────────────────────────────────
const QUALITY_V3 = [
  'MOSC','UTOP','TORA','ADRI','COMI','HDBK','PHDC','TMGH','SWDY','IRON',
  'OCDI','AMOC','EFID','ORWE','ACGC','CLHO','HELI','POUL','KNGC','VALU',
];

const DAY_NAMES = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت'];
const last = arr => arr?.length > 0 ? arr[arr.length - 1] : null;

// ── قراءة سياق النظام من DB + ملف orchestrator ─────────────────────────────
const ROOT_DIR = new URL('..', import.meta.url).pathname;

function getRegimeContext(db) {
  try {
    const regime  = db.prepare("SELECT regime FROM regime_history ORDER BY date DESC LIMIT 1").get();
    const breadth = db.prepare("SELECT breadth_score, signal FROM market_breadth_daily ORDER BY date DESC LIMIT 1").get();

    // قراءة posture من ملف JSON (orchestrator_log.json) لأنه لا يُخزّن في جدول
    let postureData = null;
    try {
      const logPath = join(ROOT_DIR, 'data', 'orchestrator_log.json');
      const raw = JSON.parse(readFileSync(logPath, 'utf8'));
      const last = Array.isArray(raw) ? raw[raw.length - 1] : raw;
      postureData = { posture: last?.posture ?? null, exposure_pct: last?.exposure_pct ?? null };
    } catch {}

    return {
      regime:        regime?.regime         ?? 'UNKNOWN',
      breadthSignal: breadth?.signal        ?? 'UNKNOWN',
      breadthScore:  breadth?.breadth_score ?? null,
      posture:       postureData?.posture   ?? null,
      exposure:      postureData?.exposure_pct ?? null,
    };
  } catch { return { regime: 'UNKNOWN', breadthSignal: 'UNKNOWN' }; }
}

/** أعلى إشارات نظام UES (Ph 75) من unified_signals */
function getTopUESSignals(db) {
  try {
    // نأخذ أحدث تاريخ متاح (يعالج فرق التوقيت UTC vs Cairo)
    const latestDate = db.prepare(
      "SELECT MAX(signal_date) as d FROM unified_signals"
    ).get()?.d ?? TODAY;

    const rows = db.prepare(`
      SELECT symbol, unified_score, conviction_tier, liquidity_tier,
             dna_score, cycle_score, entry_price, t1_target, stop_loss, r_ratio
      FROM unified_signals
      WHERE signal_date=?
        AND conviction_tier IN ('ULTRA_CONVICTION','HIGH_CONVICTION')
      ORDER BY unified_score DESC LIMIT 10
    `).all(latestDate);
    return rows;
  } catch { return []; }
}

/** دورات السوق الكلية من جدول market_cycles */
function getCycleContext(db) {
  try {
    const today = new Date(TODAY);
    const rows = db.prepare(`
      SELECT symbol, period_days, next_peak_date, next_trough_date, confidence
      FROM market_cycles
      WHERE symbol IS NULL OR symbol='MARKET'
      ORDER BY confidence DESC LIMIT 5
    `).all();
    return rows.map(r => {
      const peakDate   = r.next_peak_date   ? new Date(r.next_peak_date)   : null;
      const troughDate = r.next_trough_date ? new Date(r.next_trough_date) : null;
      const daysToPeak   = peakDate   ? Math.round((peakDate   - today) / 86400000) : null;
      const daysToTrough = troughDate ? Math.round((troughDate - today) / 86400000) : null;
      return {
        period:       Math.round(r.period_days),
        confidence:   +(r.confidence * 100).toFixed(0),
        daysToPeak,
        daysToTrough,
        peakDate:   r.next_peak_date   ? String(r.next_peak_date).slice(0,10)   : null,
        troughDate: r.next_trough_date ? String(r.next_trough_date).slice(0,10) : null,
      };
    });
  } catch { return []; }
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  SIGNAL GENERATORS
// ═══════════════════════════════════════════════════════════════════════════

/** ⚡ SCALP — RSI oversold + دعم + حجم يتحرك */
function getScalpSignals() {
  const signals = [];

  for (const sym of [...new Set(EGX_UNIVERSE)]) {
    const bars = getOHLCV(sym, 60);
    if (!bars || bars.length < 20) continue;

    try {
      const closes  = bars.map(b => b.close);
      const highs   = bars.map(b => b.high);
      const lows    = bars.map(b => b.low);
      const volumes = bars.map(b => b.volume ?? 0);
      const curr    = closes[closes.length - 1];

      // RSI
      const rsiArr = TI.RSI.calculate({ period: 14, values: closes });
      const rsi = last(rsiArr);
      if (!rsi || rsi >= 40) continue; // فقط oversold

      // ATR للحجم SL/TP
      const atrArr = TI.ATR.calculate({ period: 14, high: highs, low: lows, close: closes });
      const atr    = last(atrArr) ?? curr * 0.02;

      // حجم المتوسط
      const avgVol = volumes.length >= 5 ? ss.mean(volumes.slice(-20)) : 1;
      const volR   = avgVol > 0 ? volumes[volumes.length - 1] / avgVol : 1;

      // دعم قريب
      const recentLows = lows.slice(-20).filter(l => l < curr);
      const support    = recentLows.length > 0 ? Math.max(...recentLows) : null;
      const nearSup    = support && Math.abs(curr - support) / curr < 0.01;

      if (!nearSup && volR < 1.3) continue;

      const sl  = +(curr - atr * 1.5).toFixed(3);
      const t1  = +(curr + atr * 2.0).toFixed(3);
      const rr  = +((t1 - curr) / Math.max(curr - sl, 0.001)).toFixed(1);

      if (rr < 1.8 || sl <= 0) continue;

      const reasons = [];
      if (rsi < 30)  reasons.push(`RSI ${rsi.toFixed(0)} (oversold شديد)`);
      else           reasons.push(`RSI ${rsi.toFixed(0)}`);
      if (nearSup)   reasons.push(`قريب من دعم ${support?.toFixed(2)}`);
      if (volR > 1.5) reasons.push(`حجم ${volR.toFixed(1)}x`);

      signals.push({
        symbol: sym, type: 'scalp',
        currentPrice: +curr.toFixed(3),
        entry: +curr.toFixed(3), sl, t1, rr,
        rsi: +rsi.toFixed(1), volRatio: +volR.toFixed(2),
        reason: reasons.join(' + '),
        isQuality: QUALITY_V3.includes(sym),
      });
    } catch { /* تجاهل */ }
  }

  return signals
    .sort((a, b) => {
      const s = r => (r.isQuality ? 10 : 0) + (40 - r.rsi) + r.rr;
      return s(b) - s(a);
    })
    .slice(0, 5);
}

/** 🔄 SHORT SWING — الـ scorer الحالي (Volume Accumulation + Institutional Retest) */
function getShortSwingSignals(db) {
  const signals = [];

  for (const sym of [...new Set(EGX_UNIVERSE)]) {
    const bars = getOHLCV(sym, 300);
    if (!bars || bars.length < 10) continue;

    try {
      const lastBar = bars[bars.length - 1];
      const last5   = bars.slice(-5);

      const scored = scoreSetup({
        symbol: sym,
        quote:  { close: lastBar.close, open: lastBar.open, high: lastBar.high, low: lastBar.low, volume: lastBar.volume },
        last_5_bars: last5,
        all_bars:    bars,
      });

      if (!scored.rejected && scored.score >= 60) {
        // مؤشرات إضافية
        const ind = quickScan(bars);
        signals.push({
          ...scored,
          type:       'short_swing',
          rsiVal:     ind?.rsi  ? +ind.rsi.toFixed(1)      : null,
          adxVal:     ind?.adx  ? +ind.adx.adx.toFixed(1)  : null,
          aboveEma20: ind?.ema20 ? lastBar.close > ind.ema20 : null,
          isQuality:  QUALITY_V3.includes(sym),
        });
      }
    } catch { /* تجاهل */ }
  }

  // Enrich with UES from unified_signals for better ranking
  const today = new Date().toISOString().split('T')[0];
  try {
    const stmt = db.prepare("SELECT symbol, unified_score, conviction_tier, liquidity_tier FROM unified_signals WHERE signal_date=? AND symbol=?");
    for (const sig of signals) {
      try {
        const row = stmt.get(today, sig.symbol);
        if (row) { sig.ues = row.unified_score; sig.convictionTier = row.conviction_tier; sig.liqTier = row.liquidity_tier; }
      } catch {}
    }
  } catch {}

  return signals
    .sort((a, b) => {
      const bonus = r => (r.isQuality ? 10 : 0) + (r.setupId === 'volume_accumulation' ? 5 : 0) + (r.ues ? r.ues * 0.1 : 0);
      return (b.score + bonus(b)) - (a.score + bonus(a));
    })
    .slice(0, 8);
}

/** 📈 LONG SWING — EMA50 صاعدة + ADX ≥ 18 + تماسك */
function getLongSwingSignals() {
  const signals = [];

  for (const sym of [...new Set(EGX_UNIVERSE)]) {
    const bars = getOHLCV(sym, 300);
    if (!bars || bars.length < 55) continue;

    try {
      const closes = bars.map(b => b.close);
      const highs  = bars.map(b => b.high);
      const lows   = bars.map(b => b.low);
      const curr   = closes[closes.length - 1];

      const ema50arr = TI.EMA.calculate({ period: 50, values: closes });
      if (!ema50arr || ema50arr.length < 5) continue;

      const ema50    = ema50arr[ema50arr.length - 1];
      const ema50_10 = ema50arr[Math.max(0, ema50arr.length - 10)];
      const ema50up  = ema50 > ema50_10 * 1.002;
      if (!ema50up || curr < ema50 * 0.995) continue;

      // ADX
      let adxVal = null;
      try {
        const adxArr = TI.ADX.calculate({ period: 14, high: highs, low: lows, close: closes });
        adxVal = last(adxArr)?.adx;
      } catch {}
      if (adxVal !== null && adxVal < 16) continue;

      // RSI — لا overbought
      const rsiArr = TI.RSI.calculate({ period: 14, values: closes });
      const rsi = last(rsiArr);
      if (rsi && rsi > 72) continue;

      // تماسك: تذبذب < 9% آخر 15 شمعة
      const l15    = closes.slice(-15);
      const range15 = (Math.max(...l15) - Math.min(...l15)) / curr;
      const isConsolidating = range15 < 0.09;

      // ATR للمستويات
      const atr = last(TI.ATR.calculate({ period: 14, high: highs, low: lows, close: closes })) ?? curr * 0.025;

      const reasons = [`EMA50 صاعدة`];
      if (adxVal) reasons.push(`ADX ${adxVal.toFixed(0)}`);
      if (isConsolidating) reasons.push('تماسك سعري');

      const slCalc = +(curr - atr * 2.5).toFixed(3);
      const t1Calc = +(curr + atr * 4.0).toFixed(3);
      const t2Calc = +(curr + atr * 7.0).toFixed(3);
      const rrCalc = +(4.0 / 2.5).toFixed(1);
      signals.push({
        symbol:   sym, type: 'long_swing',
        currentPrice: +curr.toFixed(3),
        ema50: +ema50.toFixed(3),
        adx:   adxVal ? +adxVal.toFixed(1) : null,
        rsi:   rsi    ? +rsi.toFixed(1)    : null,
        consolidating: isConsolidating,
        priceRange15Pct: +(range15 * 100).toFixed(1),
        entry: +curr.toFixed(3),
        sl:    slCalc,
        t1:    t1Calc,
        t2:    t2Calc,
        rr:    rrCalc,
        levels: { sl: slCalc, t1: t1Calc, t2: t2Calc, rr1: rrCalc },
        reason: reasons.join(' + '),
        isQuality: QUALITY_V3.includes(sym),
      });
    } catch { /* تجاهل */ }
  }

  return signals
    .sort((a, b) => {
      const s = r => (r.isQuality ? 15 : 0) + (r.consolidating ? 10 : 0) + (r.adx ?? 15);
      return s(b) - s(a);
    })
    .slice(0, 6);
}

/** 🏦 INVESTMENT — من البيانات المالية أو الجودة التاريخية */
function getInvestmentSignals(db) {
  // أولاً: من جدول financial_data إذا موجود
  try {
    const fromDB = db.prepare(`
      SELECT f.symbol, f.pe_ratio, f.pb_ratio, f.dividend_yield,
             f.earnings_growth, f.sector
      FROM financial_data f
      WHERE (f.pe_ratio IS NULL OR f.pe_ratio < 12)
        AND (f.pb_ratio IS NULL OR f.pb_ratio < 2.5)
      ORDER BY f.pe_ratio ASC NULLS LAST
      LIMIT 6
    `).all();

    if (fromDB.length > 0) {
      return fromDB.map(f => {
        const bars = getOHLCV(f.symbol, 5);
        const curr = bars?.length > 0 ? bars[bars.length - 1].close : null;
        return {
          symbol:   f.symbol,
          type:     'investment',
          currentPrice: curr,
          pe: f.pe_ratio, pb: f.pb_ratio,
          dividend: f.dividend_yield,
          growth:   f.earnings_growth,
          sector:   f.sector,
          isQuality: QUALITY_V3.includes(f.symbol),
          reason: [
            f.pe_ratio        != null ? `P/E ${(+f.pe_ratio).toFixed(2)}` : null,
            f.pb_ratio        != null ? `P/B ${(+f.pb_ratio).toFixed(2)}` : null,
            f.dividend_yield  != null ? `توزيعات ${(+f.dividend_yield).toFixed(2)}%` : null,
            f.earnings_growth != null ? `نمو ${(+f.earnings_growth).toFixed(1)}%` : null,
          ].filter(Boolean).join(' | '),
        };
      });
    }
  } catch { /* تجاهل */ }

  // fallback: أسهم الجودة v3 بدون بيانات مالية
  return QUALITY_V3.slice(0, 4).map(sym => {
    const bars = getOHLCV(sym, 5);
    const curr = bars?.length > 0 ? bars[bars.length - 1].close : null;
    return {
      symbol: sym, type: 'investment',
      currentPrice: curr,
      isQuality: true,
      reason: 'جودة تاريخية عالية v3',
      note:   '⚠️ أضف بيانات P/E و P/B عبر: saveFinancialData(symbol, {pe_ratio, pb_ratio, ...})',
    };
  });
}

/** 💎 UNDERVALUED — Z-Score منخفض أو P/B < 1 */
function getUndervaluedSignals(db) {
  const signals = [];

  // من البيانات المالية
  try {
    const fromDB = db.prepare(`
      SELECT symbol, pb_ratio, pe_ratio, market_cap
      FROM financial_data WHERE pb_ratio < 1.3
      ORDER BY pb_ratio ASC LIMIT 5
    `).all();

    for (const f of fromDB) {
      const bars = getOHLCV(f.symbol, 5);
      const curr = bars?.length > 0 ? bars[bars.length - 1].close : null;
      signals.push({
        symbol: f.symbol, type: 'undervalued',
        currentPrice: curr,
        pb: f.pb_ratio, pe: f.pe_ratio,
        reason: `P/B ${f.pb_ratio != null ? (+f.pb_ratio).toFixed(2) : 'N/A'} — أقل من القيمة الدفترية`,
      });
    }
  } catch { /* تجاهل */ }

  // Z-Score سعري على الأسهم ذات الجودة
  for (const sym of QUALITY_V3) {
    if (signals.find(s => s.symbol === sym)) continue;
    const bars = getOHLCV(sym, 300);
    if (!bars || bars.length < 60) continue;

    try {
      const closes = bars.map(b => b.close);
      const curr   = closes[closes.length - 1];
      const mean   = ss.mean(closes);
      const std    = ss.standardDeviation(closes);
      if (std <= 0) continue;
      const z = (curr - mean) / std;

      if (z <= -1.5) {
        signals.push({
          symbol: sym, type: 'undervalued',
          currentPrice: +curr.toFixed(3),
          zScore: +z.toFixed(2),
          meanPrice: +mean.toFixed(3),
          reason: `Z-Score ${z.toFixed(2)} — رخيص تاريخياً (أقل من المتوسط بـ ${Math.abs(z).toFixed(1)}σ)`,
          pb: null,
        });
      }
    } catch { /* تجاهل */ }
  }

  return signals.slice(0, 5);
}

/** قراءة قطاع السهم من جدول stock_universe */
function getSymbolSector(db, symbol) {
  try {
    const row = db.prepare("SELECT sector FROM stock_universe WHERE symbol=?").get(symbol);
    return row?.sector ?? null;
  } catch { return null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  MARKET BREADTH
// ═══════════════════════════════════════════════════════════════════════════
function getMarketBreadth() {
  const sample = [...new Set(EGX_UNIVERSE)].slice(0, 120);
  let up = 0, down = 0, flat = 0;

  for (const sym of sample) {
    const bars = getOHLCV(sym, 3);
    if (!bars || bars.length < 2) continue;
    const chg = (bars[bars.length-1].close - bars[bars.length-2].close) / bars[bars.length-2].close;
    if (chg > 0.005) up++;
    else if (chg < -0.005) down++;
    else flat++;
  }
  return { up, down, flat, total: up + down + flat };
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  REPORT BUILDER
// ═══════════════════════════════════════════════════════════════════════════
function buildReport({ scalp, shortSwing, longSwing, investment, undervalued, breadth, openPos, yesterday, macro, regimeCtx, uesSignals, cycleCtx }) {
  const SEP  = '─'.repeat(62);
  const DSEP = '═'.repeat(62);
  const L    = [];

  L.push(DSEP);
  L.push(`         📊  EGX DAILY REPORT — Dr. Husam`);
  L.push(`         ${DAY_NAMES[new Date(TODAY).getDay()]}  ${TODAY}`);
  L.push(DSEP);

  // ── مزاج السوق ──────────────────────────────────────────────────────
  if (breadth && breadth.total > 0) {
    const upPct = ((breadth.up / breadth.total) * 100).toFixed(0);
    // اعتمد على النظام (regime) إن توفّر، وإلا على نسبة الصاعد محلياً
    const rg = regimeCtx?.regime;
    const mood  = rg === 'BULL' ? '🟢 صاعد'
                : rg === 'BEAR' ? '🔴 هابط'
                : breadth.up > breadth.down * 1.3 ? '🟢 صاعد'
                : breadth.down > breadth.up * 1.3  ? '🔴 هابط'
                : '🟡 محايد';
    L.push(`\n📊 مزاج السوق: ${mood}  (من ${breadth.total} سهم)`);
    L.push(`   رافع: ${breadth.up} | هابط: ${breadth.down} | ثابت: ${breadth.flat} | نسبة صاعد: ${upPct}%`);
    if (regimeCtx?.regime && regimeCtx.regime !== 'UNKNOWN') {
      const expStr = regimeCtx.exposure != null ? ` (${regimeCtx.exposure.toFixed(0)}%)` : '';
      const regimeLine = `   🧠 النظام: ${regimeCtx.regime} | ${regimeCtx.breadthSignal ?? ''} | وضعية: ${regimeCtx.posture ?? 'N/A'}${expStr}`;
      L.push(regimeLine);
    }
  }

  // ── 🔄 دورات السوق (FFT Cycles) ─────────────────────────────────────
  if (cycleCtx && cycleCtx.length > 0) {
    L.push(`\n${SEP}`);
    L.push(`🔄  دورات السوق — FFT Analysis`);
    L.push(SEP);
    for (const c of cycleCtx) {
      const confStr = `ثقة ${c.confidence}%`;
      let timing = '';
      if (c.daysToPeak != null && c.daysToPeak >= 0 && c.daysToPeak <= 14) {
        timing = `  ⬆️  ذروة خلال ${c.daysToPeak} يوم (${c.peakDate})`;
      } else if (c.daysToPeak != null && c.daysToPeak > 14) {
        timing = `  📅 ذروة: ${c.peakDate} (بعد ${c.daysToPeak} يوم)`;
      } else if (c.daysToPeak != null && c.daysToPeak < 0) {
        timing = `  📉 مرّت الذروة (منذ ${Math.abs(c.daysToPeak)} يوم)`;
      }
      if (c.daysToTrough != null && c.daysToTrough >= 0 && c.daysToTrough <= 10) {
        timing += `  ⬇️  قاع خلال ${c.daysToTrough} يوم`;
      }
      L.push(`   • دورة ${c.period} يوم  |  ${confStr}${timing}`);
    }
  }

  // ── 🤖 أقوى إشارات UES (Ph 75 — DNA + Cycles) ──────────────────────
  if (uesSignals && uesSignals.length > 0) {
    L.push(`\n${SEP}`);
    L.push(`🤖  أقوى إشارات النظام (UES — DNA + دورات)`);
    L.push(`   الذكاء الاصطناعي × التحليل الفني × الموسمية × الدورات`);
    L.push(SEP);
    for (let i = 0; i < uesSignals.length; i++) {
      const s = uesSignals[i];
      const convIcon = s.conviction_tier === 'ULTRA_CONVICTION' ? '🔥' : '⭐';
      const tierStr  = s.conviction_tier === 'ULTRA_CONVICTION' ? 'ULTRA' : 'HIGH';
      const liqStr   = s.liquidity_tier  ? `[${s.liquidity_tier}]` : '';
      const dna      = s.dna_score   != null ? `DNA:${(+s.dna_score).toFixed(0)}`   : '';
      const cyc      = s.cycle_score != null ? `Cyc:${(+s.cycle_score).toFixed(0)}` : '';
      const meta     = [dna, cyc].filter(Boolean).join(' ');
      L.push(`   ${i+1}. ${convIcon} ${s.symbol}  UES:${(+s.unified_score).toFixed(1)}  ${tierStr} ${liqStr}  ${meta}`);
      if (s.entry_price && s.t1_target && s.stop_loss) {
        const ep = (+s.entry_price).toFixed(3);
        const t1 = (+s.t1_target).toFixed(3);
        const sl = (+s.stop_loss).toFixed(3);
        const rr = s.r_ratio ? `R:R ${(+s.r_ratio).toFixed(1)}x` : '';
        L.push(`      دخول: ${ep}  T1: ${t1}  SL: ${sl}  ${rr}`);
      }
    }
  }

  // ── متابعة المراكز ──────────────────────────────────────────────────
  if (openPos.length > 0 || yesterday.length > 0) {
    L.push(`\n${SEP}`);
    L.push(`📌 متابعة`);
    L.push(SEP);
    for (const p of openPos) {
      const bars = getOHLCV(p.symbol, 2);
      const curr = bars?.length > 0 ? bars[bars.length-1].close : null;
      if (curr && p.entry_price) {
        const pnl  = ((curr - p.entry_price) / p.entry_price * 100);
        const sign = pnl >= 0 ? '+' : '';
        const icon = pnl >= 0 ? '✅' : '⚠️';
        L.push(`   ${icon} ${p.symbol}: دخل ${p.entry_price} → الآن ${curr} = ${sign}${pnl.toFixed(1)}%`);
      }
    }
    for (const y of yesterday) {
      L.push(`   (أمس) ${y.symbol}: نقاط ${y.score} | دخول ~${y.entry_high ?? '?'}`);
    }
  }

  // ── ⚡ SCALP ────────────────────────────────────────────────────────
  L.push(`\n${SEP}`);
  L.push(`⚡  SCALP — جلسة اليوم فقط  (دخول قبل 11:00 AM)`);
  L.push(`   هدف: +1.5–2.5%  |  SL: -0.8–1.2%  |  R:R ≥ 1.8`);
  L.push(SEP);
  if (scalp.length === 0) {
    L.push(`   ❌  لا إعدادات scalp واضحة اليوم`);
  } else {
    for (let i = 0; i < scalp.length; i++) {
      const s = scalp[i];
      const q = s.isQuality ? ' ★' : '';
      L.push(`   ${i+1}.${q} ${s.symbol}  —  دخول ${s.entry}  |  SL ${s.sl}  |  T1 ${s.t1}  (R:R ${s.rr}x)`);
      L.push(`      ${s.reason}`);
    }
  }

  // ── 🔄 SHORT SWING ──────────────────────────────────────────────────
  L.push(`\n${SEP}`);
  L.push(`🔄  SHORT SWING — 3–7 أيام`);
  L.push(`   هدف: +4–8%  |  SL: هيكلي  |  R:R ≥ 2`);
  L.push(SEP);
  if (shortSwing.length === 0) {
    L.push(`   ❌  لا إعدادات short swing اليوم`);
  } else {
    for (let i = 0; i < shortSwing.length; i++) {
      const s = shortSwing[i];
      const q = s.isQuality ? ' ★' : '';
      L.push(`   ${i+1}.${q} ${s.symbol}  —  نقاط: ${s.score}/100 (${s.grade})  |  ${s.setupType}`);
      L.push(`      دخول: ${s.levels?.entryLow}–${s.levels?.entryHigh}  |  SL: ${s.levels?.sl}  |  T1: ${s.levels?.t1}  (R:R ${s.levels?.rr1}x)`);
      const extras = [];
      if (s.rsiVal  != null) extras.push(`RSI ${s.rsiVal}`);
      if (s.adxVal  != null) extras.push(`ADX ${s.adxVal}${s.adxVal >= 25 ? '✅' : ''}`);
      if (extras.length)     L.push(`      ${extras.join('  |  ')}`);
    }
  }

  // ── 📈 LONG SWING ───────────────────────────────────────────────────
  L.push(`\n${SEP}`);
  L.push(`📈  LONG SWING — 2–4 أسابيع`);
  L.push(`   هدف: +10–20%  |  SL: ~2.5×ATR  |  R:R ≥ 2.5`);
  L.push(SEP);
  if (longSwing.length === 0) {
    L.push(`   ❌  لا إعدادات long swing اليوم`);
  } else {
    for (let i = 0; i < longSwing.length; i++) {
      const s = longSwing[i];
      const q = s.isQuality ? ' ★' : '';
      L.push(`   ${i+1}.${q} ${s.symbol}  —  ${s.reason}`);
      L.push(`      سعر: ${s.currentPrice}  |  EMA50: ${s.ema50}  |  SL: ${s.sl}  |  T1: ${s.t1}  |  T2: ${s.t2}`);
      const extras = [];
      if (s.rsi) extras.push(`RSI ${s.rsi}`);
      if (s.consolidating) extras.push('تماسك ✅');
      if (extras.length)   L.push(`      ${extras.join('  |  ')}`);
    }
  }

  // ── 🏦 INVESTMENT ───────────────────────────────────────────────────
  L.push(`\n${SEP}`);
  L.push(`🏦  INVESTMENT — 6–12 شهر`);
  L.push(`   هدف: +25–50%  |  نظرة طويلة المدى`);
  L.push(SEP);
  if (investment.length === 0) {
    L.push(`   ℹ️  أضف بيانات مالية عبر saveFinancialData() للحصول على توصيات استثمارية`);
  } else {
    for (let i = 0; i < investment.length; i++) {
      const s = investment[i];
      const q = s.isQuality ? ' ★' : '';
      const cp = s.currentPrice != null ? (+s.currentPrice).toFixed(3) : null;
      L.push(`   ${i+1}.${q} ${s.symbol}${cp ? `  —  سعر: ${cp}` : ''}`);
      L.push(`      ${s.reason}`);
      if (s.note) L.push(`      ${s.note}`);
    }
  }

  // ── 💎 UNDERVALUED ──────────────────────────────────────────────────
  L.push(`\n${SEP}`);
  L.push(`💎  UNDERVALUED — أسهم تحت قيمتها الحقيقية (1–2 سنة)`);
  L.push(SEP);
  if (undervalued.length === 0) {
    L.push(`   ℹ️  أضف بيانات P/B أو انتظر Z-Score ≤ -1.5 على أسهم الجودة`);
  } else {
    for (let i = 0; i < undervalued.length; i++) {
      const s = undervalued[i];
      const cp = s.currentPrice != null ? (+s.currentPrice).toFixed(3) : null;
      L.push(`   ${i+1}. ${s.symbol}${cp ? `  —  سعر: ${cp}` : ''}`);
      const reason = s.pb != null ? s.reason.replace(String(s.pb), (+s.pb).toFixed(2)) : s.reason;
      L.push(`      ${reason}`);
    }
  }

  // ── 🌍 MACRO ──────────────────────────────────────────────────────────
  if (macro && !macro.error) {
    L.push(`\n${SEP}`);
    const tvLive = macro.tradingview_data ? '📡 TradingView Live' : '🌐 APIs';
    L.push(`🌍  بيانات الاقتصاد الكلي  (${tvLive})`);
    L.push(SEP);
    if (macro.usd_egp) {
      const usdChange = macro.usd_egp_change ? ` (${macro.usd_egp_change >= 0 ? '+' : ''}${macro.usd_egp_change?.toFixed(4)})` : '';
      L.push(`   💵 USD/EGP: ${(+macro.usd_egp).toFixed(2)}${usdChange}  (${macro.usd_egp_date ?? ''})`);
    }
    if (macro.inflation_pct) {
      const infDir = macro.inflation_momentum === 'falling' ? ' ↘️ هابط' : macro.inflation_momentum === 'rising' ? ' ↗️ صاعد' : ' →';
      const infChange = macro.inflation_change ? ` (${macro.inflation_change >= 0 ? '+' : ''}${macro.inflation_change?.toFixed(1)}%)` : '';
      L.push(`   📈 تضخم (EGIRYY): ${macro.inflation_pct.toFixed(1)}%${infChange}${infDir}  (${macro.inflation_year ?? ''})`);
    }
    if (macro.cbe_rate_pct ?? macro.lending_rate_pct) {
      const cbe = macro.cbe_rate_pct ?? macro.lending_rate_pct;
      const cbeDir = macro.cbe_rate_momentum === 'falling' ? ' ↘️ خفّضها' : macro.cbe_rate_momentum === 'rising' ? ' ↗️ رفعها' : ' →';
      const cbeChange = macro.cbe_rate_change ? ` (${macro.cbe_rate_change >= 0 ? '+' : ''}${macro.cbe_rate_change?.toFixed(1)}%)` : '';
      L.push(`   🏦 فائدة CBE (EGINTR): ${(+cbe).toFixed(1)}%${cbeChange}${cbeDir}  (${macro.cbe_rate_year ?? ''})`);
    }
    if (macro.real_interest_rate != null) {
      const rr = macro.real_interest_rate;
      const rrIcon = rr < -5 ? '🟢' : rr < 0 ? '🟡' : rr < 5 ? '🟠' : '🔴';
      L.push(`   ${rrIcon} فائدة حقيقية: ${(+rr).toFixed(1)}%  →  ${rr < 0 ? 'أسهم > ودائع' : rr < 5 ? 'بيئة محايدة' : 'ودائع > أسهم'}`);
    }
    if (macro.strategic_bias)   L.push(`   ⚖️  التوجه: ${macro.strategic_bias}`);
    if (macro.interpretation?.length) {
      for (const line of macro.interpretation) {
        L.push(`   • ${line}`);
      }
    }
    // اتجاه التضخم الشهري (آخر 4 نقاط)
    if (macro.inflation_trend?.length >= 3) {
      const trend = macro.inflation_trend.slice(-4);
      const trendStr = trend.map(b => `${b.date.slice(2)}: ${b.value.toFixed(1)}%`).join(' → ');
      L.push(`   📉 اتجاه التضخم: ${trendStr}`);
    }
  }

  // ── Footer ───────────────────────────────────────────────────────────
  L.push(`\n${DSEP}`);
  L.push(`★ = جودة تاريخية عالية v3  |  Backtest v3: WR 19.7%  |  PF 2.31x  |  19,336 إشارة`);
  L.push(`Generated: ${new Date().toISOString()}`);
  L.push(DSEP);

  return L.join('\n');
}

// ═══════════════════════════════════════════════════════════════════════════
// ██  MAIN
// ═══════════════════════════════════════════════════════════════════════════
async function main() {
  const db = getDB();

  process.stdout.write(`🔄  توليد التقرير — ${TODAY}...\n`);

  // مراكز مفتوحة
  let openPos = [];
  try { openPos = db.prepare(`SELECT symbol, entry_price FROM trades WHERE result='open' ORDER BY entry_date DESC LIMIT 8`).all(); } catch {}

  // إشارات الأمس
  let yesterday = [];
  try {
    const yDate = new Date(); yDate.setDate(yDate.getDate() - 1);
    yesterday = db.prepare(`SELECT symbol, score, entry_high FROM scans WHERE scan_date=? AND rejected=0 AND score>=60 ORDER BY score DESC LIMIT 5`).all(yDate.toISOString().split('T')[0]);
  } catch {}

  process.stdout.write(`  🧠 Regime context...\n`);
  const regimeCtx = getRegimeContext(db);

  process.stdout.write(`  ⚡ Scalp signals...\n`);
  const scalp = getScalpSignals();

  process.stdout.write(`  🔄 Short swing signals...\n`);
  const shortSwing = getShortSwingSignals(db);

  process.stdout.write(`  📈 Long swing signals...\n`);
  const longSwing = getLongSwingSignals();

  process.stdout.write(`  🏦 Investment signals...\n`);
  const investment = getInvestmentSignals(db);

  process.stdout.write(`  💎 Undervalued signals...\n`);
  const undervaluedRaw = getUndervaluedSignals(db);

  // إزالة التكرار بين Investment و Undervalued
  const investSyms = new Set(investment.map(s => s.symbol));
  const undervalued = undervaluedRaw.filter(s => !investSyms.has(s.symbol));

  process.stdout.write(`  📊 Market breadth...\n`);
  const breadth = getMarketBreadth();

  // ── بيانات الاقتصاد الكلي ─────────────────────────────────────────────
  // أولاً: نحاول قراءة آخر snapshot محفوظ من DB (TradingView Live إذا توفر)
  // ثانياً: fallback لـ pythonMacroData() إذا كانت DB فارغة
  let macro = null;
  if (MACRO_MODE) {
    process.stdout.write(`  🌍 Macro data (قراءة من DB — TradingView Live)...\n`);
    try {
      // قراءة آخر snapshot من DB — نفضّل TradingView Live إن وُجد خلال 48 ساعة
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

      if (lastRow && lastRow.raw_json) {
        const raw = JSON.parse(lastRow.raw_json);
        macro = {
          ...raw,
          usd_egp:          lastRow.usd_egp      ?? raw.usd_egp,
          inflation_pct:    lastRow.inflation     ?? raw.inflation_pct,
          cbe_rate_pct:     lastRow.cbe_rate      ?? raw.cbe_rate_pct,
          lending_rate_pct: lastRow.lending_rate  ?? raw.lending_rate_pct ?? raw.cbe_rate_pct,
          _db_source:       lastRow.source,
          _fetched_at:      lastRow.fetched_at,
        };
        const tvTag = (lastRow.source ?? '').includes('tradingview') ? '📡 TradingView Live' : '🌐 APIs';
        process.stdout.write(`     ${tvTag} | USD/EGP=${macro.usd_egp?.toFixed(2) ?? 'N/A'} | تضخم=${macro.inflation_pct?.toFixed(1) ?? 'N/A'}% | CBE=${macro.cbe_rate_pct?.toFixed(1) ?? 'N/A'}% | فائدة حقيقية=${macro.real_interest_rate?.toFixed(1) ?? 'N/A'}%\n`);
      } else {
        // Fallback: جلب من Python/APIs
        process.stdout.write(`     ⚠️  DB فارغة — جلب من Python...\n`);
        const mr = await pythonMacroData();
        if (mr.success !== false) {
          macro = mr;
        }
      }
    } catch (e) {
      process.stdout.write(`     ⚠️  Macro error: ${e.message}\n`);
    }
  }

  // ── UES top signals + cycle context (Ph 75) ──────────────────────────────
  process.stdout.write(`  🤖 UES top signals + cycle context...\n`);
  const uesSignals = getTopUESSignals(db);
  const cycleCtx   = getCycleContext(db);
  if (uesSignals.length) process.stdout.write(`     UES: ${uesSignals.length} HIGH/ULTRA signals\n`);
  if (cycleCtx.length)   process.stdout.write(`     Cycles: ${cycleCtx.length} market cycles found\n`);

  const data = { scalp, shortSwing, longSwing, investment, undervalued, breadth, openPos, yesterday, macro, regimeCtx, uesSignals, cycleCtx };

  if (JSON_MODE) {
    process.stdout.write(JSON.stringify({ date: TODAY, ...data }, null, 2) + '\n');
    return;
  }

  const report = buildReport(data);
  process.stdout.write('\n' + report + '\n');

  if (SAVE_MODE) {
    saveDailyReport(TODAY, report, {
      scalp:  scalp.length,
      swing:  shortSwing.length,
      invest: investment.length,
    });
    process.stdout.write(`\n💾  التقرير محفوظ في قاعدة البيانات (${TODAY})\n`);
  }

  // ── إرسال Telegram ───────────────────────────────────────────────────────
  const shouldNotify = NOTIFY_MODE || process.env.AUTO_NOTIFY === 'true';
  if (shouldNotify) {
    if (isTelegramConfigured()) {
      process.stdout.write(`\n📤  إرسال التقرير إلى Telegram...\n`);
      const nr = await sendDailyReport(report, {
        date:       TODAY,
        scalp:      scalp.length,
        swing:      shortSwing.length,
        invest:     investment.length,
        usdEgp:     macro?.usd_egp,
        regimeCtx:  regimeCtx,
        _data:      data,        // كائن البيانات للتنسيق الجديد
      });
      if (nr.ok) {
        process.stdout.write(`✅  Telegram: التقرير أُرسل بنجاح\n`);
      } else if (nr.skipped) {
        process.stdout.write(`⏭️  Telegram: تخطي (غير مضبوط)\n`);
      } else {
        process.stdout.write(`❌  Telegram: ${nr.error}\n`);
      }
    } else {
      process.stdout.write(`\n⚠️  Telegram غير مضبوط — أضف BOT_TOKEN و CHAT_ID في .env\n`);
      process.stdout.write(`   نسخ: cp .env.template .env  ثم عدّل القيم\n`);
    }
  }

  // ملخص سريع
  process.stdout.write(`\n✅  ${TODAY}: scalp(${scalp.length}) swing(${shortSwing.length}) long(${longSwing.length}) invest(${investment.length}) under(${undervalued.length})\n`);
}

main().catch(err => {
  process.stderr.write(`\n💥 خطأ: ${err.message}\n${err.stack}\n`);
  process.exit(1);
});
