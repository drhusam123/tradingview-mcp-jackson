#!/usr/bin/env python3
"""
EGX Data Validator + Loader

Ingests freshly-synced OHLCV bars (from egx_cdp_sync.mjs JSON output) into the
SQLite DB, but ONLY after validating them, to prevent data contamination.

Usage:
    python3 egx_data_validator.py [json_path] [--dry-run]

    json_path   Path to the sync JSON (default: /tmp/egx_full_D.json)
    --dry-run   Validate + report only; do NOT insert into the DB.

Validation checks (per bar):
    - Range check:    high >= low, low <= open <= high, low <= close <= high, volume > 0
    - Positive price: open/high/low/close all > 0
    - Tradeability:   zero-volume bars are rejected from OHLCV production history
    - Spike check:    |close - prev_close| / prev_close > 50% AND volume < 2x the
                      20-bar avg volume  ->  flagged as SUSPECTED corporate action.
    - Duplicate time: dedupe by bar_time within a symbol (keep first).

Stdlib only.
"""

import sqlite3
import json
import sys
import os
import datetime

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "egx_trading.db"),
)
DEFAULT_JSON = "/tmp/egx_full_D.json"
REPORT_PATH = "/tmp/egx_validation_report.json"

SPIKE_PCT = 0.50          # >50% close-to-close move
VOL_MULTIPLIER = 2.0      # vs 20-bar avg volume
VOL_WINDOW = 20


def fmt_date(bar_time):
    """Convert unix seconds to an ISO date string (UTC), defensively."""
    try:
        return datetime.datetime.utcfromtimestamp(int(bar_time)).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError, TypeError):
        return str(bar_time)


def to_num(v):
    """Coerce to float; return None if not a finite number."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # reject NaN / inf
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def passes_range_and_positive(o, h, l, c, vol):
    """Return True if the bar passes range + positive-price checks."""
    if None in (o, h, l, c, vol):
        return False
    # positive prices
    if not (o > 0 and h > 0 and l > 0 and c > 0):
        return False
    # range checks
    if not (h >= l):
        return False
    if not (l <= o <= h):
        return False
    if not (l <= c <= h):
        return False
    if not (vol > 0):
        return False
    return True


def validate_symbol(symbol, bars):
    """
    Validate one symbol's bars.

    Returns dict with:
        valid_rows  : list of (symbol, bar_time, o, h, l, c, vol) tuples to insert
        rejected    : int count of rejected (bad) bars
        zero_volume : int count of zero-volume bars rejected from production OHLCV
        suspicious  : list of {symbol, date, bar_time, pct_change} corporate-action flags
        duplicates  : int count of duplicate bar_times dropped
    """
    valid_rows = []
    rejected = 0
    zero_volume = 0
    suspicious = []
    duplicates = 0

    if not isinstance(bars, list):
        return {"valid_rows": [], "rejected": 0, "zero_volume": 0, "suspicious": [], "duplicates": 0}

    # sort by time so spike detection / rolling avg is chronological,
    # and dedupe (keep first occurrence by original order for ties).
    seen_times = set()
    ordered = []
    for b in bars:
        if not isinstance(b, dict):
            rejected += 1
            continue
        ordered.append(b)

    # Stable sort by time; non-numeric times sort last and will be rejected.
    def sort_key(b):
        t = b.get("time")
        try:
            return (0, int(t))
        except (TypeError, ValueError):
            return (1, 0)

    ordered.sort(key=sort_key)

    prev_close = None
    recent_vols = []  # trailing window of *accepted* bar volumes

    for b in ordered:
        t = b.get("time")
        try:
            bar_time = int(t)
        except (TypeError, ValueError):
            rejected += 1
            continue

        # duplicate time within symbol -> drop (keep first)
        if bar_time in seen_times:
            duplicates += 1
            continue
        seen_times.add(bar_time)

        o = to_num(b.get("open"))
        h = to_num(b.get("high"))
        l = to_num(b.get("low"))
        c = to_num(b.get("close"))
        vol = to_num(b.get("volume"))

        if vol == 0:
            zero_volume += 1
            rejected += 1
            continue

        if not passes_range_and_positive(o, h, l, c, vol):
            rejected += 1
            continue

        # spike / corporate-action check (relative to prev accepted close)
        is_suspicious = False
        if prev_close is not None and prev_close > 0:
            pct_change = (c - prev_close) / prev_close
            if abs(pct_change) > SPIKE_PCT:
                avg_vol = (sum(recent_vols) / len(recent_vols)) if recent_vols else None
                # big price move WITHOUT a big volume confirmation -> suspected split/dividend
                if avg_vol is None or vol < VOL_MULTIPLIER * avg_vol:
                    is_suspicious = True
                    suspicious.append({
                        "symbol": symbol,
                        "date": fmt_date(bar_time),
                        "bar_time": bar_time,
                        "pct_change": round(pct_change * 100, 2),
                    })

        # roll state forward using accepted bars
        prev_close = c
        recent_vols.append(vol)
        if len(recent_vols) > VOL_WINDOW:
            recent_vols.pop(0)

        # Suspicious bars are NOT silently inserted; they are logged only.
        if is_suspicious:
            continue

        valid_rows.append((symbol, bar_time, o, h, l, c, vol))

    return {
        "valid_rows": valid_rows,
        "rejected": rejected,
        "zero_volume": zero_volume,
        "suspicious": suspicious,
        "duplicates": duplicates,
    }


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]
    dry_run = "--dry-run" in flags

    json_path = args[0] if args else DEFAULT_JSON

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print("ERROR: could not read JSON '%s': %s" % (json_path, e))
        return 1

    results = data.get("results", data) if isinstance(data, dict) else {}
    if not isinstance(results, dict):
        print("ERROR: no 'results' object found in JSON.")
        return 1

    total_symbols = 0
    total_valid = 0
    total_inserted = 0
    total_rejected = 0
    total_zero_volume = 0
    total_duplicates = 0
    all_suspicious = []
    per_symbol = {}

    # open DB (unless dry run)
    conn = None
    cur = None
    if not dry_run:
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
        except sqlite3.Error as e:
            print("ERROR: could not open DB '%s': %s" % (DB_PATH, e))
            return 1

    print("=== EGX Data Validator %s===" % ("(DRY RUN) " if dry_run else ""))
    print("Source: %s" % json_path)
    print("")

    for symbol, entry in results.items():
        total_symbols += 1

        # defensive: handle error entries / missing bars / non-dict
        if not isinstance(entry, dict):
            print("%s: 0 valid, 0 rejected, 0 suspicious  (skipped: malformed entry)" % symbol)
            per_symbol[symbol] = {"valid": 0, "rejected": 0, "zero_volume": 0,
                                  "suspicious": 0, "duplicates": 0,
                                  "inserted": 0, "note": "malformed entry"}
            continue
        if "error" in entry:
            print("%s: 0 valid, 0 rejected, 0 suspicious  (skipped: error=%s)"
                  % (symbol, entry.get("error")))
            per_symbol[symbol] = {"valid": 0, "rejected": 0, "zero_volume": 0,
                                  "suspicious": 0, "duplicates": 0, "inserted": 0,
                                  "note": "error=%s" % entry.get("error")}
            continue

        bars = entry.get("bars")
        if not bars:
            print("%s: 0 valid, 0 rejected, 0 suspicious  (no bars)" % symbol)
            per_symbol[symbol] = {"valid": 0, "rejected": 0, "zero_volume": 0,
                                  "suspicious": 0, "duplicates": 0,
                                  "inserted": 0, "note": "no bars"}
            continue

        res = validate_symbol(symbol, bars)
        n_valid = len(res["valid_rows"])
        n_rejected = res["rejected"]
        n_zero_volume = res["zero_volume"]
        n_suspicious = len(res["suspicious"])
        n_dupes = res["duplicates"]

        inserted_here = 0
        if not dry_run and res["valid_rows"]:
            try:
                cur.executemany(
                    "INSERT OR IGNORE INTO ohlcv_history_execution "
                    "(symbol, bar_time, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    res["valid_rows"],
                )
                inserted_here = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
                conn.commit()
            except sqlite3.Error as e:
                print("  WARNING: insert failed for %s: %s" % (symbol, e))
                inserted_here = 0

        dupe_note = ("  (%d dup-time dropped)" % n_dupes) if n_dupes else ""
        zero_note = ("  (%d zero-volume rejected)" % n_zero_volume) if n_zero_volume else ""
        print("%s: %d valid, %d rejected, %d suspicious%s%s"
              % (symbol, n_valid, n_rejected, n_suspicious, dupe_note, zero_note))

        total_valid += n_valid
        total_rejected += n_rejected
        total_zero_volume += n_zero_volume
        total_duplicates += n_dupes
        total_inserted += inserted_here
        all_suspicious.extend(res["suspicious"])

        per_symbol[symbol] = {
            "valid": n_valid,
            "rejected": n_rejected,
            "zero_volume": n_zero_volume,
            "suspicious": n_suspicious,
            "duplicates": n_dupes,
            "inserted": inserted_here,
        }

    if conn is not None:
        conn.close()

    # final summary
    print("")
    print("=== FINAL SUMMARY %s===" % ("(DRY RUN — nothing inserted) " if dry_run else ""))
    print("Total symbols processed : %d" % total_symbols)
    print("Total valid bars        : %d" % total_valid)
    print("Total bars inserted     : %d" % (0 if dry_run else total_inserted))
    print("Total rejected          : %d" % total_rejected)
    print("Total zero-volume reject: %d" % total_zero_volume)
    print("Total duplicate-times   : %d" % total_duplicates)
    print("Total suspicious        : %d" % len(all_suspicious))
    if all_suspicious:
        print("")
        print("Suspicious (corporate-action) bars for audit review:")
        for s in all_suspicious:
            print("  %-10s %s  pct_change=%+.2f%%"
                  % (s["symbol"], s["date"], s["pct_change"]))

    # JSON report
    report = {
        "source_json": json_path,
        "dry_run": dry_run,
        "db_path": DB_PATH,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "totals": {
            "symbols": total_symbols,
            "valid": total_valid,
            "inserted": (0 if dry_run else total_inserted),
            "rejected": total_rejected,
            "zero_volume_rejected": total_zero_volume,
            "duplicates": total_duplicates,
            "suspicious": len(all_suspicious),
        },
        "per_symbol": per_symbol,
        "suspicious": all_suspicious,
    }
    try:
        with open(REPORT_PATH, "w") as f:
            json.dump(report, f, indent=2)
        print("")
        print("Report written to: %s" % REPORT_PATH)
    except OSError as e:
        print("WARNING: could not write report: %s" % e)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
