#!/usr/bin/env python3
"""
EGX Liquidity Rotation & Pre-Explosion Discovery Engine (LRE).

Phase LRE-1.0 — Explosion Archaeology (full market, full history).
Phase LRE-2.0 — Daily scores, stage machine, rotation, 5 lists, radar.

Shadow / discovery only. Does NOT modify score_all, final_signals, promotion, or Telegram.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "egx_trading.db"

RETURN_CAP = 0.50


def capped_ret(r: float) -> float:
    return min(max(r, -RETURN_CAP * 100), RETURN_CAP * 100)


LRE_INVARIANTS = {
    "EGX_LRE_SHADOW": 1,
    "EGX_LRE_OPP_BOOST": 0,
    "no_client_path": True,
    "no_veto": True,
    "no_suppression": True,
    "no_actionable_change": True,
}

# Explosion definition (user spec)
THRESHOLDS = (0.10, 0.15, 0.20, 0.30, 0.40, 0.50)
PRIMARY_THRESH = 0.20
MIN_FORWARD = 5
MAX_FORWARD = 30
MIN_BARS = 60
EVENT_COOLDOWN = 15
VOL_RATIO_MIN = 1.15
PRIOR_EXPLODED_PCT = 0.15
PRIOR_EXPLODED_LOOKBACK = 40
FINGERPRINT_OFFSETS = (1, 3, 5, 10, 20, 40)

OUTPUTS = {
    "archaeology": DATA / "lre_explosion_archaeology.json",
    "families": DATA / "lre_explosion_families.json",
    "fingerprints": DATA / "lre_pre_explosion_fingerprints.json",
    "radar": DATA / "lre_radar_last.json",
    "daily_scores": DATA / "lre_daily_scores_last.json",
    "rotation_graph": DATA / "lre_rotation_graph_last.json",
    "report": ROOT / "docs/LRE_PHASE_1_0_EXPLOSION_ARCHAEOLOGY_REPORT.md",
    "report_daily": ROOT / "docs/LRE_PHASE_2_0_DAILY_RADAR_REPORT.md",
}

STAGE_NAMES = {
    0: "Dead",
    1: "Silent_Accumulation",
    2: "Volume_Awakening",
    3: "Supply_Absorption",
    4: "Pre_Breakout_Compression",
    5: "Ignition",
    6: "Public_Chase",
    7: "Distribution_Trap",
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.row_factory = sqlite3.Row
    return conn


def sf(v, default=None):
    try:
        if v is None:
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except (TypeError, ValueError):
        return default


def bar_date(ts: int) -> str:
    return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS lre_explosion_events (
        event_id          TEXT PRIMARY KEY,
        symbol            TEXT NOT NULL,
        signal_date       TEXT NOT NULL,
        family            TEXT NOT NULL,
        direction         TEXT DEFAULT 'UP',
        forward_return_pct REAL,
        forward_sessions  INTEGER,
        threshold_hit     TEXT,
        volume_ratio      REAL,
        prior_exploded    INTEGER DEFAULT 0,
        sector            TEXT,
        avg_vol_20        REAL,
        compression_days  INTEGER,
        fingerprint_json  TEXT,
        artifact_flag     INTEGER DEFAULT 0,
        include_research  INTEGER DEFAULT 1,
        created_at        TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_lre_expl_sym ON lre_explosion_events(symbol, signal_date);
    CREATE INDEX IF NOT EXISTS idx_lre_expl_family ON lre_explosion_events(family);
    CREATE INDEX IF NOT EXISTS idx_lre_expl_date ON lre_explosion_events(signal_date);
    CREATE TABLE IF NOT EXISTS lre_daily_scores (
        trade_date              TEXT NOT NULL,
        symbol                  TEXT NOT NULL,
        sector                  TEXT,
        stage                   INTEGER,
        stage_name              TEXT,
        abnormality_score       REAL,
        stored_energy           REAL,
        supply_exhaustion       REAL,
        liquidity_fitness       REAL,
        explosion_potential     REAL,
        vol_ratio_20            REAL,
        compression_days        INTEGER,
        move_from_low_20d_pct   REAL,
        rotation_trigger        INTEGER DEFAULT 0,
        rotation_leader         TEXT,
        analogue_score          REAL,
        artifact_risk           INTEGER DEFAULT 0,
        list_tags               TEXT,
        detail_json             TEXT,
        created_at              TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_lre_daily_eps ON lre_daily_scores(trade_date, explosion_potential DESC);
    CREATE TABLE IF NOT EXISTS lre_market_daily (
        trade_date              TEXT PRIMARY KEY,
        speculative_appetite    REAL,
        pct_vol_breakout        REAL,
        pct_up_3pct             REAL,
        pct_above_mid_range     REAL,
        breadth_signal          TEXT,
        rotation_active         INTEGER DEFAULT 0,
        detail_json             TEXT,
        created_at              TEXT DEFAULT (datetime('now'))
    );
    """)


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone())


def load_all_bars(conn: sqlite3.Connection) -> Tuple[Dict[str, List[dict]], dict]:
    """Load every symbol with OHLCV from execution table, fallback to history."""
    sources = []
    if table_exists(conn, "ohlcv_history_execution"):
        sources.append("ohlcv_history_execution")
    if table_exists(conn, "ohlcv_history"):
        sources.append("ohlcv_history")

    sectors = {}
    if table_exists(conn, "stock_universe"):
        for r in conn.execute("SELECT symbol, COALESCE(sector,'Unknown') sector FROM stock_universe"):
            sectors[r["symbol"]] = r["sector"]

    by_sym: Dict[str, List[dict]] = {}
    meta = {"sources": sources, "symbols": 0, "bars": 0, "min_date": None, "max_date": None}

    for src in sources:
        rows = conn.execute(f"""
            SELECT h.symbol, h.bar_time, h.open, h.high, h.low, h.close, h.volume,
                   COALESCE(su.sector, 'Unknown') AS sector
            FROM {src} h
            LEFT JOIN stock_universe su ON su.symbol = h.symbol
            WHERE h.close IS NOT NULL AND h.close > 0
            ORDER BY h.symbol, h.bar_time
        """).fetchall()
        for r in rows:
            sym = r["symbol"]
            bar = {
                "date": bar_date(r["bar_time"]),
                "open": sf(r["open"]), "high": sf(r["high"]),
                "low": sf(r["low"]), "close": sf(r["close"]),
                "volume": sf(r["volume"], 0.0) or 0.0,
                "sector": r["sector"] or sectors.get(sym, "Unknown"),
            }
            if src == sources[0]:
                by_sym.setdefault(sym, []).append(bar)
            elif sym not in by_sym:
                by_sym[sym] = [bar]

    all_dates = []
    for sym, bars in by_sym.items():
        by_sym[sym] = sorted(bars, key=lambda x: x["date"])
        all_dates.extend(b["date"] for b in bars)

    meta["symbols"] = len(by_sym)
    meta["bars"] = sum(len(v) for v in by_sym.values())
    if all_dates:
        meta["min_date"] = min(all_dates)
        meta["max_date"] = max(all_dates)
    return by_sym, meta


def avg_vol(bars: List[dict], idx: int, n: int = 20) -> float:
    sl = bars[max(0, idx - n):idx]
    if not sl:
        return 0.0
    return mean(b["volume"] for b in sl)


def atr_pct(bars: List[dict], idx: int, n: int = 14) -> Optional[float]:
    if idx < n:
        return None
    trs = []
    for i in range(idx - n + 1, idx + 1):
        h, l = bars[i]["high"], bars[i]["low"]
        pc = bars[i - 1]["close"] if i > 0 else bars[i]["close"]
        if h is None or l is None or pc is None:
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    c = bars[idx]["close"]
    return mean(trs) / c if trs and c else None


def clv(bar: dict) -> float:
    h, l, c = bar["high"], bar["low"], bar["close"]
    if h is None or l is None or c is None or h <= l:
        return 0.5
    return (c - l) / (h - l)


def compression_days(bars: List[dict], idx: int, lookback: int = 40, band: float = 0.06) -> int:
    if idx < 5:
        return 0
    sl = bars[max(0, idx - lookback):idx + 1]
    if len(sl) < 5:
        return 0
    hi = max(b["high"] for b in sl if b["high"])
    lo = min(b["low"] for b in sl if b["low"])
    if not hi or not lo or hi <= 0:
        return 0
    rng = (hi - lo) / hi
    if rng > band:
        return max(0, lookback - 10)
    return lookback


def drawdown_from_high(bars: List[dict], idx: int, lookback: int = 60) -> float:
    sl = bars[max(0, idx - lookback):idx + 1]
    if not sl:
        return 0.0
    hi = max(b["high"] for b in sl if b["high"])
    c = bars[idx]["close"]
    if not hi or not c or hi <= 0:
        return 0.0
    return (hi - c) / hi


def prior_exploded(bars: List[dict], idx: int) -> bool:
    if idx < PRIOR_EXPLODED_LOOKBACK + 5:
        return False
    sl = bars[max(0, idx - PRIOR_EXPLODED_LOOKBACK):idx]
    lo = min(b["low"] for b in sl if b["low"])
    c = bars[idx - 1]["close"]
    if not lo or not c or lo <= 0:
        return False
    return (c - lo) / lo >= PRIOR_EXPLODED_PCT


def forward_max_return(bars: List[dict], idx: int) -> Tuple[float, int]:
    """Max return from signal close within MIN_FORWARD..MAX_FORWARD sessions."""
    entry = bars[idx]["close"]
    if not entry or entry <= 0:
        return 0.0, 0
    best_r, best_n = 0.0, 0
    for j in range(idx + MIN_FORWARD, min(len(bars), idx + MAX_FORWARD + 1)):
        hi = bars[j]["high"]
        if hi and hi > 0:
            r = (hi - entry) / entry
            if r > best_r:
                best_r, best_n = r, j - idx
    return best_r, best_n


def fingerprint_at(bars: List[dict], idx: int) -> dict:
    out = {}
    for off in FINGERPRINT_OFFSETS:
        i = idx - off
        if i < 0:
            continue
        b = bars[i]
        av20 = avg_vol(bars, i, 20)
        vr = b["volume"] / av20 if av20 > 0 else 1.0
        atr5 = atr_pct(bars, i, 5)
        atr20 = atr_pct(bars, i, 20)
        out[f"T-{off}"] = {
            "vol_ratio": round(vr, 3),
            "clv": round(clv(b), 3),
            "atr_compress": round(atr5 / atr20, 3) if atr5 and atr20 and atr20 > 0 else None,
            "compression_days": compression_days(bars, i),
            "drawdown_60d": round(drawdown_from_high(bars, i), 3),
            "close": b["close"],
        }
    return out


def classify_family(
    bars: List[dict], idx: int, fwd_ret: float, sector_moves: int,
    avg_v20: float, price: float,
) -> Tuple[str, bool]:
    """Returns (family, artifact_flag)."""
    comp = compression_days(bars, idx)
    dd = drawdown_from_high(bars, idx, 60)
    vr = bars[idx]["volume"] / avg_v20 if avg_v20 > 0 else 1.0
    turnover_proxy = avg_v20 * price if price else 0

    # F — artifact / ghost
    if avg_v20 < 500 or turnover_proxy < 50_000:
        return "F_artifact_illiquid", True
    if vr > 8 and fwd_ret > 0.25 and comp < 5:
        return "F_artifact_spike", True
    rng_day = (bars[idx]["high"] - bars[idx]["low"]) / bars[idx]["close"] if bars[idx]["close"] else 0
    if vr > 6 and rng_day < 0.02:
        return "F_artifact_ghost", True

    # E — sector wave
    if sector_moves >= 2:
        return "E_sector_rotation", False

    # B — violent bounce
    if dd >= 0.22:
        return "B_violent_bounce", False

    # A — long accumulation
    if comp >= 15 and vr >= 1.2 and fwd_ret >= 0.15:
        return "A_long_accumulation", False

    # C — low-float sensitivity
    if turnover_proxy < 300_000 and fwd_ret >= 0.20:
        return "C_low_float_pump", False

    # D — news (no feed in v1)
    return "D_unclassified", False


def build_sector_index(by_sym: Dict[str, List[dict]]) -> Dict[str, Dict[str, List[float]]]:
    """sector -> date -> list of daily returns."""
    idx: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for sym, bars in by_sym.items():
        sector = bars[0]["sector"] if bars else "Unknown"
        for i in range(1, len(bars)):
            p0, p1 = bars[i - 1]["close"], bars[i]["close"]
            if p0 and p1 and p0 > 0:
                idx[sector][bars[i]["date"]].append((p1 - p0) / p0)
    return idx


def sector_peers_moved(sector_idx: dict, sector: str, signal_date: str, dates: List[str], idx_pos: int) -> int:
    if idx_pos < 5:
        return 0
    recent = dates[max(0, idx_pos - 5):idx_pos]
    count = 0
    for d in recent:
        rets = sector_idx.get(sector, {}).get(d, [])
        if rets and mean(rets) >= 0.08:
            count += 1
    return count


def detect_symbol_explosions(sym: str, bars: List[dict], sector_idx: dict) -> List[dict]:
    if len(bars) < MIN_BARS:
        return []
    dates = [b["date"] for b in bars]
    events = []
    last_kept = -EVENT_COOLDOWN - 1

    for i in range(40, len(bars) - MAX_FORWARD):
        if i - last_kept < EVENT_COOLDOWN:
            continue
        if prior_exploded(bars, i):
            continue
        av20 = avg_vol(bars, i, 20)
        vr = bars[i]["volume"] / av20 if av20 > 0 else 0
        if vr < VOL_RATIO_MIN:
            continue
        fwd_ret, fwd_n = forward_max_return(bars, i)
        if fwd_ret < PRIMARY_THRESH:
            continue

        thresh_hit = "+20"
        for t in THRESHOLDS:
            if fwd_ret >= t:
                thresh_hit = f"+{int(t * 100)}"

        sector = bars[i].get("sector", "Unknown")
        peers = sector_peers_moved(sector_idx, sector, bars[i]["date"], dates, i)
        family, artifact = classify_family(bars, i, fwd_ret, peers, av20, bars[i]["close"])

        events.append({
            "event_id": f"LRE_{sym}_{bars[i]['date']}_{thresh_hit}",
            "symbol": sym,
            "signal_date": bars[i]["date"],
            "family": family,
            "direction": "UP",
            "forward_return_pct": round(fwd_ret * 100, 2),
            "forward_sessions": fwd_n,
            "threshold_hit": thresh_hit,
            "volume_ratio": round(vr, 3),
            "prior_exploded": 0,
            "sector": sector,
            "avg_vol_20": round(av20, 1),
            "compression_days": compression_days(bars, i),
            "fingerprint_json": fingerprint_at(bars, i),
            "artifact_flag": 1 if artifact else 0,
            "include_research": 0 if artifact else 1,
        })
        last_kept = i

    return events


def aggregate_families(events: List[dict]) -> dict:
    by_f: Dict[str, List[dict]] = defaultdict(list)
    for e in events:
        by_f[e["family"]].append(e)
    out = {}
    for fam, evs in sorted(by_f.items(), key=lambda x: -len(x[1])):
        rets = [capped_ret(e["forward_return_pct"]) for e in evs]
        out[fam] = {
            "count": len(evs),
            "median_return_pct": round(median(rets), 2) if rets else None,
            "avg_return_pct": round(mean(rets), 2) if rets else None,
            "avg_forward_sessions": round(mean(e["forward_sessions"] for e in evs), 1),
            "top_symbols": [s for s, _ in Counter(e["symbol"] for e in evs).most_common(8)],
        }
    return out


def aggregate_fingerprints(events: List[dict]) -> dict:
    """Mean fingerprint by family at each T-offset (research events only)."""
    research = [e for e in events if e.get("include_research")]
    by_fam: Dict[str, List[dict]] = defaultdict(list)
    for e in research:
        by_fam[e["family"]].append(e.get("fingerprint_json") or {})

    out = {}
    for fam, fps in by_fam.items():
        off_stats = {}
        for off_key in [f"T-{o}" for o in FINGERPRINT_OFFSETS]:
            vols, clvs, comps = [], [], []
            for fp in fps:
                node = fp.get(off_key)
                if not node:
                    continue
                if node.get("vol_ratio") is not None:
                    vols.append(node["vol_ratio"])
                if node.get("clv") is not None:
                    clvs.append(node["clv"])
                if node.get("compression_days") is not None:
                    comps.append(node["compression_days"])
            if vols:
                off_stats[off_key] = {
                    "avg_vol_ratio": round(mean(vols), 3),
                    "avg_clv": round(mean(clvs), 3) if clvs else None,
                    "avg_compression_days": round(mean(comps), 1) if comps else None,
                    "n": len(vols),
                }
        out[fam] = off_stats
    return out


def publish_events(conn: sqlite3.Connection, events: List[dict]) -> int:
    conn.execute("DELETE FROM lre_explosion_events")
    for e in events:
        conn.execute("""
            INSERT OR REPLACE INTO lre_explosion_events
            (event_id, symbol, signal_date, family, direction, forward_return_pct,
             forward_sessions, threshold_hit, volume_ratio, prior_exploded, sector,
             avg_vol_20, compression_days, fingerprint_json, artifact_flag, include_research)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            e["event_id"], e["symbol"], e["signal_date"], e["family"], e["direction"],
            e["forward_return_pct"], e["forward_sessions"], e["threshold_hit"],
            e["volume_ratio"], e.get("prior_exploded", 0), e["sector"],
            e.get("avg_vol_20"), e.get("compression_days"),
            json.dumps(e.get("fingerprint_json") or {}, default=str),
            e.get("artifact_flag", 0), e.get("include_research", 1),
        ))
    conn.commit()
    return len(events)


def render_report(doc: dict) -> str:
    lines = [
        "# LRE Phase 1.0 — Explosion Archaeology Report",
        "",
        f"**Generated:** {doc['at']}",
        f"**Universe:** {doc['meta']['symbols']} symbols | {doc['meta']['bars']:,} bars",
        f"**History:** {doc['meta'].get('min_date')} → {doc['meta'].get('max_date')}",
        "",
        "## Invariants",
        "",
        "```text",
        "Shadow only — no change to final_signals / actionable / promotion / Telegram",
        "EGX_LRE_SHADOW=1 | EGX_LRE_OPP_BOOST=0 | no veto | no suppression",
        "```",
        "",
        f"## Events mined: **{doc['total_events']}** (research-grade: **{doc['research_events']}**)",
        "",
        "### By family",
        "",
    ]
    for fam, stats in doc.get("families", {}).items():
        lines.append(
            f"- **{fam}**: n={stats['count']} median={stats['median_return_pct']}% "
            f"avg_sessions={stats['avg_forward_sessions']}"
        )
    lines.extend([
        "",
        "### Threshold hits (all events)",
        "",
    ])
    for k, v in doc.get("threshold_counts", {}).items():
        lines.append(f"- {k}: {v}")
    lines.extend([
        "",
        "### Top symbols (research events)",
        "",
    ])
    for sym, n in doc.get("top_symbols", [])[:15]:
        lines.append(f"- {sym}: {n}")
    lines.extend([
        "",
        "## Next: LRE-2.0 Self-Baseline + Stage Machine",
        "",
        "```text",
        "Supply engine only — client path after OOS gate pass",
        "```",
    ])
    return "\n".join(lines)


def find_date_idx(bars: List[dict], trade_date: str) -> Optional[int]:
    for i, b in enumerate(bars):
        if b["date"] == trade_date:
            return i
    return None


def vol_ratio(bars: List[dict], idx: int, n: int = 20) -> float:
    av = avg_vol(bars, idx, n)
    return bars[idx]["volume"] / av if av > 0 else 1.0


def move_from_low_pct(bars: List[dict], idx: int, n: int = 20) -> float:
    sl = bars[max(0, idx - n):idx + 1]
    lo = min((b["low"] for b in sl if b["low"]), default=None)
    c = bars[idx]["close"]
    if not lo or not c or lo <= 0:
        return 0.0
    return (c - lo) / lo


def liquidity_fitness(bars: List[dict], idx: int) -> Tuple[float, bool]:
    av20 = avg_vol(bars, idx, 20)
    price = bars[idx]["close"] or 0
    turnover = av20 * price
    artifact = av20 < 500 or turnover < 50_000
    fit = min(100.0, math.log10(max(turnover, 1)) * 12)
    return fit, artifact


def abnormality_score(bars: List[dict], idx: int) -> float:
    vz20 = vol_ratio(bars, idx, 20)
    vz60 = vol_ratio(bars, idx, 60) if idx >= 60 else vz20
    atr5 = atr_pct(bars, idx, 5)
    atr20 = atr_pct(bars, idx, 20)
    atr_exp = (atr5 / atr20) if atr5 and atr20 and atr20 > 0 else 1.0
    clv_dev = abs(clv(bars[idx]) - 0.5) * 2
    accel = max(0, vz20 - vz60)
    return min(100.0, 22 * min(vz20, 4) + 18 * min(atr_exp, 2.5) + 16 * clv_dev + 14 * accel)


def supply_exhaustion_score(bars: List[dict], idx: int) -> float:
    if idx < 9:
        return 0.0
    greens, reds = [], []
    for i in range(idx - 9, idx + 1):
        o, c = bars[i]["open"], bars[i]["close"]
        if o and c and o > 0:
            ret = (c - o) / o
            (greens if ret >= 0 else reds).append(abs(ret))
    if not reds:
        return 55.0
    gr = mean(greens) if greens else 0.0
    rr = mean(reds)
    ratio = gr / (rr + 1e-9)
    return min(100.0, max(0.0, 35 + 30 * min(ratio, 2.5)))


def stored_energy_score(bars: List[dict], idx: int) -> float:
    comp = compression_days(bars, idx)
    vz = vol_ratio(bars, idx, 20)
    clv_val = clv(bars[idx])
    atr5 = atr_pct(bars, idx, 5)
    atr20 = atr_pct(bars, idx, 20)
    compress = (atr20 / atr5) if atr5 and atr20 and atr5 > 0 else 1.0
    return min(100.0, comp * 1.2 + min(vz, 3.5) * 12 + clv_val * 18 + min(compress, 3) * 6)


def analogue_score_for_symbol(conn: sqlite3.Connection, symbol: str) -> float:
    rows = conn.execute(
        "SELECT forward_return_pct FROM lre_explosion_events WHERE symbol=? AND include_research=1",
        (symbol,),
    ).fetchall()
    if not rows:
        return 42.0
    rets = [capped_ret(r["forward_return_pct"]) for r in rows]
    return min(100.0, 35 + median(rets) * 1.2)


def classify_stage(bars: List[dict], idx: int, move20: float, vz: float, se: float) -> Tuple[int, str]:
    comp = compression_days(bars, idx)
    if move20 >= 0.25:
        return 7, STAGE_NAMES[7]
    if move20 >= 0.18:
        return 6, STAGE_NAMES[6]
    if move20 >= 0.12 and vz >= 2.0:
        return 5, STAGE_NAMES[5]
    if comp >= 18 and vz >= 1.6 and se >= 50:
        return 4, STAGE_NAMES[4]
    if se >= 52 and comp >= 8:
        return 3, STAGE_NAMES[3]
    if vz >= 1.35:
        return 2, STAGE_NAMES[2]
    if comp >= 12 or (vz >= 1.15 and comp >= 8):
        return 1, STAGE_NAMES[1]
    return 0, STAGE_NAMES[0]


def explosion_potential(
    abn: float, energy: float, se: float, liq: float, analog: float,
    rotation: bool, move20: float, artifact: bool,
) -> float:
    if artifact:
        return 0.0
    penalty = 0.0
    if move20 >= 0.15:
        penalty += 25
    if move20 >= 0.22:
        penalty += 20
    base = (
        0.22 * abn + 0.20 * energy + 0.18 * se + 0.12 * liq
        + 0.15 * analog + (10 if rotation else 0)
    )
    return max(0.0, min(100.0, base - penalty))


def load_rotation_triggers(
    conn: sqlite3.Connection, by_sym: Dict[str, List[dict]], trade_date: str,
) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not table_exists(conn, "stock_lead_lag"):
        return out
    rows = conn.execute("""
        SELECT leader_symbol, follower_symbol, lag_days, correlation
        FROM stock_lead_lag
        WHERE computed_date = (SELECT MAX(computed_date) FROM stock_lead_lag)
          AND correlation >= 0.30
    """).fetchall()
    leader_move: Dict[str, float] = {}
    for sym, bars in by_sym.items():
        idx = find_date_idx(bars, trade_date)
        if idx is None or idx < 5:
            continue
        p0, p1 = bars[idx - 5]["close"], bars[idx]["close"]
        if p0 and p1 and p0 > 0:
            leader_move[sym] = (p1 - p0) / p0
    for r in rows:
        lead, fol = r["leader_symbol"], r["follower_symbol"]
        mv = leader_move.get(lead, 0)
        if mv >= 0.08 and fol not in out:
            fol_move = 0.0
            fb = by_sym.get(fol)
            fi = find_date_idx(fb, trade_date) if fb else None
            if fb and fi and fi >= 5 and fb[fi - 5]["close"]:
                fol_move = (fb[fi]["close"] - fb[fi - 5]["close"]) / fb[fi - 5]["close"]
            if fol_move < 0.12:
                out[fol] = {
                    "leader": lead,
                    "leader_move_5d_pct": round(mv * 100, 2),
                    "lag_days": r["lag_days"],
                    "correlation": r["correlation"],
                }
    return out


def compute_speculative_appetite(by_sym: Dict[str, List[dict]], trade_date: str, conn) -> dict:
    vol_break, up3, above_mid = 0, 0, 0
    total = 0
    for bars in by_sym.values():
        idx = find_date_idx(bars, trade_date)
        if idx is None or idx < 25:
            continue
        total += 1
        if vol_ratio(bars, idx, 20) >= 2.0:
            vol_break += 1
        if bars[idx - 1]["close"] and bars[idx]["close"]:
            dret = (bars[idx]["close"] - bars[idx - 1]["close"]) / bars[idx - 1]["close"]
            if dret >= 0.03:
                up3 += 1
        sl = bars[idx - 20:idx]
        hi = max(b["high"] for b in sl if b["high"])
        lo = min(b["low"] for b in sl if b["low"])
        mid = (hi + lo) / 2 if hi and lo else None
        if mid and bars[idx]["close"] and bars[idx]["close"] >= mid:
            above_mid += 1
    breadth_sig = None
    if table_exists(conn, "market_breadth_enhanced"):
        br = conn.execute(
            "SELECT signal, pct_above_ema20 FROM market_breadth_enhanced WHERE date=?",
            (trade_date,),
        ).fetchone()
        if br:
            breadth_sig = br["signal"]
    pct_v = 100 * vol_break / total if total else 0
    pct_u = 100 * up3 / total if total else 0
    pct_m = 100 * above_mid / total if total else 0
    sai = min(100.0, pct_v * 0.4 + pct_u * 0.35 + pct_m * 0.25)
    return {
        "speculative_appetite": round(sai, 1),
        "pct_vol_breakout": round(pct_v, 1),
        "pct_up_3pct": round(pct_u, 1),
        "pct_above_mid_range": round(pct_m, 1),
        "breadth_signal": breadth_sig,
        "universe_scored": total,
    }


def assign_lists(stage: int, eps: float, move20: float, artifact: bool, rotation: bool) -> List[str]:
    tags = []
    if artifact:
        tags.append("artifact_excluded")
        return tags
    if stage == 2:
        tags.append("volume_awakening")
    if stage in (1, 2) and move20 < 0.06:
        tags.append("silent_accumulation")
    if stage in (3, 4) and eps >= 50:
        tags.append("ignition_candidates")
    if stage >= 5 or move20 >= 0.15:
        tags.append("do_not_chase")
    if rotation and move20 < 0.12:
        tags.append("next_rotation")
    return tags


def score_symbol_daily(
    conn: sqlite3.Connection, sym: str, bars: List[dict], trade_date: str,
    rotation_map: Dict[str, dict],
) -> Optional[dict]:
    idx = find_date_idx(bars, trade_date)
    if idx is None or idx < 40:
        return None
    liq, artifact = liquidity_fitness(bars, idx)
    if artifact:
        artifact_risk = 1
    else:
        artifact_risk = 0
    move20 = move_from_low_pct(bars, idx, 20)
    vz = vol_ratio(bars, idx, 20)
    abn = abnormality_score(bars, idx)
    energy = stored_energy_score(bars, idx)
    se = supply_exhaustion_score(bars, idx)
    analog = analogue_score_for_symbol(conn, sym)
    rot = rotation_map.get(sym)
    stage, stage_name = classify_stage(bars, idx, move20, vz, se)
    eps = explosion_potential(abn, energy, se, liq, analog, bool(rot), move20, bool(artifact_risk))
    tags = assign_lists(stage, eps, move20, bool(artifact_risk), bool(rot))
    return {
        "trade_date": trade_date,
        "symbol": sym,
        "sector": bars[idx].get("sector", "Unknown"),
        "stage": stage,
        "stage_name": stage_name,
        "abnormality_score": round(abn, 1),
        "stored_energy": round(energy, 1),
        "supply_exhaustion": round(se, 1),
        "liquidity_fitness": round(liq, 1),
        "explosion_potential": round(eps, 1),
        "vol_ratio_20": round(vz, 2),
        "compression_days": compression_days(bars, idx),
        "move_from_low_20d_pct": round(move20 * 100, 2),
        "rotation_trigger": 1 if rot else 0,
        "rotation_leader": rot.get("leader") if rot else None,
        "analogue_score": round(analog, 1),
        "artifact_risk": artifact_risk,
        "list_tags": tags,
        "detail_json": {"rotation": rot} if rot else {},
    }


def publish_daily_scores(conn: sqlite3.Connection, scores: List[dict], market: dict) -> None:
    td = market["trade_date"]
    conn.execute("DELETE FROM lre_daily_scores WHERE trade_date=?", (td,))
    for s in scores:
        conn.execute("""
            INSERT OR REPLACE INTO lre_daily_scores
            (trade_date, symbol, sector, stage, stage_name, abnormality_score,
             stored_energy, supply_exhaustion, liquidity_fitness, explosion_potential,
             vol_ratio_20, compression_days, move_from_low_20d_pct, rotation_trigger,
             rotation_leader, analogue_score, artifact_risk, list_tags, detail_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            s["trade_date"], s["symbol"], s["sector"], s["stage"], s["stage_name"],
            s["abnormality_score"], s["stored_energy"], s["supply_exhaustion"],
            s["liquidity_fitness"], s["explosion_potential"], s["vol_ratio_20"],
            s["compression_days"], s["move_from_low_20d_pct"], s["rotation_trigger"],
            s["rotation_leader"], s["analogue_score"], s["artifact_risk"],
            json.dumps(s["list_tags"]), json.dumps(s.get("detail_json") or {}, default=str),
        ))
    conn.execute("""
        INSERT OR REPLACE INTO lre_market_daily
        (trade_date, speculative_appetite, pct_vol_breakout, pct_up_3pct,
         pct_above_mid_range, breadth_signal, rotation_active, detail_json)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        td, market.get("speculative_appetite"), market.get("pct_vol_breakout"),
        market.get("pct_up_3pct"), market.get("pct_above_mid_range"),
        market.get("breadth_signal"), 1 if market.get("rotation_candidates") else 0,
        json.dumps(market, default=str),
    ))
    conn.commit()


def build_five_lists(scores: List[dict]) -> dict:
    lists = {
        "volume_awakening": [],
        "silent_accumulation": [],
        "ignition_candidates": [],
        "do_not_chase": [],
        "next_rotation": [],
    }
    for s in sorted(scores, key=lambda x: -(x.get("explosion_potential") or 0)):
        tags = s.get("list_tags") or []
        row = {
            "symbol": s["symbol"],
            "stage": s["stage_name"],
            "eps": s["explosion_potential"],
            "move_20d_pct": s["move_from_low_20d_pct"],
            "vol_ratio": s["vol_ratio_20"],
            "rotation_leader": s.get("rotation_leader"),
        }
        for tag in tags:
            if tag in lists:
                lists[tag].append(row)
    for k in lists:
        lists[k] = lists[k][:25]
    return lists


def render_daily_report(doc: dict) -> str:
    m = doc.get("market", {})
    lists = doc.get("lists", {})
    lines = [
        "# LRE Phase 2.0 — Pre-Explosion Daily Radar",
        "",
        f"**Date:** {doc.get('trade_date')}",
        f"**Generated:** {doc['at']}",
        "",
        "## Market State",
        "",
        f"- Speculative Appetite: **{m.get('speculative_appetite')}**",
        f"- Vol breakout stocks: {m.get('pct_vol_breakout')}%",
        f"- Up >3% today: {m.get('pct_up_3pct')}%",
        f"- Breadth: {m.get('breadth_signal')}",
        f"- Rotation candidates: {m.get('rotation_candidates', 0)}",
        "",
        "## Invariants",
        "",
        "```text",
        "Shadow only — no actionable / promotion / Telegram impact",
        "```",
        "",
    ]
    for name, title in [
        ("ignition_candidates", "Ignition Candidates (Stage 3–4)"),
        ("next_rotation", "Next Rotation"),
        ("silent_accumulation", "Silent Accumulation"),
        ("volume_awakening", "Volume Awakening"),
        ("do_not_chase", "Do Not Chase"),
    ]:
        lines.append(f"### {title}")
        lines.append("")
        for r in lists.get(name, [])[:12]:
            lines.append(
                f"- **{r['symbol']}** eps={r['eps']} stage={r['stage']} "
                f"move={r['move_20d_pct']}% vol={r['vol_ratio']}x"
                + (f" leader={r['rotation_leader']}" if r.get("rotation_leader") else "")
            )
        lines.append("")
    return "\n".join(lines)


def cmd_daily(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ LRE-2.0: Daily Pre-Explosion Radar ═══", flush=True)

    conn = connect()
    ensure_tables(conn)
    by_sym, meta = load_all_bars(conn)
    trade_date = params.get("trade_date") or params.get("date") or meta.get("max_date")
    if not trade_date:
        conn.close()
        return {"success": False, "error": "no trade_date"}

    print(f"  scoring {meta['symbols']} symbols @ {trade_date}...", flush=True)
    rotation_map = load_rotation_triggers(conn, by_sym, trade_date)
    scores: List[dict] = []
    for sym, bars in by_sym.items():
        row = score_symbol_daily(conn, sym, bars, trade_date, rotation_map)
        if row:
            scores.append(row)

    market = compute_speculative_appetite(by_sym, trade_date, conn)
    market["trade_date"] = trade_date
    market["rotation_candidates"] = len(rotation_map)
    lists = build_five_lists(scores)
    publish_daily_scores(conn, scores, market)

    top_eps = sorted(scores, key=lambda x: -(x.get("explosion_potential") or 0))[:20]
    doc = {
        "at": at,
        "phase": "LRE-2.0",
        "invariants": LRE_INVARIANTS,
        "trade_date": trade_date,
        "symbols_scored": len(scores),
        "market": market,
        "lists": lists,
        "top_eps": top_eps,
        "rotation_edges": list(rotation_map.items())[:30],
    }
    OUTPUTS["radar"].write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["daily_scores"].write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["rotation_graph"].write_text(
        json.dumps({"at": at, "trade_date": trade_date, "edges": rotation_map}, indent=2, default=str),
        encoding="utf-8",
    )
    OUTPUTS["report_daily"].write_text(render_daily_report(doc), encoding="utf-8")
    conn.close()

    print(
        f"  done. scored={len(scores)} ignition={len(lists['ignition_candidates'])} "
        f"rotation={len(lists['next_rotation'])} SAI={market.get('speculative_appetite')}",
        flush=True,
    )
    return {
        "success": True,
        "trade_date": trade_date,
        "symbols_scored": len(scores),
        "speculative_appetite": market.get("speculative_appetite"),
        "lists": {k: len(v) for k, v in lists.items()},
    }


def cmd_archaeology(params: Optional[dict] = None) -> dict:
    params = params or {}
    at = datetime.now(timezone.utc).isoformat()
    print("═══ LRE-1.0: Full-Market Explosion Archaeology ═══", flush=True)

    conn = connect()
    ensure_tables(conn)
    by_sym, meta = load_all_bars(conn)
    print(f"  loaded {meta['symbols']} symbols, {meta['bars']:,} bars", flush=True)

    sector_idx = build_sector_index(by_sym)
    all_events: List[dict] = []
    skipped_short = 0

    for i, (sym, bars) in enumerate(sorted(by_sym.items())):
        if len(bars) < MIN_BARS:
            skipped_short += 1
            continue
        evs = detect_symbol_explosions(sym, bars, sector_idx)
        all_events.extend(evs)
        if (i + 1) % 50 == 0:
            print(f"    scanned {i+1}/{len(by_sym)} symbols, events={len(all_events)}", flush=True)

    research = [e for e in all_events if e.get("include_research")]
    families = aggregate_families(all_events)
    fp_agg = aggregate_fingerprints(all_events)
    thresh_counts = Counter(e["threshold_hit"] for e in all_events)
    top_syms = Counter(e["symbol"] for e in research).most_common(25)

    print(f"  publishing {len(all_events)} events ({len(research)} research)...", flush=True)
    publish_events(conn, all_events)

    doc = {
        "at": at,
        "phase": "LRE-1.0",
        "invariants": LRE_INVARIANTS,
        "meta": meta,
        "skipped_short_history": skipped_short,
        "total_events": len(all_events),
        "research_events": len(research),
        "artifact_events": len(all_events) - len(research),
        "families": families,
        "threshold_counts": dict(thresh_counts),
        "top_symbols": top_syms,
        "explosion_rule": {
            "forward_min_sessions": MIN_FORWARD,
            "forward_max_sessions": MAX_FORWARD,
            "primary_threshold": PRIMARY_THRESH,
            "volume_ratio_min": VOL_RATIO_MIN,
            "prior_exploded_exclude": PRIOR_EXPLODED_PCT,
        },
    }

    OUTPUTS["archaeology"].write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    OUTPUTS["families"].write_text(json.dumps(families, indent=2), encoding="utf-8")
    OUTPUTS["fingerprints"].write_text(json.dumps(fp_agg, indent=2), encoding="utf-8")
    OUTPUTS["radar"].write_text(json.dumps({
        "at": at, "phase": "LRE-1.0", "mode": "archaeology_complete",
        "research_events": len(research), "families": list(families.keys()),
    }, indent=2), encoding="utf-8")
    OUTPUTS["report"].write_text(render_report({**doc, "families": families}), encoding="utf-8")
    conn.close()

    print(f"  done. total={len(all_events)} research={len(research)}", flush=True)
    return {
        "success": True,
        "symbols": meta["symbols"],
        "bars": meta["bars"],
        "total_events": len(all_events),
        "research_events": len(research),
        "outputs": [str(p.relative_to(ROOT)) for p in OUTPUTS.values()],
    }


COMMANDS = {
    "archaeology": cmd_archaeology,
    "daily": cmd_daily,
    "run": cmd_daily,
}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "daily"
    p: dict = {}
    if len(sys.argv) > 2:
        try:
            p = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            p = {}
    fn = COMMANDS.get(cmd, cmd_archaeology)
    print(json.dumps(fn(p), indent=2))
