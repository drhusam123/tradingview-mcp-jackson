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
  behavioral_filters: {
    block_volatile_client: true,
    block_dormant_client: true,
    explosive_max_rsi: 70,
    explosive_min_vol_ratio: 2.5,
    explosive_ultra_thin_vol: 1.0,
    volatile_max_rsi: 65,
    volatile_min_vol_ratio: 2.5,
    high_false_signal_rate_max: 0.65,
    block_upper_third_close: true,
    max_close_position: 0.66,
    block_volume_chase: true,
    max_vol_ratio_chase: 3.5,
    repeat_ultra_loss_lookback_days: 120,
    max_ultra_losses_per_symbol: 1,
    require_indicator_cache: true,
  },
};

export function loadEgxRules() {
  let rules = { ...DEFAULT_RULES };
  if (existsSync(RULES_PATH)) {
    try {
      rules = { ...DEFAULT_RULES, ...JSON.parse(readFileSync(RULES_PATH, 'utf8')) };
    } catch { /* keep defaults */ }
  }
  const runtimePath = join(ROOT, 'data/egx_rules_runtime.json');
  if (existsSync(runtimePath)) {
    try {
      const rt = JSON.parse(readFileSync(runtimePath, 'utf8'));
      if (rt.behavioral_filters) {
        rules.behavioral_filters = { ...rules.behavioral_filters, ...rt.behavioral_filters };
      }
      if (rt.lessons_filters) {
        rules.lessons_filters = { ...rules.lessons_filters, ...rt.lessons_filters };
      }
      rules._runtime_overlay_at = rt.at;
    } catch { /* ignore bad runtime file */ }
  }
  return rules;
}

function dbReadonly() {
  if (!existsSync(DB_PATH)) return null;
  const d = new Database(DB_PATH, { readonly: true });
  d.pragma('busy_timeout = 5000');
  return d;
}

function parseBreakdown(sig) {
  if (!sig?.source_breakdown) return {};
  try {
    return typeof sig.source_breakdown === 'string'
      ? JSON.parse(sig.source_breakdown)
      : sig.source_breakdown;
  } catch {
    return {};
  }
}

function signalContext(d, symbol, signalDate, { historical = false } = {}) {
  let sig = d.prepare(`
    SELECT symbol, setup_type, score, entry_price, entry_high, stop_loss,
           t1_target, r_ratio, confidence, source_breakdown
    FROM final_signals
    WHERE trade_date=? AND symbol=? AND actionable=1
  `).get(signalDate, symbol);

  if (!sig && historical) {
    const ro = d.prepare(`
      SELECT symbol, signal_date, entry_price, stop_loss, t1_target,
             behavioral_class, conviction_tier, ues, ml_score
      FROM recommendation_outcomes
      WHERE signal_date=? AND symbol=?
    `).get(signalDate, symbol);
    const fs = d.prepare(`
      SELECT setup_type, entry_high, r_ratio, source_breakdown
      FROM final_signals WHERE trade_date=? AND symbol=?
    `).get(signalDate, symbol);
    if (ro) {
      const bd = parseBreakdown(fs);
      sig = {
        symbol: ro.symbol,
        setup_type: fs?.setup_type ?? null,
        score: ro.ues ?? null,
        entry_price: ro.entry_price,
        entry_high: fs?.entry_high ?? ro.entry_price,
        stop_loss: ro.stop_loss,
        t1_target: ro.t1_target,
        r_ratio: fs?.r_ratio ?? null,
        confidence: null,
        source_breakdown: JSON.stringify({
          ...bd,
          behavioral_class: ro.behavioral_class,
          quality_gate_passed: true,
        }),
      };
    }
  }

  const ind = d.prepare(`
    SELECT vol_ratio_20, bb_position, rsi14, close_position
    FROM indicators_cache
    WHERE symbol=? AND bar_date=?
    ORDER BY bar_date DESC LIMIT 1
  `).get(symbol, signalDate);

  let behavior = null;
  try {
    behavior = d.prepare(`
      SELECT behavioral_class, false_signal_rate
      FROM stock_behavioral_memory WHERE symbol=?
    `).get(symbol);
  } catch { /* table optional */ }
  if (!behavior) {
    const bd = parseBreakdown(sig);
    if (bd?.behavioral_class) {
      behavior = { behavioral_class: bd.behavioral_class, false_signal_rate: bd.false_signal_rate ?? null };
    }
  }

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

  return { sig, ind, behavior, volDecayPct, lastClose: lastClose?.close ?? null };
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

function evaluateOne(symbol, signalDate, rules, filters, behavioralFilters = {}, opts = {}) {
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

  const ctx = signalContext(d, symbol, signalDate, opts);
  d.close();

  if (!ctx.sig) {
    return {
      symbol,
      signal_date: signalDate,
      decision: 'BLOCKED',
      failed_conditions: ['no_signal'],
      conditions: {},
      warnings: [],
    };
  }

  const conditions = {};
  const failed = [];
  const warnings = [];
  const setup = (ctx.sig?.setup_type || '').toLowerCase();
  const nearAth = setup.includes('near ath') || setup.includes('ath');
  const breakoutish = setup.includes('breakout') || setup.includes('power');

  const counterfactual = Boolean(opts.counterfactual || opts.historical);
  const rr = ctx.sig?.r_ratio ?? 0;
  if (!counterfactual || ctx.sig?.r_ratio != null) {
    conditions.min_rr = cond('min_rr', true, rr, rules.min_rr, rr >= rules.min_rr);
    if (conditions.min_rr.result === 'FAIL') failed.push('min_rr');
  }

  if (!counterfactual) {
    conditions.structural_sl = cond(
      'structural_sl',
      true,
      ctx.sig?.stop_loss ?? null,
      'present',
      Boolean(ctx.sig?.stop_loss && ctx.sig.stop_loss > 0),
    );
    if (conditions.structural_sl.result === 'FAIL') failed.push('structural_sl');
  }

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

  if (!counterfactual && ctx.sig?.entry_high && ctx.lastClose) {
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

  const bclass = (ctx.behavior?.behavioral_class || parseBreakdown(ctx.sig).behavioral_class || 'UNKNOWN').toUpperCase();
  const rsi = ctx.ind?.rsi14 ?? null;
  const fsr = ctx.behavior?.false_signal_rate ?? null;
  const bf = behavioralFilters;

  if (bf.block_dormant_client !== false && bclass === 'DORMANT') {
    conditions.behavioral_dormant = cond('behavioral_dormant', true, bclass, 'not DORMANT', false);
    failed.push('behavioral_dormant');
  }

  const fsrMax = bf.high_false_signal_rate_max ?? 0.65;
  if (fsr != null && fsr > fsrMax) {
    conditions.false_signal_rate = cond('false_signal_rate', true, fsr, `max ${fsrMax}`, false);
    failed.push('false_signal_rate');
  }

  const explosiveMaxRsi = bf.explosive_max_rsi ?? 70;
  if (bclass === 'EXPLOSIVE' && rsi != null && rsi > explosiveMaxRsi) {
    conditions.explosive_rsi = cond('explosive_rsi', true, rsi, `max ${explosiveMaxRsi}`, false);
    failed.push('explosive_rsi');
  }

  const cp = ctx.ind?.close_position ?? null;
  if (bf.block_upper_third_close !== false && cp != null && cp > (bf.max_close_position ?? 0.66)) {
    conditions.upper_third_close = cond(
      'upper_third_close',
      true,
      cp.toFixed(2),
      `max ${bf.max_close_position ?? 0.66}`,
      false,
    );
    failed.push('upper_third_close');
  }

  const explosiveMinVol = bf.explosive_min_vol_ratio ?? 2.5;
  const ultraThinVol = bf.explosive_ultra_thin_vol ?? 1.0;
  if (bclass === 'EXPLOSIVE' && vol != null && vol < explosiveMinVol) {
    const thinWarn = vol >= ultraThinVol;
    conditions.explosive_min_vol = cond(
      'explosive_min_vol',
      false,
      vol,
      thinWarn ? `warn < ${explosiveMinVol}x` : `ultra-thin < ${ultraThinVol}x`,
      thinWarn,
    );
    if (thinWarn) {
      warnings.push(`explosive vol ${vol}x below lesson band ${explosiveMinVol}x`);
    }
  }
  if (bclass === 'EXPLOSIVE' && vol != null && vol < ultraThinVol) {
    let priorLosses = 0;
    if (bf.max_ultra_losses_per_symbol != null) {
      const d3 = dbReadonly();
      if (d3) {
        const lookback = bf.repeat_ultra_loss_lookback_days ?? 120;
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - lookback);
        priorLosses = d3.prepare(`
          SELECT COUNT(*) AS n FROM recommendation_outcomes
          WHERE symbol=? AND conviction_tier='ULTRA_CONVICTION'
            AND outcome_filled>=5 AND hit_t5=0
            AND signal_date>=? AND signal_date<?
        `).get(symbol, cutoff.toISOString().slice(0, 10), signalDate)?.n ?? 0;
        d3.close();
      }
    }
    if (priorLosses >= (bf.max_ultra_losses_per_symbol ?? 1)) {
      conditions.explosive_ultra_thin_repeat = cond(
        'explosive_ultra_thin_repeat',
        true,
        vol,
        `<${ultraThinVol}x + prior ULTRA loss`,
        false,
      );
      failed.push('explosive_ultra_thin_repeat');
    }
  }

  const chaseMax = bf.max_vol_ratio_chase ?? 3.5;
  if (bf.block_volume_chase !== false && vol != null && vol > chaseMax) {
    conditions.volume_chase = cond(
      'volume_chase',
      true,
      vol,
      `max ${chaseMax}x`,
      false,
    );
    failed.push('volume_chase');
  }

  const liveDelivery = !counterfactual && !opts.historical;
  if (bf.require_indicator_cache !== false && !ctx.ind && liveDelivery) {
    conditions.indicator_cache = cond('indicator_cache', true, 'missing', 'present', false);
    failed.push('indicator_cache');
  } else if (bf.require_indicator_cache !== false && !ctx.ind && !liveDelivery) {
    conditions.indicator_cache = cond('indicator_cache', false, 'missing', 'historical skip', true);
    warnings.push('indicators_cache missing for historical replay — live delivery still requires cache');
  }

  if (bf.max_ultra_losses_per_symbol != null) {
    const d2 = dbReadonly();
    if (d2) {
      const lookback = bf.repeat_ultra_loss_lookback_days ?? 120;
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - lookback);
      const lossN = d2.prepare(`
        SELECT COUNT(*) AS n FROM recommendation_outcomes
        WHERE symbol=? AND conviction_tier='ULTRA_CONVICTION'
          AND outcome_filled>=5 AND hit_t5=0
          AND signal_date>=? AND signal_date<?
      `).get(symbol, cutoff.toISOString().slice(0, 10), signalDate)?.n ?? 0;
      d2.close();
      const maxLoss = bf.max_ultra_losses_per_symbol ?? 1;
      if (lossN >= maxLoss) {
        conditions.repeat_ultra_loser = cond(
          'repeat_ultra_loser',
          true,
          lossN,
          `max ${maxLoss} losses/${lookback}d`,
          false,
        );
        failed.push('repeat_ultra_loser');
      }
    }
  }

  if (bf.block_volatile_client !== false && bclass === 'VOLATILE') {
    const volMin = bf.volatile_min_vol_ratio ?? 2.5;
    const volMax = filters.optimal_vol_ratio_max ?? 3.5;
    const volatileMaxRsi = bf.volatile_max_rsi ?? 65;
    const volOk = vol != null && vol >= volMin && vol <= volMax;
    const rsiOk = rsi == null || rsi <= volatileMaxRsi;
    const passVolatile = volOk && rsiOk;
    conditions.behavioral_volatile = cond(
      'behavioral_volatile',
      true,
      `${bclass} vol=${vol ?? '—'} rsi=${rsi ?? '—'}`,
      `vol ${volMin}–${volMax}x & rsi≤${volatileMaxRsi}`,
      passVolatile,
    );
    if (!passVolatile) failed.push('behavioral_volatile');
  }

  return {
    timestamp: new Date().toISOString(),
    market: 'EGX',
    symbol,
    signal_date: signalDate,
    setup_type: ctx.sig?.setup_type ?? null,
    behavioral_class: bclass,
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

/** Evaluate one symbol at a date (supports historical replay via recommendation_outcomes). */
export function evaluateSignalAtDate(symbol, signalDate, opts = {}) {
  const rules = loadEgxRules();
  return evaluateOne(
    symbol,
    signalDate,
    rules,
    rules.lessons_filters || {},
    rules.behavioral_filters || {},
    {
      historical: Boolean(opts.historical),
      counterfactual: Boolean(opts.counterfactual),
    },
  );
}

/**
 * @param {string} signalDate
 * @param {object} [opts]
 */
export function runEgxSafetyCheck(signalDate, opts = {}) {
  const rules = loadEgxRules();
  const filters = rules.lessons_filters || {};
  const behavioralFilters = rules.behavioral_filters || {};
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
    const dec = evaluateOne(sym, signalDate, rules, filters, behavioralFilters);
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
