#!/usr/bin/env node
/**
 * EGX Economics — جلب كامل لمؤشرات الاقتصاد الكلي المصري من TradingView
 * =========================================================================
 * يجلب 22 مؤشر اقتصادي مصري من TradingView Desktop عبر CDP (port 9222):
 *
 * التضخم والفائدة:   EGIRYY, EGINTR, EGCIR
 * النمو والناتج:     EGGDPYY
 * سوق العمل:        EGUR
 * الاحتياطيات:      EGFER, EGM2
 * التجارة:          EGBOT, EGEXP, EGIMP, EGREM, EGCA
 * المالية العامة:   EGGDG, EGGBV, EGGR, EGFE
 * الاستثمار:       EGFDI, EGED
 * الطاقة والسياحة: EGCOP, EGTA
 * الصرف الأجنبي:   FX_IDC:USDEGP
 *
 * يحفظ في:
 *   macro_economics  — time-series لكل مؤشر (تاريخي)
 *   macro_snapshot   — snapshot كامل مع macro regime مشتق
 *   macro_data       — backward-compatible (للكود القديم)
 *
 * يُشغَّل يومياً بعد market close (9:00 م) عبر cron
 *
 * الاستخدام:
 *   node scripts/fetch_economics.mjs             -- full pull from TradingView
 *   node scripts/fetch_economics.mjs --offline   -- قراءة من DB فقط
 *   node scripts/fetch_economics.mjs --notify    -- إرسال Telegram
 *   node scripts/fetch_economics.mjs --bars 24   -- آخر N bar تاريخي
 *   node scripts/fetch_economics.mjs --status    -- عرض آخر snapshot فقط
 *
 * المالك: Dr. Husam | مايو 2026
 */

import { getDB }                                 from '../src/egx/index.js';
import { sendMacroUpdate, isTelegramConfigured } from '../src/egx/notify.js';
import { ensureTradingView }                     from './lib/ensure_tv.mjs';

const OFFLINE    = process.argv.includes('--offline');
const NOTIFY     = process.argv.includes('--notify');
const STATUS     = process.argv.includes('--status');
const BARS_BACK  = parseInt(process.argv.find(a => a.startsWith('--bars'))?.split('=')[1]
                   ?? process.argv[process.argv.indexOf('--bars') + 1] ?? '24') || 24;

// رابط chart المستخدم
const CHART_ID = 'eNGQCTgH';

// ══════════════════════════════════════════════════════════════════════════
// تعريف كل المؤشرات الاقتصادية المصرية
// ══════════════════════════════════════════════════════════════════════════
const ECONOMICS_SYMBOLS = [
  // ── التضخم والفائدة ──────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGIRYY', field: 'inflation_yoy',    label: 'Inflation YoY',           unit: '%',  category: 'inflation',   dateField: 'inflation_date',   tier: 1 },
  { symbol: 'ECONOMICS:EGINTR', field: 'cbe_rate',         label: 'CBE Interest Rate',        unit: '%',  category: 'monetary',    dateField: 'cbe_rate_date',    tier: 1 },
  { symbol: 'ECONOMICS:EGCIR',  field: 'core_inflation',   label: 'Core Inflation YoY',       unit: '%',  category: 'inflation',   dateField: 'core_infl_date',   tier: 1 },
  // ── الصرف والسيولة ────────────────────────────────────────────────────
  { symbol: 'FX_IDC:USDEGP',    field: 'usd_egp',          label: 'USD/EGP',                  unit: 'EGP', category: 'fx',          dateField: 'usd_egp_date',     tier: 1 },
  // ── النمو والناتج ─────────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGGDPYY',field: 'gdp_yoy',          label: 'GDP Growth YoY',           unit: '%',  category: 'growth',      dateField: 'gdp_date',         tier: 2 },
  // ── سوق العمل ─────────────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGUR',   field: 'unemployment',     label: 'Unemployment Rate',        unit: '%',  category: 'labor',       dateField: 'unemp_date',       tier: 2 },
  // ── الاحتياطيات والنقد ───────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGFER',  field: 'fx_reserves_b',    label: 'FX Reserves',              unit: 'USD B', category: 'reserves', dateField: 'fx_res_date',     tier: 2 },
  { symbol: 'ECONOMICS:EGM2',   field: 'm2_egp_t',         label: 'Money Supply M2',          unit: 'EGP T', category: 'monetary', dateField: 'm2_date',         tier: 2 },
  // ── التجارة الخارجية ──────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGBOT',  field: 'trade_balance_m',  label: 'Trade Balance',            unit: 'USD', category: 'trade',      dateField: 'trade_date',       tier: 2 },
  { symbol: 'ECONOMICS:EGEXP',  field: 'exports_m',        label: 'Exports',                  unit: 'USD', category: 'trade',      dateField: 'trade_date',       tier: 3 },
  { symbol: 'ECONOMICS:EGIMP',  field: 'imports_m',        label: 'Imports',                  unit: 'USD', category: 'trade',      dateField: 'trade_date',       tier: 3 },
  { symbol: 'ECONOMICS:EGREM',  field: 'remittances_q',    label: 'Remittances',              unit: 'USD', category: 'trade',      dateField: 'rem_date',         tier: 2 },
  { symbol: 'ECONOMICS:EGCA',   field: 'current_account_b',label: 'Current Account',          unit: 'USD', category: 'trade',      dateField: 'ca_date',          tier: 2 },
  // ── المالية العامة ────────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGGDG',  field: 'govt_debt_gdp',    label: 'Govt Debt/GDP',            unit: '%',  category: 'fiscal',      dateField: 'debt_date',        tier: 2 },
  { symbol: 'ECONOMICS:EGGBV',  field: 'budget_balance_egp_t', label: 'Budget Balance',      unit: 'EGP', category: 'fiscal',     dateField: 'budget_date',      tier: 3 },
  { symbol: 'ECONOMICS:EGGR',   field: 'govt_revenue_egp_t',   label: 'Govt Revenue',        unit: 'EGP', category: 'fiscal',     dateField: 'budget_date',      tier: 3 },
  { symbol: 'ECONOMICS:EGFE',   field: 'fiscal_exp_egp_t', label: 'Fiscal Expenditure',       unit: 'EGP', category: 'fiscal',     dateField: 'budget_date',      tier: 3 },
  // ── الاستثمار والديون ─────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGFDI',  field: 'fdi_q_b',          label: 'FDI',                      unit: 'USD', category: 'investment',  dateField: 'fdi_date',         tier: 2 },
  { symbol: 'ECONOMICS:EGED',   field: 'external_debt_b',  label: 'External Debt',            unit: 'USD', category: 'investment',  dateField: 'ext_debt_date',    tier: 3 },
  // ── الطاقة والسياحة ───────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGCOP',  field: 'oil_production_kbd',label: 'Oil Production',          unit: 'kbd', category: 'energy',     dateField: 'oil_date',         tier: 3 },
  { symbol: 'ECONOMICS:EGTA',   field: 'tourist_arrivals_k',label: 'Tourist Arrivals',        unit: 'k',   category: 'tourism',    dateField: 'tour_date',        tier: 3 },
  // ── الإنفاق الحكومي ───────────────────────────────────────────────────
  { symbol: 'ECONOMICS:EGGSP',  field: 'govt_spending_m',  label: 'Govt Spending',            unit: 'USD', category: 'fiscal',     dateField: 'budget_date',      tier: 3 },
];

// ══════════════════════════════════════════════════════════════════════════
// Macro Regime Classifier
// ══════════════════════════════════════════════════════════════════════════
function classifyMacroRegime(snap) {
  const infl = snap.inflation_yoy;
  const cbe  = snap.cbe_rate;
  const gdp  = snap.gdp_yoy;
  const rr   = snap.real_interest_rate;
  const usd  = snap.usd_egp;
  const res  = snap.fx_reserves_b;

  // --- اتجاه التضخم (من الـhistory) ---
  const inflMom = snap.inflation_momentum ?? 'unknown';
  const rateMom = snap.rate_cycle         ?? 'unknown';

  // --- تصنيف الريجيم ---
  let regime = 'UNKNOWN';
  let score  = 50;   // نقطة البداية (محايد)
  let equityMult = 1.0;

  // MONETARY_SHOCK: رفع فائدة فجائي > 3% في خطوة واحدة
  if (cbe != null && snap._cbe_prev != null && (cbe - snap._cbe_prev) > 3) {
    regime = 'MONETARY_SHOCK';
    score  = 20;
    equityMult = 0.80;
  }
  // EGP_CRISIS: دولار > 65 أو ارتفاع >15% خلال 3 أشهر
  else if (usd != null && usd > 65) {
    regime = 'EGP_CRISIS';
    score  = 15;
    equityMult = 0.75;
  }
  // STAGFLATION_TIGHT: تضخم > 20% + GDP هابط أو بطيء
  else if (infl != null && infl > 20 && (gdp == null || gdp < 3)) {
    regime = 'STAGFLATION_TIGHT';
    score  = 25;
    equityMult = 0.85;
  }
  // HIGH_INFLATION: تضخم > 15% (بغض النظر عن باقي العوامل)
  else if (infl != null && infl > 15 && inflMom === 'rising') {
    regime = 'HIGH_INFLATION_RISING';
    score  = 35;
    equityMult = 0.90;
  }
  // DISINFLATION_EASING: تضخم هابط + فائدة هابطة ← أفضل بيئة للأسهم
  else if (inflMom === 'falling' && rateMom === 'falling' && gdp != null && gdp > 3) {
    regime = 'DISINFLATION_EASING';
    score  = 75;
    equityMult = 1.08;
  }
  // DISINFLATION_HOLD: تضخم هابط + فائدة ثابتة
  else if (inflMom === 'falling' && cbe != null && gdp != null && gdp > 2) {
    regime = 'DISINFLATION_HOLD';
    score  = 65;
    equityMult = 1.04;
  }
  // STABLE_GROWTH: تضخم معتدل (8-15%) + نمو جيد
  else if (infl != null && infl >= 8 && infl <= 15 && gdp != null && gdp >= 4) {
    regime = 'STABLE_GROWTH';
    score  = 60;
    equityMult = 1.02;
  }
  // REFLATION: تضخم هابط من قاع + نمو جيد
  else if (inflMom === 'rising' && infl != null && infl < 12 && gdp != null && gdp > 4) {
    regime = 'REFLATION';
    score  = 58;
    equityMult = 1.03;
  }
  // TIGHT_BUT_GROWING: فائدة حقيقية عالية (>5%) + نمو
  else if (rr != null && rr > 5 && gdp != null && gdp > 3) {
    regime = 'TIGHT_GROWING';
    score  = 45;
    equityMult = 0.96;
  }
  // DEFAULT: محايد
  else {
    regime = 'NEUTRAL';
    score  = 50;
    equityMult = 1.00;
  }

  // ── تأثيرات الاحتياطيات ──────────────────────────────────────────────
  if (res != null) {
    if (res < 20)       { score -= 10; equityMult -= 0.05; }  // خطر الاحتياطيات
    else if (res > 45)  { score +=  5; equityMult += 0.02; }  // احتياطيات قوية
  }

  // ── تأثير الفائدة الحقيقية ────────────────────────────────────────────
  let bias = 'NEUTRAL';
  if (rr != null) {
    if (rr < -5)       { bias = 'EQUITY_POSITIVE'; score += 5;  equityMult += 0.03; }
    else if (rr < 0)   { bias = 'EQUITY_SLIGHT_POSITIVE'; score += 2; }
    else if (rr > 8)   { bias = 'EQUITY_NEGATIVE'; score -= 8;  equityMult -= 0.04; }
    else if (rr > 4)   { bias = 'DEPOSITS_COMPETITIVE'; score -= 3; }
  }
  // تصدير مفيد للمصدرين
  if (usd != null && usd > 50 && infl != null && infl > 10) {
    bias += '+FAVOUR_EXPORTERS';
  }

  return {
    macro_regime:      regime,
    regime_score:      Math.max(0, Math.min(100, Math.round(score))),
    strategic_bias:    bias,
    equity_multiplier: parseFloat(equityMult.toFixed(3)),
  };
}

// ══════════════════════════════════════════════════════════════════════════
// جلب البيانات من TradingView عبر CDP
// ══════════════════════════════════════════════════════════════════════════
async function fetchFromTradingView() {
  try {
    const cdp = await import('chrome-remote-interface');
    const CDP = cdp.default ?? cdp;

    let client;
    try {
      client = await CDP({ host: 'localhost', port: 9222 });
    } catch (err) {
      console.log(`   ℹ️  CDP غير متاح: ${err.message}`);
      return null;
    }

    await client.Runtime.enable();
    await client.Page.enable();

    // حفظ الرمز الأصلي للرجوع إليه
    let originalUrl = null;
    try {
      const r = await client.Runtime.evaluate({ expression: 'window.location.href', returnByValue: true });
      originalUrl = r.result.value;
    } catch {}

    const results = {};
    let failCount = 0;

    for (const sym of ECONOMICS_SYMBOLS) {
      try {
        process.stdout.write(`   ⏳ ${sym.label.padEnd(30)}`);

        // التنقل إلى رمز الاقتصاد
        const encoded = encodeURIComponent(sym.symbol);
        await client.Page.navigate({
          url: `https://www.tradingview.com/chart/${CHART_ID}/?symbol=${encoded}`,
        });
        // انتظر تحميل البيانات (بيانات شهرية أبطأ من اليومية)
        const waitMs = sym.category === 'fx' ? 2500 : 4000;
        await new Promise(r => setTimeout(r, waitMs));

        // تحقق أن الرمز الصحيح مُحمَّل
        const symCheck = await client.Runtime.evaluate({
          expression: `(function(){ try{
            return window.TradingViewApi._activeChartWidgetWV.value().symbol();
          }catch(e){ return null; } })()`,
          returnByValue: true,
        });
        if (symCheck.result.value !== sym.symbol) {
          console.log(` ⚠️  لم يُحمَّل (${symCheck.result.value})`);
          failCount++;
          continue;
        }

        // قراءة آخر BARS_BACK bars
        const barsRes = await client.Runtime.evaluate({
          expression: `(function() {
            try {
              var bars = window.TradingViewApi._activeChartWidgetWV.value()
                         ._chartWidget.model().mainSeries().bars();
              if (!bars) return { error: 'no bars' };
              var end   = bars.lastIndex();
              var start = Math.max(bars.firstIndex(), end - ${BARS_BACK} + 1);
              var result = [];
              for (var i = start; i <= end; i++) {
                var v = bars.valueAt(i);
                if (v) result.push({ time: v[0], close: v[4] });
              }
              return { bars: result, total: bars.size() };
            } catch(e) { return { error: e.message }; }
          })()`,
          returnByValue: true,
        });

        const bData = barsRes.result.value;
        if (!bData || bData.error || !bData.bars?.length) {
          console.log(` ❌ لا بيانات (${bData?.error ?? 'empty'})`);
          failCount++;
          continue;
        }

        const lastBar = bData.bars[bData.bars.length - 1];
        const prevBar = bData.bars.length > 1 ? bData.bars[bData.bars.length - 2] : null;

        // تطبيع القيم حسب الوحدة
        let value = lastBar.close;
        if (sym.unit === 'USD B')  value = parseFloat((value / 1e9).toFixed(3));
        if (sym.unit === 'EGP T')  value = parseFloat((value / 1e12).toFixed(4));
        if (sym.unit === 'USD' && Math.abs(value) > 1e8)
                                   value = parseFloat((value / 1e9).toFixed(3));

        let prevValue = prevBar?.close ?? null;
        if (prevValue != null) {
          if (sym.unit === 'USD B')  prevValue = parseFloat((prevValue / 1e9).toFixed(3));
          if (sym.unit === 'EGP T')  prevValue = parseFloat((prevValue / 1e12).toFixed(4));
          if (sym.unit === 'USD' && Math.abs(prevValue) > 1e8)
                                     prevValue = parseFloat((prevValue / 1e9).toFixed(3));
        }

        results[sym.field] = {
          value,
          prev_value:  prevValue,
          change:      prevValue != null ? parseFloat((value - prevValue).toFixed(4)) : null,
          change_pct:  prevValue != null && prevValue !== 0
                       ? parseFloat(((value - prevValue) / Math.abs(prevValue) * 100).toFixed(2))
                       : null,
          date:        new Date(lastBar.time * 1000).toISOString().slice(0, 10),
          symbol:      sym.symbol,
          category:    sym.category,
          unit:        sym.unit,
          total_bars:  bData.total,
          trend_bars:  bData.bars.map(b => ({
            date:  new Date(b.time * 1000).toISOString().slice(0, 7),
            value: sym.unit === 'USD B'  ? parseFloat((b.close / 1e9).toFixed(3)) :
                   sym.unit === 'EGP T'  ? parseFloat((b.close / 1e12).toFixed(4)) :
                   sym.unit === 'USD' && Math.abs(b.close) > 1e8 ? parseFloat((b.close / 1e9).toFixed(3)) :
                   b.close,
          })),
        };

        const sign  = results[sym.field].change > 0 ? '+' : '';
        const chStr = results[sym.field].change != null
          ? `(${sign}${results[sym.field].change} ${sym.unit})` : '';
        console.log(` ✅ ${value} ${sym.unit}  ${chStr}`);

      } catch (e) {
        console.log(` ❌ ${e.message.slice(0, 60)}`);
        failCount++;
      }
    }

    // الرجوع إلى الرمز الأصلي
    if (originalUrl && Object.keys(results).length > 0) {
      try {
        await client.Page.navigate({ url: originalUrl });
        await new Promise(r => setTimeout(r, 2000));
      } catch {}
    }

    await client.close();

    console.log(`\n   📊 نجح: ${Object.keys(results).length} | فشل: ${failCount}`);
    return Object.keys(results).length > 0 ? results : null;

  } catch (err) {
    console.log(`   ℹ️  TradingView CDP: ${err.message}`);
    return null;
  }
}

// ══════════════════════════════════════════════════════════════════════════
// بناء الـ Snapshot الكامل
// ══════════════════════════════════════════════════════════════════════════
async function buildSnapshot(tvData) {
  const snap = {};

  // ── تعبئة الحقول من TradingView ─────────────────────────────────────
  for (const sym of ECONOMICS_SYMBOLS) {
    const tv = tvData?.[sym.field];
    if (tv) {
      snap[sym.field]          = tv.value;
      snap[sym.dateField]      = tv.date;
    }
  }

  // ── تطبيع الوحدات الكبيرة ──────────────────────────────────────────
  // fx_reserves: raw = $53B → نحافظ عليها بالـ B (مقسومة سابقاً)
  // m2_egp_t:    raw = 15T EGP → نحافظ عليها بالـ T

  // ── USD/EGP Fallback ─────────────────────────────────────────────────
  if (!snap.usd_egp) {
    for (const [url, name] of [
      ['https://open.er-api.com/v6/latest/USD', 'open.er-api.com'],
      ['https://api.exchangerate-api.com/v4/latest/USD', 'exchangerate-api.com'],
    ]) {
      try {
        const resp = await fetch(url, { signal: AbortSignal.timeout(10_000) });
        const data = await resp.json();
        const r = data.rates ?? data.conversion_rates ?? {};
        if (r.EGP) {
          snap.usd_egp      = parseFloat(r.EGP.toFixed(4));
          snap.usd_egp_date = (data.date ?? '').slice(0, 10);
          break;
        }
      } catch {}
    }
  }

  // ── مؤشرات مشتقة ────────────────────────────────────────────────────
  if (snap.cbe_rate != null && snap.inflation_yoy != null) {
    snap.real_interest_rate = parseFloat((snap.cbe_rate - snap.inflation_yoy).toFixed(2));
  }

  // اتجاه التضخم (آخر 3 نقاط)
  const inflTrend = tvData?.inflation_yoy?.trend_bars?.slice(-4).map(b => b.value) ?? [];
  if (inflTrend.length >= 3) {
    const last = inflTrend[inflTrend.length - 1];
    const prev = inflTrend[inflTrend.length - 2];
    const prev2 = inflTrend[inflTrend.length - 3];
    if (last > prev && prev >= prev2)      snap.inflation_momentum = 'rising';
    else if (last < prev && prev <= prev2) snap.inflation_momentum = 'falling';
    else if (last < prev)                  snap.inflation_momentum = 'falling';
    else                                   snap.inflation_momentum = 'stable';
  }

  // دورة الفائدة (آخر 3 نقاط)
  const cbeTrend = tvData?.cbe_rate?.trend_bars?.slice(-4).map(b => b.value) ?? [];
  if (cbeTrend.length >= 3) {
    const last = cbeTrend[cbeTrend.length - 1];
    const prev = cbeTrend[cbeTrend.length - 2];
    const prev2 = cbeTrend[cbeTrend.length - 3];
    if (last > prev && prev >= prev2)      snap.rate_cycle = 'rising';
    else if (last < prev && prev <= prev2) snap.rate_cycle = 'falling';
    else if (last < prev)                  snap.rate_cycle = 'falling';
    else                                   snap.rate_cycle = 'stable';
  }

  // اتجاه سعر الصرف
  const fxTrend = tvData?.usd_egp?.trend_bars?.slice(-5).map(b => b.value) ?? [];
  if (fxTrend.length >= 3) {
    const avg = fxTrend.reduce((s, v) => s + v, 0) / fxTrend.length;
    const last = fxTrend[fxTrend.length - 1];
    snap.fx_trend = last > avg * 1.02 ? 'depreciating' : last < avg * 0.98 ? 'appreciating' : 'stable';
  }

  // اتجاه النمو
  const gdpTrend = tvData?.gdp_yoy?.trend_bars?.slice(-3).map(b => b.value) ?? [];
  if (gdpTrend.length >= 2) {
    snap.growth_trend = gdpTrend[gdpTrend.length-1] > gdpTrend[gdpTrend.length-2]
      ? 'improving' : gdpTrend[gdpTrend.length-1] < gdpTrend[gdpTrend.length-2]
      ? 'slowing' : 'stable';
  }

  // ── Macro Regime ──────────────────────────────────────────────────────
  snap._cbe_prev = cbeTrend.length >= 2 ? cbeTrend[cbeTrend.length - 2] : null;
  const regime   = classifyMacroRegime(snap);
  delete snap._cbe_prev;

  return { ...snap, ...regime };
}

// ══════════════════════════════════════════════════════════════════════════
// حفظ في قاعدة البيانات
// ══════════════════════════════════════════════════════════════════════════
function saveToDB(tvData, snap) {
  const db        = getDB();
  const nowISO    = new Date().toISOString();
  const isTV      = !!tvData;

  // ── 1. macro_economics (time-series) ────────────────────────────────
  if (tvData) {
    const insertEcon = db.prepare(`
      INSERT INTO macro_economics (fetched_at, symbol, field_name, value, period_date, unit, category)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `);
    for (const sym of ECONOMICS_SYMBOLS) {
      const tv = tvData[sym.field];
      if (!tv) continue;
      insertEcon.run(nowISO, sym.symbol, sym.field, tv.value, tv.date, sym.unit, sym.category);
    }
  }

  // ── 2. macro_snapshot (full snapshot) ────────────────────────────────
  // تأكد من وجود الجدول (قد يكون المخطط القديم)
  db.prepare(`
    CREATE TABLE IF NOT EXISTS macro_snapshot (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      fetched_at TEXT NOT NULL, source TEXT,
      usd_egp REAL, usd_egp_date TEXT,
      inflation_yoy REAL, inflation_date TEXT,
      core_inflation REAL, core_infl_date TEXT,
      cbe_rate REAL, cbe_rate_date TEXT,
      real_interest_rate REAL,
      gdp_yoy REAL, gdp_date TEXT,
      unemployment REAL, unemp_date TEXT,
      fx_reserves_b REAL, fx_res_date TEXT,
      m2_egp_t REAL, m2_date TEXT,
      trade_balance_m REAL, trade_date TEXT,
      exports_m REAL, imports_m REAL,
      remittances_q REAL, rem_date TEXT,
      current_account_b REAL, ca_date TEXT,
      govt_debt_gdp REAL, debt_date TEXT,
      budget_balance_egp_t REAL, budget_date TEXT,
      govt_revenue_egp_t REAL, fiscal_exp_egp_t REAL,
      fdi_q_b REAL, fdi_date TEXT,
      external_debt_b REAL, ext_debt_date TEXT,
      oil_production_kbd REAL, oil_date TEXT,
      tourist_arrivals_k REAL, tour_date TEXT,
      govt_spending_m REAL,
      macro_regime TEXT, regime_score REAL,
      strategic_bias TEXT, equity_multiplier REAL,
      inflation_momentum TEXT, rate_cycle TEXT, fx_trend TEXT, growth_trend TEXT,
      raw_json TEXT
    )
  `).run();

  const macroSnapshotCols = [
    'fetched_at', 'source',
    'usd_egp', 'usd_egp_date',
    'inflation_yoy', 'inflation_date',
    'core_inflation', 'core_infl_date',
    'cbe_rate', 'cbe_rate_date',
    'real_interest_rate',
    'gdp_yoy', 'gdp_date',
    'unemployment', 'unemp_date',
    'fx_reserves_b', 'fx_res_date',
    'm2_egp_t', 'm2_date',
    'trade_balance_m', 'trade_date',
    'exports_m', 'imports_m',
    'remittances_q', 'rem_date',
    'current_account_b', 'ca_date',
    'govt_debt_gdp', 'debt_date',
    'budget_balance_egp_t', 'budget_date',
    'govt_revenue_egp_t', 'fiscal_exp_egp_t',
    'fdi_q_b', 'fdi_date',
    'external_debt_b', 'ext_debt_date',
    'oil_production_kbd', 'oil_date',
    'tourist_arrivals_k', 'tour_date',
    'govt_spending_m',
    'macro_regime', 'regime_score',
    'strategic_bias', 'equity_multiplier',
    'inflation_momentum', 'rate_cycle', 'fx_trend', 'growth_trend',
    'raw_json',
  ];
  const macroSnapshotVals = [
    nowISO, isTV ? 'tradingview_live' : 'fallback',
    snap.usd_egp,       snap.usd_egp_date,
    snap.inflation_yoy, snap.inflation_date,
    snap.core_inflation, snap.core_infl_date,
    snap.cbe_rate,      snap.cbe_rate_date,
    snap.real_interest_rate,
    snap.gdp_yoy,       snap.gdp_date,
    snap.unemployment,  snap.unemp_date,
    snap.fx_reserves_b, snap.fx_res_date,
    snap.m2_egp_t,      snap.m2_date,
    snap.trade_balance_m, snap.trade_date,
    snap.exports_m,     snap.imports_m,
    snap.remittances_q, snap.rem_date,
    snap.current_account_b, snap.ca_date,
    snap.govt_debt_gdp, snap.debt_date,
    snap.budget_balance_egp_t, snap.budget_date,
    snap.govt_revenue_egp_t, snap.fiscal_exp_egp_t,
    snap.fdi_q_b,       snap.fdi_date,
    snap.external_debt_b, snap.ext_debt_date,
    snap.oil_production_kbd, snap.oil_date,
    snap.tourist_arrivals_k, snap.tour_date,
    snap.govt_spending_m,
    snap.macro_regime,  snap.regime_score,
    snap.strategic_bias, snap.equity_multiplier,
    snap.inflation_momentum, snap.rate_cycle, snap.fx_trend, snap.growth_trend,
    JSON.stringify(snap),
  ];
  db.prepare(`
    INSERT INTO macro_snapshot (${macroSnapshotCols.join(', ')})
    VALUES (${macroSnapshotCols.map(() => '?').join(', ')})
  `).run(...macroSnapshotVals);

  // ── 3. macro_data (backward-compatible) ──────────────────────────────
  const existingCols = db.pragma('table_info(macro_data)').map(c => c.name);
  for (const col of ['cbe_rate', 'source']) {
    if (!existingCols.includes(col))
      db.prepare(`ALTER TABLE macro_data ADD COLUMN ${col} ${col === 'source' ? 'TEXT' : 'REAL'}`).run();
  }
  db.prepare(`
    INSERT INTO macro_data (fetched_at, usd_egp, inflation, lending_rate, cbe_rate, source, raw_json)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(
    nowISO,
    snap.usd_egp,
    snap.inflation_yoy,
    snap.cbe_rate,
    snap.cbe_rate,
    isTV ? 'tradingview_live' : 'fallback',
    JSON.stringify(snap),
  );
}

// ══════════════════════════════════════════════════════════════════════════
// عرض آخر Snapshot
// ══════════════════════════════════════════════════════════════════════════
function showStatus() {
  const db = getDB();
  try {
    const row = db.prepare(`
      SELECT * FROM macro_snapshot ORDER BY id DESC LIMIT 1
    `).get();
    if (!row) { console.log('لا توجد بيانات محفوظة بعد'); return; }
    printSnapshot(row, JSON.parse(row.raw_json ?? '{}'));
  } catch (e) {
    console.log(`لا يوجد جدول macro_snapshot بعد: ${e.message}`);
  }
}

// ══════════════════════════════════════════════════════════════════════════
// طباعة النتائج
// ══════════════════════════════════════════════════════════════════════════
function printSnapshot(snap, tvData) {
  const f  = (v, d = 2) => v != null ? (+v).toFixed(d) : 'N/A';
  const pc = (v)        => v != null ? `${v > 0 ? '+' : ''}${f(v)}%` : '';

  const REGIME_ICON = {
    DISINFLATION_EASING:  '🟢', DISINFLATION_HOLD: '🟩',
    STABLE_GROWTH:        '🟢', REFLATION:         '🟡',
    TIGHT_GROWING:        '🟡', HIGH_INFLATION_RISING: '🔴',
    STAGFLATION_TIGHT:    '🔴', MONETARY_SHOCK:    '💥',
    EGP_CRISIS:           '🆘', NEUTRAL:           '⚪',
    UNKNOWN:              '❓',
  };

  console.log('\n' + '═'.repeat(70));
  console.log('  🏛️  مؤشرات الاقتصاد الكلي المصري — Egypt Macro Dashboard');
  console.log('═'.repeat(70));

  const ri = REGIME_ICON[snap.macro_regime] ?? '⚪';
  console.log(`\n  ${ri}  Macro Regime: ${snap.macro_regime ?? 'UNKNOWN'}`);
  console.log(`  📊  Score: ${snap.regime_score ?? '?'}/100  |  Equity Multiplier: ${snap.equity_multiplier ?? 1.0}×`);
  console.log(`  ⚖️   Strategic Bias: ${snap.strategic_bias ?? '—'}`);

  console.log('\n  ── الأسعار والتضخم ─────────────────────────────────────────────');
  console.log(`  💵  USD/EGP:            ${f(snap.usd_egp, 4)}  (${snap.fx_trend ?? '—'}) [${snap.usd_egp_date ?? '?'}]`);
  console.log(`  📈  تضخم YoY:           ${f(snap.inflation_yoy)}%  (${snap.inflation_momentum ?? '—'}) [${snap.inflation_date?.slice(0,7) ?? '?'}]`);
  console.log(`  🎯  تضخم أساسي:         ${f(snap.core_inflation)}% [${snap.core_infl_date?.slice(0,7) ?? '?'}]`);

  console.log('\n  ── الفائدة والنقد ──────────────────────────────────────────────');
  console.log(`  🏦  سعر فائدة CBE:      ${f(snap.cbe_rate)}%  (${snap.rate_cycle ?? '—'}) [${snap.cbe_rate_date?.slice(0,7) ?? '?'}]`);
  if (snap.real_interest_rate != null) {
    const rr   = snap.real_interest_rate;
    const rrIc = rr < 0 ? '🟢' : rr < 4 ? '🟡' : '🔴';
    console.log(`  ${rrIc}  فائدة حقيقية:       ${f(rr)}%  (${rr < 0 ? 'أسهم أفضل من ودائع' : rr > 5 ? 'ودائع جذابة' : 'تنافس معتدل'})`);
  }

  console.log('\n  ── النمو والعمل ────────────────────────────────────────────────');
  console.log(`  📊  GDP YoY:            ${f(snap.gdp_yoy)}%  (${snap.growth_trend ?? '—'}) [${snap.gdp_date?.slice(0,7) ?? '?'}]`);
  console.log(`  👥  البطالة:            ${f(snap.unemployment)}% [${snap.unemp_date?.slice(0,7) ?? '?'}]`);

  console.log('\n  ── الاحتياطيات والسيولة ────────────────────────────────────────');
  console.log(`  🏦  احتياطيات أجنبية:   $${f(snap.fx_reserves_b, 1)}B [${snap.fx_res_date?.slice(0,7) ?? '?'}]`);
  console.log(`  💰  عرض النقود M2:       EGP ${f(snap.m2_egp_t, 2)}T [${snap.m2_date?.slice(0,7) ?? '?'}]`);

  console.log('\n  ── التجارة الخارجية ────────────────────────────────────────────');
  console.log(`  📦  الميزان التجاري:    $${f(snap.trade_balance_m, 2)}B/mo [${snap.trade_date?.slice(0,7) ?? '?'}]`);
  console.log(`  📤  صادرات:             $${f(snap.exports_m, 2)}B/mo`);
  console.log(`  📥  واردات:             $${f(snap.imports_m, 2)}B/mo`);
  console.log(`  💸  تحويلات:            $${f(snap.remittances_q, 2)}B [${snap.rem_date?.slice(0,7) ?? '?'}]`);

  console.log('\n  ── المالية العامة ──────────────────────────────────────────────');
  console.log(`  🏛️   الدين/GDP:          ${f(snap.govt_debt_gdp)}% [${snap.debt_date?.slice(0,7) ?? '?'}]`);
  console.log(`  💳  الدين الخارجي:      $${f(snap.external_debt_b, 1)}B [${snap.ext_debt_date?.slice(0,7) ?? '?'}]`);
  console.log(`  🏗️   FDI:               $${f(snap.fdi_q_b, 2)}B/qtr [${snap.fdi_date?.slice(0,7) ?? '?'}]`);

  // اتجاه التضخم
  const inflTrend = tvData?.inflation_yoy?.trend_bars ?? snap.inflation_trend_bars;
  if (inflTrend?.length) {
    console.log('\n  ── اتجاه التضخم (آخر 12 نقطة) ──────────────────────────────');
    for (const b of inflTrend.slice(-12)) {
      const bar = '█'.repeat(Math.round(b.value / 3));
      console.log(`     ${b.date}: ${b.value?.toFixed(1).padStart(5)}%  ${bar}`);
    }
  }

  // اتجاه الفائدة CBE
  const cbeTrend = tvData?.cbe_rate?.trend_bars ?? snap.cbe_trend_bars;
  if (cbeTrend?.length) {
    console.log('\n  ── اتجاه فائدة CBE (آخر 12 نقطة) ───────────────────────────');
    for (const b of cbeTrend.slice(-12)) {
      const bar = '█'.repeat(Math.round(b.value / 3));
      console.log(`     ${b.date}: ${b.value?.toFixed(2).padStart(6)}%  ${bar}`);
    }
  }

  console.log(`\n  📅 جُلب: ${snap.fetched_at?.slice(0, 16) ?? '—'}`);
  console.log('═'.repeat(70) + '\n');
}

// ══════════════════════════════════════════════════════════════════════════
// Main
// ══════════════════════════════════════════════════════════════════════════
async function main() {
  console.log('\n' + '═'.repeat(70));
  console.log('  🏛️  EGX Economics — جلب الاقتصاد الكلي المصري الكامل');
  console.log('═'.repeat(70));

  if (STATUS) {
    showStatus();
    return;
  }

  // ── Auto-launch TradingView إن كان مغلقاً ──────────────────────────────
  if (!OFFLINE) {
    await ensureTradingView({ log: msg => console.log('  ' + msg) });
  }

  let tvData = null;

  if (!OFFLINE) {
    console.log(`\n[1/3] جلب ${ECONOMICS_SYMBOLS.length} مؤشر من TradingView (CDP)...`);
    tvData = await fetchFromTradingView();
    if (!tvData) {
      console.log('   ℹ️  TradingView غير متصل — سنبني snapshot من DB + fallback');
    }
  } else {
    console.log('\n[1/3] --offline: تخطي TradingView');
  }

  console.log('\n[2/3] بناء Snapshot + Macro Regime...');
  const snap = await buildSnapshot(tvData);

  console.log('\n[3/3] حفظ في قاعدة البيانات...');
  saveToDB(tvData, snap);
  console.log(`   ✅ macro_economics, macro_snapshot, macro_data — محفوظ`);

  printSnapshot(snap, tvData ?? {});

  // Telegram
  if (NOTIFY && isTelegramConfigured()) {
    const REGIME_ICON = { DISINFLATION_EASING: '🟢', STABLE_GROWTH: '🟢', REFLATION: '🟡',
      STAGFLATION_TIGHT: '🔴', EGP_CRISIS: '🆘', NEUTRAL: '⚪' };
    const ri = REGIME_ICON[snap.macro_regime] ?? '⚪';
    await sendMacroUpdate({
      usdEgp:    snap.usd_egp,
      inflation: snap.inflation_yoy,
      cbeRate:   snap.cbe_rate,
      notes: `${ri} ${snap.macro_regime} | Score=${snap.regime_score} | EqMult=${snap.equity_multiplier}× | RealRate=${snap.real_interest_rate?.toFixed(1)}%`,
    });
    console.log('   📤  Telegram: أُرسل');
  }
}

main().catch(err => {
  console.error('💥', err.message);
  process.exit(1);
});
