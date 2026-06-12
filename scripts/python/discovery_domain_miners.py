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

    causal = _read_json("causal_discovery_last.json")
    if causal:
        for drv in causal.get("causal_drivers") or []:
            aid = f"causal_{str(drv).lower().replace(' ', '_')[:24]}"
            out.append(_atom(aid, "L2", "causal_discovery_last.json", "causal_discovery_miner",
                             cond={"driver": drv}, boost=1.06))
        for pair in causal.get("priority_pairs") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out.append(_atom(f"causal_{pair[0]}_{pair[1]}", "L2", "causal_discovery_last.json",
                                 "causal_discovery_miner", cond={"pair": pair}, boost=1.08))

    return out


def mine_canonical_price_atoms() -> list[dict]:
    """L0 canonical atoms from TRADING_LESSONS (pre-validated definitions)."""
    canonical = [
        "lower_third_close", "vol_2_5_3", "low20_retest", "not_near_ath",
        "bb_squeeze_low35", "range_lt4pct", "not_extended_3d",
        "vol_lt1_5", "upper_close", "high20_break", "vol_gt3", "vol_gt5",
    ]
    return [_atom(a, "L0", "ohlcv_history_execution", "price_structure_miner", boost=1.0) for a in canonical]


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
            JOIN explosion_predictions ep ON ep.symbol = ro.symbol AND ep.pred_date = ro.signal_date
            WHERE ro.outcome_filled >= 5 AND ep.explosion_prob >= 0.7
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
            SELECT category, label, mean_wr, n_obs
            FROM bayesian_wr
            WHERE n_obs >= 8
            ORDER BY mean_wr DESC
            LIMIT 25
            """
        ).fetchall()
        for cat, label, pwr, n in rows:
            wr_pct = (pwr or 0) * 100
            key = str(label or cat or "unknown").replace(" ", "_")[:40]
            if wr_pct >= 55:
                out.append(_atom(f"bayes_{key}", "L8", "bayesian_wr", "bayesian_wr_miner",
                                 cond={"category": cat, "label": label}, wr=round(wr_pct, 1),
                                 n=n, boost=1.05))
            elif wr_pct < 35 and n >= 10:
                out.append(_atom(f"bayes_loss_{key}", "L8", "bayesian_wr", "bayesian_wr_miner",
                                 cond={"category": cat, "label": label}, wr=round(wr_pct, 1),
                                 n=n, penalize=0.65, hard_neg=1))
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
            WHERE veto_triggered = 1 AND computed_at >= datetime('now', '-90 days')
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


def mine_institutional_retest(db) -> list[dict]:
    """A1 / F8 — institutional retest setups (TRADING_LESSONS +9.95% avg win)."""
    out = []
    try:
        rows = db.execute(
            """
            SELECT symbol, setup_type, MAX(score) AS score
            FROM scans
            WHERE scan_date >= date('now', '-12 days')
              AND rejected = 0
              AND (
                LOWER(setup_type) LIKE '%retest%'
                OR LOWER(setup_type) LIKE '%break%'
                OR LOWER(setup_type) LIKE '%accum%'
                OR LOWER(setup_type) LIKE '%inst%'
              )
            GROUP BY symbol
            HAVING score >= 65
            ORDER BY score DESC
            LIMIT 12
            """
        ).fetchall()
        if rows:
            out.append(_atom(
                "institutional_retest_gate", "L3", "scans", "institutional_retest_miner",
                cond={"n_symbols": len(rows)}, boost=1.09, n=len(rows),
            ))
            for sym, stype, score in rows[:8]:
                out.append(_atom(
                    f"retest_{sym}", "L3", "scans", "institutional_retest_miner",
                    cond={"symbol": sym, "setup_type": str(stype or "")[:40]},
                    boost=1.06, wr=float(score or 0), n=1,
                ))
        out.append(_atom(
            "retest_vol_ok", "L3", "scans", "institutional_retest_miner",
            cond={"vol_ratio": "2.5-3.5", "retest_confirmed": True}, boost=1.10,
        ))
        out.append(_atom(
            "retest_vol_fail", "L3", "scans", "institutional_retest_miner",
            cond={"vol_ratio_lt": 2.5, "near_ath_no_vol": True}, penalize=0.65, hard_neg=1,
        ))
    except sqlite3.OperationalError:
        pass
    return out


def mine_volume_accumulation(db) -> list[dict]:
    """A2 — volume accumulation / VCP cluster (TRADING_LESSONS 24.7% WR)."""
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(DISTINCT symbol) AS n
            FROM scans
            WHERE scan_date >= date('now', '-7 days')
              AND rejected = 0
              AND (
                LOWER(setup_type) LIKE '%accum%'
                OR LOWER(setup_type) LIKE '%vcp%'
                OR LOWER(setup_type) LIKE '%squeeze%'
                OR LOWER(setup_type) LIKE '%base%'
              )
            """
        ).fetchone()
        n = int(row[0]) if row and row[0] else 0
        if n >= 2:
            out.append(_atom(
                "volume_accumulation_cluster", "L3", "scans", "volume_accumulation_miner",
                cond={"n_symbols": n}, boost=1.10, n=n,
            ))
        out.extend([
            _atom("vol_accumulation_3d", "L0", "ohlcv_history", "volume_accumulation_miner",
                  cond={"rising_vol_days": 3, "range_compression": True}, boost=1.08),
            _atom("vol_sweet_spot_2_5_3", "L0", "ohlcv_history", "volume_accumulation_miner",
                  cond={"vol_ratio_min": 2.5, "vol_ratio_max": 3.5}, boost=1.10),
        ])
    except sqlite3.OperationalError:
        pass
    return out


def mine_quality_universe_v3(db) -> list[dict]:
    """A3 — historical quality symbols (backtest v3 WR leaders)."""
    quality = (
        "MOSC", "UTOP", "TORA", "ADRI", "AMES", "KWIN", "SNFI",
        "AALR", "HBCO", "AIFI", "WKOL", "IBCT",
    )
    out = [
        _atom("quality_universe_v3_gate", "L9", "stock_universe", "quality_universe_v3_miner",
              cond={"n_symbols": len(quality)}, boost=1.06, n=len(quality)),
    ]
    for sym in quality:
        out.append(_atom(
            f"quality_v3_{sym}", "L9", "stock_universe", "quality_universe_v3_miner",
            cond={"symbol": sym}, boost=1.05,
        ))
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n FROM recommendation_outcomes ro
            WHERE ro.symbol IN ({})
              AND ro.outcome_filled >= 5
              AND ro.hit_t5 = 1
              AND ro.signal_date >= date('now', '-365 days')
            """.format(",".join("?" * len(quality))),
            quality,
        ).fetchone()
        if row and row[0]:
            out[0]["backtest_n"] = int(row[0])
    except sqlite3.OperationalError:
        pass
    return out


def mine_near_ath_300(db) -> list[dict]:
    """B2 — Near-ATH requires 300-bar context + volume (TRADING_LESSONS #9)."""
    return [
        _atom("near_ath_300bar_vol", "L0", "ohlcv_history", "near_ath_miner",
              cond={"lookback_bars": 300, "vol_ratio_gte": 2.5}, boost=1.05),
        _atom("near_ath_no_vol_penalty", "L0", "ohlcv_history", "near_ath_miner",
              cond={"near_ath": True, "vol_ratio_lt": 2.5}, penalize=0.55, hard_neg=1),
    ]


def mine_delivery_feedback(db) -> list[dict]:
    """B4 — delivered-to-client symbol outcomes → boost/penalize atoms."""
    out = []
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(recommendation_outcomes)").fetchall()}
        if "client_delivered" not in cols:
            return out
        rows = db.execute(
            """
            SELECT symbol,
                   SUM(CASE WHEN hit_t5 = 1 THEN 1 ELSE 0 END) AS wins,
                   COUNT(*) AS n
            FROM recommendation_outcomes
            WHERE COALESCE(client_delivered, 0) = 1
              AND outcome_filled >= 5
              AND signal_date >= date('now', '-180 days')
            GROUP BY symbol
            HAVING n >= 2
            """
        ).fetchall()
        for sym, wins, n in rows:
            wr = (wins / n * 100.0) if n else 0.0
            if wr >= 45:
                out.append(_atom(
                    f"delivered_win_{sym}", "L10", "recommendation_outcomes", "delivery_feedback_miner",
                    cond={"symbol": sym}, boost=1.07, wr=round(wr, 1), n=n,
                ))
            elif wr < 20:
                out.append(_atom(
                    f"delivered_loss_{sym}", "L10", "recommendation_outcomes", "delivery_feedback_miner",
                    cond={"symbol": sym}, penalize=0.72, wr=round(wr, 1), n=n,
                ))
    except sqlite3.OperationalError:
        pass
    return out


def mine_peer_rs_leader(db) -> list[dict]:
    """A4 — sector RS leaders vs market (pine_analytics rs_score + stock_universe sector)."""
    out = []
    try:
        row = db.execute("SELECT MAX(trade_date) AS d FROM pine_analytics").fetchone()
        if not row or not row[0]:
            return out
        d = row[0]
        rows = db.execute(
            """
            SELECT p.symbol, p.rs_score, p.rs_percentile, u.sector
            FROM pine_analytics p
            LEFT JOIN stock_universe u ON u.symbol = p.symbol
            WHERE p.trade_date = ?
              AND p.rs_score IS NOT NULL
              AND p.rs_percentile IS NOT NULL
            ORDER BY p.rs_percentile DESC
            LIMIT 80
            """
            ,
            (d,),
        ).fetchall()
        if not rows:
            return out
        leaders = [r for r in rows if (r[2] or 0) >= 75]
        if leaders:
            out.append(_atom(
                "sector_rs_leader_gate", "L2", "pine_analytics", "peer_rs_leader_miner",
                cond={"min_rs_percentile": 75, "n": len(leaders)}, boost=1.07, n=len(leaders),
            ))
        by_sector: dict[str, list] = {}
        for sym, rs, pct, sector in rows:
            sec = str(sector or "UNKNOWN").strip()
            by_sector.setdefault(sec, []).append((sym, rs, pct))
        for sec, items in by_sector.items():
            if len(items) < 2:
                continue
            best = max(items, key=lambda x: x[2] or 0)
            if (best[2] or 0) >= 70:
                key = sec.replace(" ", "_")[:20]
                out.append(_atom(
                    f"peer_rs_{key}_{best[0]}", "L2", "pine_analytics", "peer_rs_leader_miner",
                    cond={"symbol": best[0], "sector": sec, "rs_percentile_gte": 70},
                    boost=1.06, wr=float(best[2] or 0), n=1,
                ))
    except sqlite3.OperationalError:
        pass
    return out


def mine_session_microstructure(db) -> list[dict]:
    """F9 / B1 — opening 30m + closing pressure patterns (EGX session-sensitive)."""
    out = []
    try:
        row = db.execute("SELECT MAX(trade_date) AS d FROM pine_analytics").fetchone()
        d = row[0] if row else None
        if d:
            opens = db.execute(
                """
                SELECT symbol, opening_range_high, opening_range_low, session_bias, rs_score
                FROM pine_analytics
                WHERE trade_date = ?
                  AND opening_range_high IS NOT NULL
                  AND opening_range_low IS NOT NULL
                  AND opening_range_high > opening_range_low
                """
                ,
                (d,),
            ).fetchall()
            bullish_open = 0
            for sym, or_h, or_l, bias, rs in opens:
                bias_u = str(bias or "").upper()
                if bias_u in {"LONG", "BULL", "BULLISH"} or (rs and rs >= 55):
                    bullish_open += 1
                    out.append(_atom(
                        f"session_open_bull_{sym}", "L2", "pine_analytics", "session_microstructure_miner",
                        cond={"symbol": sym, "session_bias": bias_u or "BULL"}, boost=1.05, n=1,
                    ))
            if bullish_open >= 3:
                out.append(_atom(
                    "session_open_cluster", "L2", "pine_analytics", "session_microstructure_miner",
                    cond={"bullish_open_count": bullish_open}, boost=1.06, n=bullish_open,
                ))
        cp_rows = db.execute(
            """
            SELECT symbol, closing_pressure, close_pos
            FROM closing_pressure_daily
            WHERE trade_date >= date('now', '-5 days')
              AND closing_pressure >= 0.55
              AND close_pos <= 0.40
            ORDER BY closing_pressure DESC
            LIMIT 10
            """
        ).fetchall()
        for sym, cp, pos in cp_rows:
            out.append(_atom(
                f"session_close_pressure_{sym}", "L2", "closing_pressure_daily",
                "session_microstructure_miner",
                cond={"symbol": sym, "closing_pressure_gte": 0.55, "close_pos_lte": 0.4},
                boost=1.08, wr=round(float(cp or 0) * 100, 1), n=1,
            ))
        if cp_rows:
            out.append(_atom(
                "session_close_pressure_cluster", "L2", "closing_pressure_daily",
                "session_microstructure_miner", cond={"n_symbols": len(cp_rows)}, boost=1.07, n=len(cp_rows),
            ))
    except sqlite3.OperationalError:
        pass
    return out


def mine_defensive_sector_rotation(db) -> list[dict]:
    """B3 — bank/services strength in risk-off (TRADING_LESSONS #5)."""
    out = []
    defensive = {"BANK", "BANKS", "FINANCIAL", "FINANCIALS", "SERVICES", "SERVICE", "INSURANCE", "FINANCE"}
    try:
        risk_off = False
        row = db.execute(
            """
            SELECT risk_on_score FROM cross_market_regime
            ORDER BY date DESC LIMIT 1
            """
        ).fetchone()
        if row and row[0] is not None and float(row[0]) < 45:
            risk_off = True
            out.append(_atom(
                "defensive_risk_off_gate", "L2", "cross_market_regime", "defensive_sector_miner",
                cond={"risk_on_score_lt": 45}, boost=1.05,
            ))
        rows = db.execute(
            """
            SELECT sector, pct_above_ema20, ad_ratio
            FROM sector_breadth_daily
            WHERE date >= date('now', '-7 days')
            ORDER BY date DESC
            """
        ).fetchall()
        seen = set()
        for sector, pct20, ad_ratio in rows[:40]:
            sec = str(sector or "").upper()
            if sec in seen:
                continue
            is_def = any(d in sec for d in defensive)
            if is_def and (pct20 or 0) >= 0.48:
                out.append(_atom(
                    f"defensive_{sec.replace(' ', '_')[:18]}", "L2", "sector_breadth_daily",
                    "defensive_sector_miner",
                    cond={"sector": sector, "risk_off": risk_off}, boost=1.08 if risk_off else 1.04,
                ))
                seen.add(sec)
    except sqlite3.OperationalError:
        pass
    return out


def mine_post_breakout_vol(db) -> list[dict]:
    """A5 / F6 — post-breakout volume decay penalize + retest volume confirm."""
    return [
        _atom("post_breakout_vol_collapse", "L0", "ohlcv_history_execution", "post_breakout_vol_miner",
              cond={"vol_ratio_next_lt": 0.4}, penalize=0.55, hard_neg=1),
        _atom("post_breakout_vol_ok", "L0", "ohlcv_history_execution", "post_breakout_vol_miner",
              cond={"vol_ratio_gte": 0.4, "days_after_breakout": 1}, boost=1.04),
        _atom("breakout_day_vol_confirm", "L0", "ohlcv_history_execution", "post_breakout_vol_miner",
              cond={"vol_ratio": "2.5-3.5", "breakout_bar": True}, boost=1.08),
    ]


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
                     SELECT o.open FROM ohlcv_history_execution oh
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


def mine_indicator_divergence(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n FROM indicators_cache ic
            JOIN ohlcv_history_execution oh ON oh.symbol = ic.symbol
              AND date(oh.bar_time,'unixepoch') = ic.bar_date
            WHERE ic.bar_date >= date('now', '-90 days')
              AND ic.obv_divergence = 'bearish'
              AND oh.close < oh.open
            """
        ).fetchone()
        if row and row[0] >= 15:
            out.append(_atom("rsi_obv_bear_divergence", "L1", "indicators_cache",
                             "indicator_divergence_miner",
                             cond={"obv_divergence": "bearish", "red_bar": True},
                             penalize=0.7, n=row[0]))
    except sqlite3.OperationalError:
        pass
    return out


def mine_markov_transition(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT p_bull_5d, current_state FROM markov_signal_daily
            ORDER BY date DESC LIMIT 1
            """
        ).fetchone()
        if row and row[0] is not None:
            p = float(row[0])
            state = row[1]
            if p >= 0.55:
                out.append(_atom("markov_p_bull_gate", "L2", "markov_signal_daily",
                                 "markov_transition_miner", cond={"p_bull_5d_gte": 0.55}, boost=1.06))
            elif p < 0.35:
                out.append(_atom("markov_p_bear_gate", "L2", "markov_signal_daily",
                                 "markov_transition_miner", cond={"p_bull_5d_lt": 0.35}, penalize=0.75))
            if state:
                out.append(_atom(f"markov_state_{str(state).lower()}", "L2", "markov_signal_daily",
                                 "markov_transition_miner", cond={"current_state": state}, boost=1.03))
    except sqlite3.OperationalError:
        pass
    return out


def mine_sector_rotation(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT sector, pct_above_ema20, ad_ratio, sector_rank, signal
            FROM sector_breadth_daily
            WHERE date >= date('now', '-30 days')
            ORDER BY date DESC
            """
        ).fetchall()
        if not rows:
            return out
        defensive = {"BANK", "BANKS", "FINANCIAL", "FINANCIALS", "SERVICES", "SERVICE", "INSURANCE"}
        seen = set()
        for sector, pct20, ad_ratio, rank, sig in rows[:80]:
            sec = str(sector or "").upper()
            if sec in seen:
                continue
            if any(d in sec for d in defensive) and (pct20 or 0) >= 0.5:
                out.append(_atom("sector_defensive_strength", "L9", "sector_breadth_daily",
                                 "sector_rotation_miner", cond={"sector": sector, "pct_above_ema20_gte": 0.5},
                                 boost=1.05))
                seen.add(sec)
            if (ad_ratio or 0) >= 1.2 and (pct20 or 0) >= 0.6:
                key = sec.replace(" ", "_")[:24]
                out.append(_atom(f"sector_momo_{key}", "L9", "sector_breadth_daily",
                                 "sector_rotation_miner", cond={"sector": sector}, boost=1.04, n=1))
                seen.add(sec)
    except sqlite3.OperationalError:
        pass
    return out


def mine_grid_winners(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT top_hyp_id, top_exp, n_valid, n_tested
            FROM grid_runs
            WHERE n_valid >= 1 AND top_exp IS NOT NULL
            ORDER BY top_exp DESC LIMIT 3
            """
        ).fetchall()
        for hyp, exp, nval, ntest in rows:
            out.append(_atom(f"grid_winner_{str(hyp)[:12]}", "L9", "grid_runs", "grid_winner_miner",
                             cond={"top_hyp_id": hyp}, wr=min(99.0, float(exp or 0)), n=ntest or nval,
                             boost=1.04))
        wf = db.execute(
            """
            SELECT window_id, win_rate, auc_test, n_signals
            FROM walkforward_results
            WHERE win_rate >= 0.5 AND n_signals >= 10
            ORDER BY win_rate DESC LIMIT 3
            """
        ).fetchall()
        for wid, wr, auc, n in wf:
            out.append(_atom(f"wf_win_w{wid}", "L9", "walkforward_results", "grid_winner_miner",
                             cond={"window_id": wid}, wr=round((wr or 0) * 100, 1), n=n, boost=1.05))
    except sqlite3.OperationalError:
        pass
    return out


def mine_dmids_structural(db) -> list[dict]:
    """DMIDS structural laws → fabric atoms (unified path with structural_laws_bridge)."""
    out = []
    kb = DATA / "knowledge_base"
    if not kb.exists():
        return out
    files = sorted(kb.glob("structural_laws_*.json"), reverse=True)
    if not files:
        return out
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        laws = data.get("laws") or data.get("up_laws") or []
        up, down = [], []
        for l in laws:
            dirs = l.get("directions") or l.get("direction") or l.get("bias") or []
            if isinstance(dirs, str):
                dirs = [dirs]
            sup = float(l.get("support_pct") or l.get("support") or 0)
            if any(str(d).upper() in ("UP", "BULL", "LONG", "BULLISH") for d in dirs):
                up.append(l)
            elif any(str(d).upper() in ("DOWN", "BEAR", "SHORT", "BEARISH") for d in dirs):
                if sup >= 25:
                    down.append(l)
            elif sup >= 60:
                up.append(l)
        if up:
            out.append(_atom("dmids_up_law_gate", "L9", files[0].name, "dmids_structural_miner",
                             cond={"n_up_laws": len(up), "kb_date": files[0].stem}, boost=1.05, n=len(up)))
            for law in up[:12]:
                lid = str(law.get("id") or law.get("law_number") or "law")[:24]
                sup = float(law.get("support_pct") or 0)
                eff = float(law.get("effect_size") or 1.0)
                boost = min(1.12, 1.02 + eff * 0.02) if eff else 1.04
                out.append(_atom(f"dmids_{lid}", "L9", files[0].name, "dmids_structural_miner",
                                 cond={"law_id": lid, "title": (law.get("title") or "")[:60]},
                                 boost=boost, wr=sup, n=int(law.get("n_samples") or 1)))
        for law in down[:4]:
            lid = str(law.get("id") or law.get("law_number") or "law")[:24]
            out.append(_atom(f"dmids_penalize_{lid}", "L9", files[0].name, "dmids_structural_miner",
                             cond={"law_id": lid, "bias": "DOWN"}, penalize=0.75, hard_neg=0,
                             wr=float(law.get("support_pct") or 0), n=1))
        bridge = _read_json("egx_rules_runtime.json")
        if bridge and bridge.get("structural_laws"):
            out.append(_atom("dmids_runtime_sync", "L9", "egx_rules_runtime.json", "dmids_structural_miner",
                             cond={"n_runtime_laws": len(bridge["structural_laws"])}, boost=1.02))
    except Exception:
        pass
    return out


def mine_egx_x_pro(db: sqlite3.Connection) -> list[dict]:
    """EGX-X Pro liquidity/RS scores → discovery atoms."""
    out = []
    try:
        row = db.execute("SELECT MAX(trade_date) AS d FROM egx_x_pro_daily").fetchone()
        td = row[0] if row else None
        if not td:
            return out
        top = db.execute(
            """
            SELECT symbol, x_score, stage, rs_market_score, liquidity_expansion_score, compression_score
            FROM egx_x_pro_daily
            WHERE trade_date=? AND x_score >= 70
            ORDER BY x_score DESC LIMIT 8
            """,
            (td,),
        ).fetchall()
        if top:
            out.append(_atom("xpro_high_score_gate", "L2", "egx_x_pro_daily", "egx_x_pro_miner",
                             cond={"trade_date": td, "n_high": len(top)}, boost=1.06, n=len(top)))
        for sym, xs, stage, rs_m, liq, comp in top:
            out.append(_atom(f"xpro_{sym}", "L2", "egx_x_pro_daily", "egx_x_pro_miner",
                             cond={"symbol": sym, "stage": stage}, boost=1.04,
                             wr=float(xs or 0), n=1))
            if liq and float(liq) >= 65:
                out.append(_atom(f"xpro_liq_exp_{sym}", "L2", "egx_x_pro_daily", "egx_x_pro_miner",
                                 cond={"symbol": sym, "liquidity_expansion": float(liq)}, boost=1.05))
            if comp and float(comp) >= 60:
                out.append(_atom(f"xpro_compress_{sym}", "L2", "egx_x_pro_daily", "egx_x_pro_miner",
                                 cond={"symbol": sym, "compression": float(comp)}, boost=1.04))
    except sqlite3.OperationalError:
        pass
    return out


def mine_scans_setup(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT setup_type, COUNT(*) n,
                   AVG(CASE WHEN score >= 70 THEN 1.0 ELSE 0 END) hi_score_pct
            FROM scans
            WHERE scan_date >= date('now', '-60 days')
            GROUP BY setup_type
            HAVING n >= 20
            ORDER BY hi_score_pct DESC LIMIT 3
            """
        ).fetchall()
        for setup, n, pct in row:
            if pct and pct >= 0.25:
                key = str(setup or "unknown").replace(" ", "_")[:32]
                out.append(_atom(f"scan_{key}", "L3", "scans", "scans_setup_miner",
                                 cond={"setup_type": setup, "score_gte": 70},
                                 boost=1.05, n=n))
    except sqlite3.OperationalError:
        pass
    return out


def mine_sandbox_hypotheses(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT hypothesis_id, status, precision, n_samples
            FROM sandbox_hypotheses
            WHERE UPPER(status) IN ('PROMOTED', 'ACTIVE', 'VALIDATED')
            ORDER BY precision DESC LIMIT 10
            """
        ).fetchall()
        for hid, status, prec, n in rows:
            wr_pct = (prec or 0) * 100 if (prec or 0) <= 1 else (prec or 0)
            out.append(_atom(f"sandbox_{str(hid)[:16]}", "L9", "sandbox_hypotheses",
                             "hypothesis_sandbox_bridge", cond={"hypothesis_id": hid, "status": status},
                             wr=round(wr_pct, 1), n=n or 1, boost=1.06))
    except sqlite3.OperationalError:
        pass
    return out


def mine_setup_performance(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT setup_type, win_rate, total_trades
            FROM setup_performance
            WHERE total_trades >= 3
            ORDER BY win_rate DESC
            """
        ).fetchall()
        for setup, wr, n in rows:
            key = str(setup or "setup").replace(" ", "_")[:28]
            wr_pct = (wr or 0) * 100 if (wr or 0) <= 1 else (wr or 0)
            if wr_pct >= 20:
                out.append(_atom(f"setup_perf_{key}", "L3", "setup_performance", "setup_performance_miner",
                                 cond={"setup_type": setup}, wr=round(wr_pct, 1), n=n, boost=1.05))
    except sqlite3.OperationalError:
        pass
    return out


def mine_pine_analytics(db) -> list[dict]:
    out = []
    try:
        row = db.execute("SELECT COUNT(*) FROM pine_analytics WHERE trade_date >= date('now', '-30 days')").fetchone()
        if row and row[0] >= 50:
            out.append(_atom("pine_analytics_fresh", "L2", "pine_analytics", "pine_analytics_miner",
                             cond={"min_rows_30d": 50}, boost=1.03, n=row[0]))
    except sqlite3.OperationalError:
        pass
    return out


def mine_markov_regime(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT hmm_state_label, COUNT(*) n FROM markov_regime_daily
            WHERE hmm_state_label IS NOT NULL
            GROUP BY hmm_state_label ORDER BY n DESC LIMIT 1
            """
        ).fetchone()
        if row and row[0]:
            reg = str(row[0]).upper()
            out.append(_atom(f"markov_regime_{reg.lower()}", "L2", "markov_regime_daily",
                             "markov_regime_miner", cond={"hmm_state_label": reg}, boost=1.04, n=row[1]))
    except sqlite3.OperationalError:
        pass
    return out


def mine_delivery_p6(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n,
                   AVG(CASE WHEN send_success=1 OR deliverable=1 THEN 1.0 ELSE 0 END) del_pct
            FROM notification_delivery_audit
            WHERE signal_date >= date('now', '-90 days')
            """
        ).fetchone()
        if row and row[0] >= 5:
            out.append(_atom("p6_delivery_audit", "L10", "notification_delivery_audit",
                             "delivery_audit_miner", cond={"delivered_track": True},
                             wr=round((row[1] or 0) * 100, 1), n=row[0], boost=1.0))
    except sqlite3.OperationalError:
        pass
    return out


def mine_sector_rotation_daily(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT sector, AVG(rotation_score) avg_rot, COUNT(*) n
            FROM sector_rotation_daily
            WHERE date >= date('now', '-60 days') AND rotation_score IS NOT NULL
            GROUP BY sector HAVING n >= 10
            ORDER BY avg_rot DESC LIMIT 8
            """
        ).fetchall()
        for sector, avg_rot, n in rows:
            if avg_rot and avg_rot >= 0.5:
                key = str(sector or "sec").replace(" ", "_")[:20]
                out.append(_atom(f"srot_{key}", "L2", "sector_rotation_daily",
                                 "sector_rotation_daily_miner", cond={"sector": sector},
                                 boost=1.04, n=n))
    except sqlite3.OperationalError:
        pass
    return out


def mine_explosive_moves(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n,
                   AVG(CASE WHEN direction='UP' THEN 1.0 ELSE 0 END) up_pct
            FROM explosive_moves
            WHERE explosion_date >= date('now', '-180 days')
            """
        ).fetchone()
        if row and row[0] >= 100:
            out.append(_atom("explosive_up_bias", "L4", "explosive_moves", "explosive_moves_miner",
                             cond={"direction": "UP"}, wr=round((row[1] or 0) * 100, 1), n=row[0], boost=1.05))
        rows = db.execute(
            """
            SELECT symbol, COUNT(*) n FROM explosive_moves
            WHERE explosion_date >= date('now', '-365 days')
            GROUP BY symbol HAVING n >= 5
            ORDER BY n DESC LIMIT 15
            """
        ).fetchall()
        for sym, n in rows:
            out.append(_atom(f"expl_hist_{sym}", "L4", "explosive_moves", "explosive_moves_miner",
                             cond={"symbol": sym}, n=n, boost=1.03))
    except sqlite3.OperationalError:
        pass
    return out


def mine_market_experience(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT experience_type, COUNT(*) n, AVG(outcome_score) avg_o
            FROM market_experience
            WHERE created_at >= datetime('now', '-120 days')
            GROUP BY experience_type
            HAVING n >= 50
            ORDER BY avg_o DESC LIMIT 5
            """
        ).fetchall()
        for etype, n, avg_o in row:
            key = str(etype or "exp")[:24]
            out.append(_atom(f"mexp_{key}", "L8", "market_experience", "market_experience_miner",
                             cond={"experience_type": etype}, n=n, boost=1.04))
    except sqlite3.OperationalError:
        pass
    return out


def mine_anti_law(db) -> list[dict]:
    out = []
    try:
        row = db.execute(
            """
            SELECT COUNT(*) n FROM anti_law_daily_scan
            WHERE date >= date('now', '-60 days') AND anti_law_veto = 1
            """
        ).fetchone()
        if row and row[0] >= 20:
            out.append(_atom("anti_law_veto_active", "L2", "anti_law_daily_scan",
                             "anti_law_miner", cond={"anti_law_veto": 1}, penalize=0.7,
                             hard_neg=1, n=row[0]))
        row2 = db.execute(
            """
            SELECT COUNT(*) n FROM anti_law_daily_scan
            WHERE date >= date('now', '-60 days') AND (anti_law_veto = 0 OR anti_law_veto IS NULL)
            """
        ).fetchone()
        if row2 and row2[0] >= 100:
            out.append(_atom("anti_law_clean", "L2", "anti_law_daily_scan", "anti_law_miner",
                             cond={"anti_law_veto": 0}, boost=1.04, n=row2[0]))
    except sqlite3.OperationalError:
        pass
    return out


def mine_stock_profiles(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT symbol, momentum_success_rate, avg_atr_pct
            FROM stock_profiles_deep
            WHERE momentum_success_rate >= 0.4
            ORDER BY momentum_success_rate DESC LIMIT 15
            """
        ).fetchall()
        for sym, msr, atr in rows:
            out.append(_atom(f"profile_{sym}", "L9", "stock_profiles_deep", "stock_profiles_miner",
                             cond={"symbol": sym}, wr=round((msr or 0) * 100, 1), boost=1.04, n=1))
    except sqlite3.OperationalError:
        pass
    return out


def mine_meta_label(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT symbol, AVG(meta_prob) avg_p, COUNT(*) n
            FROM meta_label_scores
            WHERE date >= date('now', '-90 days')
            GROUP BY symbol HAVING n >= 3 AND avg_p >= 0.6
            ORDER BY avg_p DESC LIMIT 10
            """
        ).fetchall()
        for sym, avg_p, n in rows:
            out.append(_atom(f"meta_{sym}", "L4", "meta_label_scores", "meta_label_miner",
                             cond={"symbol": sym, "meta_prob_gte": 0.6},
                             wr=round((avg_p or 0) * 100, 1), n=n, boost=1.05))
    except sqlite3.OperationalError:
        pass
    return out


def mine_validation_results(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT pattern_name, support_rate, n_samples, effect_size
            FROM validation_results
            WHERE n_samples >= 20 AND support_rate >= 0.2
            ORDER BY effect_size DESC LIMIT 12
            """
        ).fetchall()
        for pname, sr, n, es in rows:
            key = str(pname or "pat").replace(" ", "_")[:28]
            out.append(_atom(f"val_{key}", "L3", "validation_results", "validation_results_miner",
                             cond={"pattern_name": pname}, wr=round((sr or 0) * 100, 1),
                             n=n, boost=1.04))
    except sqlite3.OperationalError:
        pass
    return out


def mine_law_competition(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT pattern_name, variant_name, variant_precision, improvement_pp
            FROM law_competition
            WHERE beats_base = 1 AND improvement_pp >= 3
            ORDER BY improvement_pp DESC LIMIT 8
            """
        ).fetchall()
        for pname, vname, prec, imp in rows:
            key = f"{pname}_{vname}".replace(" ", "_")[:32]
            out.append(_atom(f"law_{key}", "L9", "law_competition", "law_competition_miner",
                             cond={"pattern_name": pname, "variant": vname},
                             wr=round((prec or 0) * 100 if (prec or 0) <= 1 else prec or 0, 1),
                             boost=1.05, n=1))
    except sqlite3.OperationalError:
        pass
    return out


def mine_contagion(db) -> list[dict]:
    out = []
    try:
        rows = db.execute(
            """
            SELECT source, target, edge_weight
            FROM contagion_network
            WHERE edge_weight >= 0.5
            ORDER BY edge_weight DESC LIMIT 5
            """
        ).fetchall()
        for src, tgt, w in rows:
            out.append(_atom(f"contagion_{src}_{tgt}", "L2", "contagion_network", "contagion_miner",
                             cond={"source": src, "target": tgt}, boost=1.03, n=1))
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
    atoms.extend(mine_indicator_divergence(db))
    atoms.extend(mine_scans_setup(db))
    atoms.extend(mine_setup_performance(db))
    atoms.extend(mine_outcome_weighted(db))
    ml_atoms, hard_syms = mine_ml_errors(db)
    atoms.extend(ml_atoms)
    atoms.extend(mine_bayesian_wr(db))
    atoms.extend(mine_arbitration_vetoes(db))
    atoms.extend(mine_alpha_universe(db))
    atoms.extend(mine_breadth_regime(db))
    atoms.extend(mine_markov_transition(db))
    atoms.extend(mine_sector_rotation(db))
    atoms.extend(mine_grid_winners(db))
    atoms.extend(mine_dmids_structural(db))
    atoms.extend(mine_egx_x_pro(db))
    atoms.extend(mine_sandbox_hypotheses(db))
    atoms.extend(mine_pine_analytics(db))
    atoms.extend(mine_markov_regime(db))
    atoms.extend(mine_delivery_p6(db))
    atoms.extend(mine_sector_rotation_daily(db))
    atoms.extend(mine_explosive_moves(db))
    atoms.extend(mine_market_experience(db))
    atoms.extend(mine_anti_law(db))
    atoms.extend(mine_stock_profiles(db))
    atoms.extend(mine_meta_label(db))
    atoms.extend(mine_validation_results(db))
    atoms.extend(mine_law_competition(db))
    atoms.extend(mine_contagion(db))
    atoms.extend(mine_spectral(db))
    atoms.extend(mine_institutional_retest(db))
    atoms.extend(mine_volume_accumulation(db))
    atoms.extend(mine_quality_universe_v3(db))
    atoms.extend(mine_near_ath_300(db))
    atoms.extend(mine_delivery_feedback(db))
    atoms.extend(mine_peer_rs_leader(db))
    atoms.extend(mine_session_microstructure(db))
    atoms.extend(mine_defensive_sector_rotation(db))
    atoms.extend(mine_post_breakout_vol(db))
    atoms.extend(mine_cross_market(db))
    atoms.extend(mine_tsfresh_patterns(db))
    atoms.extend(mine_survival_conformal(db))
    atoms.extend(mine_dom_liquidity(db))
    atoms.extend(mine_entry_gap(db))
    db.close()

    extras = {"hard_negative_symbols": hard_syms}
    return atoms, extras
