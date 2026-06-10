/**
 * EGX safety check — Jackson bot.js pattern adapted for EGX signals.
 * Every condition must PASS (or WARN-only) before a signal is cleared for delivery.
 */
import Database from 'better-sqlite3';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { DB_PATH, countActionable } from './delivery_audit.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
export const RULES_PATH = join(ROOT, 'egx_rules.json');
export const SAFETY_LOG = join(ROOT, 'data/safety-check-log.json');

const DEFAULT_RULES = {
  min_rr: 2.0,
  max_signals_per_day: 3,
  max_open_positions: 6,
  lessons_filters: {
    near_ath_min_vol_ratio: 2.5,
    post_breakout_vol_decay_max_pct: 60,
    optimal_vol_ratio_min: 2.5,
    optimal_vol_ratio_max: 3.5,
    max_open_above_entry_pct: 0.5,
  },
};

export function loadEgxRules() {
  if (!existsSync(RULES_PATH)) return { ...DEFAULT_RULES };
  try {
    return { ...DEFAULT_RULES, ...JSON.parse(readFileSync(RULES_PATH, 'utf8')) };
  } catch {
    return { ...DEFAULT_RULES };
  }
}

function dbReadonly() {
  if (!existsSync(DB_PATH)) return null;
  const d = new Database(DB_PATH, { readonly: true });
  d.pragma('busy_timeout = 5000');
  return d;
}

function signalContext(d, symbol, signalDate) {
  const sig = d.prepare(`
    SELECT symbol, setup_type, score, entry_price, entry_high, stop_loss,
           t1_target, r_ratio, confidence, source_breakdown
    FROM final_signals
    WHERE trade_date=? AND symbol=? AND actionable=1
  `).get(signalDate, symbol);

  const ind = d.prepare(`
    SELECT vol_ratio_20, bb_position, rsi14, close_position
    FROM indicators_cache
    WHERE symbol=? AND bar_date=?
    ORDER BY bar_date DESC LIMIT 1
  `).get(symbol, signalDate);

  const vols = d.prepare(`
    SELECT date(bar_time, 'unixepoch') AS d, volume
    FROM ohlcv_history
    WHERE symbol=? AND date(bar_time,'unixepoch') <= ?
    ORDER BY bar_time DESC LIMIT 5
  `).all(symbol, signalDate);

  let volDecayPct = null;
  if (vols.length >= 2) {
    const today = vols[0]?.volume ?? 0;
    const prev = vols[1]?.volume ?? 0;
    if (prev > 0) volDecayPct = Math.round((1 - today / prev) * 100);
  }

  const lastClose = d.prepare(`
    SELECT close FROM ohlcv_history
    WHERE symbol=? ORDER BY bar_time DESC LIMIT 1
  `).get(symbol);

  return { sig, ind, volDecayPct, lastClose: lastClose?.close ?? null };
}

function cond(name, required, actual, threshold, pass) {
  return {
    name,
    required,
    actual,
    threshold,
    result: pass ? 'PASS' : (required ? 'FAIL' : 'WARN'),
  };
}

function evaluateOne(symbol, signalDate, rules, filters) {
  const d = dbReadonly();
  if (!d) {
    return {
      symbol,
      signal_date: signalDate,
      decision: 'BLOCKED',
      failed_conditions: ['no_database'],
      conditions: {},
      warnings: [],
    };
  }

  const ctx = signalContext(d, symbol, signalDate);
  d.close();

  const conditions = {};
  const failed = [];
  const warnings = [];
  const setup = (ctx.sig?.setup_type || '').toLowerCase();
  const nearAth = setup.includes('near ath') || setup.includes('ath');
  const breakoutish = setup.includes('breakout') || setup.includes('power');

  const rr = ctx.sig?.r_ratio ?? 0;
  conditions.min_rr = cond('min_rr', true, rr, rules.min_rr, rr >= rules.min_rr);
  if (conditions.min_rr.result === 'FAIL') failed.push('min_rr');

  conditions.structural_sl = cond(
    'structural_sl',
    true,
    ctx.sig?.stop_loss ?? null,
    'present',
    Boolean(ctx.sig?.stop_loss && ctx.sig.stop_loss > 0),
  );
  if (conditions.structural_sl.result === 'FAIL') failed.push('structural_sl');

  const vol = ctx.ind?.vol_ratio_20 ?? null;
  if (nearAth) {
    const minVol = filters.near_ath_min_vol_ratio ?? 2.5;
    conditions.near_ath_volume = cond('near_ath_volume', true, vol, minVol, (vol ?? 0) >= minVol);
    if (conditions.near_ath_volume.result === 'FAIL') failed.push('near_ath_volume');
  }

  if (breakoutish && ctx.volDecayPct != null) {
    const maxDecay = filters.post_breakout_vol_decay_max_pct ?? 60;
    conditions.post_breakout_volume = cond(
      'post_breakout_volume',
      true,
      `${ctx.volDecayPct}% decay`,
      `max ${maxDecay}%`,
      ctx.volDecayPct <= maxDecay,
    );
    if (conditions.post_breakout_volume.result === 'FAIL') failed.push('post_breakout_volume');
  }

  if (vol != null) {
    const lo = filters.optimal_vol_ratio_min ?? 2.5;
    const hi = filters.optimal_vol_ratio_max ?? 3.5;
    const inBand = vol >= lo && vol <= hi;
    conditions.optimal_volume_band = cond(
      'optimal_volume_band',
      false,
      vol,
      `${lo}–${hi}x`,
      inBand,
    );
    if (!inBand) warnings.push(`volume ${vol}x outside optimal ${lo}–${hi}x`);
  }

  if (ctx.sig?.entry_high && ctx.lastClose) {
    const maxAbove = filters.max_open_above_entry_pct ?? 0.5;
    const entryMid = (ctx.sig.entry_price + ctx.sig.entry_high) / 2;
    const pctAbove = entryMid > 0 ? ((ctx.lastClose - entryMid) / entryMid) * 100 : 0;
    conditions.entry_zone_open = cond(
      'entry_zone_open',
      true,
      `${pctAbove.toFixed(2)}%`,
      `max ${maxAbove}%`,
      pctAbove <= maxAbove,
    );
    if (conditions.entry_zone_open.result === 'FAIL') failed.push('entry_zone_open');
  }

  return {
    timestamp: new Date().toISOString(),
    market: 'EGX',
    symbol,
    signal_date: signalDate,
    setup_type: ctx.sig?.setup_type ?? null,
    score: ctx.sig?.score ?? null,
    decision: failed.length ? 'BLOCKED' : 'PASS',
    failed_conditions: failed,
    conditions: Object.fromEntries(
      Object.entries(conditions).map(([k, v]) => [k, {
        required: v.required,
        actual: v.actual,
        threshold: v.threshold,
        result: v.result,
      }]),
    ),
    warnings,
  };
}

/**
 * @param {string} signalDate
 * @param {object} [opts]
 */
export function runEgxSafetyCheck(signalDate, opts = {}) {
  const rules = loadEgxRules();
  const filters = rules.lessons_filters || {};
  const maxSignals = parseInt(
    process.env.EGX_MAX_SIGNALS_PER_DAY || String(rules.max_signals_per_day || 3),
    10,
  );
  const maxOpen = parseInt(
    process.env.EGX_MAX_OPEN_POSITIONS || String(rules.max_open_positions || 6),
    10,
  );
  const veto = opts.veto ?? process.env.EGX_SAFETY_VETO !== '0';

  const act = countActionable(signalDate);
  const global = {};

  let openCount = 0;
  const d = dbReadonly();
  if (d) {
    openCount = d.prepare(`
      SELECT COUNT(*) AS n FROM portfolio_positions
      WHERE status IN ('OPEN','PARTIAL_T1','PARTIAL_T2')
    `).get()?.n ?? 0;
    d.close();
  }

  global.max_open_positions = {
    required: true,
    actual: openCount,
    threshold: maxOpen,
    result: openCount < maxOpen ? 'PASS' : 'FAIL',
  };

  const decisions = [];
  const ranked = [...act.symbols];
  for (let i = 0; i < act.symbols.length; i++) {
    const sym = act.symbols[i];
    const dec = evaluateOne(sym, signalDate, rules, filters);
    if (i >= maxSignals) {
      dec.decision = 'BLOCKED';
      dec.failed_conditions.push('max_signals_per_day');
      dec.conditions.max_signals_per_day = {
        required: true,
        actual: i + 1,
        threshold: maxSignals,
        result: 'FAIL',
      };
    }
    decisions.push(dec);
  }

  const blocked = decisions.filter(x => x.decision === 'BLOCKED').map(x => x.symbol);
  const passed = decisions.filter(x => x.decision === 'PASS').map(x => x.symbol);
  const globalFail = global.max_open_positions.result === 'FAIL';

  const deliverableAfterSafety = veto
    ? passed.length
    : act.deliverable;

  const ok = !globalFail && (veto ? passed.length > 0 || act.deliverable === 0 : true);

  return {
    ok,
    veto,
    signal_date: signalDate,
    actionable: act.db,
    deliverable_before: act.deliverable,
    deliverable_after: deliverableAfterSafety,
    passed_symbols: passed,
    blocked_symbols: blocked,
    global_conditions: global,
    decisions,
    paper_trading: process.env.EGX_PAPER_TRADING === 'true',
  };
}

export function appendSafetyLog(result) {
  mkdirSync(join(ROOT, 'data'), { recursive: true });
  const existing = existsSync(SAFETY_LOG)
    ? JSON.parse(readFileSync(SAFETY_LOG, 'utf8'))
    : [];
  const entry = {
    logged_at: new Date().toISOString(),
    type: 'egx_safety_check',
    ...result,
  };
  const trimmed = [...existing, entry].slice(-500);
  writeFileSync(SAFETY_LOG, JSON.stringify(trimmed, null, 2));
  return entry;
}
