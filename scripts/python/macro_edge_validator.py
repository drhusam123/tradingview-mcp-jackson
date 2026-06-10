#!/usr/bin/env python3
"""
Macro Edge Validator
====================
Tests whether cross-market/macro features have out-of-sample predictive value
for EGX sector returns before they are allowed into client-facing signal logic.
"""
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "egx_trading.db"


def corr(xs, ys):
    if len(xs) < 8 or len(xs) != len(ys):
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return 0.0
    return max(-1.0, min(1.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (dx * dy)))


def ts_date(ts):
    raw = str(ts)
    if "-" in raw:
        return raw[:10]
    ts = int(float(raw))
    if ts > 10_000_000_000:
        ts //= 1000
    return datetime.utcfromtimestamp(ts).date().isoformat()


def ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_edge_audit (
            run_date TEXT NOT NULL,
            feature_name TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_name TEXT NOT NULL,
            lag_days INTEGER NOT NULL,
            n_is INTEGER,
            n_oos INTEGER,
            ic_is REAL,
            ic_oos REAL,
            hit_rate_oos REAL,
            accepted INTEGER DEFAULT 0,
            reason TEXT,
            details_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (run_date, feature_name, target_type, target_name, lag_days)
        )
    """)
    conn.commit()


def load_cross_market_returns(conn):
    rows = conn.execute("""
        SELECT asset, bar_time, close
        FROM cross_market_daily
        WHERE close IS NOT NULL AND close > 0
        ORDER BY asset, bar_time
    """).fetchall()
    by_asset = defaultdict(list)
    for r in rows:
        by_asset[r["asset"]].append((ts_date(r["bar_time"]), float(r["close"])))

    out = {}
    for asset, vals in by_asset.items():
        vals.sort()
        series = {}
        for i in range(1, len(vals)):
            d, c = vals[i]
            _, p = vals[i - 1]
            if p > 0:
                series[d] = (c / p) - 1.0
        if len(series) >= 80:
            out[f"cross_{asset.lower()}_ret1"] = series
    return out


def load_sector_returns(conn):
    rows = conn.execute("""
        WITH daily AS (
          SELECT date(h.bar_time,'unixepoch') AS d,
                 COALESCE(NULLIF(u.sector,''),'Unknown') AS sector,
                 h.symbol,
                 h.close,
                 LAG(h.close) OVER (PARTITION BY h.symbol ORDER BY h.bar_time) AS prev_close
          FROM ohlcv_history_features h
          JOIN stock_universe u ON u.symbol = h.symbol
          WHERE h.close > 0
        )
        SELECT d, sector, AVG(close / prev_close - 1.0) AS ret, COUNT(*) AS n
        FROM daily
        WHERE prev_close > 0
          AND ABS(close / prev_close - 1.0) < 0.50
          AND sector != 'Unknown'
        GROUP BY d, sector
        HAVING n >= 3
        ORDER BY d, sector
    """).fetchall()
    out = defaultdict(dict)
    for r in rows:
        out[r["sector"]][r["d"]] = float(r["ret"])
    return dict(out)


def load_symbol_returns(conn, max_symbols=80):
    symbols = [r["symbol"] for r in conn.execute("""
        SELECT symbol
        FROM ohlcv_history_features
        GROUP BY symbol
        ORDER BY COUNT(*) DESC
        LIMIT ?
    """, (max_symbols,)).fetchall()]
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(f"""
        SELECT symbol, date(bar_time,'unixepoch') AS d, close,
               LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time) AS prev_close
        FROM ohlcv_history_features
        WHERE symbol IN ({placeholders}) AND close > 0
        ORDER BY symbol, bar_time
    """, symbols).fetchall()
    out = defaultdict(dict)
    for r in rows:
        p = r["prev_close"]
        if p and p > 0:
            ret = float(r["close"]) / float(p) - 1.0
            if abs(ret) < 0.50:
                out[r["symbol"]][r["d"]] = ret
    return dict(out)


def eval_link(feature_series, target_series, lag):
    dates = sorted(set(feature_series) & set(target_series))
    pairs = []
    for i, d in enumerate(dates):
        j = i + lag
        if j < len(dates):
            td = dates[j]
            pairs.append((d, float(feature_series[d]), float(target_series[td])))
    if len(pairs) < 80:
        return None

    # Purged chronological split: last 30% OOS with a 5-observation embargo.
    split = int(len(pairs) * 0.65)
    embargo = min(5, max(0, len(pairs) - split - 10))
    train = pairs[:split]
    test = pairs[split + embargo:]
    if len(train) < 50 or len(test) < 25:
        return None

    xs_is = [p[1] for p in train]
    ys_is = [p[2] for p in train]
    xs_oos = [p[1] for p in test]
    ys_oos = [p[2] for p in test]
    ic_is = corr(xs_is, ys_is)
    ic_oos = corr(xs_oos, ys_oos)
    sign = 1 if ic_is >= 0 else -1
    hit = sum(1 for x, y in zip(xs_oos, ys_oos) if (sign * x * y) > 0) / len(xs_oos)
    return {
        "n_is": len(train),
        "n_oos": len(test),
        "ic_is": ic_is,
        "ic_oos": ic_oos,
        "hit_rate_oos": hit,
    }


def accepted_result(r, target_type):
    if not r:
        return False, "insufficient_samples"
    if target_type != "sector":
        return False, "symbol_macro_edge_context_only"
    if r["n_oos"] < 50:
        return False, "small_oos"
    if abs(r["ic_is"]) < 0.10:
        return False, "weak_is"
    if (r["ic_is"] >= 0) != (r["ic_oos"] >= 0):
        return False, "sign_unstable"
    if abs(r["ic_oos"]) < 0.10:
        return False, "weak_oos"
    if r["hit_rate_oos"] < 0.56:
        return False, "low_oos_hit_rate"
    return True, "accepted"


def latest_clean_date(conn):
    row = conn.execute("SELECT MAX(date(bar_time,'unixepoch')) AS d FROM ohlcv_history_features").fetchone()
    return row["d"] if row else None


def write_sector_features(conn, accepted_rows, features):
    feature_date = latest_clean_date(conn)
    if not feature_date:
        return {"feature_date": None, "rows_written": 0}

    sector_scores = defaultdict(list)
    for row in accepted_rows:
        if row["target_type"] != "sector":
            continue
        fseries = features.get(row["feature_name"], {})
        latest_dates = [d for d in fseries if d <= feature_date]
        if not latest_dates:
            continue
        latest_d = max(latest_dates)
        latest_ret = fseries[latest_d]
        sign = 1.0 if row["ic_is"] >= 0 else -1.0
        score = sign * latest_ret * abs(row["ic_oos"]) * row["hit_rate_oos"]
        sector_scores[row["target_name"]].append(score)

    symbols = conn.execute("""
        SELECT symbol, COALESCE(NULLIF(sector,''),'Unknown') AS sector
        FROM stock_universe
    """).fetchall()
    now = datetime.utcnow().isoformat(timespec="seconds")
    written = 0
    for s in symbols:
        vals = sector_scores.get(s["sector"])
        if not vals:
            continue
        value = max(-1.0, min(1.0, statistics.mean(vals) * 100.0))
        conn.execute("""
            INSERT OR REPLACE INTO feature_store
            (feature_date, symbol, feature_name, feature_value, version, source_table, computed_at)
            VALUES (?, ?, 'macro_edge_sector_score', ?, 'macro_edge_v1', 'macro_edge_audit', ?)
        """, (feature_date, s["symbol"], value, now))
        written += 1
    conn.commit()
    return {
        "feature_date": feature_date,
        "rows_written": written,
        "n_accepted_sectors": len(sector_scores),
    }


def run():
    conn = sqlite3.connect(str(DB), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    ensure(conn)

    features = load_cross_market_returns(conn)
    targets = {f"sector:{k}": v for k, v in load_sector_returns(conn).items()}
    targets.update({f"symbol:{k}": v for k, v in load_symbol_returns(conn).items()})

    run_date = datetime.utcnow().date().isoformat()
    rows = []
    accepted = []
    for fname, fseries in features.items():
        for tname, tseries in targets.items():
            target_type, target_name = tname.split(":", 1)
            for lag in (1, 2, 3, 5):
                r = eval_link(fseries, tseries, lag)
                ok, reason = accepted_result(r, target_type)
                if not r:
                    continue
                row = {
                    "run_date": run_date,
                    "feature_name": fname,
                    "target_type": target_type,
                    "target_name": target_name,
                    "lag_days": lag,
                    **r,
                    "accepted": 1 if ok else 0,
                    "reason": reason,
                }
                rows.append(row)
                if ok:
                    accepted.append(row)
                conn.execute("""
                    INSERT OR REPLACE INTO macro_edge_audit
                    (run_date, feature_name, target_type, target_name, lag_days,
                     n_is, n_oos, ic_is, ic_oos, hit_rate_oos,
                     accepted, reason, details_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    run_date, fname, target_type, target_name, lag,
                    r["n_is"], r["n_oos"], r["ic_is"], r["ic_oos"], r["hit_rate_oos"],
                    1 if ok else 0, reason, json.dumps(row, ensure_ascii=False),
                ))

    accepted_sorted = sorted(
        accepted,
        key=lambda x: (abs(x["ic_oos"]), x["hit_rate_oos"], x["n_oos"]),
        reverse=True,
    )
    feature_write = write_sector_features(conn, accepted_sorted, features)
    conn.commit()
    conn.close()

    return {
        "success": True,
        "run_date": run_date,
        "n_features": len(features),
        "n_targets": len(targets),
        "n_tests": len(rows),
        "n_accepted": len(accepted_sorted),
        "accepted_top": accepted_sorted[:20],
        "feature_write": feature_write,
        "client_gate": "ALLOW_MACRO_EDGE" if accepted_sorted else "BLOCK_MACRO_EDGE",
        "note": "Accepted rows may be used as macro edge features; blocked rows remain context only.",
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
