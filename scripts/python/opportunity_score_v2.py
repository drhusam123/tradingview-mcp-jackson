#!/usr/bin/env python3
"""
Opportunity Score Engine v2
===========================

Institutional-style EGX opportunity ranking:
market -> sector -> stock -> liquidity -> behavior -> structure -> risk.

This is a discovery/ranking layer. Client-ready recommendations still require
the final_signals gate and proof-pack validation.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discovery_constants import FINAL_SIGNALS_PROD_WHERE  # noqa: E402

# TRADING_LESSONS v3 historical quality symbols
QUALITY_SYMBOLS_V3 = frozenset({
    "MOSC", "UTOP", "TORA", "ADRI", "AMES", "KWIN", "SNFI",
    "AALR", "HBCO", "AIFI", "WKOL", "IBCT",
})

TV_ATOM_BOOSTS = {
    "VWAP_RECLAIM": {"smart_money": 8.0, "structure": 4.0, "flag": "TV_VWAP_RECLAIM"},
    "VP_POC_RECLAIM": {"structure": 7.0, "smart_money": 4.0, "flag": "TV_VP_POC_RECLAIM"},
    "ABSORPTION_BAR": {"smart_money": 10.0, "behavioral": 5.0, "flag": "TV_ABSORPTION"},
    "PARTICIPATION_SHOCK": {"liquidity": 6.0, "flag": "TV_PARTICIPATION_SHOCK"},
    "CVD_BULL_DIV_PROXY": {"smart_money": 9.0, "structure": 3.0, "flag": "TV_CVD_BULL_DIV"},
    "CMF_POSITIVE_PROXY": {"smart_money": 5.0, "rs_sector": 3.0, "flag": "TV_CMF_POSITIVE"},
}


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH), timeout=60)
    db.row_factory = sqlite3.Row
    return db


def table_exists(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(db, table):
        return set()
    return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}


def clamp(x: Optional[float], lo: float = 0.0, hi: float = 100.0) -> float:
    if x is None or isinstance(x, str):
        return lo
    try:
        if math.isnan(float(x)) or math.isinf(float(x)):
            return lo
        return max(lo, min(hi, float(x)))
    except Exception:
        return lo


def safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def bar_date(ts: Any) -> str:
    return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")


def ema(values: Sequence[float], n: int) -> Optional[float]:
    vals = [safe_float(v) for v in values if safe_float(v) is not None]
    if len(vals) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = mean(vals[:n])
    for v in vals[n:]:
        e = v * k + e * (1.0 - k)
    return e


def ret(bars: Sequence[sqlite3.Row], n: int) -> Optional[float]:
    if len(bars) <= n:
        return None
    a = safe_float(bars[-n - 1]["close"])
    b = safe_float(bars[-1]["close"])
    if not a or a <= 0 or b is None:
        return None
    return (b / a) - 1.0


def avg_volume(bars: Sequence[sqlite3.Row], n: int) -> Optional[float]:
    if len(bars) < n:
        return None
    vals = [safe_float(b["volume"], 0.0) or 0.0 for b in bars[-n:]]
    return mean(vals) if vals else None


def atr_pct(bars: Sequence[sqlite3.Row], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        high = safe_float(bars[i]["high"])
        low = safe_float(bars[i]["low"])
        prev = safe_float(bars[i - 1]["close"])
        if high is None or low is None or prev is None:
            continue
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    close = safe_float(bars[-1]["close"])
    if not trs or not close:
        return None
    return mean(trs) / close


def close_position(bar: sqlite3.Row) -> Optional[float]:
    high = safe_float(bar["high"])
    low = safe_float(bar["low"])
    close = safe_float(bar["close"])
    if high is None or low is None or close is None or high <= low:
        return None
    return (close - low) / (high - low)


def range_pct(bar: sqlite3.Row) -> Optional[float]:
    high = safe_float(bar["high"])
    low = safe_float(bar["low"])
    close = safe_float(bar["close"])
    if high is None or low is None or not close:
        return None
    return (high - low) / close


def load_bars(db: sqlite3.Connection) -> Dict[str, List[sqlite3.Row]]:
    from db_ohlcv import OHLCV_TABLE
    source = OHLCV_TABLE if table_exists(db, OHLCV_TABLE) else "ohlcv_history"
    rows = db.execute(
        f"""
        SELECT h.symbol, h.bar_time, h.open, h.high, h.low, h.close, h.volume,
               COALESCE(su.sector, 'Unknown') AS sector
        FROM {source} h
        LEFT JOIN stock_universe su ON su.symbol = h.symbol
        WHERE h.close IS NOT NULL AND h.close > 0
        ORDER BY h.symbol, h.bar_time
        """
    ).fetchall()
    by_symbol: Dict[str, List[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)
    return dict(by_symbol)


def latest_market_date(by_symbol: Dict[str, List[sqlite3.Row]]) -> str:
    mx = max(int(rows[-1]["bar_time"]) for rows in by_symbol.values() if rows)
    return bar_date(mx)


def latest_rows(db: sqlite3.Connection, table: str, date_col: str, key_col: str = "symbol") -> Dict[str, sqlite3.Row]:
    if not table_exists(db, table):
        return {}
    rows = db.execute(
        f"""
        SELECT t.*
        FROM {table} t
        JOIN (
          SELECT {key_col} AS k, MAX({date_col}) AS d
          FROM {table}
          GROUP BY {key_col}
        ) x ON x.k = t.{key_col} AND x.d = t.{date_col}
        """
    ).fetchall()
    return {r[key_col]: r for r in rows if r[key_col] is not None}


def latest_final_signals(db: sqlite3.Connection) -> Dict[str, sqlite3.Row]:
    if not table_exists(db, "final_signals"):
        return {}
    d = db.execute(
        f"SELECT MAX(trade_date) AS d FROM final_signals WHERE {FINAL_SIGNALS_PROD_WHERE}"
    ).fetchone()["d"]
    if not d:
        return {}
    rows = db.execute("SELECT * FROM final_signals WHERE trade_date=?", (d,)).fetchall()
    return {r["symbol"]: r for r in rows}


def latest_breadth(db: sqlite3.Connection) -> Optional[sqlite3.Row]:
    for table in ("market_breadth_enhanced", "market_breadth_daily", "market_breadth_history"):
        if table_exists(db, table):
            date_col = "date" if "date" in columns(db, table) else "computed_date"
            row = db.execute(f"SELECT * FROM {table} ORDER BY {date_col} DESC LIMIT 1").fetchone()
            if row:
                return row
    return None


def market_context(db: sqlite3.Connection, by_symbol: Dict[str, List[sqlite3.Row]]) -> Tuple[float, Dict[str, Any]]:
    market_bars = by_symbol.get("EGX30") or by_symbol.get("EGX30TR") or []
    evidence: Dict[str, Any] = {}
    if len(market_bars) >= 220:
        closes = [safe_float(b["close"], 0.0) or 0.0 for b in market_bars]
        last = closes[-1]
        e50 = ema(closes, 50)
        e200 = ema(closes, 200)
        r20 = ret(market_bars, 20) or 0.0
        score = 35.0
        if e50 and last > e50:
            score += 20.0
        if e200 and last > e200:
            score += 20.0
        score += clamp(50 + r20 * 700, 0, 20)
        evidence.update({"market_symbol": market_bars[-1]["symbol"], "market_ret20": round(r20, 4)})
    else:
        stock_rets = [ret(rows, 20) for s, rows in by_symbol.items() if s != "EGX30" and len(rows) > 80]
        vals = [v for v in stock_rets if v is not None]
        med = median(vals) if vals else 0.0
        score = clamp(50 + med * 700, 20, 80)
        evidence.update({"market_symbol": "EGX_UNIVERSE_PROXY", "market_ret20": round(med, 4)})

    breadth = latest_breadth(db)
    if breadth:
        bcols = set(breadth.keys())
        bscore = safe_float(breadth["breadth_score"], None) if "breadth_score" in bcols else None
        if bscore is not None:
            score = 0.65 * score + 0.35 * clamp(bscore)
            evidence["breadth_score"] = round(bscore, 2)
        elif "pct_above_ema50" in bcols:
            p = safe_float(breadth["pct_above_ema50"], 0.0) or 0.0
            if p <= 1.5:
                p *= 100
            score = 0.70 * score + 0.30 * clamp(p)
            evidence["pct_above_ema50"] = round(p, 2)
        if "signal" in bcols:
            evidence["breadth_signal"] = breadth["signal"]
    return clamp(score), evidence


def sector_context(db: sqlite3.Connection, by_symbol: Dict[str, List[sqlite3.Row]]) -> Dict[str, Dict[str, float]]:
    sector_returns: Dict[str, List[float]] = defaultdict(list)
    sector_above50: Dict[str, List[float]] = defaultdict(list)
    for symbol, rows in by_symbol.items():
        if symbol.startswith("EGX") or len(rows) < 80:
            continue
        sector = rows[-1]["sector"] or "Unknown"
        r20 = ret(rows, 20)
        if r20 is not None:
            sector_returns[sector].append(r20)
        closes = [safe_float(b["close"], 0.0) or 0.0 for b in rows]
        e50 = ema(closes, 50)
        if e50:
            sector_above50[sector].append(1.0 if closes[-1] > e50 else 0.0)

    raw = {}
    for sector, vals in sector_returns.items():
        raw[sector] = {
            "ret20": mean(vals) if vals else 0.0,
            "above50": mean(sector_above50.get(sector, [0.5])) * 100.0,
        }
    if not raw:
        return {}

    ranked = sorted(raw.items(), key=lambda kv: kv[1]["ret20"], reverse=True)
    n = max(1, len(ranked) - 1)
    out = {}
    for i, (sector, vals) in enumerate(ranked):
        rank_score = 100.0 * (1.0 - i / n)
        out[sector] = {
            "sector_strength_score": clamp(0.65 * rank_score + 0.35 * vals["above50"]),
            "sector_ret20": vals["ret20"],
            "sector_rank": i + 1,
            "sector_above50": vals["above50"],
        }

    if table_exists(db, "sector_breadth_daily"):
        try:
            rows = db.execute(
                """
                SELECT s.*
                FROM sector_breadth_daily s
                JOIN (SELECT MAX(date) AS d FROM sector_breadth_daily) x ON x.d = s.date
                """
            ).fetchall()
            for r in rows:
                sector = r["sector"]
                if sector in out:
                    pct = safe_float(r["pct_above_ema50"], None)
                    mom = safe_float(r["momentum_5d"], None)
                    if pct is not None:
                        pct = pct * 100 if pct <= 1.5 else pct
                        out[sector]["sector_strength_score"] = clamp(
                            0.75 * out[sector]["sector_strength_score"] + 0.25 * pct
                        )
                    if mom is not None:
                        out[sector]["sector_momentum_5d"] = mom
        except Exception:
            pass
    return out


def recent_consolidation_days(bars: Sequence[sqlite3.Row], lookback: int = 80) -> int:
    if len(bars) < 25:
        return 0
    tail = bars[-lookback:]
    closes = [safe_float(b["close"], 0.0) or 0.0 for b in tail]
    high20 = max(closes[-20:])
    low20 = min(closes[-20:])
    if not high20:
        return 0
    band = (high20 - low20) / high20
    days = 0
    for b in reversed(tail):
        c = safe_float(b["close"])
        if c is None:
            break
        if low20 * 0.97 <= c <= high20 * 1.03:
            days += 1
        else:
            break
    if band > 0.18:
        days = int(days * 0.65)
    return days


def score_symbol(
    symbol: str,
    rows: Sequence[sqlite3.Row],
    market_score: float,
    market_ev: Dict[str, Any],
    sectors: Dict[str, Dict[str, float]],
    liq: Dict[str, sqlite3.Row],
    pine: Dict[str, sqlite3.Row],
    dna: Dict[str, sqlite3.Row],
    memory: Dict[str, sqlite3.Row],
    finals: Dict[str, sqlite3.Row],
    universe_market_ret20: float,
    opp_tuning: Optional[Dict[str, Any]] = None,
    tv_features: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if len(rows) < 80 or symbol.startswith("EGX"):
        return None

    latest = rows[-1]
    sector = latest["sector"] or "Unknown"
    closes = [safe_float(b["close"], 0.0) or 0.0 for b in rows]
    close = closes[-1]
    if close <= 0:
        return None

    r5 = ret(rows, 5) or 0.0
    r20 = ret(rows, 20) or 0.0
    r60 = ret(rows, 60) or 0.0
    e10 = ema(closes, 10)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    atr = atr_pct(rows, 14)
    cp = close_position(latest)
    rp = range_pct(latest)
    vol5 = avg_volume(rows, 5) or 0.0
    vol10 = avg_volume(rows, 10) or 0.0
    vol60 = avg_volume(rows, 60) or 0.0
    vol_ratio = (vol10 / vol60) if vol60 > 0 else 1.0
    dry_ratio = (vol5 / vol60) if vol60 > 0 else 1.0
    cons_days = recent_consolidation_days(rows)

    sector_ev = sectors.get(sector, {})
    sector_score = sector_ev.get("sector_strength_score", 45.0)
    sector_ret20 = sector_ev.get("sector_ret20", universe_market_ret20)

    rs_market = r20 - universe_market_ret20
    rs_sector = r20 - sector_ret20
    rs_market_score = clamp(50 + rs_market * 550 + (8 if r5 > 0 else -4))
    rs_sector_score = clamp(50 + rs_sector * 550)

    liq_row = liq.get(symbol)
    liq_base = safe_float(liq_row["liquidity_score"], None) if liq_row else None
    if liq_base is None:
        liq_base = clamp(45 + min(vol_ratio, 4.0) * 12)
    vol10_vs20 = 1.0
    if len(rows) >= 21:
        v20 = avg_volume(rows, 20) or 0.0
        if v20 > 0:
            vol10_vs20 = vol10 / v20
    liquidity_expansion_score = clamp(0.45 * liq_base + 0.55 * (
        35 if vol_ratio < 0.55 else
        58 if vol_ratio < 0.95 else
        72 if vol_ratio < 1.5 else
        88 if vol_ratio <= 3.0 else
        70
    ))
    # TRADING_LESSONS #10 — volume 2.5–3x sweet spot (session vol proxy)
    if 2.5 <= vol10_vs20 <= 3.2:
        liquidity_expansion_score = clamp(liquidity_expansion_score + 10)
    elif vol10_vs20 > 4.5:
        liquidity_expansion_score = clamp(liquidity_expansion_score - 8)

    dry_score = clamp(95 - abs(dry_ratio - 0.42) * 80)
    compression_score = clamp(cons_days * 2.0 + (20 if atr is not None and atr < 0.055 else 0))
    dna_row = dna.get(symbol)
    mem_row = memory.get(symbol)
    dna_score = 50.0
    if dna_row:
        dna_score += clamp((safe_float(dna_row["explosion_rate_pct"], 0.0) or 0.0) - 25, -15, 20)
        dna_score += clamp((safe_float(dna_row["best_precursor_precision"], 0.0) or 0.0) * 35, 0, 25)
        if str(dna_row["drift_direction"]).upper() in {"ACCELERATING", "INCREASING"}:
            dna_score += 8
        if str(dna_row["archetype"]).upper().startswith("EXPLOSIVE"):
            dna_score += 7
    if mem_row:
        dna_score += clamp((safe_float(mem_row["best_precursor_sr"], 0.0) or 0.0) * 25, 0, 18)
        dna_score -= clamp((safe_float(mem_row["false_signal_rate"], 0.0) or 0.0) * 20, 0, 15)
    behavioral_score = clamp(0.38 * dry_score + 0.32 * compression_score + 0.30 * dna_score)

    above50 = bool(e50 and close > e50)
    above200 = bool(e200 and close > e200)
    align = bool(e10 and e21 and e50 and e10 >= e21 >= e50)
    high20 = max(closes[-20:])
    high60 = max(closes[-60:])
    low20 = min(closes[-20:])
    structure_score = 35.0
    if above50:
        structure_score += 18
    if above200:
        structure_score += 16
    if align:
        structure_score += 12
    if close >= high20 * 0.94:
        structure_score += 10
    if close >= high60 * 0.88 and low20 > min(closes[-60:]) * 1.03:
        structure_score += 9
    if cp is not None:
        structure_score += (cp - 0.5) * 12
        # TRADING_LESSONS #8 — lower third close = highest WR zone
        if cp <= 0.33:
            structure_score += 8
        elif cp >= 0.82:
            structure_score -= 5
    structure_score = clamp(structure_score)

    atrv = atr or 0.0
    risk_score = 70.0
    if atrv <= 0:
        risk_score = 45.0
    elif atrv < 0.018:
        risk_score = 58.0
    elif atrv <= 0.085:
        risk_score = 88.0 - abs(atrv - 0.045) * 280
    else:
        risk_score = 45.0
    if liq_row and str(liq_row["liquidity_tier"]).upper() in {"D", "E", "ILLIQUID"}:
        risk_score -= 18
    risk_score = clamp(risk_score)

    absorption = False
    if rp is not None and vol_ratio >= 1.45:
        avg_range = mean([range_pct(b) or 0.0 for b in rows[-20:]])
        absorption = rp <= avg_range * 0.75 and abs(r5) < 0.05
    smart_money_score = clamp(
        0.45 * liquidity_expansion_score
        + 0.30 * dry_score
        + (20 if absorption else 0)
        + (8 if cp is not None and 0.35 <= cp <= 0.75 else 0)
    )

    failure_penalty = 0.0
    high300 = max(closes[-300:]) if len(closes) >= 60 else high60
    pct_from_ath = (high300 - close) / high300 if high300 > 0 else 1.0
    # TRADING_LESSONS #1 — near ATH without volume confirmation
    if pct_from_ath <= 0.03 and vol10_vs20 < 2.5:
        failure_penalty += 14
    # TRADING_LESSONS #2 / A5 — post-breakout session volume collapse
    post_breakout_vol_collapse = False
    if len(rows) >= 6:
        prior_vols = [float(b.get("volume") or 0) for b in rows[-6:-1]]
        today_vol = float(rows[-1].get("volume") or 0)
        avg20v = avg_volume(rows, 20) or 0.0
        if prior_vols and avg20v > 0:
            peak = max(prior_vols)
            if peak >= avg20v * 2.5 and today_vol < peak * 0.4:
                post_breakout_vol_collapse = True
                failure_penalty += 12
    false_rate = safe_float(dna_row["false_breakout_rate_pct"], None) if dna_row else None
    if false_rate is not None:
        failure_penalty += clamp(false_rate - 35, 0, 25)
    if symbol in finals and finals[symbol]["veto_reason"]:
        failure_penalty += 10
    recent_wick_fail = False
    if len(rows) >= 3:
        y = rows[-2]
        yh = safe_float(y["high"])
        yc = safe_float(y["close"])
        if yh and yc and yh >= high60 * 0.99 and yc < yh * 0.94:
            recent_wick_fail = True
            failure_penalty += 8
    mem_row = memory.get(symbol)
    bclass = str(mem_row["behavioral_class"] or "").upper() if mem_row else ""
    if opp_tuning:
        downrank = {str(x).upper() for x in opp_tuning.get("downrank_classes") or []}
        if bclass and bclass in downrank:
            failure_penalty += float(opp_tuning.get("failure_penalty_boost") or 0) * 0.5
        failure_penalty += float(opp_tuning.get("failure_penalty_boost") or 0) * 0.15
    failure_penalty = clamp(failure_penalty, 0, 35)

    final_row = finals.get(symbol)
    final_boost = 0.0
    final_veto = None
    if final_row:
        final_score = safe_float(final_row["score"], 0.0) or 0.0
        final_boost = clamp((final_score - 55) * 0.18, 0, 5)
        final_veto = final_row["veto_reason"]

    tv_boost = 0.0
    tv_atoms: list[str] = []
    if tv_features:
        raw_atoms = None
        try:
            raw_atoms = tv_features["atoms"] if "atoms" in tv_features.keys() else None
            if not raw_atoms and tv_features["atoms_json"]:
                raw_atoms = json.loads(tv_features["atoms_json"])
        except Exception:
            raw_atoms = []
        for atom in raw_atoms or []:
            cfg = TV_ATOM_BOOSTS.get(atom)
            if not cfg:
                continue
            tv_atoms.append(atom)
            tv_boost += cfg.get("smart_money", 0) * 0.04
            tv_boost += cfg.get("structure", 0) * 0.03
            tv_boost += cfg.get("liquidity", 0) * 0.03
            tv_boost += cfg.get("behavioral", 0) * 0.02
            tv_boost += cfg.get("rs_sector", 0) * 0.02
            if cfg.get("smart_money"):
                smart_money_score = clamp(smart_money_score + cfg["smart_money"])
            if cfg.get("structure"):
                structure_score = clamp(structure_score + cfg["structure"])
            if cfg.get("liquidity"):
                liquidity_expansion_score = clamp(liquidity_expansion_score + cfg["liquidity"])
            if cfg.get("behavioral"):
                behavioral_score = clamp(behavioral_score + cfg["behavioral"])
            if cfg.get("rs_sector"):
                rs_sector_score = clamp(rs_sector_score + cfg["rs_sector"])

    quality_boost = 3.5 if symbol in QUALITY_SYMBOLS_V3 else 0.0

    opportunity_score = (
        market_score * 0.10
        + sector_score * 0.14
        + rs_market_score * 0.14
        + rs_sector_score * 0.10
        + liquidity_expansion_score * 0.14
        + behavioral_score * 0.16
        + structure_score * 0.12
        + risk_score * 0.05
        + smart_money_score * 0.05
        + final_boost
        + tv_boost
        + quality_boost
        - failure_penalty * 0.38
    )
    opportunity_score = clamp(opportunity_score)

    flags = []
    if market_score >= 55:
        flags.append("MARKET_OK")
    if sector_score >= 65:
        flags.append("SECTOR_STRONG")
    if rs_market_score >= 60:
        flags.append("RS_MARKET")
    if rs_sector_score >= 58:
        flags.append("RS_SECTOR")
    if 0.18 <= dry_ratio <= 0.62:
        flags.append("VOLUME_DRYUP")
    if 1.35 <= vol_ratio <= 3.20:
        flags.append("LIQUIDITY_EXPANSION")
    if 2.5 <= vol10_vs20 <= 3.2:
        flags.append("VOL_SWEET_SPOT")
    if cp is not None and cp <= 0.33:
        flags.append("LOWER_THIRD_CLOSE")
    if pct_from_ath <= 0.03 and vol10_vs20 < 2.5:
        flags.append("NEAR_ATH_LOW_VOL")
    if cons_days >= 25 and (high20 - low20) / high20 <= 0.16:
        flags.append("VCP_PROXY")
    if above50:
        flags.append("ABOVE_EMA50")
    if above200:
        flags.append("ABOVE_EMA200")
    if align:
        flags.append("EMA_ALIGNMENT")
    if atr is not None and 0.018 <= atr <= 0.085:
        flags.append("ATR_OK")
    if absorption:
        flags.append("ABSORPTION")
    for atom in tv_atoms:
        cfg = TV_ATOM_BOOSTS.get(atom, {})
        flag = cfg.get("flag")
        if flag:
            flags.append(flag)
    if symbol in QUALITY_SYMBOLS_V3:
        flags.append("QUALITY_V3")
    if recent_wick_fail:
        flags.append("RECENT_WICK_FAIL")
    if post_breakout_vol_collapse:
        flags.append("POST_BREAKOUT_VOL_COLLAPSE")
    if final_row and int(final_row["actionable"] or 0) == 1:
        flags.append("FINAL_ACTIONABLE")
    if final_veto:
        flags.append("FINAL_VETO")

    if opportunity_score >= 78 and structure_score >= 65 and risk_score >= 55 and not final_veto:
        stage = "ACTIONABLE_CANDIDATE"
    elif opportunity_score >= 75 and final_veto:
        stage = "QUALIFIED_DISCOVERY"
    elif opportunity_score >= 70 and ("VCP_PROXY" in flags or "LIQUIDITY_EXPANSION" in flags):
        stage = "NEAR_BREAKOUT"
    elif opportunity_score >= 62 and ("VOLUME_DRYUP" in flags or "ABSORPTION" in flags):
        stage = "EARLY_ACCUMULATION"
    elif opportunity_score >= 55:
        stage = "WATCH"
    else:
        stage = "AVOID"

    evidence = {
        **market_ev,
        "close": round(close, 4),
        "ret5": round(r5, 4),
        "ret20": round(r20, 4),
        "ret60": round(r60, 4),
        "sector_ret20": round(sector_ret20, 4),
        "rs_market": round(rs_market, 4),
        "rs_sector": round(rs_sector, 4),
        "vol10_vs_60": round(vol_ratio, 3),
        "dry5_vs_60": round(dry_ratio, 3),
        "consolidation_days": cons_days,
        "atr_pct": round(atrv, 4),
        "close_position": round(cp, 3) if cp is not None else None,
        "vol10_vs20": round(vol10_vs20, 3),
        "pct_from_ath": round(pct_from_ath, 4),
        "above_ema50": above50,
        "above_ema200": above200,
        "final_score": safe_float(final_row["score"], None) if final_row else None,
        "final_actionable": int(final_row["actionable"] or 0) if final_row else None,
        "final_veto": final_veto,
        "pine_source": pine[symbol]["source_script"] if symbol in pine and "source_script" in pine[symbol].keys() else None,
        "tv_atoms": tv_atoms,
        "tv_score": safe_float(tv_features["tv_score"] if tv_features and "tv_score" in tv_features.keys() else None, None),
        "quality_v3": symbol in QUALITY_SYMBOLS_V3,
    }

    return {
        "trade_date": bar_date(latest["bar_time"]),
        "symbol": symbol,
        "sector": sector,
        "market_regime_score": round(market_score, 2),
        "sector_strength_score": round(sector_score, 2),
        "rs_market_score": round(rs_market_score, 2),
        "rs_sector_score": round(rs_sector_score, 2),
        "liquidity_expansion_score": round(liquidity_expansion_score, 2),
        "behavioral_fingerprint_score": round(behavioral_score, 2),
        "structure_score": round(structure_score, 2),
        "risk_score": round(risk_score, 2),
        "smart_money_score": round(smart_money_score, 2),
        "failure_penalty": round(failure_penalty, 2),
        "opportunity_score": round(opportunity_score, 2),
        "stage": stage,
        "flags_json": json.dumps(flags, ensure_ascii=False),
        "evidence_json": json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    }


def ensure_table(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunity_score_v2 (
          trade_date TEXT NOT NULL,
          symbol TEXT NOT NULL,
          sector TEXT,
          market_regime_score REAL,
          sector_strength_score REAL,
          rs_market_score REAL,
          rs_sector_score REAL,
          liquidity_expansion_score REAL,
          behavioral_fingerprint_score REAL,
          structure_score REAL,
          risk_score REAL,
          smart_money_score REAL,
          failure_penalty REAL,
          opportunity_score REAL,
          stage TEXT,
          flags_json TEXT,
          evidence_json TEXT,
          created_at TEXT DEFAULT (datetime('now')),
          PRIMARY KEY (trade_date, symbol)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_opp_v2_date_score ON opportunity_score_v2(trade_date, opportunity_score DESC)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_opp_v2_stage ON opportunity_score_v2(stage)")


def _load_opp_tuning(params: Dict[str, Any]) -> Dict[str, Any]:
    tuning: Dict[str, Any] = {"failure_penalty_boost": 0.0, "downrank_classes": [], "reasons": []}
    try:
        from discovery_feedback_loader import load_feedback_queue, load_opportunity_tuning

        queue = params.get("feedback_queue") or load_feedback_queue()
        followup = params.get("opportunity_followup")
        tuning = load_opportunity_tuning(queue, followup)
    except Exception:
        pass
    try:
        from discovery_manifest_loader import load_ml_manifest

        manifest = params.get("discovery_ml_manifest") or load_ml_manifest(params)
        tuning["fabric_priority_atoms"] = list(manifest.get("priority_atoms") or [])
        tuning["fabric_penalize_atoms"] = list(manifest.get("penalize_atoms") or [])
        tuning["hard_negative_symbols"] = set(manifest.get("hard_negative_symbols") or [])
        if manifest.get("universe_gate"):
            tuning["universe_gate"] = manifest["universe_gate"]
    except Exception:
        tuning.setdefault("fabric_priority_atoms", [])
        tuning.setdefault("hard_negative_symbols", set())
    return tuning


def run(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    opp_tuning = _load_opp_tuning(params)
    db = connect()
    ensure_table(db)
    by_symbol = load_bars(db)
    if not by_symbol:
        raise SystemExit("No OHLCV data available")
    trade_date = latest_market_date(by_symbol)

    market_score, market_ev = market_context(db, by_symbol)
    sectors = sector_context(db, by_symbol)
    liq = latest_rows(db, "liquidity_profile", "computed_date")
    pine = latest_rows(db, "pine_analytics", "trade_date")
    tv_feat = latest_rows(db, "tv_discovery_features", "trade_date")
    dna = latest_rows(db, "stock_dna", "updated_at")
    memory = latest_rows(db, "stock_behavioral_memory", "last_updated")
    finals = latest_final_signals(db)

    rets = [ret(rows, 20) for s, rows in by_symbol.items() if not s.startswith("EGX") and len(rows) >= 80]
    universe_market_ret20 = median([x for x in rets if x is not None]) if rets else 0.0

    hn_syms = opp_tuning.get("hard_negative_symbols") or set()
    rows_out = []
    for symbol, rows in by_symbol.items():
        if symbol in hn_syms:
            continue
        item = score_symbol(
            symbol,
            rows,
            market_score,
            market_ev,
            sectors,
            liq,
            pine,
            dna,
            memory,
            finals,
            universe_market_ret20,
            opp_tuning=opp_tuning,
            tv_features=tv_feat.get(symbol),
        )
        if item:
            rows_out.append(item)

    rows_out.sort(key=lambda r: r["opportunity_score"], reverse=True)
    db.execute("DELETE FROM opportunity_score_v2 WHERE trade_date=?", (trade_date,))
    stmt = db.cursor()
    for r in rows_out:
        stmt.execute(
            """
            INSERT OR REPLACE INTO opportunity_score_v2
            (trade_date, symbol, sector, market_regime_score, sector_strength_score,
             rs_market_score, rs_sector_score, liquidity_expansion_score,
             behavioral_fingerprint_score, structure_score, risk_score,
             smart_money_score, failure_penalty, opportunity_score, stage,
             flags_json, evidence_json)
            VALUES
            (:trade_date, :symbol, :sector, :market_regime_score, :sector_strength_score,
             :rs_market_score, :rs_sector_score, :liquidity_expansion_score,
             :behavioral_fingerprint_score, :structure_score, :risk_score,
             :smart_money_score, :failure_penalty, :opportunity_score, :stage,
             :flags_json, :evidence_json)
            """,
            r,
        )
    db.commit()

    stage_counts = dict(
        db.execute(
            """
            SELECT stage, COUNT(*) n
            FROM opportunity_score_v2
            WHERE trade_date=?
            GROUP BY stage
            ORDER BY n DESC
            """,
            (trade_date,),
        ).fetchall()
    )
    top = rows_out[:15]
    lower_third_count = sum(
        1 for r in rows_out if "LOWER_THIRD_CLOSE" in json.loads(r.get("flags_json") or "[]")
    )
    vol_sweet_count = sum(
        1 for r in rows_out if "VOL_SWEET_SPOT" in json.loads(r.get("flags_json") or "[]")
    )
    near_ath_risk = sum(
        1 for r in rows_out if "NEAR_ATH_LOW_VOL" in json.loads(r.get("flags_json") or "[]")
    )
    tv_feature_count = sum(
        1 for r in rows_out
        if any(f.startswith("TV_") for f in json.loads(r.get("flags_json") or "[]"))
    )
    quality_v3_count = sum(
        1 for r in rows_out if "QUALITY_V3" in json.loads(r.get("flags_json") or "[]")
    )
    qualified_plus = sum(
        stage_counts.get(s, 0)
        for s in ("QUALIFIED_DISCOVERY", "ACTIONABLE_CANDIDATE", "NEAR_BREAKOUT")
    )
    avg_opp = mean([r["opportunity_score"] for r in rows_out]) if rows_out else 0.0
    tuning_out = dict(opp_tuning)
    if isinstance(tuning_out.get("hard_negative_symbols"), set):
        tuning_out["hard_negative_symbols"] = sorted(tuning_out["hard_negative_symbols"])
    result = {
        "success": True,
        "trade_date": trade_date,
        "symbols_scored": len(rows_out),
        "market_regime_score": round(market_score, 2),
        "opp_tuning": tuning_out,
        "stage_counts": stage_counts,
        "qualified_plus": qualified_plus,
        "avg_opportunity_score": round(avg_opp, 2),
        "lower_third_count": lower_third_count,
        "vol_sweet_spot_count": vol_sweet_count,
        "near_ath_risk_count": near_ath_risk,
        "tv_feature_count": tv_feature_count,
        "quality_v3_count": quality_v3_count,
        "top": [
            {
                "symbol": r["symbol"],
                "sector": r["sector"],
                "score": r["opportunity_score"],
                "stage": r["stage"],
                "flags": json.loads(r["flags_json"]),
            }
            for r in top
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def report(limit: int = 20) -> None:
    db = connect()
    if not table_exists(db, "opportunity_score_v2"):
        print(json.dumps({"success": False, "error": "opportunity_score_v2 is not built yet"}, indent=2))
        return
    d = db.execute("SELECT MAX(trade_date) d FROM opportunity_score_v2").fetchone()["d"]
    rows = db.execute(
        """
        SELECT symbol, sector, opportunity_score, stage, flags_json
        FROM opportunity_score_v2
        WHERE trade_date=?
        ORDER BY opportunity_score DESC
        LIMIT ?
        """,
        (d, limit),
    ).fetchall()
    print(f"Opportunity Score v2 — {d}")
    for i, r in enumerate(rows, 1):
        flags = ", ".join(json.loads(r["flags_json"] or "[]")[:5])
        print(f"{i:02d}. {r['symbol']:6s} {r['opportunity_score']:5.1f} {r['stage']:20s} {r['sector'] or 'Unknown'} | {flags}")


def status() -> None:
    db = connect()
    if not table_exists(db, "opportunity_score_v2"):
        print(json.dumps({"exists": False}, indent=2))
        return
    row = db.execute(
        """
        SELECT MAX(trade_date) latest, COUNT(*) rows,
               SUM(stage='ACTIONABLE_CANDIDATE') actionable_candidates,
               SUM(stage='QUALIFIED_DISCOVERY') qualified_discovery,
               SUM(stage='NEAR_BREAKOUT') near_breakout,
               SUM(stage='EARLY_ACCUMULATION') early_accumulation
        FROM opportunity_score_v2
        """
    ).fetchone()
    print(json.dumps(dict(row), ensure_ascii=False, indent=2))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    params: Dict[str, Any] = {}
    if len(sys.argv) > 2 and cmd == "run":
        try:
            params = json.loads(sys.argv[2])
        except Exception:
            params = {}
    if cmd == "run":
        run(params)
    elif cmd == "report":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        report(limit)
    elif cmd == "status":
        status()
    else:
        raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
