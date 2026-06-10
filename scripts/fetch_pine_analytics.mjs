#!/usr/bin/env node
/**
 * Phase 53 — Pine Analytics Fetcher
 * ====================================
 * يُشغّل Pine Scripts على TradingView ويقرأ النتائج عبر MCP
 * ثم يحفظها في pine_analytics لكل سهم
 *
 * يعمل بعد تحميل أحد الـ Pine Scripts التالية على الشارت:
 *   - "EGX MCP Exporter"        (preferred fixed-key exporter)
 *   - "EGX Volume Profile"
 *   - "EGX Session Analytics"
 *   - "EGX Relative Strength"
 *
 * Usage:
 *   node scripts/fetch_pine_analytics.mjs volume     -- Volume Profile
 *   node scripts/fetch_pine_analytics.mjs session    -- Session Analytics
 *   node scripts/fetch_pine_analytics.mjs rs         -- Relative Strength
 *   node scripts/fetch_pine_analytics.mjs all        -- كل الـ scripts (default)
 *   node scripts/fetch_pine_analytics.mjs all --symbol COMI
 *   node scripts/fetch_pine_analytics.mjs all --max-symbols 30
 *   node scripts/fetch_pine_analytics.mjs all --local-fallback --all-symbols
 */

import { setSymbol, setTimeframe }           from '../src/core/chart.js';
import { getPineLabels, getPineTables,
         getStudyValues, getOhlcv }           from '../src/core/data.js';
import { getDB, savePineAnalytics, EGX_UNIVERSE } from '../src/egx/index.js';
import { toTvSymbol }                         from '../src/egx/tv_symbols.js';

const args    = process.argv.slice(2);
const MODE    = args.find(a => !a.startsWith('--')) ?? 'all';
const SINGLE  = (() => { const i = args.indexOf('--symbol'); return i >= 0 ? args[i+1] : null; })();
const SYMBOLS = (() => {
  const i = args.indexOf('--symbols');
  if (i < 0 || !args[i + 1]) return null;
  return args[i + 1].split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
})();
const MAX_SYM = (() => { const i = args.indexOf('--max-symbols'); return i >= 0 ? Math.max(1, +args[i+1] || 30) : null; })();
const ALL_FLAG = args.includes('--all-symbols');
const LOCAL_FALLBACK = args.includes('--local-fallback') || args.includes('--local-only');
const LOCAL_ONLY = args.includes('--local-only');
const DELAY   = process.env.DELAY_MS ? +process.env.DELAY_MS : (LOCAL_ONLY ? 0 : 2000);

const db = getDB();
function loadTopSymbols(limit) {
  const rows = db.prepare(`
    SELECT DISTINCT symbol, MAX(score) as score
    FROM scans
    WHERE scan_date=(SELECT MAX(scan_date) FROM scans) AND rejected=0
    GROUP BY symbol
    ORDER BY score DESC
    LIMIT ?
  `).all(limit);
  return rows.map(r => r.symbol);
}

function loadUniverseSymbols() {
  try {
    const rows = db.prepare(`
      SELECT symbol FROM stock_universe
      WHERE COALESCE(status, 'fetched') IN ('fetched','active','ok')
      ORDER BY symbol
    `).all();
    if (rows.length) return rows.map(r => r.symbol);
  } catch { /* fallback below */ }
  return [...new Set(EGX_UNIVERSE)];
}

const ALL_SYMBOLS = SINGLE
  ? [SINGLE]
  : (SYMBOLS?.length
    ? [...new Set(SYMBOLS)]
    : (ALL_FLAG || LOCAL_FALLBACK ? loadUniverseSymbols() : (MAX_SYM ? loadTopSymbols(MAX_SYM) : [...new Set(EGX_UNIVERSE)])));

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function toNum(v) {
  if (v == null) return null;
  const n = Number(String(v).replace(/[,%]/g, '').trim());
  return Number.isFinite(n) ? n : null;
}

function normalizeTableRows(study) {
  const rows = study?.rows ?? study?.tables?.flatMap(t => t.rows ?? []) ?? [];
  return rows.map(row => {
    if (Array.isArray(row)) return row.map(x => String(x ?? '').trim());
    if (row?.cells) return row.cells.map(x => String(x?.text ?? x ?? '').trim());
    return [String(row ?? '').trim()];
  });
}

function parseMcpExporter(tables) {
  for (const study of (tables?.studies ?? [])) {
    const name = String(study.name ?? '').toUpperCase();
    if (!name.includes('EGX MCP EXPORTER')) continue;
    const kv = {};
    for (const row of normalizeTableRows(study)) {
      if (row.length < 2) continue;
      const key = String(row[0] ?? '').trim().toUpperCase();
      const val = String(row[1] ?? '').trim();
      if (key) kv[key] = val;
    }
    if (kv.EXPORTER !== 'EGX_MCP_EXPORTER_V1') continue;
    return {
      volume_poc: null,
      volume_vah: null,
      volume_val: null,
      vwap: toNum(kv.VWAP),
      opening_range_high: toNum(kv.OR_HIGH),
      opening_range_low: toNum(kv.OR_LOW),
      session_bias: kv.SESSION_BIAS || null,
      rs_score: toNum(kv.RS_SCORE),
      rs_percentile: toNum(kv.RS_PERCENTILE),
      raw_pine_data: {
        exporter: kv.EXPORTER,
        symbol: kv.SYMBOL,
        close: toNum(kv.CLOSE),
        vol_ratio: toNum(kv.VOL_RATIO),
        close_position: toNum(kv.CLOSE_POSITION),
        atr_pct: toNum(kv.ATR_PCT),
        trend_score: toNum(kv.TREND_SCORE),
        squeeze: toNum(kv.SQUEEZE),
        keys: kv,
      },
      source_script: 'egx_mcp_exporter_v1',
    };
  }
  return null;
}

function ema(values, len) {
  const k = 2 / (len + 1);
  let out = values[0];
  for (const v of values.slice(1)) out = v * k + out * (1 - k);
  return out;
}

function sma(values) {
  const vals = values.filter(v => Number.isFinite(v));
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

function rsi(values, len = 14) {
  if (values.length <= len) return null;
  let gains = 0, losses = 0;
  for (let i = values.length - len; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    if (d >= 0) gains += d;
    else losses -= d;
  }
  if (losses === 0) return 100;
  const rs = gains / losses;
  return 100 - (100 / (1 + rs));
}

function atr(bars, len = 14) {
  if (bars.length <= len) return null;
  const trs = [];
  for (let i = bars.length - len; i < bars.length; i++) {
    const b = bars[i];
    const p = bars[i - 1];
    trs.push(Math.max(
      Number(b.high) - Number(b.low),
      Math.abs(Number(b.high) - Number(p.close)),
      Math.abs(Number(b.low) - Number(p.close)),
    ));
  }
  return sma(trs);
}

function computeExporterFromBars(symbol, bars) {
  if (!Array.isArray(bars) || bars.length < 60) return null;
  const closes = bars.map(b => Number(b.close)).filter(Number.isFinite);
  const vols = bars.map(b => Number(b.volume ?? 0));
  const last = bars[bars.length - 1];
  const close = Number(last.close);
  const ema21 = ema(closes.slice(-80), 21);
  const ema55 = ema(closes.slice(-120), 55);
  const rsi14 = rsi(closes, 14);
  const volAvg = sma(vols.slice(-21, -1));
  const volRatio = volAvg && volAvg > 0 ? Number(last.volume ?? 0) / volAvg : null;
  const recent = bars.slice(-20);
  const rangeHigh = Math.max(...recent.map(b => Number(b.high)));
  const rangeLow = Math.min(...recent.map(b => Number(b.low)));
  const closePosition = (rangeHigh > rangeLow) ? (close - rangeLow) / (rangeHigh - rangeLow) : 0.5;
  const atr14 = atr(bars, 14);
  const atrPct = atr14 && close > 0 ? atr14 / close * 100 : null;
  const trendScore = ema55 ? (ema21 / ema55 - 1) * 100 : null;
  const sessionBias = close >= ema21 && ema21 >= ema55 ? 'ABOVE_VWAP' : close <= ema21 && ema21 <= ema55 ? 'BELOW_VWAP' : 'AT_VWAP';
  return {
    volume_poc: null,
    volume_vah: null,
    volume_val: null,
    vwap: ema21,
    opening_range_high: rangeHigh,
    opening_range_low: rangeLow,
    session_bias: sessionBias,
    rs_score: rsi14,
    rs_percentile: rsi14 == null ? null : Math.max(0, Math.min(100, rsi14)),
    raw_pine_data: {
      exporter: 'EGX_MCP_EXPORTER_V1_TV_OHLCV_FALLBACK',
      symbol,
      close,
      vol_ratio: volRatio,
      close_position: closePosition,
      atr_pct: atrPct,
      trend_score: trendScore,
      source: 'TradingView data_get_ohlcv',
    },
    source_script: 'egx_mcp_exporter_tv_ohlcv_fallback',
  };
}

function localBars(symbol, count = 140) {
  const rows = db.prepare(`
    SELECT symbol, bar_time, open, high, low, close, volume
    FROM ohlcv_history_execution
    WHERE symbol=?
    ORDER BY bar_time DESC
    LIMIT ?
  `).all(symbol, count);
  return rows.reverse();
}

function localTradeDate(bars) {
  const ts = bars?.[bars.length - 1]?.bar_time;
  if (!ts) return new Date().toISOString().split('T')[0];
  return new Date(Number(ts) * 1000).toISOString().split('T')[0];
}

// ─── Volume Profile Parser ────────────────────────────────────────────────────
function parseVolumeProfile(tables) {
  // Look for a table with POC/VAH/VAL data
  for (const study of (tables?.studies ?? [])) {
    const rows = study.rows ?? [];
    let poc = null, vah = null, val = null;
    for (const row of rows) {
      const text = (row.join ? row.join(' ') : String(row)).toUpperCase();
      if (text.includes('POC')) {
        const m = text.match(/[\d.]+/);
        if (m) poc = parseFloat(m[0]);
      }
      if (text.includes('VAH')) {
        const m = text.match(/[\d.]+/);
        if (m) vah = parseFloat(m[0]);
      }
      if (text.includes('VAL')) {
        const m = text.match(/[\d.]+/);
        if (m) val = parseFloat(m[0]);
      }
    }
    if (poc || vah || val) return { volume_poc: poc, volume_vah: vah, volume_val: val };
  }
  // Fallback: check labels
  return null;
}

// ─── Session Analytics Parser ────────────────────────────────────────────────
function parseSessionAnalytics(labels, studyValues) {
  let vwap = null, orHigh = null, orLow = null, sessionBias = null;
  for (const study of (labels?.studies ?? [])) {
    for (const lbl of (study.labels ?? [])) {
      const txt = String(lbl.text ?? '').toUpperCase();
      const price = lbl.price ?? null;
      if (txt.includes('VWAP') && price) vwap = price;
      if (txt.includes('OR HIGH') && price) orHigh = price;
      if (txt.includes('OR LOW') && price) orLow = price;
      if (txt.includes('BIAS')) {
        sessionBias = txt.includes('BULL') ? 'ABOVE_VWAP' : txt.includes('BEAR') ? 'BELOW_VWAP' : 'AT_VWAP';
      }
    }
  }
  // Also try from study values
  if (studyValues?.values) {
    for (const [k, v] of Object.entries(studyValues.values)) {
      if (k.toLowerCase().includes('vwap') && !vwap) vwap = v;
    }
  }
  return (vwap || orHigh) ? { vwap, opening_range_high: orHigh, opening_range_low: orLow, session_bias: sessionBias } : null;
}

// ─── Relative Strength Parser ────────────────────────────────────────────────
function parseRelativeStrength(tables, labels) {
  let rsScore = null, rsPerc = null;
  for (const study of (labels?.studies ?? tables?.studies ?? [])) {
    const rows = study.rows ?? study.labels ?? [];
    for (const row of rows) {
      const txt = String(row.text ?? (row.join ? row.join(' ') : row)).toUpperCase();
      if (txt.includes('RS') || txt.includes('STRENGTH')) {
        const nums = txt.match(/[-\d.]+/g);
        if (nums) rsScore = parseFloat(nums[0]);
        if (nums?.[1]) rsPerc = parseFloat(nums[1]);
      }
    }
  }
  return rsScore !== null ? { rs_score: rsScore, rs_percentile: rsPerc } : null;
}

async function fetchForSymbol(symbol, mode) {
  if (LOCAL_ONLY) {
    const bars = localBars(symbol);
    return computeExporterFromBars(symbol, bars);
  }

  try {
    await setSymbol({ symbol: toTvSymbol(symbol) });
    await sleep(700);
    await setTimeframe({ timeframe: 'D' });
    await sleep(500);
  } catch (e) {
    if (LOCAL_FALLBACK) {
      const fallback = computeExporterFromBars(symbol, localBars(symbol));
      return fallback ? { ...fallback, source_script: 'egx_mcp_exporter_local_ohlcv_fallback' } : null;
    }
    throw e;
  }

  const data = {};

  if (mode === 'exporter' || mode === 'all') {
    try {
      const tables = await getPineTables({ study_filter: 'EGX MCP Exporter' });
      const parsed = parseMcpExporter(tables);
      if (parsed) Object.assign(data, parsed);
    } catch { /* exporter may not be loaded */ }
    if (!data.source_script) {
      try {
        const ohlcv = await getOhlcv({ count: 120 });
        const fallback = computeExporterFromBars(symbol, ohlcv?.bars ?? []);
        if (fallback) Object.assign(data, fallback);
      } catch {
        if (LOCAL_FALLBACK) {
          const fallback = computeExporterFromBars(symbol, localBars(symbol));
          if (fallback) Object.assign(data, { ...fallback, source_script: 'egx_mcp_exporter_local_ohlcv_fallback' });
        }
      }
    }
  }

  if (mode === 'volume' || mode === 'all') {
    try {
      const tables = await getPineTables({ study_filter: 'Volume Profile' });
      const parsed = parseVolumeProfile(tables);
      if (parsed) Object.assign(data, { ...parsed, source_script: 'volume_profile' });
    } catch { /* Pine script may not be loaded */ }
  }

  if (mode === 'session' || mode === 'all') {
    try {
      const labels = await getPineLabels({ study_filter: 'Session' });
      const vals   = await getStudyValues();
      const parsed = parseSessionAnalytics(labels, vals);
      if (parsed) Object.assign(data, { ...parsed, source_script: data.source_script ?? 'session_analytics' });
    } catch { /* Pine script may not be loaded */ }
  }

  if (mode === 'rs' || mode === 'all') {
    try {
      const tables = await getPineTables({ study_filter: 'Relative' });
      const labels = await getPineLabels({ study_filter: 'Relative' });
      const parsed = parseRelativeStrength(tables, labels);
      if (parsed) Object.assign(data, { ...parsed, source_script: data.source_script ?? 'relative_strength' });
    } catch { /* Pine script may not be loaded */ }
  }

  return Object.keys(data).length > 1 ? data : null;
}

async function main() {
  try {
    const { initPhase49to55Schema } = await import('../src/egx/index.js');
    initPhase49to55Schema();
  } catch { /* already initialized */ }

  process.stdout.write(`
╔════════════════════════════════════════════════════════════════╗
║         Pine Analytics Fetcher — Phase 53                      ║
╠════════════════════════════════════════════════════════════════╣
║  Mode    : ${String(MODE).padEnd(20)}                            ║
║  Symbols : ${String(ALL_SYMBOLS.length).padEnd(4)}                                            ║
║  Date    : latest available bar per symbol                 ║
╚════════════════════════════════════════════════════════════════╝
`);

  let ok = 0, skip = 0, err = 0;

  for (let i = 0; i < ALL_SYMBOLS.length; i++) {
    const sym = ALL_SYMBOLS[i];
    process.stdout.write(`\r  ${String(i+1).padStart(3)}/${ALL_SYMBOLS.length}  ${String(sym).padEnd(8)} ok:${ok} skip:${skip} err:${err}  `);

    try {
      const data = await fetchForSymbol(sym, MODE);
      if (data && Object.keys(data).length > 0) {
        const bars = localBars(sym, 1);
        savePineAnalytics(sym, localTradeDate(bars), data);
        ok++;
      } else {
        skip++;
      }
    } catch (e) {
      err++;
      process.stderr.write(`\n  ⚠️  ${sym}: ${e.message}\n`);
    }
    await sleep(DELAY);
  }

  process.stdout.write(`\n\n  ✅ Done: ${ok} saved  ⏭️ ${skip} skipped  ❌ ${err} errors\n`);

  // Restore
  await setTimeframe({ timeframe: 'D' }).catch(() => {});
  process.exit(0);
}

main().catch(e => { console.error('Fatal:', e.message); process.exit(1); });
