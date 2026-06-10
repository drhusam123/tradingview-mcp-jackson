/**
 * ULTRA loss autopsy — setup + indicator context for residual losses.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';
import { evaluateSignalAtDate } from './egx_safety_check.mjs';
import { runCounterfactualSafety } from './counterfactual_safety.mjs';

const TIER = 'ULTRA_CONVICTION';

function volDecayPct(db, symbol, signalDate) {
  const vols = db.prepare(`
    SELECT date(bar_time, 'unixepoch') AS d, volume
    FROM ohlcv_history
    WHERE symbol=? AND date(bar_time,'unixepoch') <= ?
    ORDER BY bar_time DESC LIMIT 3
  `).all(symbol, signalDate);
  if (vols.length < 2) return null;
  const today = vols[0]?.volume ?? 0;
  const prev = vols[1]?.volume ?? 0;
  if (!prev) return null;
  return Math.round((1 - today / prev) * 100);
}

function patternFlags(row) {
  const flags = [];
  const vol = row.vol_ratio_20;
  const cp = row.close_position;
  const rsi = row.rsi14;
  const cls = (row.behavioral_class || '').toUpperCase();
  const setup = (row.setup_type || '').toLowerCase();

  if (cls === 'EXPLOSIVE' && vol != null && vol < 2.5) flags.push('explosive_low_vol');
  if (vol != null && vol > 3.5) flags.push('volume_chase');
  if (cp != null && cp > 0.66) flags.push('upper_third_close');
  if (cp != null && cp < 0.33) flags.push('lower_third_close');
  if (cls === 'EXPLOSIVE' && rsi != null && rsi > 70) flags.push('explosive_overbought');
  if (row.vol_decay_pct != null && row.vol_decay_pct > 60) flags.push('post_breakout_vol_collapse');
  if (setup.includes('near ath') && vol != null && vol < 2.5) flags.push('near_ath_thin_vol');
  if (row.repeat_loss_count >= 2) flags.push('repeat_ultra_loser');
  if (!row.setup_type) flags.push('missing_setup');
  if (vol == null && rsi == null) flags.push('missing_indicators');

  return flags;
}

export function runLossAutopsy({ tier = TIER, lookbackDays = 120 } = {}) {
  if (!existsSync(DB_PATH)) return { error: 'NO_DB' };

  const counter = runCounterfactualSafety({ tier });
  const residual = counter.loss_symbols_still_passing || [];
  const nResidual = residual.length;

  const db = new Database(DB_PATH, { readonly: true });
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - lookbackDays);
  const cutoffIso = cutoff.toISOString().slice(0, 10);

  const allLosses = db.prepare(`
    SELECT symbol, signal_date, return_t5, hit_stop, behavioral_class, ues, ml_score
    FROM recommendation_outcomes
    WHERE conviction_tier=? AND outcome_filled>=5 AND hit_t5=0
      AND signal_date >= ?
    ORDER BY signal_date DESC
  `).all(tier, cutoffIso);

  const lossCounts = {};
  for (const l of allLosses) {
    lossCounts[l.symbol] = (lossCounts[l.symbol] || 0) + 1;
  }

  const cases = [];
  for (const item of residual.length ? residual : allLosses.map(l => ({
    symbol: l.symbol, date: l.signal_date, class: l.behavioral_class,
  }))) {
    const symbol = item.symbol;
    const signalDate = item.date;
    const ro = db.prepare(`
      SELECT * FROM recommendation_outcomes WHERE symbol=? AND signal_date=?
    `).get(symbol, signalDate);
    const fs = db.prepare(`
      SELECT setup_type, r_ratio, score FROM final_signals WHERE symbol=? AND trade_date=?
    `).get(symbol, signalDate);
    const ic = db.prepare(`
      SELECT vol_ratio_20, rsi14, close_position, bb_position
      FROM indicators_cache WHERE symbol=? AND bar_date=?
    `).get(symbol, signalDate);

    const row = {
      symbol,
      signal_date: signalDate,
      return_t5: ro?.return_t5,
      hit_stop: ro?.hit_stop,
      behavioral_class: ro?.behavioral_class || item.class,
      setup_type: fs?.setup_type,
      r_ratio: fs?.r_ratio,
      vol_ratio_20: ic?.vol_ratio_20,
      rsi14: ic?.rsi14,
      close_position: ic?.close_position,
      vol_decay_pct: volDecayPct(db, symbol, signalDate),
      repeat_loss_count: lossCounts[symbol] || 0,
    };
    row.flags = patternFlags(row);
    row.safety = evaluateSignalAtDate(symbol, signalDate, { historical: true, counterfactual: true });
    cases.push(row);
  }

  const flagCounts = {};
  const symbolCounts = {};
  for (const c of cases) {
    symbolCounts[c.symbol] = (symbolCounts[c.symbol] || 0) + 1;
    for (const f of c.flags) flagCounts[f] = (flagCounts[f] || 0) + 1;
  }

  const proposed_rules = [];
  if ((flagCounts.explosive_low_vol || 0) >= 2) {
    proposed_rules.push({
      id: 'explosive_min_vol',
      rule: 'EXPLOSIVE requires vol_ratio_20 >= 2.5 at delivery',
      evidence: flagCounts.explosive_low_vol,
    });
  }
  if ((flagCounts.upper_third_close || 0) >= 2) {
    proposed_rules.push({
      id: 'block_upper_third',
      rule: 'Block ULTRA delivery when close_position > 0.66 (upper third)',
      evidence: flagCounts.upper_third_close,
    });
  }
  if ((flagCounts.repeat_ultra_loser || 0) >= 2) {
    proposed_rules.push({
      id: 'repeat_symbol_block',
      rule: 'Block symbol with 2+ ULTRA losses in 120d lookback',
      evidence: flagCounts.repeat_ultra_loser,
    });
  }
  if ((flagCounts.volume_chase || 0) >= 2) {
    proposed_rules.push({
      id: 'volume_chase_cap',
      rule: 'Block when vol_ratio_20 > 3.5 (chase)',
      evidence: flagCounts.volume_chase,
    });
  }
  if ((flagCounts.missing_indicators || 0) >= 3) {
    proposed_rules.push({
      id: 'require_indicator_cache',
      rule: 'Block delivery when indicators_cache missing for signal date',
      evidence: flagCounts.missing_indicators,
    });
  }

  db.close();

  return {
    tier,
    n_residual_losses: nResidual,
    n_cases_analyzed: cases.length,
    n_all_losses_lookback: allLosses.length,
    counterfactual_blocked_losses: counter.would_block_losses ?? null,
    flag_counts: flagCounts,
    repeat_symbols: Object.entries(symbolCounts)
      .filter(([, n]) => n >= 2)
      .map(([symbol, n]) => ({ symbol, losses: n })),
    proposed_rules,
    cases: cases.slice(0, 20),
  };
}
