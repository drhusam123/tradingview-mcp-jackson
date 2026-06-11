#!/usr/bin/env python3
"""
Discovery domain miners — propose atoms from L0-L9 tables.
Each returns list of dicts compatible with discovery_atom_registry.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

NOW = lambda: datetime.now(timezone.utc).isoformat()


def _atom(atom_id, layer, table, miner, cond=None, regime=None, boost=1.0, penalize=1.0,
          hard_neg=0, ml_col=None, wr=None, n=None, lift=None):
    return {
        "atom_id": atom_id,
        "source_layer": layer,
        "source_table": table,
        "source_miner": miner,
        "condition_json": json.dumps(cond or {"atom": atom_id}),
        "regime_filter": regime,
        "boost_weight": boost,
        "penalize_weight": penalize,
        "hard_negative": hard_neg,
        "ml_feature_col": ml_col,
        "backtest_wr": wr,
        "backtest_n": n,
        "backtest_lift": lift,
        "status": "proposed",
        "proposed_at": NOW(),
    }


def _read_json(name):
    p = DATA / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def mine_json_sources() -> list[dict]:
    """Counterfactual, regime sweep, hypothesis bridge JSON → atoms."""
    out = []
    cf = _read_json("counterfactual_atoms_last.json")
    if cf:
        for a in cf.get("boost_atoms") or cf.get("priority_atoms") or []:
            out.append(_atom(a, "L8", "counterfactual_atoms_last.json", "counterfactual_atoms", boost=1.12))
        for a in cf.get("penalize_atoms") or []:
            out.append(_atom(a, "L8", "counterfactual_atoms_last.json", "counterfactual_atoms",
                             penalize=0.65, hard_neg=1 if a in ("vol_gt5", "very_upper_close") else 0))
        for pair in cf.get("seed_pairs") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out.append(_atom(f"{pair[0]}_{pair[1]}", "L8", "counterfactual_atoms_last.json",
                                 "counterfactual_atoms", cond={"pair": pair}, boost=1.1))

    rs = _read_json("regime_conditional_sweep_last.json")
    if rs:
        for a in rs.get("priority_atoms") or []:
            out.append(_atom(a, "L2", "markov_regime_daily", "regime_conditional_sweep", boost=1.08))
        for block in rs.get("regimes") or []:
            reg = block.get("regime")
            for pair in block.get("seed_pairs") or []:
                if len(pair) == 2:
                    out.append(_atom(f"{pair[0]}_{pair[1]}_{reg}", "L2", "markov_regime_daily",
                                     "regime_conditional_sweep", cond={"pair": pair}, regime=reg, boost=1.1))

    hb = _read_json("hypothesis_sandbox_bridge_last.json")
    if hb:
        for a in hb.get("priority_atoms") or []:
            out.append(_atom(a, "L9", "sandbox_hypotheses", "hypothesis_sandbox_bridge", boost=1.06))

    tv = _read_json("tv_microstructure_last.json")
    if tv:
        for flag in ("vwap_reclaim", "absorption_bar", "participation_shock"):
            out.append(_atom(f"tv_{flag}", "L2", "tv_discovery_features", "tv_microstructure",
                             cond={"tv_flag": flag}, boost=1.1))

    return out


def mine_canonical_price_atoms() -> list[dict]:
    """L0 canonical atoms from TRADING_LESSONS (pre-validated definitions)."""
    canonical = [
        "lower_third_close", "vol_2_5_3", "low20_retest", "not_near_ath",
        "bb_squeeze_low35", "range_lt4pct", "not_extended_3d",
        "vol_lt1_5", "upper_close", "high20_break", "vol_gt3", "vol_gt5",
    ]
    return [_atom(a, "L0", "ohlcv_history", "price_structure_miner", boost=1.0) for a in canonical]


def mine_closing_pressure(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n,
                   AVG(CASE WHEN close_pos <= 0.33 THEN 1.0 ELSE 0 END) low_third_pct
            FROM closing_pressure_daily
            WHERE trade_date >= date('now', '-120 days')
            """
        ).fetchone()
        if row and row[0] > 500:
            out.append(_atom("cp_lower_third", "L2", "closing_pressure_daily", "closing_pressure_miner",
                             cond={"close_pos_lte": 0.33}, ml_col="close_pos", boost=1.15))
            out.append(_atom("cp_high_pressure", "L2", "closing_pressure_daily", "closing_pressure_miner",
                             cond={"closing_pressure_gte": 0.6}, ml_col="closing_pressure", boost=1.08))
    except sqlite3.OperationalError:
        pass
    return out


def mine_indicators_confluence(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n
            FROM indicators_cache
            WHERE bar_date >= date('now', '-90 days')
              AND rsi14 BETWEEN 35 AND 55
              AND obv_divergence = 'bullish'
            """
        ).fetchone()
        if row and row[0] >= 20:
            out.append(_atom("rsi_obv_bull_confluence", "L1", "indicators_cache",
                             "indicators_confluence_miner",
                             cond={"rsi14_range": [35, 55], "obv_divergence": "bullish"}, boost=1.12))
        row2 = db.execute(
            """
            SELECT COUNT(*) n FROM indicators_cache
            WHERE bar_date >= date('now', '-90 days') AND rsi14 BETWEEN 40 AND 65
              AND above_ema20 = 1 AND above_ema50 = 1
            """
        ).fetchone()
        if row2 and row2[0] >= 30:
            out.append(_atom("rsi_ema_stack", "L1", "indicators_cache", "indicators_confluence_miner",
                             cond={"above_ema20": 1, "above_ema50": 1}, boost=1.08))
    except sqlite3.OperationalError:
        pass
    return out


def mine_outcome_weighted(db) -> list[dict]:
    """L8 — atoms correlated with winning outcomes."""
    out = []
    try:
        rows = db.execute(
            """
            SELECT behavioral_class, COUNT(*) n,
                   AVG(CASE WHEN hit_t5 = 1 THEN 1.0 ELSE 0 END) wr
            FROM recommendation_outcomes
            WHERE outcome_filled >= 5 AND hit_t5 IS NOT NULL
              AND signal_date >= date('now', '-180 days')
            GROUP BY behavioral_class
            HAVING n >= 8
            """
        ).fetchall()
        for r in rows:
            beh, n, wr = r[0], r[1], (r[2] or 0) * 100
            if not beh:
                continue
            if wr >= 25:
                out.append(_atom(f"outcome_win_{beh.lower()}", "L8", "recommendation_outcomes",
                                 "outcome_weighted_quant", cond={"behavioral": beh},
                                 wr=round(wr, 1), n=n, boost=1.1))
            elif wr < 18:
                out.append(_atom(f"outcome_loss_{beh.lower()}", "L8", "recommendation_outcomes",
                                 "outcome_weighted_quant", cond={"behavioral": beh},
                                 wr=round(wr, 1), n=n, penalize=0.6, hard_neg=1))
    except sqlite3.OperationalError:
        pass
    return out


def mine_ml_errors(db) -> list[dict]:
    """L4 — false positive explosion predictions."""
    out = []
    hard_syms = []
    try:
        rows = db.execute(
            """
            SELECT ro.symbol, COUNT(*) n,
                   AVG(CASE WHEN ro.hit_t5 = 1 THEN 1.0 ELSE 0 END) wr
            FROM recommendation_outcomes ro
            JOIN explosion_predictions ep ON ep.symbol = ro.symbol AND ep.trade_date = ro.signal_date
            WHERE ro.outcome_filled >= 5 AND ep.probability >= 0.7
              AND ro.signal_date >= date('now', '-120 days')
            GROUP BY ro.symbol
            HAVING n >= 3 AND wr < 0.25
            """
        ).fetchall()
        for sym, n, wr in rows:
            hard_syms.append(sym)
            out.append(_atom(f"ml_fp_{sym}", "L4", "explosion_predictions", "ml_error_miner",
                             cond={"symbol": sym, "high_ml_prob": True},
                             wr=round((wr or 0) * 100, 1), n=n, penalize=0.5, hard_neg=1))
        if hard_syms:
            out.append(_atom("ml_false_positive_gate", "L4", "explosion_predictions", "ml_error_miner",
                             cond={"hard_negative_symbols": hard_syms[:40]}, hard_neg=1))
    except sqlite3.OperationalError:
        pass
    return out, hard_syms


def mine_bayesian_wr(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT label, posterior_wr, n_obs
            FROM bayesian_wr
            WHERE n_obs >= 10
            ORDER BY posterior_wr DESC
            LIMIT 20
            """
        ).fetchall()
        for label, pwr, n in rows:
            if pwr and pwr >= 55:
                out.append(_atom(f"bayes_{label}", "L8", "bayesian_wr", "bayesian_wr_miner",
                                 cond={"label": label}, wr=pwr, n=n, boost=1.05))
    except sqlite3.OperationalError:
        pass
    return out


def mine_arbitration_vetoes(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT veto_reason, COUNT(*) n
            FROM arbitration_decisions
            WHERE veto_triggered = 1 AND created_at >= datetime('now', '-90 days')
            GROUP BY veto_reason
            HAVING n >= 5
            ORDER BY n DESC
            LIMIT 12
            """
        ).fetchall()
        for reason, n in rows:
            key = str(reason or "unknown").replace(":", "_").replace(" ", "_")[:48]
            out.append(_atom(f"veto_{key}", "L6", "arbitration_decisions", "arbitration_veto_miner",
                             cond={"veto_reason": reason}, penalize=0.7, n=n))
    except sqlite3.OperationalError:
        pass
    return out


def mine_alpha_universe(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            "SELECT COUNT(*) FROM alpha_rankings WHERE is_alive = 1"
        ).fetchone()
        if row and row[0] > 0:
            out.append(_atom("alpha_alive_gate", "L9", "alpha_rankings", "alpha_universe_gate",
                             cond={"is_alive": 1, "min_grade": "B"}, boost=1.0))
    except sqlite3.OperationalError:
        pass
    return out


def mine_breadth_regime(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT breadth_score, signal FROM market_breadth_enhanced
            ORDER BY date DESC LIMIT 1
            """
        ).fetchone()
        if row:
            score, sig = row[0], row[1]
            if score and score >= 55:
                out.append(_atom("breadth_bullish", "L2", "market_breadth_enhanced",
                                 "breadth_regime_miner", cond={"breadth_score_gte": 55}, boost=1.06))
            elif score and score < 40:
                out.append(_atom("breadth_bearish", "L2", "market_breadth_enhanced",
                                 "breadth_regime_miner", cond={"breadth_score_lt": 40}, penalize=0.75))
    except sqlite3.OperationalError:
        pass
    return out


def mine_spectral(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT feature_name, AVG(feature_value) avg_v, COUNT(*) n
            FROM feature_store
            WHERE feature_name LIKE 'fft_%' OR feature_name LIKE 'spectral_%'
            GROUP BY feature_name
            HAVING n >= 50
            LIMIT 8
            """
        ).fetchall()
        for fname, avg_v, n in rows:
            out.append(_atom(f"spec_{fname}", "L4", "feature_store", "spectral_atom_bridge",
                             cond={"feature": fname}, ml_col=fname, n=n, boost=1.04))
    except sqlite3.OperationalError:
        pass
    return out


def mine_post_breakout_vol(db) -> list[dict]:
    return [_atom("post_breakout_vol_collapse", "L0", "ohlcv_history", "post_breakout_vol_miner",
                  cond={"vol_ratio_next_lt": 0.4}, penalize=0.55, hard_neg=1)]


def mine_cross_market(db) -> list[dict]:
    """L2/L9 — risk-on/off from cross_market_regime."""
    out = []
    try:
        row = db.execute(
            """
            SELECT risk_on_score, macro_headwind
            FROM cross_market_regime
            ORDER BY date DESC LIMIT 1
            """
        ).fetchone()
        if row and row[0] is not None:
            ros = float(row[0])
            if ros >= 60:
                out.append(_atom("cross_risk_on", "L2", "cross_market_regime", "cross_market_miner",
                                 cond={"risk_on_score_gte": 60}, boost=1.06))
            elif ros < 40:
                out.append(_atom("cross_risk_off", "L2", "cross_market_regime", "cross_market_miner",
                                 cond={"risk_on_score_lt": 40}, penalize=0.72))
            if row[1] and str(row[1]).upper() in ("HIGH", "SEVERE"):
                out.append(_atom("cross_macro_headwind", "L2", "cross_market_regime", "cross_market_miner",
                                 cond={"macro_headwind": row[1]}, penalize=0.8))
    except sqlite3.OperationalError:
        pass
    return out


def mine_tsfresh_patterns(db) -> list[dict]:
    """L4 — discriminating tsfresh_daily dimensions."""
    out = []
    try:
        row = db.execute("SELECT COUNT(DISTINCT trade_date) FROM tsfresh_daily").fetchone()
        if not row or row[0] < 10:
            return out
        stats = db.execute(
            """
            SELECT AVG(feat_entropy) ent, AVG(feat_autocorr1) ac, COUNT(*) n
            FROM tsfresh_daily
            WHERE trade_date >= date('now', '-60 days')
            """
        ).fetchone()
        if stats and stats[2] and stats[2] >= 100:
            if stats[0] and stats[0] >= 2.5:
                out.append(_atom("tsf_high_entropy", "L4", "tsfresh_daily", "tsfresh_pattern_miner",
                                 cond={"feat_entropy_gte": 2.5}, ml_col="feat_entropy", boost=1.05))
            if stats[1] and abs(stats[1]) >= 0.35:
                out.append(_atom("tsf_strong_autocorr", "L4", "tsfresh_daily", "tsfresh_pattern_miner",
                                 cond={"feat_autocorr1_abs_gte": 0.35}, ml_col="feat_autocorr1", boost=1.04))
    except sqlite3.OperationalError:
        pass
    return out


def mine_survival_conformal(db) -> list[dict]:
    """L4 — TP-first + conformal confidence gates."""
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n,
                   AVG(p_tp_first) avg_tp
            FROM survival_exit_profile
            WHERE date >= date('now', '-90 days') AND p_tp_first >= 0.55
            """
        ).fetchone()
        if row and row[0] and row[0] >= 20:
            out.append(_atom("survival_tp_first", "L4", "survival_exit_profile", "survival_conformal_miner",
                             cond={"p_tp_first_gte": 0.55}, boost=1.08, n=row[0], wr=round((row[1] or 0) * 100, 1)))
        row2 = db.execute(
            """
            SELECT COUNT(*) n
            FROM conformal_scores
            WHERE date >= date('now', '-60 days') AND confident = 1
            """
        ).fetchone()
        if row2 and row2[0] and row2[0] >= 15:
            out.append(_atom("conformal_confident", "L4", "conformal_scores", "survival_conformal_miner",
                             cond={"confident": 1}, boost=1.06, n=row2[0]))
    except sqlite3.OperationalError:
        pass
    return out


def mine_dom_liquidity(db) -> list[dict]:
    """L2 — bid/ask imbalance from DOM snapshots."""
    out = []
    try:
        rows = db.execute(
            """
            SELECT symbol, bids, asks, spread_pct
            FROM dom_snapshots
            WHERE snapshot_time >= strftime('%s', datetime('now', '-14 days'))
            ORDER BY snapshot_time DESC
            LIMIT 200
            """
        ).fetchall()
        if not rows:
            return out
        tight_spread = sum(1 for r in rows if r[3] is not None and r[3] <= 0.35)
        if tight_spread >= 10:
            out.append(_atom("dom_tight_spread", "L2", "dom_snapshots", "dom_liquidity_miner",
                             cond={"spread_pct_lte": 0.35}, boost=1.05, n=tight_spread))
        imbalanced = 0
        for sym, bids, asks, _ in rows:
            try:
                b = json.loads(bids) if bids else []
                a = json.loads(asks) if asks else []
                bvol = sum(float(x.get("v", x.get("volume", 0)) if isinstance(x, dict) else 0) for x in b[:5])
                avol = sum(float(x.get("v", x.get("volume", 0)) if isinstance(x, dict) else 0) for x in a[:5])
                if bvol > avol * 1.4:
                    imbalanced += 1
            except Exception:
                continue
        if imbalanced >= 8:
            out.append(_atom("dom_bid_imbalance", "L2", "dom_snapshots", "dom_liquidity_miner",
                             cond={"bid_vol_gt_ask_1_4x": True}, boost=1.07, n=imbalanced))
    except sqlite3.OperationalError:
        pass
    return out


def mine_entry_gap(db) -> list[dict]:
    """L0/L5 — Rule #3: open above entry zone >0.5%."""
    out = [
        _atom("entry_gap_chase", "L0", "final_signals", "entry_gap_miner",
              cond={"open_above_entry_pct_gt": 0.5}, penalize=0.65, hard_neg=1),
    ]
    try:
        rows = db.execute(
            """
            SELECT fs.symbol, fs.trade_date, fs.entry_high,
                   (
                     SELECT o.open FROM ohlcv_history oh
                     WHERE oh.symbol = fs.symbol
                       AND date(oh.bar_time, 'unixepoch') = date(fs.trade_date, '+1 day')
                     LIMIT 1
                   ) AS next_open
            FROM final_signals fs
            WHERE fs.trade_date >= date('now', '-120 days')
              AND fs.entry_high IS NOT NULL AND fs.entry_high > 0
            LIMIT 500
            """
        ).fetchall()
        chased = 0
        total = 0
        for sym, td, entry_high, nopen in rows:
            if nopen is None or not entry_high:
                continue
            total += 1
            gap_pct = (float(nopen) - float(entry_high)) / float(entry_high) * 100
            if gap_pct > 0.5:
                chased += 1
        if total >= 10:
            chase_rate = chased / total * 100
            out[0] = _atom("entry_gap_chase", "L0", "final_signals", "entry_gap_miner",
                           cond={"open_above_entry_pct_gt": 0.5}, penalize=0.65, hard_neg=1,
                           wr=round(chase_rate, 1), n=total)
            if chase_rate < 25:
                out.append(_atom("entry_gap_clean", "L0", "final_signals", "entry_gap_miner",
                                 cond={"open_above_entry_pct_lte": 0.5}, boost=1.05, n=total))
    except sqlite3.OperationalError:
        pass
    return out


def run_all_miners() -> tuple[list[dict], dict]:
    """Execute all domain miners; return atoms + extras for manifest."""
    if not DB_PATH.exists():
        return mine_json_sources() + mine_canonical_price_atoms(), {}

    db = sqlite3.connect(DB_PATH, timeout=60)
    atoms = []
    atoms.extend(mine_json_sources())
    atoms.extend(mine_canonical_price_atoms())
    atoms.extend(mine_closing_pressure(db))
    atoms.extend(mine_indicators_confluence(db))
    atoms.extend(mine_outcome_weighted(db))
    ml_atoms, hard_syms = mine_ml_errors(db)
    atoms.extend(ml_atoms)
    atoms.extend(mine_bayesian_wr(db))
    atoms.extend(mine_arbitration_vetoes(db))
    atoms.extend(mine_alpha_universe(db))
    atoms.extend(mine_breadth_regime(db))
    atoms.extend(mine_spectral(db))
    atoms.extend(mine_post_breakout_vol(db))
    atoms.extend(mine_cross_market(db))
    atoms.extend(mine_tsfresh_patterns(db))
    atoms.extend(mine_survival_conformal(db))
    atoms.extend(mine_dom_liquidity(db))
    atoms.extend(mine_entry_gap(db))
    db.close()

    extras = {"hard_negative_symbols": hard_syms}
    return atoms, extras
