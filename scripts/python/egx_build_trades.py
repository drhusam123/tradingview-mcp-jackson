#!/usr/bin/env python3
"""
egx_build_trades.py — CLEAN trade-list extractor for the EGX stock ML system.

WHY THIS EXISTS
---------------
The existing `recommendation_outcomes` table is CONTAMINATED: it contains
returns like -1408% and +79631% that come from corporate-action / split
price discontinuities, not from tradeable moves. Downstream robustness tests
must NOT consume those. This script produces a fresh, sanitized trade list by
re-simulating a transparent entry rule and computing ACTUAL forward returns
directly from clean OHLCV closes.

PROXY TRADE GENERATOR (documented honestly)
-------------------------------------------
We do NOT use `explosive_moves` as the positive set — that would be look-ahead
(the explosion is the outcome we are trying to predict). Instead we use a
simple, transparent, causal "the system would look here" breakout proxy:

    For each (symbol, bar) in the test window, mark a candidate entry when:
      * volume on the bar  > 1.5 x its trailing 20-bar average volume, AND
      * the bar closed in the UPPER HALF of its own high-low range
        (close >= low + 0.5*(high-low)) — i.e. a momentum/breakout bar.

This is a PROXY for where the model would generate a signal. It is causal
(uses only data up to and including the entry bar) and yields a manageable
few-hundred-to-few-thousand candidate trades.

For each candidate we compute the REAL 5-trading-day forward return from clean
closes (5th available bar after entry, holiday/gap-safe), apply round-trip
costs, attach regime + window, then SANITIZE.

OUTPUT: /tmp/egx_clean_trades.json
stdlib only.
"""

import sqlite3
import json
import statistics
from datetime import datetime, timezone

DB_PATH = "/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db"
OUT_PATH = "/tmp/egx_clean_trades.json"

# ---- parameters --------------------------------------------------------------
START_DATE = "2024-07-01"   # walk-forward test period start
END_DATE = "2025-12-31"     # walk-forward test period end
FORWARD_BARS = 5            # hold = 5th available trading bar after entry
VOL_MULT = 1.5              # volume > 1.5x trailing 20-bar avg
VOL_LOOKBACK = 20           # bars used for the volume average (>=20 required)
COST_ROUND_TRIP = 0.002     # 0.1% buy + 0.1% sell
MAX_ABS_RETURN = 0.60       # drop |return| > 60% as split/CA distortion
CA_BUFFER_BARS = 5          # drop trades within +/- 5 trading days of a CA event
WIN_THRESHOLD = 0.07        # a "win" = return_pct >= 7%
EXCLUDE_SYMBOLS = {"EGX30"} # index, not a tradeable stock


def to_unix(date_str, end_of_day=False):
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        return int(dt.timestamp()) + 86400  # inclusive of the whole END day
    return int(dt.timestamp())


def bar_date(unix_sec):
    return datetime.fromtimestamp(unix_sec, tz=timezone.utc).strftime("%Y-%m-%d")


def window_id_for(date_str):
    # 1 = 2024H2, 2 = 2025H1, 3 = 2025H2
    if "2024-07-01" <= date_str <= "2024-12-31":
        return 1
    if "2025-01-01" <= date_str <= "2025-06-30":
        return 2
    if "2025-07-01" <= date_str <= "2025-12-31":
        return 3
    return None


def load_regime(con):
    rows = con.execute(
        "SELECT date, regime FROM regime_history ORDER BY date"
    ).fetchall()
    return [(d, r) for d, r in rows]


def regime_on(regime_rows, date_str):
    """Nearest-prior regime lookup (last regime with date <= entry date)."""
    found = "UNKNOWN"
    for d, r in regime_rows:
        if d <= date_str:
            found = r
        else:
            break
    return found


def load_corporate_actions(con):
    """Return {symbol: sorted list of event_date strings}."""
    ca = {}
    for sym, ev in con.execute(
        "SELECT symbol, event_date FROM corporate_actions"
    ).fetchall():
        ca.setdefault(sym, []).append(ev)
    for sym in ca:
        ca[sym].sort()
    return ca


def main():
    con = sqlite3.connect(DB_PATH)

    regime_rows = load_regime(con)
    ca_map = load_corporate_actions(con)

    symbols = [
        s for (s,) in con.execute(
            "SELECT DISTINCT symbol FROM ohlcv_history_execution ORDER BY symbol"
        ).fetchall()
        if s not in EXCLUDE_SYMBOLS
    ]

    win_start = to_unix(START_DATE)
    win_end = to_unix(END_DATE, end_of_day=True)

    # counters
    n_candidates = 0
    dropped_no_forward = 0
    dropped_bad_price = 0
    dropped_corrupt = 0      # |return| > 60%
    dropped_ca = 0           # near corporate action
    trades = []

    for sym in symbols:
        # Full clean series for the symbol, chronological.
        bars = con.execute(
            "SELECT bar_time, high, low, close, volume "
            "FROM ohlcv_history_execution WHERE symbol=? ORDER BY bar_time",
            (sym,),
        ).fetchall()
        if len(bars) < VOL_LOOKBACK + 1:
            continue

        ca_dates = ca_map.get(sym, [])

        n = len(bars)
        for i in range(n):
            bt, high, low, close, vol = bars[i]

            # entry must be inside the test window
            if bt < win_start or bt >= win_end:
                continue

            # need >= 20 prior bars for the volume average
            if i < VOL_LOOKBACK:
                continue

            # --- proxy entry rule (causal) ---
            prior_vols = [bars[j][4] for j in range(i - VOL_LOOKBACK, i)]
            prior_vols = [v for v in prior_vols if v is not None]
            if len(prior_vols) < VOL_LOOKBACK:
                continue
            avg_vol = sum(prior_vols) / len(prior_vols)
            if avg_vol <= 0:
                continue
            if vol is None or vol <= VOL_MULT * avg_vol:
                continue

            # upper-half-of-range close
            if high is None or low is None or close is None:
                continue
            rng = high - low
            if rng <= 0:
                # zero-range bar: treat as not a breakout signal
                continue
            if close < low + 0.5 * rng:
                continue

            # candidate confirmed
            n_candidates += 1

            entry_price = close
            entry_date = bar_date(bt)

            # forward 5th AVAILABLE bar (holiday/gap-safe)
            exit_idx = i + FORWARD_BARS
            if exit_idx >= n:
                dropped_no_forward += 1
                continue
            exit_bt, _, _, exit_close, _ = bars[exit_idx]
            exit_date = bar_date(exit_bt)
            exit_price = exit_close

            # bad/missing prices
            if (entry_price is None or exit_price is None
                    or entry_price <= 0 or exit_price <= 0):
                dropped_bad_price += 1
                continue

            return_pct = (exit_price - entry_price) / entry_price

            # SANITIZE: drop split/CA-distorted extreme moves
            if abs(return_pct) > MAX_ABS_RETURN:
                dropped_corrupt += 1
                continue

            # SANITIZE: drop trades within +/-5 trading days of a CA event.
            # We map the CA buffer to trading bars: find bar indices whose date
            # matches a CA event, then exclude entries within CA_BUFFER_BARS.
            if ca_dates:
                near_ca = False
                lo_d = bar_date(bars[max(0, i - CA_BUFFER_BARS)][0])
                hi_d = bar_date(bars[min(n - 1, i + CA_BUFFER_BARS)][0])
                # also extend hi_d to cover the exit window
                hi_idx = min(n - 1, max(i + CA_BUFFER_BARS, exit_idx + CA_BUFFER_BARS))
                hi_d = bar_date(bars[hi_idx][0])
                for ev in ca_dates:
                    if lo_d <= ev <= hi_d:
                        near_ca = True
                        break
                if near_ca:
                    dropped_ca += 1
                    continue

            net_return_pct = return_pct - COST_ROUND_TRIP
            regime = regime_on(regime_rows, entry_date)
            wid = window_id_for(entry_date)

            trades.append({
                "symbol": sym,
                "entry_date": entry_date,
                "entry_price": round(entry_price, 4),
                "exit_date": exit_date,
                "exit_price": round(exit_price, 4),
                "return_pct": round(return_pct, 6),
                "net_return_pct": round(net_return_pct, 6),
                "regime": regime,
                "window_id": wid,
            })

    con.close()

    # ---- aggregate stats ----
    n_trades = len(trades)
    nets = [t["net_return_pct"] for t in trades]
    rets = [t["return_pct"] for t in trades]
    n_wins = sum(1 for r in rets if r >= WIN_THRESHOLD)
    win_rate = (n_wins / n_trades) if n_trades else 0.0
    avg_net = statistics.mean(nets) if nets else 0.0
    median_net = statistics.median(nets) if nets else 0.0

    meta = {
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4),
        "avg_net_return": round(avg_net, 6),
        "median_net_return": round(median_net, 6),
        "date_range": f"{START_DATE} to {END_DATE}",
        "n_dropped_corrupt": dropped_corrupt,
        "cost_per_trade": COST_ROUND_TRIP,
        "win_threshold": WIN_THRESHOLD,
        "proxy_rule": ("vol>1.5x 20-bar avg AND close in upper half of range; "
                       "5-bar forward return from clean closes"),
        "dropped": {
            "corrupt_gt60pct": dropped_corrupt,
            "near_corporate_action": dropped_ca,
            "bad_or_missing_price": dropped_bad_price,
            "no_forward_bar": dropped_no_forward,
        },
        "n_candidates": n_candidates,
    }

    with open(OUT_PATH, "w") as f:
        json.dump({"meta": meta, "trades": trades}, f, indent=2)

    # ---- console summary ----
    def breakdown(key):
        groups = {}
        for t in trades:
            groups.setdefault(t[key], []).append(t)
        out = {}
        for k, ts in groups.items():
            r = [x["return_pct"] for x in ts]
            nn = [x["net_return_pct"] for x in ts]
            w = sum(1 for x in r if x >= WIN_THRESHOLD) / len(ts)
            out[k] = (len(ts), w, statistics.mean(nn))
        return out

    print("=" * 68)
    print("EGX CLEAN TRADE-LIST BUILDER")
    print("=" * 68)
    print(f"Date range            : {START_DATE} to {END_DATE}")
    print(f"Symbols scanned       : {len(symbols)}")
    print(f"Total candidates      : {n_candidates}")
    print(f"Dropped (corrupt>60%) : {dropped_corrupt}")
    print(f"Dropped (near CA)     : {dropped_ca}")
    print(f"Dropped (bad price)   : {dropped_bad_price}")
    print(f"Dropped (no fwd bar)  : {dropped_no_forward}")
    print(f"FINAL trades          : {n_trades}")
    print("-" * 68)
    print(f"Win rate (>= {WIN_THRESHOLD:.0%})    : {win_rate:.4f}  ({n_wins}/{n_trades})")
    print(f"Avg net return        : {avg_net:+.4%}")
    print(f"Median net return     : {median_net:+.4%}")
    print("-" * 68)
    print("Breakdown by window (id: n, win_rate, avg_net):")
    for wid in sorted(breakdown('window_id').keys(), key=lambda x: (x is None, x)):
        n_, w_, a_ = breakdown('window_id')[wid]
        label = {1: "2024H2", 2: "2025H1", 3: "2025H2"}.get(wid, str(wid))
        print(f"   {wid} ({label:6}): n={n_:5d}  win={w_:.3f}  avg_net={a_:+.4%}")
    print("Breakdown by regime (n, win_rate, avg_net):")
    for reg, (n_, w_, a_) in sorted(breakdown('regime').items()):
        print(f"   {reg:10}: n={n_:5d}  win={w_:.3f}  avg_net={a_:+.4%}")
    print("-" * 68)
    print(f"Wrote {n_trades} trades -> {OUT_PATH}")


if __name__ == "__main__":
    main()
