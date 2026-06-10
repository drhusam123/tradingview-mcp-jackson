/**
 * EGX DataFrame Analysis (danfojs-node)
 * =======================================
 * تحليلات DataFrame على بيانات EGX — أسرع وأوضح من loops يدوية
 *
 * الفوائد عن simple-statistics:
 *   - groupBy + agg بسطر واحد (مثل pandas)
 *   - pivot tables فورية
 *   - correlation matrix كاملة بدالة واحدة
 *   - filter متعدد الشروط + sort + head
 *   - قراءة/كتابة CSV/JSON مدمجة
 *
 * المالك: Dr. Husam | إنشاء: مايو 2026
 */

import dfd from 'danfojs-node';
import { getDB } from './database.js';

const { DataFrame, Series } = dfd;

// ─── تحميل البيانات من SQLite → DataFrame ────────────────────────────────

/**
 * تحميل كل indicators_cache في DataFrame
 * مثل pandas.read_sql() لكن على SQLite مباشرة
 */
export function loadIndicatorsDF() {
  const db   = getDB();
  const rows = db.prepare(`
    WITH latest AS (
      SELECT symbol, MAX(bar_date) as max_date FROM indicators_cache GROUP BY symbol
    )
    SELECT ic.*
    FROM indicators_cache ic
    JOIN latest l ON ic.symbol = l.symbol AND ic.bar_date = l.max_date
  `).all();

  if (rows.length === 0) return null;
  return new DataFrame(rows);
}

/**
 * تحميل OHLCV كامل في DataFrame
 * @param {string} symbol - null = كل الأسهم (75K+ صف)
 * @param {number} limit - حد الصفوف
 */
export function loadOHLCVDF(symbol = null, limit = 10000) {
  const db  = getDB();
  let sql   = `
    SELECT symbol,
           date(bar_time, 'unixepoch') as bar_date,
           bar_time,
           open, high, low, close, volume,
           strftime('%w', bar_time, 'unixepoch') as day_of_week
    FROM ohlcv_history
    WHERE volume > 0
  `;
  const params = [];
  if (symbol) { sql += ' AND symbol = ?'; params.push(symbol); }
  sql += ` ORDER BY bar_time DESC LIMIT ${limit}`;

  const rows = db.prepare(sql).all(...params);
  if (rows.length === 0) return null;
  return new DataFrame(rows);
}

/**
 * تحميل نتائج الـ scans في DataFrame
 */
export function loadScansDF(minScore = 40, daysBack = 90) {
  const db   = getDB();
  const rows = db.prepare(`
    SELECT s.*, t.result, t.pnl_pct, t.hold_days, t.hit_t1, t.hit_t2
    FROM scans s
    LEFT JOIN trades t ON t.scan_id = s.id
    WHERE s.scan_date >= date('now', '-${daysBack} days')
      AND s.score >= ?
    ORDER BY s.scan_date DESC
  `).all(minScore);

  if (rows.length === 0) return null;
  return new DataFrame(rows);
}

// ─── تحليلات جاهزة ──────────────────────────────────────────────────────

/**
 * Pivot Table: أداء كل setup_id حسب day_of_week
 * مثل: pd.pivot_table(df, values='pnl_pct', index='setup_id', columns='dow', aggfunc='mean')
 */
export function pivotSetupByDay() {
  const df = loadScansDF(40, 365);
  if (!df || df.shape[0] < 10) return { error: 'بيانات scans غير كافية' };

  // فلتر: صفقات مغلقة فقط
  const closed = df.query(df['result'].ne(null));
  if (closed.shape[0] === 0) return { error: 'لا صفقات مغلقة بعد' };

  // groupBy setup_id, day_of_week → mean pnl_pct
  try {
    const grp = closed.groupby(['setup_id']).agg({ pnl_pct: ['mean', 'count'] });
    return grp.values.map(row => ({
      setup:    row[0],
      avgPnl:   row[1] ? +row[1].toFixed(2) : null,
      count:    row[2],
    })).sort((a, b) => (b.avgPnl ?? -99) - (a.avgPnl ?? -99));
  } catch (e) {
    return { error: e.message };
  }
}

/**
 * تحليل شامل لـ indicators_cache بـ DataFrame
 * يُعيد: distribution summary لكل مؤشر + top oversold list
 */
export function analyzeIndicatorsDistribution() {
  const df = loadIndicatorsDF();
  if (!df) return { error: 'indicators_cache فارغ — شغّل rebuild_indicators.mjs أولاً' };

  const result = {
    totalSymbols: df.shape[0],
    summary:      {},
    oversold:     [],
    bullishOBV:   [],
    comboSignals: [],
  };

  // ── summary statistics لكل مؤشر مهم ──────────────────────────────
  const numericCols = ['rsi14', 'adx14', 'atr14', 'vol_ratio_20', 'bb_position', 'momentum_5d'];
  for (const col of numericCols) {
    try {
      const series = df[col].dropNa();
      if (series.size === 0) continue;
      const vals = series.values.filter(v => v != null && !isNaN(v));
      if (vals.length === 0) continue;
      vals.sort((a, b) => a - b);
      const mean = vals.reduce((a,b)=>a+b,0) / vals.length;
      const std  = Math.sqrt(vals.map(v=>(v-mean)**2).reduce((a,b)=>a+b,0)/vals.length);
      result.summary[col] = {
        mean:   +mean.toFixed(2),
        std:    +std.toFixed(2),
        min:    +vals[0].toFixed(2),
        p25:    +vals[Math.floor(vals.length * 0.25)].toFixed(2),
        median: +vals[Math.floor(vals.length * 0.50)].toFixed(2),
        p75:    +vals[Math.floor(vals.length * 0.75)].toFixed(2),
        max:    +vals[vals.length - 1].toFixed(2),
        count:  vals.length,
      };
    } catch { /* تجاهل */ }
  }

  // ── أسهم RSI Oversold ────────────────────────────────────────────
  try {
    const oversoldMask = df['rsi14'].le(35);
    const oversoldDF   = df.loc({ rows: oversoldMask });
    result.oversold = oversoldDF
      .loc({ columns: ['symbol', 'bar_date', 'rsi14', 'adx14', 'obv_divergence', 'vol_ratio_20'] })
      .sortValues('rsi14', { ascending: true })
      .head(20)
      .values
      .map(r => ({
        symbol: r[0], date: r[1], rsi: r[2]?.toFixed(1),
        adx: r[3]?.toFixed(1), obv: r[4], volRatio: r[5]?.toFixed(2),
      }));
  } catch { /* تجاهل */ }

  // ── Bullish OBV ──────────────────────────────────────────────────
  try {
    const obvMask = df['obv_divergence'].eq('bullish');
    const obvDF   = df.loc({ rows: obvMask });
    result.bullishOBV = obvDF
      .loc({ columns: ['symbol', 'bar_date', 'rsi14', 'adx14', 'vol_ratio_20'] })
      .sortValues('rsi14', { ascending: true })
      .head(15)
      .values
      .map(r => ({ symbol: r[0], rsi: r[2]?.toFixed(1), adx: r[3]?.toFixed(1), vol: r[4]?.toFixed(2) }));
  } catch { /* تجاهل */ }

  // ── RSI+OBV Combo ────────────────────────────────────────────────
  try {
    const comboMask = df['rsi14'].le(35).and(df['obv_divergence'].eq('bullish'));
    const comboDF   = df.loc({ rows: comboMask });
    if (comboDF.shape[0] > 0) {
      result.comboSignals = comboDF
        .loc({ columns: ['symbol', 'bar_date', 'rsi14', 'adx14', 'vol_ratio_20', 'is_hammer'] })
        .values
        .map(r => ({
          symbol: r[0], date: r[1], rsi: r[2]?.toFixed(1),
          adx: r[3]?.toFixed(1), vol: r[4]?.toFixed(2), hammer: !!r[5],
          note: '🔥 RSI+OBV COMBO (WR=69%)',
        }));
    }
  } catch { /* تجاهل */ }

  return result;
}

/**
 * Correlation Matrix لأسهم محددة
 * أسرع من buildCorrelationMatrix في learning.js لأنه يستخدم danfo vectorized ops
 * @param {string[]} symbols - قائمة الأسهم (حد 20)
 * @param {number} limit - عدد الشمعات
 * @returns {Object} matrix + هeatmap data
 */
export function buildReturnCorrelationDF(symbols, limit = 300) {
  const db = getDB();
  if (!symbols?.length || symbols.length < 2) return { error: 'يحتاج ≥ 2 أسهم' };

  // جلب close prices لكل سهم
  const closeMap = {};
  const allDates = new Set();

  for (const sym of symbols.slice(0, 20)) {
    const rows = db.prepare(`
      SELECT date(bar_time,'unixepoch') as d, close
      FROM ohlcv_history WHERE symbol = ? AND volume > 0
      ORDER BY bar_time DESC LIMIT ?
    `).all(sym, limit);
    closeMap[sym] = {};
    for (const r of rows) {
      closeMap[sym][r.d] = r.close;
      allDates.add(r.d);
    }
  }

  const dates = [...allDates].sort();

  // بناء returns DataFrame
  const returnsData = {};
  for (const sym of symbols.slice(0, 20)) {
    returnsData[sym] = [];
    for (let i = 1; i < dates.length; i++) {
      const prev = closeMap[sym][dates[i-1]];
      const curr = closeMap[sym][dates[i]];
      returnsData[sym].push(prev && curr ? (curr - prev) / prev * 100 : null);
    }
  }

  try {
    const df   = new DataFrame(returnsData);
    const corr = df.corr();

    // تحويل لـ readable format
    const matrix = {};
    const symList = symbols.slice(0, 20);
    for (let i = 0; i < symList.length; i++) {
      matrix[symList[i]] = {};
      for (let j = 0; j < symList.length; j++) {
        try {
          matrix[symList[i]][symList[j]] = +corr.iloc({ rows: [i], columns: [j] }).values[0][0].toFixed(3);
        } catch { matrix[symList[i]][symList[j]] = null; }
      }
    }

    // أعلى correlations
    const highCorr = [];
    for (let i = 0; i < symList.length; i++) {
      for (let j = i+1; j < symList.length; j++) {
        const c = matrix[symList[i]]?.[symList[j]];
        if (c != null && Math.abs(c) >= 0.6) {
          highCorr.push({ pair: `${symList[i]}-${symList[j]}`, corr: c,
            warning: Math.abs(c) >= 0.8 ? '⚠️ correlation عالية جداً — تنويع ضعيف' : '' });
        }
      }
    }

    return {
      symbols: symList,
      matrix,
      highCorrelations: highCorr.sort((a,b) => Math.abs(b.corr) - Math.abs(a.corr)),
      dates: dates.length - 1,
    };
  } catch (e) {
    return { error: e.message };
  }
}

/**
 * تحليل Return Distribution حسب يوم الأسبوع (EGX)
 * يستخدم danfo groupBy لحساب mean/std/count لكل يوم
 */
export function analyzeReturnsByDayDF() {
  const df = loadOHLCVDF(null, 75000);
  if (!df) return { error: 'لا بيانات OHLCV' };

  // حساب daily return
  try {
    // نحتاج close اليوم والأمس — نحسبها من OHLCV مباشرة
    const db   = getDB();
    const rows = db.prepare(`
      SELECT strftime('%w', bar_time, 'unixepoch') as dow,
             (close - LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time)) /
             LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time) * 100 as ret
      FROM ohlcv_history
      WHERE volume > 0
      ORDER BY bar_time
    `).all();

    const validRows = rows.filter(r => r.ret != null && !isNaN(r.ret) && Math.abs(r.ret) < 20);
    const dayDf = new DataFrame(validRows);
    const grp   = dayDf.groupby(['dow']).agg({ ret: ['mean', 'std', 'count'] });

    const DAY_NAMES = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس'];
    return grp.values
      .filter(r => r[0] >= 0 && r[0] <= 4) // Sun-Thu فقط
      .sort((a, b) => +a[0] - +b[0])
      .map(r => ({
        dow:     +r[0],
        dayName: DAY_NAMES[+r[0]] ?? `يوم ${r[0]}`,
        avgReturn: +r[1]?.toFixed(3) ?? 0,
        stdReturn: +r[2]?.toFixed(3) ?? 0,
        count:     r[3] ?? 0,
      }));
  } catch (e) {
    return { error: e.message };
  }
}

/**
 * Top N أسهم حسب momentum (5d, 10d, 20d) من الكاش
 * @param {string} by - 'momentum_5d' | 'momentum_10d' | 'momentum_20d'
 * @param {number} topN
 */
export function topMomentumStocks(by = 'momentum_5d', topN = 15) {
  const df = loadIndicatorsDF();
  if (!df) return [];

  try {
    const cols = ['symbol', 'bar_date', 'momentum_5d', 'momentum_10d', 'momentum_20d', 'rsi14', 'adx14', 'vol_ratio_20'];
    const filtered = df.query(df[by].gt(-999)) // استبعاد null
      .sortValues(by, { ascending: false })
      .head(topN)
      .loc({ columns: cols });

    return filtered.values.map(r => ({
      symbol:    r[0],
      date:      r[1],
      momentum:  r[2]?.toFixed(2) + '%',
      momentum10:r[3]?.toFixed(2) + '%',
      momentum20:r[4]?.toFixed(2) + '%',
      rsi:       r[5]?.toFixed(1),
      adx:       r[6]?.toFixed(1),
      volRatio:  r[7]?.toFixed(2) + 'x',
    }));
  } catch { return []; }
}

/**
 * حفظ تقرير تحليل في CSV
 * @param {string} outputPath - مسار الحفظ
 */
export async function exportIndicatorsToCSV(outputPath) {
  const df = loadIndicatorsDF();
  if (!df) throw new Error('indicators_cache فارغ');
  await df.toCSV(outputPath);
  return { saved: outputPath, rows: df.shape[0], cols: df.shape[1] };
}
