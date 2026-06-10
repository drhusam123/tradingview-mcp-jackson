#!/usr/bin/env python3
"""
Gate Doctor Audit
=================
Clinical diagnosis of every veto gate: who it blocked, and what happened after.

Levels:
  1. Sequential funnel — where stocks die in the real pipeline order
  2. Independent gate audit — each gate tested in isolation (all_blocking_gates)
  3. Exclusive / co-blocker — marginal contribution of each gate

Commands:
  fill_outcomes   — compute forward returns, MFE/MAE, TP-before-SL
  backfill        — rebuild gate_audit_snapshots from final_signals (partial historical)
  rescore         — re-run score_all to populate full multi-gate snapshots
  audit           — full Gate Doctor report
  false_blocks    — top rejected winners per gate
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "egx_trading.db"
REPORT_DIR = ROOT / "data" / "research_reports"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (name,),
    ).fetchone())


def ensure_gate_audit_table(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS gate_audit_snapshots (
        signal_date           TEXT NOT NULL,
        symbol                TEXT NOT NULL,
        ues                   REAL,
        ml_score              REAL,
        meta_prob             REAL,
        survival_p_tp         REAL,
        survival_p_sl         REAL,
        scan_score            REAL,
        quant_matches         INTEGER,
        ad_ratio              REAL,
        vol_ratio             REAL,
        rsi14                 REAL,
        close_position        REAL,
        spectral_regime       TEXT,
        behavioral_class      TEXT,
        breadth_signal        TEXT,
        regime                TEXT,
        conviction            TEXT,
        anti_law              INTEGER DEFAULT 0,
        quality_gate_passed   INTEGER DEFAULT 0,
        quality_gate_failures TEXT,
        final_edge_passed     INTEGER,
        final_edge_failure    TEXT,
        hard_gate_failure     TEXT,
        forecast_veto         TEXT,
        actionable            INTEGER DEFAULT 0,
        veto_reason           TEXT,
        first_blocking_gate   TEXT,
        exclusive_blockers    TEXT,
        all_blocking_gates    TEXT,
        entry_price           REAL,
        stop_loss             REAL,
        t1_target             REAL,
        ret_1d                REAL,
        ret_3d                REAL,
        ret_5d                REAL,
        ret_10d               REAL,
        ret_20d               REAL,
        mfe_5d                REAL,
        mae_5d                REAL,
        mfe_10d               REAL,
        mae_10d               REAL,
        tp_before_sl          INTEGER,
        winner_5d             INTEGER,
        loser_5d              INTEGER,
        outcomes_filled       INTEGER DEFAULT 0,
        created_at            TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (signal_date, symbol)
    );
    CREATE INDEX IF NOT EXISTS idx_gate_audit_date
        ON gate_audit_snapshots(signal_date, actionable);
    """)


def ohlcv_table(conn) -> str:
    if table_exists(conn, "ohlcv_history_execution"):
        return "ohlcv_history_execution"
    return "ohlcv_history"


def load_bars(conn):
    table = ohlcv_table(conn)
    rows = conn.execute(f"""
        SELECT symbol,
               date(bar_time,'unixepoch') d,
               open, high, low, close
        FROM {table}
        WHERE close IS NOT NULL AND close > 0
        ORDER BY symbol, bar_time
    """).fetchall()
    by_sym = defaultdict(list)
    idx = {}
    for r in rows:
        by_sym[r["symbol"]].append((
            r["d"],
            float(r["open"] or r["close"]),
            float(r["high"] or r["close"]),
            float(r["low"] or r["close"]),
            float(r["close"]),
        ))
    for sym, arr in by_sym.items():
        idx[sym] = {d: i for i, (d, *_) in enumerate(arr)}
    return by_sym, idx


def fwd_close_return(by_sym, idx, symbol, signal_date, horizon):
    arr = by_sym.get(symbol)
    pos = idx.get(symbol, {}).get(signal_date)
    if arr is None or pos is None or pos + horizon >= len(arr):
        return None
    c0 = arr[pos][4]
    c1 = arr[pos + horizon][4]
    if c0 <= 0:
        return None
    return (c1 / c0) - 1.0


def forward_bars(by_sym, idx, symbol, signal_date, n):
    arr = by_sym.get(symbol)
    pos = idx.get(symbol, {}).get(signal_date)
    if arr is None or pos is None:
        return []
    return arr[pos + 1: pos + 1 + n]


def mfe_mae(bars, entry):
    if not bars or not entry or entry <= 0:
        return None, None
    highs = [(h - entry) / entry for _, _, h, _, _ in bars]
    lows = [(l - entry) / entry for _, _, _, l, _ in bars]
    return max(highs), min(lows)


def tp_before_sl(entry, stop, target, bars):
    if not entry or entry <= 0 or not bars:
        return None
    for _, _, h, l, _ in bars:
        hit_tp = target and h >= target
        hit_sl = stop and l <= stop
        if hit_tp and hit_sl:
            return 0
        if hit_tp:
            return 1
        if hit_sl:
            return 0
    return None


def winner_loser(ret5, mfe5, mae5, tp_sl):
    if tp_sl == 1:
        return 1, 0
    if tp_sl == 0:
        return 0, 1
    if ret5 is not None:
        if ret5 >= 0.03:
            return 1, 0
        if ret5 <= -0.03:
            return 0, 1
    if mfe5 is not None and mfe5 >= 0.05:
        return 1, 0
    if mae5 is not None and mae5 <= -0.05:
        return 0, 1
    if ret5 is not None:
        return (1, 0) if ret5 > 0 else (0, 1)
    return None, None


def normalize_gate(gate: str) -> str:
    g = str(gate or "").strip()
    for prefix in ("QG:", "QUALITY_GATE:", "HARD_GATE:"):
        if g.startswith(prefix):
            g = g[len(prefix):]
    return g or "unknown"


def parse_json_list(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def gates_for_row(row) -> list[str]:
    all_g = parse_json_list(row["all_blocking_gates"])
    if all_g:
        return [normalize_gate(g) for g in all_g]
    gates = []
    if row["hard_gate_failure"]:
        gates.append(normalize_gate(f"HARD_GATE:{row['hard_gate_failure']}"))
    for r in parse_json_list(row["quality_gate_failures"]):
        gates.append(normalize_gate(r))
    if int(row["anti_law"] or 0):
        gates.append("ANTI_LAW")
    if row["final_edge_failure"] and not int(row["final_edge_passed"] or 0):
        gates.append(normalize_gate(row["final_edge_failure"]))
    if row["forecast_veto"]:
        gates.append(normalize_gate(row["forecast_veto"]))
    if row["veto_reason"] and not gates:
        gates.append(normalize_gate(str(row["veto_reason"]).split("|")[0]))
    return gates


def exclusive_for_row(row) -> list[str]:
    excl = parse_json_list(row["exclusive_blockers"])
    return [normalize_gate(g) for g in excl]


def cmd_fill_outcomes(params: dict):
    ensure_gate_audit_table(conn := connect())
    by_sym, idx = load_bars(conn)
    start = params.get("start_date")
    end = params.get("end_date")
    q = "SELECT * FROM gate_audit_snapshots WHERE outcomes_filled=0"
    args = []
    if start:
        q += " AND signal_date>=?"
        args.append(start)
    if end:
        q += " AND signal_date<=?"
        args.append(end)
    rows = conn.execute(q, args).fetchall()
    updated = 0
    for r in rows:
        sym, d = r["symbol"], r["signal_date"]
        entry = r["entry_price"]
        if not entry or entry <= 0:
            entry_row = by_sym.get(sym)
            pos = idx.get(sym, {}).get(d)
            if entry_row and pos is not None:
                entry = entry_row[pos][4]
        ret1 = fwd_close_return(by_sym, idx, sym, d, 1)
        ret3 = fwd_close_return(by_sym, idx, sym, d, 3)
        ret5 = fwd_close_return(by_sym, idx, sym, d, 5)
        ret10 = fwd_close_return(by_sym, idx, sym, d, 10)
        ret20 = fwd_close_return(by_sym, idx, sym, d, 20)
        bars5 = forward_bars(by_sym, idx, sym, d, 5)
        bars10 = forward_bars(by_sym, idx, sym, d, 10)
        mfe5, mae5 = mfe_mae(bars5, entry)
        mfe10, mae10 = mfe_mae(bars10, entry)
        tpsl = tp_before_sl(entry, r["stop_loss"], r["t1_target"], bars10)
        win5, lose5 = winner_loser(ret5, mfe5, mae5, tpsl)
        filled = int(ret5 is not None)
        conn.execute("""
            UPDATE gate_audit_snapshots SET
                ret_1d=?, ret_3d=?, ret_5d=?, ret_10d=?, ret_20d=?,
                mfe_5d=?, mae_5d=?, mfe_10d=?, mae_10d=?,
                tp_before_sl=?, winner_5d=?, loser_5d=?, outcomes_filled=?
            WHERE signal_date=? AND symbol=?
        """, (
            ret1, ret3, ret5, ret10, ret20,
            mfe5, mae5, mfe10, mae10,
            tpsl, win5, lose5, filled, d, sym,
        ))
        updated += 1
    conn.commit()
    conn.close()
    result = {"success": True, "updated": updated}
    if params.get("check_pending", True):
        result["pending_outcomes"] = cmd_check_pending_outcomes({
            "auto_run": params.get("auto_run", True),
            "start_date": start,
            "end_date": end,
        })
    return result


def _pending_cohort_status(conn, start: str, end: str) -> dict:
    rows = conn.execute("""
        SELECT signal_date, COUNT(*) AS n, SUM(outcomes_filled) AS filled,
               SUM(CASE WHEN ret_5d IS NOT NULL THEN 1 ELSE 0 END) AS ret5
        FROM gate_audit_snapshots
        WHERE signal_date >= ? AND signal_date <= ?
        GROUP BY signal_date
        ORDER BY signal_date
    """, (start, end)).fetchall()
    by_date = [dict(r) for r in rows]
    total = sum(r["n"] for r in by_date)
    filled = sum(r["filled"] or 0 for r in by_date)
    ret5 = sum(r["ret5"] or 0 for r in by_date)
    return {
        "start_date": start,
        "end_date": end,
        "total_snapshots": total,
        "outcomes_filled": filled,
        "ret_5d_count": ret5,
        "evaluable_pct": round(filled / total, 3) if total else 0.0,
        "by_date": by_date,
        "ready": total > 0 and filled >= total * 0.80,
    }


def cmd_check_pending_outcomes(params: dict | None = None):
    """Monitor C_PENDING cohort; auto-run shadow audits when ret_5d is ready."""
    params = params or {}
    auto_run = params.get("auto_run", False)
    conn = connect()
    audits = []
    reminders = []

    for spec in PENDING_OUTCOME_AUDITS:
        start = params.get("start_date", spec["start_date"])
        end = params.get("end_date", spec["end_date"])
        status = _pending_cohort_status(conn, start, end)
        min_pct = spec.get("min_evaluable_pct", 0.80)
        ready = status["total_snapshots"] > 0 and status["evaluable_pct"] >= min_pct

        entry = {
            "id": spec["id"],
            "label": spec["label"],
            "status": status,
            "ready": ready,
            "min_evaluable_pct": min_pct,
            "auto_ran": False,
            "audit_result": None,
        }

        if not ready:
            missing = [
                d["signal_date"] for d in status["by_date"]
                if (d["filled"] or 0) < (d["n"] or 0)
            ]
            reminders.append({
                "audit": spec["id"],
                "message": (
                    f"{spec['label']}: ret_5d pending "
                    f"({status['outcomes_filled']}/{status['total_snapshots']} filled, "
                    f"{status['evaluable_pct']:.0%})"
                ),
                "missing_dates": missing,
                "next_action": (
                    f"npm run egx:gate:doctor:pending-outcomes  "
                    f"(re-check after {missing[-1] if missing else end} + 5 sessions)"
                ),
            })
        elif auto_run:
            sys.path.insert(0, str(ROOT / "scripts" / "python"))
            mod = __import__(spec["module"])
            handler = getattr(mod, f"cmd_{spec['command']}")
            audit_result = handler({"start_date": start, "end_date": end})
            entry["auto_ran"] = True
            entry["audit_result"] = {
                "recommendation": audit_result.get("recommendation"),
                "report_txt": audit_result.get("report_txt"),
                "verdict_ready_for_production": audit_result.get(
                    "verdict_ready_for_production"),
            }
        audits.append(entry)

    conn.close()
    return {
        "success": True,
        "phase": "pending_outcomes_monitor",
        "today_note": "C_PENDING gates need ret_5d — survival/meta/conformal",
        "audits": audits,
        "reminders": reminders,
        "any_ready": any(a["ready"] for a in audits),
        "any_auto_ran": any(a["auto_ran"] for a in audits),
    }


def cmd_backfill(params: dict):
    """Partial backfill from final_signals when snapshots missing (single-gate legacy)."""
    ensure_gate_audit_table(conn := connect())
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date")
    q = """
        SELECT fs.*, us.conviction_tier conviction, us.breadth_signal,
               us.gate_reason us_gate, us.behavioral_class
        FROM final_signals fs
        LEFT JOIN unified_signals us
          ON us.signal_date=fs.trade_date AND us.symbol=fs.symbol
        WHERE fs.trade_date>=?
    """
    args = [start]
    if end:
        q += " AND fs.trade_date<=?"
        args.append(end)
    q += " ORDER BY fs.trade_date, fs.symbol"
    rows = conn.execute(q, args).fetchall()
    inserted = 0
    for r in rows:
        exists = conn.execute(
            "SELECT 1 FROM gate_audit_snapshots WHERE signal_date=? AND symbol=?",
            (r["trade_date"], r["symbol"]),
        ).fetchone()
        if exists:
            continue
        bd = {}
        if r["source_breakdown"]:
            try:
                bd = json.loads(r["source_breakdown"])
            except json.JSONDecodeError:
                pass
        veto = r["veto_reason"] or ""
        qg_fail = []
        if veto.startswith("QUALITY_GATE:"):
            qg_fail = [veto.split(":", 1)[1]]
        elif r["us_gate"] and not str(r["us_gate"]).startswith("HARD_GATE"):
            qg_fail = [str(r["us_gate"])]
        all_gates = []
        if veto:
            all_gates.append(veto.split("|")[0].strip())
        conn.execute("""
            INSERT OR REPLACE INTO gate_audit_snapshots
            (signal_date, symbol, ues, ml_score, scan_score, regime, conviction,
             breadth_signal, behavioral_class, quality_gate_passed, quality_gate_failures,
             actionable, veto_reason, first_blocking_gate, all_blocking_gates,
             entry_price, stop_loss, t1_target, final_edge_passed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r["trade_date"], r["symbol"],
            bd.get("ues") or r["score"], bd.get("ml") or r["source_ml"],
            bd.get("rules") or r["source_rules"], r["regime"],
            r["conviction"], r["breadth_signal"], r["behavioral_class"],
            1 if bd.get("quality_gate_passed") else int(not qg_fail),
            json.dumps(qg_fail),
            int(r["actionable"] or 0), veto,
            all_gates[0] if all_gates else None,
            json.dumps(all_gates),
            r["entry_price"], r["stop_loss"], r["t1_target"],
            1 if bd.get("final_edge_passed", True) else 0,
        ))
        inserted += 1
    conn.commit()
    conn.close()
    return {"success": True, "inserted": inserted, "note": "Run rescore for full multi-gate snapshots"}


def cmd_rescore(params: dict):
    sys.path.insert(0, str(ROOT / "scripts" / "python"))
    from signal_integration import cmd_score_all, ensure_tables

    conn = connect()
    ensure_tables(conn)
    conn.close()

    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date")
    dates = connect().execute("""
        SELECT DISTINCT trade_date FROM final_signals
        WHERE trade_date>=? """ + (" AND trade_date<=?" if end else "") + """
        ORDER BY trade_date
    """, ([start, end] if end else [start])).fetchall()
    results = []
    for (d,) in dates:
        try:
            res = cmd_score_all({"date": d})
            results.append({"date": d, "ok": res.get("success", True), "n": res.get("n_scored")})
        except Exception as e:
            results.append({"date": d, "ok": False, "error": str(e)})

    fill = cmd_fill_outcomes({
        "start_date": start,
        "end_date": end,
        "check_pending": True,
        "auto_run": params.get("auto_run", True),
    })
    return {
        "success": True,
        "rescored": results,
        "fill_outcomes": {k: fill[k] for k in ("success", "updated", "pending_outcomes")},
    }


def evaluable(row) -> bool:
    return int(row["outcomes_filled"] or 0) == 1 and row["ret_5d"] is not None


PERIOD_A_END = "2026-06-05"
PERIOD_B_END = "2026-06-07"
C_PERIOD_START = "2026-06-08"
PENDING_GATES = {"survival_sl_dominant", "meta_label_low", "conformal_low_edge"}

PENDING_OUTCOME_AUDITS = [
    {
        "id": "survival_meta_2.11",
        "label": "Survival / Meta shadow (Phase 2.11)",
        "start_date": C_PERIOD_START,
        "end_date": "2026-06-10",
        "min_evaluable_pct": 0.80,
        "module": "survival_meta_policy",
        "command": "outcome",
        "report_glob": "survival_meta_policy_shadow_*.txt",
    },
]


def period_bucket(signal_date: str) -> str:
    if signal_date <= PERIOD_A_END:
        return "A_FULL_5D"
    if signal_date <= PERIOD_B_END:
        return "B_PARTIAL_5D"
    return "C_PENDING_5D"


def risk_pct(entry, stop) -> float | None:
    if not entry or not stop or entry <= stop:
        return None
    return (entry - stop) / entry


def mfe_in_r(mfe, entry, stop) -> float | None:
    r = risk_pct(entry, stop)
    if r is None or r <= 0 or mfe is None:
        return None
    return mfe / r


def time_to_tp_sl(entry, stop, target, bars):
    ttp = ttsl = None
    if not entry or entry <= 0:
        return ttp, ttsl
    for i, (_, _, h, l, _) in enumerate(bars):
        if ttp is None and target and h >= target:
            ttp = i + 1
        if ttsl is None and stop and l <= stop:
            ttsl = i + 1
    return ttp, ttsl


def classify_winner_types(row, bars10=None) -> dict:
    entry = row.get("entry_price")
    stop = row.get("stop_loss")
    target = row.get("t1_target")
    ret5 = row.get("ret_5d")
    ret20 = row.get("ret_20d")
    mfe5 = row.get("mfe_5d")
    mae5 = row.get("mae_5d")
    tp_sl = row.get("tp_before_sl")
    mfe_r = mfe_in_r(mfe5, entry, stop)

    raw = ret5 is not None and ret5 > 0
    sl_before_tp = tp_sl == 0
    tp_before = tp_sl == 1
    tradable = bool(
        mfe5 is not None and mfe5 >= 0.05
        and (mae5 is None or mae5 > -0.05)
        and tp_sl != 0
    )
    clean = bool(tp_before and mfe_r is not None and mfe_r >= 2.0)
    dirty = bool(raw and not clean and not tradable)
    late = bool((ret5 is None or ret5 <= 0) and ret20 is not None and ret20 > 0.03)

    ttp = ttsl = None
    if bars10 is not None:
        ttp, ttsl = time_to_tp_sl(entry, stop, target, bars10)

    return {
        "raw_winner": int(raw),
        "tradable_winner": int(tradable),
        "clean_winner": int(clean),
        "dirty_winner": int(dirty),
        "late_winner": int(late),
        "sl_before_tp": int(sl_before_tp),
        "tp_before_sl": tp_sl,
        "mfe_r": round(mfe_r, 2) if mfe_r is not None else None,
        "time_to_tp": ttp,
        "time_to_sl": ttsl,
    }


def sole_blocker(row, gate: str) -> bool:
    gates = set(gates_for_row(row))
    if len(gates) != 1:
        return False
    return normalize_gate(next(iter(gates))) == normalize_gate(gate)


def co_blocked(row, gate: str) -> bool:
    gates = set(gates_for_row(row))
    gn = normalize_gate(gate)
    return gn in {normalize_gate(g) for g in gates} and len(gates) > 1


def load_enriched_rows(conn, start: str, end: str | None = None):
    ensure_gate_audit_table(conn)
    q = """
        SELECT g.*, fs.setup_type, fs.r_ratio, fs.source_breakdown,
               slp.sector, slp.liquidity_tier,
               al.strongest_anti_law, al.triggered_types anti_law_types, al.safety_level
        FROM gate_audit_snapshots g
        LEFT JOIN final_signals fs
          ON fs.trade_date=g.signal_date AND fs.symbol=g.symbol
        LEFT JOIN symbol_liquidity_profile slp ON slp.symbol=g.symbol
        LEFT JOIN anti_law_daily_scan al
          ON al.symbol=g.symbol AND al.date=g.signal_date
        WHERE g.signal_date>=?
    """
    args = [start]
    if end:
        q += " AND g.signal_date<=?"
        args.append(end)
    rows = []
    for r in conn.execute(q, args).fetchall():
        d = dict(r)
        bd = {}
        if d.get("source_breakdown"):
            try:
                bd = json.loads(d["source_breakdown"])
            except json.JSONDecodeError:
                pass
        d["_breakdown"] = bd
        fem = bd.get("final_edge_metrics") or {}
        d["effective_rr"] = fem.get("effective_rr") or bd.get("effective_rr")
        d["stop_dist_pct"] = fem.get("stop_dist_pct")
        d["_period"] = period_bucket(d["signal_date"])
        rows.append(d)
    return rows


def parse_effective_rr(row) -> float | None:
    v = row.get("effective_rr")
    if v is not None:
        return float(v)
    entry = row.get("entry_price")
    stop = row.get("stop_loss")
    target = row.get("t1_target")
    if entry and stop and target and entry > stop:
        return (target - entry) / (entry - stop)
    return None


def would_be_actionable_exclusive_loo(row, gate: str) -> bool:
    """Leave-one-out: stock blocked exclusively by this gate (no co-blockers)."""
    if int(row.get("actionable") or 0):
        return False
    if not sole_blocker(row, gate):
        return False
    if int(row.get("anti_law") or 0):
        return False
    if int(row.get("quality_gate_passed") or 0) == 0:
        qf = [normalize_gate(x) for x in parse_json_list(row.get("quality_gate_failures"))]
        if qf:
            return False
    gn = normalize_gate(gate)
    if gn.startswith("FINAL_EDGE"):
        if not int(row.get("final_edge_passed") or 0):
            fe = normalize_gate(row.get("final_edge_failure") or "")
            if fe != gn:
                return False
    if row.get("forecast_veto"):
        fv = normalize_gate(row["forecast_veto"])
        if fv != gn:
            return False
    conv = row.get("conviction") or ""
    if conv not in ("ULTRA_CONVICTION", "HIGH_CONVICTION", "MEDIUM_CONVICTION"):
        return False
    entry, stop, target = row.get("entry_price"), row.get("stop_loss"), row.get("t1_target")
    if not (entry and stop and target):
        return False
    if not (stop < entry < target):
        return False
    rr = row.get("r_ratio") or parse_effective_rr(row)
    if rr is not None and rr < 1.3:
        if gn != "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC":
            return False
    return True


def gate_outcome_status(gate: str, period_seen: set) -> str:
    if gate in PENDING_GATES and period_seen.issubset({"C_PENDING_5D"}):
        return "PENDING_OUTCOME_DATA"
    return "EVALUABLE"


def build_confusion_matrix(eval_rows, bars_cache=None):
    stats = defaultdict(lambda: {
        "blocked_count": 0,
        "clean_winners_blocked": 0,
        "dirty_winners_blocked": 0,
        "tradable_winners_blocked": 0,
        "raw_winners_blocked": 0,
        "losers_blocked": 0,
        "tp_before_sl_blocked": 0,
        "sl_before_tp_blocked": 0,
        "saved_loss_sum": 0.0,
        "missed_gain_sum": 0.0,
        "periods": set(),
    })
    for r in eval_rows:
        key = (r["signal_date"], r["symbol"])
        bars10 = bars_cache.get(key) if bars_cache else None
        wt = classify_winner_types(r, bars10)
        gates = set(gates_for_row(r))
        for g in gates:
            st = stats[g]
            st["blocked_count"] += 1
            st["periods"].add(r["_period"])
            if wt["clean_winner"]:
                st["clean_winners_blocked"] += 1
                if r.get("ret_5d"):
                    st["missed_gain_sum"] += r["ret_5d"]
            if wt["dirty_winner"]:
                st["dirty_winners_blocked"] += 1
            if wt["tradable_winner"]:
                st["tradable_winners_blocked"] += 1
            if wt["raw_winner"]:
                st["raw_winners_blocked"] += 1
            if int(r.get("loser_5d") or 0):
                st["losers_blocked"] += 1
                if r.get("ret_5d"):
                    st["saved_loss_sum"] += abs(min(r["ret_5d"], 0))
            if wt["tp_before_sl"] == 1:
                st["tp_before_sl_blocked"] += 1
            if wt["sl_before_tp"]:
                st["sl_before_tp_blocked"] += 1

    rows = []
    for gate, st in sorted(stats.items(), key=lambda kv: kv[1]["blocked_count"], reverse=True):
        bc = st["blocked_count"] or 1
        rows.append({
            "gate": gate,
            "blocked_count": st["blocked_count"],
            "clean_winners_blocked": st["clean_winners_blocked"],
            "dirty_winners_blocked": st["dirty_winners_blocked"],
            "tradable_winners_blocked": st["tradable_winners_blocked"],
            "raw_winners_blocked": st["raw_winners_blocked"],
            "losers_blocked": st["losers_blocked"],
            "tp_before_sl_blocked": st["tp_before_sl_blocked"],
            "sl_before_tp_blocked": st["sl_before_tp_blocked"],
            "blocked_winner_ratio": round(st["raw_winners_blocked"] / bc, 3),
            "blocked_clean_ratio": round(st["clean_winners_blocked"] / bc, 3),
            "saved_loss_ratio": round(st["losers_blocked"] / bc, 3),
            "outcome_status": gate_outcome_status(gate, st["periods"]),
        })
    return rows


def build_exclusive_audit(eval_rows, bars_cache=None):
    stats = defaultdict(lambda: {
        "exclusive_blocks": 0,
        "exclusive_clean_winners": 0,
        "exclusive_tradable_winners": 0,
        "exclusive_losers": 0,
        "co_blocked_clean_winners": 0,
        "co_blocked_total": 0,
        "missed_gain": 0.0,
        "saved_loss": 0.0,
    })
    for r in eval_rows:
        key = (r["signal_date"], r["symbol"])
        wt = classify_winner_types(r, bars_cache.get(key) if bars_cache else None)
        gates = set(gates_for_row(r))
        for g in gates:
            st = stats[g]
            if sole_blocker(r, g):
                st["exclusive_blocks"] += 1
                if wt["clean_winner"]:
                    st["exclusive_clean_winners"] += 1
                    st["missed_gain"] += r.get("ret_5d") or 0
                if wt["tradable_winner"]:
                    st["exclusive_tradable_winners"] += 1
                if int(r.get("loser_5d") or 0):
                    st["exclusive_losers"] += 1
                    st["saved_loss"] += abs(min(r.get("ret_5d") or 0, 0))
            elif co_blocked(r, g):
                st["co_blocked_total"] += 1
                if wt["clean_winner"]:
                    st["co_blocked_clean_winners"] += 1

    rows = []
    for gate, st in sorted(stats.items(), key=lambda kv: kv[1]["exclusive_clean_winners"], reverse=True):
        net = st["saved_loss"] - st["missed_gain"]
        rows.append({
            "gate": gate,
            "exclusive_blocks": st["exclusive_blocks"],
            "exclusive_clean_winners": st["exclusive_clean_winners"],
            "exclusive_tradable_winners": st["exclusive_tradable_winners"],
            "exclusive_losers": st["exclusive_losers"],
            "co_blocked_clean_winners": st["co_blocked_clean_winners"],
            "co_blocked_total": st["co_blocked_total"],
            "exclusive_net_utility": round(net, 4),
        })
    return rows


def build_loo_audit(eval_rows, bars_cache=None):
    stats = defaultdict(lambda: {
        "new_actionables": 0,
        "new_clean_winners": 0,
        "new_tradable_winners": 0,
        "new_losers": 0,
        "net_r": 0.0,
        "gains": [],
        "losses": [],
    })
    for r in eval_rows:
        key = (r["signal_date"], r["symbol"])
        wt = classify_winner_types(r, bars_cache.get(key) if bars_cache else None)
        for g in set(gates_for_row(r)):
            if not would_be_actionable_exclusive_loo(r, g):
                continue
            st = stats[g]
            st["new_actionables"] += 1
            ret5 = r.get("ret_5d") or 0
            st["net_r"] += ret5
            if wt["clean_winner"]:
                st["new_clean_winners"] += 1
                st["gains"].append(ret5)
            elif wt["tradable_winner"]:
                st["new_tradable_winners"] += 1
                st["gains"].append(ret5)
            if int(r.get("loser_5d") or 0):
                st["new_losers"] += 1
                st["losses"].append(ret5)

    rows = []
    for gate, st in sorted(stats.items(), key=lambda kv: kv[1]["new_actionables"], reverse=True):
        gains = st["gains"]
        losses = st["losses"]
        pf = None
        if losses:
            pos = sum(x for x in gains if x > 0)
            neg = abs(sum(x for x in losses if x < 0))
            pf = round(pos / neg, 2) if neg > 0 else None
        rows.append({
            "gate_removed": gate,
            "new_actionables": st["new_actionables"],
            "new_clean_winners": st["new_clean_winners"],
            "new_tradable_winners": st["new_tradable_winners"],
            "new_losers": st["new_losers"],
            "net_r": round(st["net_r"], 4),
            "profit_factor": pf,
            "max_adverse": round(min(st["losses"]), 4) if st["losses"] else None,
        })
    return rows


def build_net_utility(eval_rows, bars_cache=None):
    rows = []
    cm = {x["gate"]: x for x in build_confusion_matrix(eval_rows, bars_cache)}
    ex = {x["gate"]: x for x in build_exclusive_audit(eval_rows, bars_cache)}
    for gate in sorted(cm.keys(), key=lambda g: cm[g]["blocked_count"], reverse=True):
        c, e = cm[gate], ex.get(gate, {})
        saved = e.get("saved_loss", 0) or sum(
            abs(min(r.get("ret_5d") or 0, 0))
            for r in eval_rows
            if gate in gates_for_row(r) and int(r.get("loser_5d") or 0)
        )
        missed = e.get("missed_gain", 0) or sum(
            r.get("ret_5d") or 0
            for r in eval_rows
            if gate in gates_for_row(r) and classify_winner_types(
                r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None
            )["clean_winner"]
        )
        rows.append({
            "gate": gate,
            "saved_loss": round(saved, 4),
            "missed_gain": round(missed, 4),
            "net_utility": round(saved - missed, 4),
            "clean_winners_blocked": c["clean_winners_blocked"],
            "losers_blocked": c["losers_blocked"],
            "outcome_status": c["outcome_status"],
        })
    return rows


def build_overlap_matrix(eval_rows, min_overlap=5):
    pair_counts = defaultdict(int)
    gate_counts = defaultdict(int)
    for r in eval_rows:
        gates = sorted(set(gates_for_row(r)))
        for g in gates:
            gate_counts[g] += 1
        for i, a in enumerate(gates):
            for b in gates[i + 1:]:
                pair_counts[(a, b)] += 1

    rows = []
    for (a, b), cnt in sorted(pair_counts.items(), key=lambda kv: kv[1], reverse=True):
        if cnt < min_overlap:
            continue
        denom = min(gate_counts[a], gate_counts[b]) or 1
        rows.append({
            "gate_a": a,
            "gate_b": b,
            "overlap_count": cnt,
            "overlap_ratio": round(cnt / denom, 3),
        })
    return rows[:50]


def build_false_negatives_detail(eval_rows, bars_cache=None, limit=50):
    hits = []
    for r in eval_rows:
        if int(r.get("actionable") or 0):
            continue
        key = (r["signal_date"], r["symbol"])
        wt = classify_winner_types(r, bars_cache.get(key) if bars_cache else None)
        if not wt["clean_winner"] and not wt["tradable_winner"]:
            continue
        gates = gates_for_row(r)
        sole = [g for g in gates if sole_blocker(r, g)]
        hits.append({
            "date": r["signal_date"],
            "symbol": r["symbol"],
            "period": r["_period"],
            "setup_type": r.get("setup_type"),
            "sector": r.get("sector"),
            "ues": r.get("ues"),
            "scan_score": r.get("scan_score"),
            "quant_matches": r.get("quant_matches"),
            "ret_5d": round((r.get("ret_5d") or 0) * 100, 2),
            "mfe_5d": round((r.get("mfe_5d") or 0) * 100, 2),
            "mae_5d": round((r.get("mae_5d") or 0) * 100, 2),
            "mfe_r": wt["mfe_r"],
            "tp_before_sl": wt["tp_before_sl"],
            "time_to_tp": wt["time_to_tp"],
            "time_to_sl": wt["time_to_sl"],
            "winner_class": "clean" if wt["clean_winner"] else "tradable",
            "all_blockers": gates,
            "exclusive_blocker": sole[0] if sole else None,
            "veto_reason": r.get("veto_reason"),
        })
    hits.sort(key=lambda x: (x["winner_class"] == "clean", x["ret_5d"]), reverse=True)
    return hits[:limit]


def low_rule_score_audit(eval_rows, bars_cache=None):
    gate = "FINAL_EDGE:LOW_RULE_SCORE"
    cohort = [r for r in eval_rows if gate in gates_for_row(r)]
    bug_candidates = [
        r for r in cohort
        if safe_float(r.get("scan_score"), 0) < 55
        and int(r.get("quant_matches") or 0) >= 6
        and int(r.get("quality_gate_passed") or 0) == 1
    ]
    clean_bug = []
    for r in bug_candidates:
        wt = classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)
        if wt["clean_winner"] or wt["tradable_winner"]:
            clean_bug.append({
                "date": r["signal_date"], "symbol": r["symbol"],
                "scan_score": r.get("scan_score"), "quant_matches": r.get("quant_matches"),
                "ues": r.get("ues"), "ml_score": r.get("ml_score"),
                "winner_class": "clean" if wt["clean_winner"] else "tradable",
                "ret_5d": round((r.get("ret_5d") or 0) * 100, 2),
                "sole_blocker": sole_blocker(r, gate),
                "other_blockers": [g for g in gates_for_row(r) if g != gate],
            })
    exclusive_clean = sum(
        1 for r in cohort
        if sole_blocker(r, gate)
        and classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)["clean_winner"]
    )
    return {
        "total_blocked": len(cohort),
        "scan_zero_quant6_plus": len(bug_candidates),
        "bug_cohort_tradable_or_clean": len(clean_bug),
        "exclusive_clean_winners": exclusive_clean,
        "co_blocked_clean_winners": sum(
            1 for r in cohort
            if co_blocked(r, gate)
            and classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)["clean_winner"]
        ),
        "verdict": (
            "FIX_BUG_CONFIRMED" if exclusive_clean >= 5 or len(clean_bug) >= 10
            else "FIX_BUG_LIKELY" if len(clean_bug) >= 3
            else "INCONCLUSIVE_NEED_MORE_DATA"
        ),
        "samples": clean_bug[:15],
    }


def rr_too_low_audit(eval_rows, bars_cache=None):
    gate = "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC"
    cohort = [r for r in eval_rows if gate in gates_for_row(r)]
    rows = []
    for r in cohort:
        wt = classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)
        eff_rr = parse_effective_rr(r)
        rows.append({
            "date": r["signal_date"], "symbol": r["symbol"],
            "effective_rr": round(eff_rr, 2) if eff_rr else None,
            "r_ratio": r.get("r_ratio"),
            "stop_dist_pct": r.get("stop_dist_pct"),
            "mfe_5d": round((r.get("mfe_5d") or 0) * 100, 2),
            "mae_5d": round((r.get("mae_5d") or 0) * 100, 2),
            "tp_before_sl": wt["tp_before_sl"],
            "clean_winner": wt["clean_winner"],
            "sole_blocker": sole_blocker(r, gate),
            "other_blockers": [g for g in gates_for_row(r) if g != gate],
        })
    clean_sole = [x for x in rows if x["clean_winner"] and x["sole_blocker"]]
    clean_co = [x for x in rows if x["clean_winner"] and not x["sole_blocker"]]
    low_rr_but_clean = [x for x in rows if x["clean_winner"] and x["effective_rr"] is not None and x["effective_rr"] < 1.3]
    return {
        "total_blocked": len(cohort),
        "clean_winners_blocked": sum(1 for x in rows if x["clean_winner"]),
        "exclusive_clean_winners": len(clean_sole),
        "co_blocked_clean_winners": len(clean_co),
        "low_rr_calc_but_traded_clean": len(low_rr_but_clean),
        "verdict": (
            "RR_CALC_BUG_LIKELY" if len(low_rr_but_clean) >= 10
            else "RR_GATE_OVERSTRICT" if len(clean_sole) >= 5
            else "MOSTLY_CO_BLOCKED" if len(clean_co) > len(clean_sole)
            else "REVIEW"
        ),
        "samples": sorted(rows, key=lambda x: x["mfe_5d"], reverse=True)[:15],
    }


def forecast_down_audit(eval_rows, bars_cache=None):
    gates = {"FORECAST_DOWN", "FORECAST_DOWNSIDE_DOMINANT"}
    cohort = [r for r in eval_rows if gates & set(gates_for_row(r))]
    by_dim = defaultdict(lambda: {"n": 0, "clean_win": 0, "lose": 0, "ret5_sum": 0.0})

    def bucket_row(r, dim, val):
        if not val:
            return
        key = f"{dim}:{val}"
        wt = classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)
        by_dim[key]["n"] += 1
        if wt["clean_winner"]:
            by_dim[key]["clean_win"] += 1
        if int(r.get("loser_5d") or 0):
            by_dim[key]["lose"] += 1
        by_dim[key]["ret5_sum"] += r.get("ret_5d") or 0

    for r in cohort:
        bucket_row(r, "setup", r.get("setup_type") or "unknown")
        bucket_row(r, "sector", r.get("sector") or "unknown")
        bucket_row(r, "breadth", r.get("breadth_signal") or "unknown")
        ml = r.get("ml_score")
        ml_b = "ml_high" if ml and ml >= 80 else "ml_mid" if ml and ml >= 60 else "ml_low"
        bucket_row(r, "ml_bucket", ml_b)
        vr = r.get("vol_ratio")
        vb = "vol_high" if vr and vr >= 2.5 else "vol_low"
        bucket_row(r, "volume_regime", vb)

    breakdown = []
    for key, st in sorted(by_dim.items(), key=lambda kv: kv[1]["clean_win"], reverse=True):
        n = st["n"] or 1
        breakdown.append({
            "bucket": key,
            "blocked": st["n"],
            "clean_winners_blocked": st["clean_win"],
            "losers_blocked": st["lose"],
            "avg_ret5": round(st["ret5_sum"] / n, 4),
            "false_negative_rate": round(st["clean_win"] / n, 3),
        })
    return {"total_blocked": len(cohort), "by_bucket": breakdown[:30]}


def anti_law_subrule_audit(eval_rows, bars_cache=None):
    cohort = [r for r in eval_rows if int(r.get("anti_law") or 0) or "ANTI_LAW" in gates_for_row(r)]
    by_rule = defaultdict(lambda: {"n": 0, "clean": 0, "lose": 0})
    for r in cohort:
        rule = r.get("strongest_anti_law") or "UNKNOWN"
        types = parse_json_list(r.get("anti_law_types"))
        if types:
            for t in types:
                rule = str(t)
                break
        wt = classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)
        by_rule[rule]["n"] += 1
        if wt["clean_winner"]:
            by_rule[rule]["clean"] += 1
        if int(r.get("loser_5d") or 0):
            by_rule[rule]["lose"] += 1
    return {
        "total_anti_law_blocks": len(cohort),
        "by_sub_rule": [
            {
                "sub_rule": k,
                "blocked": v["n"],
                "clean_winners_blocked": v["clean"],
                "losers_blocked": v["lose"],
            }
            for k, v in sorted(by_rule.items(), key=lambda kv: kv[1]["n"], reverse=True)
        ],
    }


def high_volume_chase_audit(eval_rows, bars_cache=None):
    gate = "high_volume_chase"
    cohort = [r for r in eval_rows if gate in gates_for_row(r)]
    rows = []
    for r in cohort:
        wt = classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])) if bars_cache else None)
        rows.append({
            "date": r["signal_date"], "symbol": r["symbol"],
            "ret_1d": round((r.get("ret_1d") or 0) * 100, 2),
            "ret_3d": round((r.get("ret_3d") or 0) * 100, 2),
            "ret_5d": round((r.get("ret_5d") or 0) * 100, 2),
            "mfe_5d": round((r.get("mfe_5d") or 0) * 100, 2),
            "mae_5d": round((r.get("mae_5d") or 0) * 100, 2),
            "tp_before_sl": wt["tp_before_sl"],
            "vol_ratio": r.get("vol_ratio"),
            "clean_winner": wt["clean_winner"],
        })
    return {
        "total_blocked": len(cohort),
        "clean_winners": sum(1 for x in rows if x["clean_winner"]),
        "avg_ret_1d": round(mean([x["ret_1d"] for x in rows if x["ret_1d"] is not None]), 2) if rows else None,
        "avg_ret_3d": round(mean([x["ret_3d"] for x in rows if x["ret_3d"] is not None]), 2) if rows else None,
        "samples": rows[:15],
    }


def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def build_decision_board(cm, ex, loo, net, specials):
    board = []
    cm_map = {x["gate"]: x for x in cm}
    ex_map = {x["gate"]: x for x in ex}
    loo_map = {x["gate_removed"]: x for x in loo}
    net_map = {x["gate"]: x for x in net}

    focus = [
        "FINAL_EDGE:LOW_RULE_SCORE",
        "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC",
        "FORECAST_DOWN",
        "FORECAST_DOWNSIDE_DOMINANT",
        "high_volume_chase",
        "survival_sl_dominant",
        "meta_label_low",
        "FINAL_EDGE:SL_NOT_BELOW_RECENT_STRUCTURE",
        "ANTI_LAW",
    ]
    for gate in focus:
        c = cm_map.get(gate, {})
        e = ex_map.get(gate, {})
        l = loo_map.get(gate, {})
        n = net_map.get(gate, {})
        status = c.get("outcome_status", "EVALUABLE")
        if status == "PENDING_OUTCOME_DATA":
            action = "WAIT"
            diagnosis = "بوابة حديثة — لا ret_5d كافٍ"
            confidence = "LOW"
        elif gate == "FINAL_EDGE:LOW_RULE_SCORE":
            lr = specials.get("low_rule_score", {})
            diagnosis = lr.get("verdict", "REVIEW")
            action = "Fix Bug" if "FIX_BUG" in diagnosis else "WAIT"
            confidence = "HIGH" if lr.get("exclusive_clean_winners", 0) >= 5 else "MEDIUM"
        elif gate == "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC":
            rr = specials.get("rr_too_low", {})
            diagnosis = rr.get("verdict", "REVIEW")
            action = "Recalibrate RR" if "BUG" in diagnosis or "OVERSTRICT" in diagnosis else "Soft"
            confidence = "MEDIUM"
        elif gate.startswith("FORECAST"):
            diagnosis = "يقتل Clean Winners — ليس veto عامًا"
            action = "Soft / Conditional Override"
            confidence = "HIGH" if c.get("clean_winners_blocked", 0) >= 20 else "MEDIUM"
        elif gate == "high_volume_chase":
            diagnosis = "FNR عالي — قد يكون regime يونيو مختلف"
            action = "Conditional / لا تحذف"
            confidence = "MEDIUM"
        elif c.get("blocked_clean_ratio", 0) <= 0.15 and c.get("saved_loss_ratio", 0) >= 0.55:
            diagnosis = "حارس ممتاز"
            action = "Keep Hard"
            confidence = "HIGH"
        elif n.get("net_utility", 0) < 0 and c.get("clean_winners_blocked", 0) >= 5:
            diagnosis = "قيمة صافية سلبية"
            action = "Soft / Recalibrate"
            confidence = "MEDIUM"
        else:
            diagnosis = "يحتاج مزيد بيانات"
            action = "Review"
            confidence = "LOW"

        board.append({
            "gate": gate,
            "evidence": {
                "blocked": c.get("blocked_count"),
                "clean_winners_blocked": c.get("clean_winners_blocked"),
                "exclusive_clean_winners": e.get("exclusive_clean_winners"),
                "co_blocked_clean_winners": e.get("co_blocked_clean_winners"),
                "loo_new_actionables": l.get("new_actionables"),
                "net_utility": n.get("net_utility"),
            },
            "diagnosis": diagnosis,
            "action": action,
            "confidence": confidence,
            "outcome_status": status,
        })
    return board


def cmd_audit_phase2(params: dict):
    conn = connect()
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period_filter = params.get("period", "A_FULL_5D")

    rows = load_enriched_rows(conn, start, end)
    by_sym, idx = load_bars(conn)
    conn.close()

    if not rows:
        return {"success": False, "error": "no snapshots"}

    bars_cache = {}
    for r in rows:
        if not evaluable(r):
            continue
        key = (r["signal_date"], r["symbol"])
        bars_cache[key] = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)

    if period_filter == "ALL":
        eval_rows = [r for r in rows if evaluable(r)]
    else:
        eval_rows = [r for r in rows if evaluable(r) and r["_period"] == period_filter]

    report = {
        "success": True,
        "phase": 2,
        "period_filter": period_filter,
        "period_note": {
            "A_FULL_5D": "2026-06-01..05 — primary clinical cohort",
            "B_PARTIAL_5D": "2026-06-06..07 — partial",
            "C_PENDING_5D": "2026-06-08+ — survival/meta pending",
        },
        "n_snapshots": len(rows),
        "n_evaluable": len(eval_rows),
        "table_1_confusion_matrix": build_confusion_matrix(eval_rows, bars_cache),
        "table_2_exclusive_blockers": build_exclusive_audit(eval_rows, bars_cache),
        "table_3_leave_one_out": build_loo_audit(eval_rows, bars_cache),
        "table_4_overlap_matrix": build_overlap_matrix(eval_rows),
        "table_5_false_negatives_detail": build_false_negatives_detail(eval_rows, bars_cache),
        "table_6_decision_board": None,
        "deep_dives": {
            "low_rule_score": low_rule_score_audit(eval_rows, bars_cache),
            "rr_too_low": rr_too_low_audit(eval_rows, bars_cache),
            "forecast_down": forecast_down_audit(eval_rows, bars_cache),
            "anti_law_subrules": anti_law_subrule_audit(eval_rows, bars_cache),
            "high_volume_chase": high_volume_chase_audit(eval_rows, bars_cache),
        },
        "table_6_net_utility": build_net_utility(eval_rows, bars_cache),
    }
    report["table_6_decision_board"] = build_decision_board(
        report["table_1_confusion_matrix"],
        report["table_2_exclusive_blockers"],
        report["table_3_leave_one_out"],
        report["table_6_net_utility"],
        report["deep_dives"],
    )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{period_filter}"
    json_path = REPORT_DIR / f"gate_doctor_phase2_{tag}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    t1 = report["table_1_confusion_matrix"]
    t2 = report["table_2_exclusive_blockers"]
    t3 = report["table_3_leave_one_out"]
    lr = report["deep_dives"]["low_rule_score"]
    lines = [
        "Gate Doctor Phase 2 — Clinical Audit",
        f"Period: {start} → {end} | Cohort: {period_filter} | Evaluable: {len(eval_rows)}",
        "",
        "=== Table 1: Confusion Matrix (Clean vs Dirty) ===",
        f"{'Gate':<34} {'Blk':>5} {'Clean':>6} {'Dirty':>6} {'Lose':>6} {'TP|SL':>6} {'SL|TP':>6}",
    ]
    for g in t1[:20]:
        lines.append(
            f"{g['gate']:<34} {g['blocked_count']:>5} {g['clean_winners_blocked']:>6} "
            f"{g['dirty_winners_blocked']:>6} {g['losers_blocked']:>6} "
            f"{g['tp_before_sl_blocked']:>6} {g['sl_before_tp_blocked']:>6}"
        )
    lines += ["", "=== Table 2: Exclusive Blockers ===",
              f"{'Gate':<34} {'Excl':>5} {'ExClean':>8} {'CoClean':>8} {'NetUtil':>8}"]
    for g in t2[:15]:
        lines.append(
            f"{g['gate']:<34} {g['exclusive_blocks']:>5} {g['exclusive_clean_winners']:>8} "
            f"{g['co_blocked_clean_winners']:>8} {g['exclusive_net_utility']:>8.3f}"
        )
    lines += ["", "=== Table 3: Leave-One-Gate-Out (exclusive only) ===",
              f"{'Gate':<34} {'NewAct':>7} {'Clean':>6} {'Lose':>6} {'NetR':>8}"]
    for g in t3[:15]:
        lines.append(
            f"{g['gate_removed']:<34} {g['new_actionables']:>7} {g['new_clean_winners']:>6} "
            f"{g['new_losers']:>6} {g['net_r']:>8.3f}"
        )
    lines += [
        "",
        "=== LOW_RULE_SCORE Deep Dive ===",
        f"Total blocked: {lr['total_blocked']}",
        f"scan<55 + quant>=6 (bug cohort): {lr['scan_zero_quant6_plus']}",
        f"Bug cohort tradable/clean: {lr['bug_cohort_tradable_or_clean']}",
        f"Exclusive clean winners: {lr['exclusive_clean_winners']}",
        f"Co-blocked clean winners: {lr['co_blocked_clean_winners']}",
        f"VERDICT: {lr['verdict']}",
        "",
        "=== Decision Board ===",
    ]
    for d in report["table_6_decision_board"]:
        lines.append(f"{d['gate']:<34} {d['action']:<22} [{d['confidence']}] {d['diagnosis']}")

    txt_path = REPORT_DIR / f"gate_doctor_phase2_{tag}.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


POST_P0_GATE_KEYS = [
    ("FORECAST_DOWN", "FORECAST_DOWN"),
    ("LOW_RULE_SCORE", "FINAL_EDGE:LOW_RULE_SCORE"),
    ("negative_breadth", "negative_breadth_ad"),
    ("ANTI_LAW", "ANTI_LAW"),
    ("survival_sl_dominant", "survival_sl_dominant"),
    ("meta_label_low", "meta_label_low"),
]


def _phase2_gate_metrics(report: dict, gate_key: str) -> dict:
    cm = {x["gate"]: x for x in report.get("table_1_confusion_matrix", [])}
    ex = {x["gate"]: x for x in report.get("table_2_exclusive_blockers", [])}
    loo = {x["gate_removed"]: x for x in report.get("table_3_leave_one_out", [])}
    board = {x["gate"]: x for x in report.get("table_6_decision_board", [])}
    c = cm.get(gate_key, {})
    e = ex.get(gate_key, {})
    l = loo.get(gate_key, {})
    b = board.get(gate_key, {})
    status = c.get("outcome_status")
    if not status and gate_key in PENDING_GATES:
        status = "PENDING_OUTCOME_DATA"
    elif not status:
        status = "EVALUABLE" if c else "NOT_IN_COHORT"
    return {
        "clean_blocked": c.get("clean_winners_blocked"),
        "exclusive_clean": e.get("exclusive_clean_winners"),
        "co_clean": e.get("co_blocked_clean_winners"),
        "loo_clean": l.get("new_clean_winners"),
        "loo_new_actionables": l.get("new_actionables"),
        "blocked": c.get("blocked_count"),
        "outcome_status": status,
        "board_action": b.get("action"),
        "board_diagnosis": b.get("diagnosis"),
    }


def _post_p0_clinical_decision(label: str, before: dict, after: dict) -> str:
    if label in ("survival_sl_dominant", "meta_label_low"):
        if after.get("outcome_status") == "PENDING_OUTCOME_DATA" or after.get("clean_blocked") is None:
            return "PENDING OUTCOME DATA"
    if label == "FORECAST_DOWN":
        ec = after.get("exclusive_clean") or 0
        cc = after.get("co_clean") or 0
        cb = after.get("clean_blocked") or 0
        if cb >= 20 and ec == 0 and cc >= 15:
            return "Soft penalty — co-blocker dominant"
        if cb >= 20:
            return "Soft / conditional override"
        return "Review"
    if label == "LOW_RULE_SCORE":
        ec = after.get("exclusive_clean") or 0
        loo = after.get("loo_clean") or 0
        if ec <= 1 and loo <= 1:
            return "Fix bug — limited marginal impact"
        if ec >= 3:
            return "Fix bug — exclusive clean impact"
        return "Fix bug — verify quant path"
    if label == "negative_breadth":
        cb = after.get("clean_blocked") or 0
        ec = after.get("exclusive_clean") or 0
        if cb >= 15 and ec <= 2:
            return "Convert veto → tiered penalty"
        if cb >= 10:
            return "Review — relative strength override?"
        return "Keep / review"
    if label == "ANTI_LAW":
        return "Decompose sub-rules — no block fix"
    if label == "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY":
        return "P0 fixed — WATCH_REENTRY path"
    return after.get("board_action") or "Review"


def cmd_audit_post_p0(params: dict):
    """Phase 2.6 — before/after P0 gate comparison on clean baseline."""
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period = params.get("period", "A_FULL_5D")
    tag = f"{start}_{end}_{period}"

    baseline_path = REPORT_DIR / params.get(
        "baseline_json",
        f"gate_doctor_phase2_pre_p0_{tag}.json",
    )
    if not baseline_path.exists():
        return {"success": False, "error": f"baseline not found: {baseline_path}"}

    post = cmd_audit_phase2({"start_date": start, "end_date": end, "period": period})
    if not post.get("success"):
        return post

    pre = json.loads(baseline_path.read_text(encoding="utf-8"))
    post_path = REPORT_DIR / f"gate_doctor_phase2_{tag}.json"
    post_data = json.loads(post_path.read_text(encoding="utf-8"))

    # RR/STALE shift (P0 diagnostic)
    rr_before = _phase2_gate_metrics(pre, "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC")
    rr_after = _phase2_gate_metrics(post_data, "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC")
    stale_after = _phase2_gate_metrics(post_data, "FINAL_EDGE:STALE_TARGET_WATCH_REENTRY")

    rows = []
    for label, gate_key in POST_P0_GATE_KEYS:
        b = _phase2_gate_metrics(pre, gate_key)
        a = _phase2_gate_metrics(post_data, gate_key)
        rows.append({
            "gate": label,
            "gate_key": gate_key,
            "clean_before": b.get("clean_blocked"),
            "clean_after": a.get("clean_blocked"),
            "delta_clean": (
                (a.get("clean_blocked") or 0) - (b.get("clean_blocked") or 0)
                if a.get("clean_blocked") is not None and b.get("clean_blocked") is not None
                else None
            ),
            "exclusive_clean_after": a.get("exclusive_clean"),
            "co_clean_after": a.get("co_clean"),
            "loo_clean_after": a.get("loo_clean"),
            "loo_new_actionables_after": a.get("loo_new_actionables"),
            "blocked_after": a.get("blocked"),
            "outcome_status": a.get("outcome_status"),
            "decision": _post_p0_clinical_decision(label, b, a),
        })

    report = {
        "success": True,
        "phase": "2.6_post_p0",
        "period": period,
        "cohort": {"start": start, "end": end},
        "n_evaluable_before": pre.get("n_evaluable"),
        "n_evaluable_after": post_data.get("n_evaluable"),
        "p0_rr_reclassification": {
            "rr_clean_before": rr_before.get("clean_blocked"),
            "rr_clean_after": rr_after.get("clean_blocked"),
            "stale_clean_after": stale_after.get("clean_blocked"),
            "stale_blocked_after": stale_after.get("blocked"),
        },
        "comparison_table": rows,
        "forecast_deep_dive": post_data.get("deep_dives", {}).get("forecast_down"),
        "low_rule_deep_dive": post_data.get("deep_dives", {}).get("low_rule_score"),
        "anti_law_subrules": post_data.get("deep_dives", {}).get("anti_law_subrules"),
        "decision_board_after": post_data.get("table_6_decision_board"),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_tag = f"{start}_{end}"
    json_out = REPORT_DIR / f"gate_doctor_post_p0_audit_{out_tag}.json"
    txt_out = REPORT_DIR / f"gate_doctor_post_p0_audit_{out_tag}.txt"
    json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Gate Doctor — Post-P0 Clinical Audit (Phase 2.6)",
        f"Period: {start} → {end} | Cohort: {period}",
        f"Evaluable: before={pre.get('n_evaluable')} after={post_data.get('n_evaluable')}",
        "",
        "=== P0 Risk Pipeline Reclassification ===",
        f"RR_TOO_LOW clean blocked:  before={rr_before.get('clean_blocked')}  after={rr_after.get('clean_blocked')}",
        f"STALE_TARGET clean blocked: after={stale_after.get('clean_blocked')}  (total blocked={stale_after.get('blocked')})",
        "",
        "=== Gate Comparison (before P0 → after P0) ===",
        f"{'Gate':<22} {'CleanBef':>8} {'CleanAft':>8} {'Δ':>5} {'ExclClean':>9} {'CoClean':>8} {'LOO':>5} {'Decision':<36}",
    ]
    for r in rows:
        delta = r["delta_clean"]
        delta_s = f"{delta:+d}" if delta is not None else "—"
        lines.append(
            f"{r['gate']:<22} "
            f"{r['clean_before'] if r['clean_before'] is not None else '—':>8} "
            f"{r['clean_after'] if r['clean_after'] is not None else '—':>8} "
            f"{delta_s:>5} "
            f"{r['exclusive_clean_after'] if r['exclusive_clean_after'] is not None else '—':>9} "
            f"{r['co_clean_after'] if r['co_clean_after'] is not None else '—':>8} "
            f"{r['loo_clean_after'] if r['loo_clean_after'] is not None else '—':>5} "
            f"{r['decision']:<36}"
        )

    lines += [
        "",
        "=== FORECAST_DOWN — Post-P0 Deep Dive (top buckets) ===",
    ]
    for b in (report.get("forecast_deep_dive") or {}).get("by_bucket", [])[:8]:
        lines.append(
            f"  {b['bucket']:<28} blocked={b['blocked']:>3} clean={b['clean_winners_blocked']:>3} "
            f"FNR={b['false_negative_rate']:.3f} avg_ret5={b['avg_ret5']}"
        )

    lines += ["", "=== LOW_RULE_SCORE — Post-P0 ==="]
    lr = report.get("low_rule_deep_dive") or {}
    lines.append(f"  Total blocked: {lr.get('total_blocked')}")
    lines.append(f"  scan<55 + quant>=6 bug cohort: {lr.get('scan_zero_quant6_plus')}")
    lines.append(f"  Exclusive clean: {lr.get('exclusive_clean_winners')} | Co-blocked clean: {lr.get('co_blocked_clean_winners')}")
    lines.append(f"  VERDICT: {lr.get('verdict')}")

    lines += ["", "=== ANTI_LAW Sub-Rules (post-P0) ==="]
    for s in (report.get("anti_law_subrules") or {}).get("by_sub_rule", [])[:8]:
        lines.append(
            f"  {s['sub_rule']:<32} blocked={s['blocked']:>3} clean={s['clean_winners_blocked']:>3} lose={s['losers_blocked']:>3}"
        )

    lines += [
        "",
        "=== Clinical Priority (post-P0 baseline) ===",
        "1. FORECAST_DOWN      — first candidate if co-blocked clean remains high",
        "2. LOW_RULE_SCORE     — bug fix if marginal LOO ≤1",
        "3. negative_breadth   — tiered penalty if exclusive clean low",
        "4. ANTI_LAW           — decompose sub-rules individually",
        "5. survival/meta      — PENDING until ret_5d for 08-10 cohort",
        "",
        "NO FIXES APPLIED — audit only.",
    ]
    txt_out.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_out)
    report["report_txt"] = str(txt_out)
    report["phase2_after_json"] = str(post_path)
    report["baseline_json"] = str(baseline_path)
    return report


def gate_decision(blocked_loser_pct, blocked_winner_pct, n_blocked, n_eval):
    if n_blocked < 5 or n_eval < 3:
        return "INSUFFICIENT_DATA"
    if blocked_winner_pct >= 0.35 and blocked_loser_pct < 0.50:
        return "Recalibrate"
    if blocked_winner_pct >= 0.25 and blocked_loser_pct >= 0.55:
        return "Soft"
    if blocked_winner_pct >= 0.30 and blocked_loser_pct < 0.45:
        return "Fix"
    if blocked_loser_pct >= 0.65 and blocked_winner_pct <= 0.15:
        return "Keep"
    if blocked_loser_pct >= 0.55 and blocked_winner_pct <= 0.20:
        return "Keep"
    if blocked_winner_pct > blocked_loser_pct:
        return "Recalibrate"
    if blocked_loser_pct >= 0.50:
        return "Soft"
    return "Review"


def cmd_audit(params: dict):
    ensure_gate_audit_table(conn := connect())
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date")
    q = "SELECT * FROM gate_audit_snapshots WHERE signal_date>=?"
    args = [start]
    if end:
        q += " AND signal_date<=?"
        args.append(end)
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()

    if not rows:
        return {"success": False, "error": "no snapshots — run backfill or rescore first"}

    eval_rows = [r for r in rows if evaluable(r)]
    dates = sorted({r["signal_date"] for r in rows})

    # Sequential funnel per date
    funnel = {}
    for d in dates:
        day = [r for r in rows if r["signal_date"] == d]
        funnel[d] = {
            "total": len(day),
            "quality_pass": sum(int(r["quality_gate_passed"] or 0) for r in day),
            "final_edge_pass": sum(int(r["final_edge_passed"] or 0) for r in day),
            "actionable": sum(int(r["actionable"] or 0) for r in day),
            "top_first_blocker": _top_counts([r.get("first_blocking_gate") for r in day if not int(r["actionable"] or 0)], 5),
        }

    # Independent gate audit
    gate_stats = defaultdict(lambda: {
        "blocked": 0, "winners_blocked": 0, "losers_blocked": 0,
        "eval_blocked": 0, "saved_loss_ret5": [], "killed_win_ret5": [],
        "exclusive_blocked": 0, "co_blocked": 0, "late_only": 0,
    })

    for r in eval_rows:
        gates = gates_for_row(r)
        excl = set(exclusive_for_row(r))
        first = normalize_gate(r.get("first_blocking_gate") or "")
        win = int(r["winner_5d"] or 0)
        lose = int(r["loser_5d"] or 0)
        ret5 = r["ret_5d"]
        for g in set(gates):
            st = gate_stats[g]
            st["blocked"] += 1
            st["eval_blocked"] += 1
            if win:
                st["winners_blocked"] += 1
                st["killed_win_ret5"].append(ret5)
            if lose:
                st["losers_blocked"] += 1
                st["saved_loss_ret5"].append(ret5)
            if g in excl:
                st["exclusive_blocked"] += 1
            elif len(gates) > 1:
                st["co_blocked"] += 1
            if first and g != first and g in gates:
                st["late_only"] += 1

    gate_report = []
    for gate, st in sorted(gate_stats.items(), key=lambda kv: kv[1]["blocked"], reverse=True):
        eb = st["eval_blocked"] or 1
        wl = st["winners_blocked"] / eb
        ll = st["losers_blocked"] / eb
        fnr = round(wl, 3)
        gate_report.append({
            "gate": gate,
            "blocked": st["blocked"],
            "eval_blocked": st["eval_blocked"],
            "winners_blocked": st["winners_blocked"],
            "losers_blocked": st["losers_blocked"],
            "false_negative_rate": fnr,
            "true_negative_rate": round(ll, 3),
            "avg_killed_win_ret5": round(mean(st["killed_win_ret5"]), 4) if st["killed_win_ret5"] else None,
            "avg_saved_loss_ret5": round(mean(st["saved_loss_ret5"]), 4) if st["saved_loss_ret5"] else None,
            "exclusive_blocked": st["exclusive_blocked"],
            "co_blocked": st["co_blocked"],
            "late_blocker_hits": st["late_only"],
            "decision": gate_decision(ll, wl, st["blocked"], eb),
        })

    false_negative_leaders = sorted(gate_report, key=lambda x: x["false_negative_rate"], reverse=True)[:10]
    true_negative_leaders = sorted(gate_report, key=lambda x: x["true_negative_rate"], reverse=True)[:10]

    actionable = [r for r in eval_rows if int(r["actionable"] or 0)]
    passed_win = sum(int(r["winner_5d"] or 0) for r in actionable)
    passed_lose = sum(int(r["loser_5d"] or 0) for r in actionable)

    report = {
        "success": True,
        "period": {"start": start, "end": end or dates[-1]},
        "n_snapshots": len(rows),
        "n_evaluable_5d": len(eval_rows),
        "n_dates": len(dates),
        "actionable_evaluable": len(actionable),
        "actionable_winners_5d": passed_win,
        "actionable_losers_5d": passed_lose,
        "sequential_funnel": funnel,
        "independent_gate_audit": gate_report,
        "false_negative_leaders": false_negative_leaders,
        "true_negative_leaders": true_negative_leaders,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORT_DIR / f"gate_doctor_audit_{start}_{end or 'latest'}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    txt_lines = [
        "Gate Doctor Audit",
        f"Period: {start} → {end or dates[-1]}",
        f"Snapshots: {len(rows)} | Evaluable 5d: {len(eval_rows)}",
        "",
        "=== Independent Gate Audit ===",
        f"{'Gate':<32} {'Blk':>5} {'WinBlk':>7} {'LoseBlk':>8} {'FNR':>6} {'TNR':>6} {'Decision':<12}",
    ]
    for g in gate_report[:25]:
        txt_lines.append(
            f"{g['gate']:<32} {g['blocked']:>5} {g['winners_blocked']:>7} "
            f"{g['losers_blocked']:>8} {g['false_negative_rate']:>6.1%} "
            f"{g['true_negative_rate']:>6.1%} {g['decision']:<12}"
        )
    txt_path = REPORT_DIR / f"gate_doctor_audit_{start}_{end or 'latest'}.txt"
    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")
    report["report_json"] = str(out_path)
    report["report_txt"] = str(txt_path)
    return report


def _top_counts(items, n):
    c = defaultdict(int)
    for x in items:
        if x:
            c[normalize_gate(x)] += 1
    return sorted(c.items(), key=lambda kv: kv[1], reverse=True)[:n]


def cmd_false_blocks(params: dict):
    ensure_gate_audit_table(conn := connect())
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date")
    gate_filter = params.get("gate")
    limit = int(params.get("limit", 30))
    q = """
        SELECT * FROM gate_audit_snapshots
        WHERE signal_date>=? AND outcomes_filled=1 AND winner_5d=1 AND actionable=0
    """
    args = [start]
    if end:
        q += " AND signal_date<=?"
        args.append(end)
    q += " ORDER BY ret_5d DESC"
    rows = [dict(r) for r in conn.execute(q, args).fetchall()]
    conn.close()

    hits = []
    for r in rows:
        gates = gates_for_row(r)
        if gate_filter and normalize_gate(gate_filter) not in gates:
            continue
        hits.append({
            "date": r["signal_date"],
            "symbol": r["symbol"],
            "ues": r["ues"],
            "ml_score": r["ml_score"],
            "ret_5d": round(r["ret_5d"] * 100, 2) if r["ret_5d"] is not None else None,
            "mfe_5d": round(r["mfe_5d"] * 100, 2) if r["mfe_5d"] is not None else None,
            "tp_before_sl": r["tp_before_sl"],
            "blocking_gates": gates,
            "exclusive": exclusive_for_row(r),
            "veto_reason": r["veto_reason"],
        })
    hits.sort(key=lambda x: x["ret_5d"] or 0, reverse=True)
    return {"success": True, "n": len(hits[:limit]), "false_blocks": hits[:limit]}


def cmd_run(params: dict):
    """Full pipeline: backfill → fill_outcomes → pending check → audit."""
    backfill = cmd_backfill(params)
    fill = cmd_fill_outcomes({**params, "check_pending": True, "auto_run": True})
    audit = cmd_audit(params)
    return {
        "success": True,
        "backfill": backfill,
        "fill_outcomes": fill,
        "pending_outcomes": fill.get("pending_outcomes"),
        "audit": {k: audit[k] for k in audit if k != "independent_gate_audit"},
        "gate_count": len(audit.get("independent_gate_audit", [])),
        "report_txt": audit.get("report_txt"),
        "report_json": audit.get("report_json"),
    }


RR_GATE = "FINAL_EDGE:RR_TOO_LOW_AFTER_RECALC"
PULLBACK_SETUP = ("retest", "accumulation", "pullback", "institutional", "mean reversion", "support")
BREAKOUT_SETUP = ("breakout", "power", "explosion", "momentum", "trend continuation")


def bar_on_date(by_sym, idx, symbol, signal_date):
    arr = by_sym.get(symbol)
    pos = idx.get(symbol, {}).get(signal_date)
    if arr is None or pos is None:
        return None
    d, o, h, l, c = arr[pos]
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


def next_open(by_sym, idx, symbol, signal_date):
    arr = by_sym.get(symbol)
    pos = idx.get(symbol, {}).get(signal_date)
    if arr is None or pos is None or pos + 1 >= len(arr):
        return None
    return arr[pos + 1][1]


def compute_rr(entry, stop, target):
    if entry is None or stop is None or target is None:
        return None, ["missing_levels"]
    entry, stop, target = float(entry), float(stop), float(target)
    issues = []
    if stop >= entry:
        issues.append("invalid_stop")
        return None, issues
    if target <= entry:
        issues.append("invalid_target_or_stale")
        return None, issues
    risk = entry - stop
    if risk <= 0:
        issues.append("zero_risk")
        return None, issues
    return round((target - entry) / risk, 4), issues


def setup_kind(setup_type: str | None) -> str:
    text = (setup_type or "").lower()
    if any(k in text for k in PULLBACK_SETUP):
        return "pullback_accumulation"
    if any(k in text for k in BREAKOUT_SETUP):
        return "breakout_immediate"
    return "unknown"


def classify_entry_type(row, close, entry_high) -> str:
    entry = safe_float(row.get("entry_price"), None)
    target = safe_float(row.get("t1_target"), None)
    stop = safe_float(row.get("stop_loss"), None)
    if entry is None or close is None:
        return "unknown"
    if stop is not None and stop >= entry:
        return "bad_stop"
    if target is not None and target <= entry:
        return "bad_target"
    if target is not None and close >= target:
        return "stale_target"
    gap = (close - entry) / entry
    if gap > 0.02:
        return "already_moved"
    if entry_high and close > safe_float(entry_high, entry) * 1.005:
        return "already_moved"
    if gap > 0.005:
        if setup_kind(row.get("setup_type")) == "pullback_accumulation":
            return "pullback_entry"
        return "already_moved"
    if setup_kind(row.get("setup_type")) == "breakout_immediate":
        return "breakout_entry"
    return "pullback_entry"


def effective_rr_old(entry, close, stop, target):
    if entry is None or close is None or stop is None or target is None:
        return None, None
    eff = max(float(entry), float(close))
    rr, issues = compute_rr(eff, stop, target)
    return eff, rr if not issues else None


def classify_forensic_bucket(row, close, next_open, entry_type, rr_entry, rr_close,
                             rr_next, eff_rr_old, flags):
    entry = safe_float(row.get("entry_price"), None)
    stop = safe_float(row.get("stop_loss"), None)
    target = safe_float(row.get("t1_target"), None)
    tp_sl = row.get("tp_before_sl")
    mfe5 = row.get("mfe_5d") or 0
    ret1 = row.get("ret_1d") or 0
    gap = ((close - entry) / entry) if entry and close else 0

    if "invalid_stop" in flags or entry_type == "bad_stop":
        return "BAD_STOP_STRUCTURE"
    if target is not None and entry is not None and target <= entry:
        return "BAD_TARGET"

    # close already at/above scan target — levels stale, not weak RR at entry zone
    if target and close and close >= target:
        if rr_entry is not None and rr_entry >= 1.3:
            return "STALE_TARGET"
        return "BAD_TARGET"

    if (abs(ret1) >= 0.15 or gap >= 0.08) and mfe5 >= 0.25:
        return "NEWS/SPIKE_EXCEPTION"

    if (rr_entry is not None and rr_entry >= 1.3
            and eff_rr_old is not None and eff_rr_old < 1.3
            and tp_sl == 1):
        if entry_type in ("pullback_entry", "breakout_entry") and gap <= 0.02:
            return "RR_BUG_CONFIRMED"
        if setup_kind(row.get("setup_type")) == "pullback_accumulation" and gap > 0.005:
            return "ENTRY_MODEL_MISMATCH"

    if (entry_type == "already_moved" or gap > 0.02
            or (rr_close is not None and rr_close < 1.3)
            or (rr_next is not None and rr_next < 1.3)):
        if rr_entry is not None and rr_entry >= 1.3 and eff_rr_old is not None and eff_rr_old < 1.3:
            return "ENTRY_MODEL_MISMATCH"
        if (rr_close is not None and rr_close < 1.3) or gap > 0.03:
            return "CHASE_RISK_VALID"

    if eff_rr_old is not None and eff_rr_old < 1.3 and tp_sl == 1 and mfe5 >= 0.10:
        if rr_entry is not None and rr_entry >= 1.3:
            return "ENTRY_MODEL_MISMATCH"
        return "RR_BUG_CONFIRMED"

    return "REVIEW"


def build_rr_forensic_case(row, by_sym, idx, bars_cache):
    sym, d = row["symbol"], row["signal_date"]
    bar = bar_on_date(by_sym, idx, sym, d)
    close = bar["close"] if bar else None
    nopen = next_open(by_sym, idx, sym, d)
    entry = row.get("entry_price")
    stop = row.get("stop_loss")
    target = row.get("t1_target")
    entry_high = row.get("entry_high")

    rr_entry, flags_e = compute_rr(entry, stop, target)
    rr_close, flags_c = compute_rr(close, stop, target) if close else (None, [])
    rr_next, flags_n = compute_rr(nopen, stop, target) if nopen else (None, [])
    eff_ent, eff_rr_old = effective_rr_old(entry, close, stop, target)
    flags = list(set(flags_e + flags_c + flags_n))
    entry_type = classify_entry_type(row, close, entry_high)
    bucket = classify_forensic_bucket(
        row, close, nopen, entry_type, rr_entry, rr_close, rr_next, eff_rr_old, flags,
    )
    key = (d, sym)
    wt = classify_winner_types(row, bars_cache.get(key))
    risk = risk_pct(entry, stop)
    realized_r = (row.get("mfe_5d") / risk) if risk and row.get("mfe_5d") is not None else None

    gap_pct = round(((close - entry) / entry * 100), 2) if entry and close else None
    return {
        "date": d,
        "symbol": sym,
        "setup_type": row.get("setup_type"),
        "setup_kind": setup_kind(row.get("setup_type")),
        "entry_type": entry_type,
        "forensic_bucket": bucket,
        "close": close,
        "next_open": nopen,
        "entry_price": entry,
        "entry_high": entry_high,
        "target_price": target,
        "structural_stop": stop,
        "gap_close_vs_entry_pct": gap_pct,
        "r_ratio_original": row.get("r_ratio"),
        "effective_entry_old": eff_ent,
        "effective_rr_old": eff_rr_old,
        "rr_using_entry": rr_entry,
        "rr_using_close": rr_close,
        "rr_using_next_open": rr_next,
        "realized_R_5d_mfe": round(realized_r, 2) if realized_r is not None else None,
        "ret_5d_pct": round((row.get("ret_5d") or 0) * 100, 2),
        "MFE_5d_pct": round((row.get("mfe_5d") or 0) * 100, 2),
        "MAE_5d_pct": round((row.get("mae_5d") or 0) * 100, 2),
        "TP_before_SL": row.get("tp_before_sl"),
        "all_blockers": gates_for_row(row),
        "exclusive_rr_only": sole_blocker(row, RR_GATE),
        "flags": flags,
        "winner_class": "clean" if wt["clean_winner"] else "tradable",
    }


def cmd_audit_rr_forensic(params: dict):
    """Phase 2.1 — RR calculation forensic audit for clean winners blocked by RR_TOO_LOW."""
    conn = connect()
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period_filter = params.get("period", "A_FULL_5D")

    rows = load_enriched_rows(conn, start, end)
    q = """
        SELECT entry_high FROM final_signals
        WHERE trade_date=? AND symbol=?
    """
    for r in rows:
        fs = conn.execute(q, (r["signal_date"], r["symbol"])).fetchone()
        if fs:
            r["entry_high"] = fs["entry_high"]

    by_sym, idx = load_bars(conn)
    conn.close()

    bars_cache = {}
    for r in rows:
        if not evaluable(r):
            continue
        key = (r["signal_date"], r["symbol"])
        bars_cache[key] = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)

    cohort = []
    for r in rows:
        if period_filter != "ALL" and r["_period"] != period_filter:
            continue
        if not evaluable(r):
            continue
        if RR_GATE not in gates_for_row(r):
            continue
        wt = classify_winner_types(r, bars_cache.get((r["signal_date"], r["symbol"])))
        if not wt["clean_winner"]:
            continue
        cohort.append(r)

    cases = [build_rr_forensic_case(r, by_sym, idx, bars_cache) for r in cohort]
    cases.sort(key=lambda x: x["MFE_5d_pct"] or 0, reverse=True)

    bucket_counts = defaultdict(int)
    entry_type_counts = defaultdict(int)
    old_wrong = 0
    chase_valid = 0
    target_stop_bug = 0

    for c in cases:
        bucket_counts[c["forensic_bucket"]] += 1
        entry_type_counts[c["entry_type"]] += 1
        if (c["rr_using_entry"] is not None and c["rr_using_entry"] >= 1.3
                and c["effective_rr_old"] is not None and c["effective_rr_old"] < 1.3):
            old_wrong += 1
        if c["forensic_bucket"] == "CHASE_RISK_VALID":
            chase_valid += 1
        if c["forensic_bucket"] in ("BAD_STOP_STRUCTURE", "BAD_TARGET", "STALE_TARGET"):
            target_stop_bug += 1

    rr_bug = bucket_counts["RR_BUG_CONFIRMED"] + bucket_counts["ENTRY_MODEL_MISMATCH"]
    stale_target = bucket_counts["STALE_TARGET"]
    stale_with_good_entry_rr = sum(
        1 for c in cases
        if c["forensic_bucket"] == "STALE_TARGET"
        and c["rr_using_entry"] is not None and c["rr_using_entry"] >= 1.3
    )

    def pick_symbols(symbols):
        return [c for c in cases if c["symbol"] in symbols]

    spotlight = {
        "ELSH": pick_symbols(["ELSH"]),
        "FIRE": pick_symbols(["FIRE"]),
        "top10_mfe": cases[:10],
    }

    fix_rr_pipeline = stale_with_good_entry_rr + rr_bug
    summary = {
        "total_cases": len(cases),
        "bucket_counts": dict(sorted(bucket_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "entry_type_counts": dict(entry_type_counts),
        "old_rr_wrong_but_entry_rr_ok": old_wrong,
        "pct_old_rr_wrong": round(old_wrong / max(len(cases), 1), 3),
        "stale_target_at_signal": stale_target,
        "stale_target_with_rr_entry_ok": stale_with_good_entry_rr,
        "pct_stale_target": round(stale_target / max(len(cases), 1), 3),
        "rr_bug_or_entry_mismatch": rr_bug,
        "pct_rr_bug_or_mismatch": round(rr_bug / max(len(cases), 1), 3),
        "chase_risk_valid": chase_valid,
        "target_or_stop_bug": target_stop_bug,
        "needs_rr_pipeline_fix": fix_rr_pipeline,
        "pct_needs_rr_pipeline_fix": round(fix_rr_pipeline / max(len(cases), 1), 3),
        "verdict": (
            "FIX_STALE_LEVELS_AND_RR_ENTRY_MODEL"
            if stale_with_good_entry_rr >= len(cases) * 0.5
            else "FIX_RR_CALCULATION_FIRST"
            if fix_rr_pipeline >= len(cases) * 0.5
            else "MIXED_FIX_RR_AND_ENTRY_MODEL"
            if fix_rr_pipeline >= len(cases) * 0.3
            else "GATE_MOSTLY_VALID_REVIEW_ENTRY_MODEL"
        ),
        "root_cause": (
            "أغلب الحالات: close >= target عند الإشارة — مستويات scan قديمة + effective_entry=close يُفسد RR"
            if stale_with_good_entry_rr >= len(cases) * 0.4
            else "خلط بين chase حقيقي وإعادة تسعير الدخول"
        ),
        "recommendation": (
            "1) إذا close>=target → لا تحسب RR_TOO_LOW، صنّف chase/stale\n"
            "2) لا تستخدم max(entry,close) عندما close>target\n"
            "3) أعد بناء target/SL من السياق الحالي أو استخدم entry_price فقط لـ pullback"
        ),
    }

    report = {
        "success": True,
        "phase": "2.1",
        "title": "RR Forensic Audit — Clean Winners blocked by RR_TOO_LOW",
        "period": {"start": start, "end": end, "cohort": period_filter},
        "summary": summary,
        "cases": cases,
        "spotlight": spotlight,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{period_filter}"
    json_path = REPORT_DIR / f"rr_forensic_audit_{tag}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "RR Forensic Audit — Phase 2.1",
        f"Clean winners blocked by {RR_GATE}",
        f"Period: {start} → {end} | Cohort: {period_filter} | Cases: {len(cases)}",
        "",
        "=== Summary ===",
        f"VERDICT: {summary['verdict']}",
        f"Old RR wrong but entry RR ok: {old_wrong}/{len(cases)} ({summary['pct_old_rr_wrong']:.1%})",
        f"RR_BUG + ENTRY_MISMATCH: {rr_bug} ({summary['pct_rr_bug_or_mismatch']:.1%})",
        f"CHASE_RISK_VALID: {chase_valid}",
        f"Target/Stop bugs: {target_stop_bug}",
        f"Recommendation: {summary['recommendation']}",
        "",
        "=== Bucket Counts ===",
    ]
    for k, v in sorted(bucket_counts.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {k:<28} {v:>4}")

    lines += ["", "=== Entry Type ==="]
    for k, v in sorted(entry_type_counts.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {k:<28} {v:>4}")

    lines += ["", "=== Top 10 MFE ===",
              f"{'Date':<12} {'Sym':<6} {'Bucket':<22} {'rr_ent':>7} {'rr_old':>7} {'MFE%':>7} {'Gap%':>6}"]
    for c in cases[:10]:
        lines.append(
            f"{c['date']:<12} {c['symbol']:<6} {c['forensic_bucket']:<22} "
            f"{c['rr_using_entry'] or 0:>7.2f} {c['effective_rr_old'] or 0:>7.2f} "
            f"{c['MFE_5d_pct'] or 0:>7.1f} {c['gap_close_vs_entry_pct'] or 0:>6.1f}"
        )

    lines += ["", "=== ELSH ==="]
    for c in spotlight["ELSH"]:
        lines.append(json.dumps(c, ensure_ascii=False))
    lines += ["", "=== FIRE ==="]
    for c in spotlight["FIRE"]:
        lines.append(json.dumps(c, ensure_ascii=False))

    txt_path = REPORT_DIR / f"rr_forensic_audit_{tag}.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    report["cases"] = cases
    return report


def trading_days_between(idx, symbol, d_from, d_to):
    pos_from = idx.get(symbol, {}).get(d_from)
    pos_to = idx.get(symbol, {}).get(d_to)
    if pos_from is None or pos_to is None:
        return None
    return abs(pos_to - pos_from)


def resolve_scan_levels(conn, signal_date, symbol):
    """Mirror score_all risk-level resolution (read-only)."""
    out = {
        "level_source": "missing",
        "scan_id": None,
        "scan_date": None,
        "setup_id": None,
        "setup_type_scan": None,
        "scan_score": None,
        "level_created_at": None,
        "entry_low": None,
        "entry_high": None,
        "entry_price_resolved": None,
        "stop_loss": None,
        "t1_target": None,
        "r_ratio_scan": None,
        "close_price_scan": None,
        "used_fallback": False,
    }
    sr = None
    try:
        sr = conn.execute(
            """SELECT id, scan_date, entry_low, entry_high, stop_loss, t1, t2, rr1, close_price,
                      setup_type, setup_id, score, volume_ratio, created_at
               FROM scans WHERE scan_date=? AND symbol=? AND rejected=0
               ORDER BY score DESC LIMIT 1""",
            (signal_date, symbol),
        ).fetchone()
        if sr:
            out["level_source"] = "scans_same_day"
        else:
            sr = conn.execute(
                """SELECT id, scan_date, entry_low, entry_high, stop_loss, t1, t2, rr1, close_price,
                          setup_type, setup_id, score, volume_ratio, created_at
                   FROM scans
                   WHERE scan_date >= date(?, '-10 days') AND scan_date <= ?
                     AND symbol=? AND rejected=0
                   ORDER BY score DESC, scan_date DESC LIMIT 1""",
                (signal_date, signal_date, symbol),
            ).fetchone()
            if sr:
                out["level_source"] = "scans_lookback_10d"
    except Exception:
        sr = None

    if sr:
        out.update({
            "scan_id": sr["id"],
            "scan_date": sr["scan_date"],
            "setup_id": sr["setup_id"],
            "setup_type_scan": sr["setup_type"],
            "scan_score": sr["score"],
            "level_created_at": sr["created_at"] or sr["scan_date"],
            "entry_low": safe_float(sr["entry_low"]),
            "entry_high": safe_float(sr["entry_high"]),
            "entry_price_resolved": safe_float(sr["close_price"]) or safe_float(sr["entry_low"]),
            "stop_loss": safe_float(sr["stop_loss"]),
            "t1_target": safe_float(sr["t1"]),
            "r_ratio_scan": safe_float(sr["rr1"]),
            "close_price_scan": safe_float(sr["close_price"]),
        })
        return out

    try:
        ohlcv = conn.execute(
            """SELECT close FROM ohlcv_history_execution
               WHERE symbol=? AND date(bar_time,'unixepoch') <= ?
               ORDER BY bar_time DESC LIMIT 1""",
            (symbol, signal_date),
        ).fetchone()
        atr = conn.execute(
            """SELECT atr14 FROM indicators_cache
               WHERE symbol=? AND bar_date <= ? ORDER BY bar_date DESC LIMIT 1""",
            (symbol, signal_date),
        ).fetchone()
        if ohlcv and ohlcv["close"]:
            entry = float(ohlcv["close"])
            atr14 = float(atr["atr14"]) if atr and atr["atr14"] else entry * 0.02
            atr_pct = min(atr14 / entry if entry > 0 else 0.02, 0.08)
            stop = round(entry * (1.0 - 1.5 * atr_pct), 4)
            t1 = round(entry * (1.0 + 3.0 * atr_pct), 4)
            stop_dist = max(entry - stop, 0.0001)
            rr = round((t1 - entry) / stop_dist, 2)
            out.update({
                "level_source": "atr_fallback",
                "scan_date": signal_date,
                "level_created_at": signal_date,
                "entry_price_resolved": entry,
                "entry_high": round(entry * 1.005, 4),
                "stop_loss": stop,
                "t1_target": t1,
                "r_ratio_scan": rr,
                "close_price_scan": entry,
                "used_fallback": True,
            })
    except Exception:
        pass
    return out


def classify_staleness_bucket(close, entry, stop, target, level_age_days, forensic_bucket=None):
    if entry is None or close is None:
        return "UNKNOWN"
    one_r = (entry - stop) if stop is not None and entry > stop else None
    if stop is not None and stop >= entry:
        return "INVALID_STOP"
    if stop is not None and stop >= close:
        return "INVALID_MARKET_STOP"
    if target is not None and close >= target:
        return "STALE_TARGET"
    if one_r and close > entry + 2 * one_r:
        return "CHASE_NOT_SWING"
    if one_r and close > entry + one_r:
        return "ENTRY_ALREADY_GONE"
    if level_age_days is not None and level_age_days >= 2 and close > entry * 1.05:
        return "STALE_SCAN_LEVELS"
    if forensic_bucket:
        return forensic_bucket
    if close > entry * 1.005:
        return "ENTRY_MODEL_MISMATCH"
    return "SWING_ACTIONABLE"


def recent_low_n(by_sym, idx, symbol, signal_date, n=8):
    arr = by_sym.get(symbol)
    pos = idx.get(symbol, {}).get(signal_date)
    if arr is None or pos is None:
        return None
    window = arr[max(0, pos - n + 1): pos + 1]
    lows = [b[3] for b in window]
    return min(lows) if lows else None


def simulate_risk_rebuild(close, next_open, old_entry, old_stop, old_target, setup_kind, atr14,
                          staleness_bucket):
    old_rr, _ = compute_rr(old_entry, old_stop, old_target)
    _, old_eff_rr = effective_rr_old(old_entry, close, old_stop, old_target)

    rebuilt_entry = rebuilt_stop = rebuilt_target = None
    classification = "REVIEW"

    if staleness_bucket in ("STALE_TARGET", "CHASE_NOT_SWING", "ENTRY_ALREADY_GONE"):
        classification = "MISSED_MOVE_WATCH_REENTRY"
        rebuilt_entry = next_open if next_open else close
        if old_stop and old_stop < rebuilt_entry * 0.98:
            rebuilt_stop = old_stop
        else:
            atr_pct = min((atr14 or rebuilt_entry * 0.02) / rebuilt_entry, 0.08) if rebuilt_entry else 0.03
            rebuilt_stop = round(rebuilt_entry * (1.0 - 1.5 * atr_pct), 4)
        risk = max(rebuilt_entry - rebuilt_stop, 0.0001)
        rebuilt_target = round(rebuilt_entry + 2.0 * risk, 4)
    elif setup_kind == "pullback_accumulation":
        rebuilt_entry = old_entry
        rebuilt_stop = old_stop
        rebuilt_target = old_target
        classification = "SWING_ACTIONABLE_PULLBACK"
    else:
        rebuilt_entry = next_open if next_open else close
        if old_stop and old_stop < rebuilt_entry:
            rebuilt_stop = old_stop
        else:
            atr_pct = min((atr14 or rebuilt_entry * 0.02) / rebuilt_entry, 0.08) if rebuilt_entry else 0.03
            rebuilt_stop = round(rebuilt_entry * (1.0 - 1.5 * atr_pct), 4)
        risk = max(rebuilt_entry - rebuilt_stop, 0.0001)
        rebuilt_target = round(rebuilt_entry + 2.0 * risk, 4)
        classification = "REBUILD_RISK_LEVELS"

    new_rr, new_flags = compute_rr(rebuilt_entry, rebuilt_stop, rebuilt_target)
    if staleness_bucket in ("STALE_TARGET", "CHASE_NOT_SWING") and new_rr and new_rr >= 1.3:
        classification = "WATCH_REENTRY_REBUILT_OK"
    elif staleness_bucket in ("STALE_TARGET", "CHASE_NOT_SWING"):
        classification = "CHASE_NOT_SWING_NO_REBUILD"

    return {
        "old_entry": old_entry,
        "old_target": old_target,
        "old_stop": old_stop,
        "rebuilt_entry": rebuilt_entry,
        "rebuilt_target": rebuilt_target,
        "rebuilt_stop": rebuilt_stop,
        "old_rr": old_rr,
        "old_effective_rr": old_eff_rr,
        "new_rr": new_rr,
        "rebuild_flags": new_flags,
        "classification_after_rebuild": classification,
    }


def _load_rr_blocked_cohort(conn, by_sym, idx, start, end, period_filter, bars_cache):
    rows = load_enriched_rows(conn, start, end)
    cohort = []
    for r in rows:
        if period_filter != "ALL" and r["_period"] != period_filter:
            continue
        if not evaluable(r):
            continue
        if RR_GATE not in gates_for_row(r):
            continue
        key = (r["signal_date"], r["symbol"])
        wt = classify_winner_types(r, bars_cache.get(key))
        if not wt["clean_winner"]:
            continue
        cohort.append(r)
    return cohort


def cmd_audit_risk_lineage(params: dict):
    """Phase 2.2 — trace entry/target/stop lineage and simulate rebuild (no production patch)."""
    conn = connect()
    start = params.get("start_date", "2026-06-01")
    end = params.get("end_date", "2026-06-10")
    period_filter = params.get("period", "A_FULL_5D")

    by_sym, idx = load_bars(conn)
    bars_cache = {}
    for r in load_enriched_rows(conn, start, end):
        if evaluable(r):
            key = (r["signal_date"], r["symbol"])
            bars_cache[key] = forward_bars(by_sym, idx, r["symbol"], r["signal_date"], 10)

    cohort = _load_rr_blocked_cohort(conn, by_sym, idx, start, end, period_filter, bars_cache)

    table1 = []
    table3 = []
    bucket_stats = defaultdict(lambda: {
        "count": 0, "clean_winners": 0, "level_ages": [], "close_vs_target": [],
    })
    source_counts = defaultdict(int)
    root_causes = defaultdict(int)

    for r in cohort:
        sym, d = r["symbol"], r["signal_date"]
        bar = bar_on_date(by_sym, idx, sym, d)
        close = bar["close"] if bar else None
        nopen = next_open(by_sym, idx, sym, d)
        levels = resolve_scan_levels(conn, d, sym)
        atr_row = conn.execute(
            "SELECT atr14 FROM indicators_cache WHERE symbol=? AND bar_date<=? ORDER BY bar_date DESC LIMIT 1",
            (sym, d),
        ).fetchone()
        atr14 = safe_float(atr_row["atr14"]) if atr_row and atr_row["atr14"] else None

        entry = r.get("entry_price") or levels["entry_price_resolved"]
        stop = r.get("stop_loss") or levels["stop_loss"]
        target = r.get("t1_target") or levels["t1_target"]
        scan_date = levels["scan_date"]
        level_age = trading_days_between(idx, sym, scan_date, d) if scan_date else None

        key = (d, sym)
        forensic = build_rr_forensic_case(r, by_sym, idx, bars_cache)
        stale_bucket = classify_staleness_bucket(
            close, entry, stop, target, level_age, forensic.get("forensic_bucket"),
        )

        close_vs_entry = round((close - entry) / entry * 100, 2) if entry and close else None
        close_vs_target = round((close - target) / target * 100, 2) if target and close else None

        sk = setup_kind(r.get("setup_type") or levels["setup_type_scan"])
        rebuild = simulate_risk_rebuild(
            close, nopen, entry, stop, target, sk, atr14, stale_bucket,
        )

        source_counts[levels["level_source"]] += 1
        if levels["level_source"] == "scans_lookback_10d":
            root_causes["stale_scan_lookback"] += 1
        if level_age is not None and level_age >= 2:
            root_causes["level_age_gt_2_sessions"] += 1
        if close and target and close >= target:
            root_causes["close_above_target_no_invalidation"] += 1
        if stale_bucket in ("ENTRY_MODEL_MISMATCH",):
            root_causes["effective_entry_model_mismatch"] += 1
        if levels["used_fallback"]:
            root_causes["atr_fallback_used"] += 1

        row1 = {
            "symbol": sym,
            "date": d,
            "entry": entry,
            "target": target,
            "stop": stop,
            "close": close,
            "level_source": levels["level_source"],
            "level_age_days": level_age,
            "scan_date": scan_date,
            "scan_id": levels["scan_id"],
            "setup_id": levels["setup_id"],
            "rule_name": levels["setup_type_scan"] or r.get("setup_type"),
            "setup_type_current": r.get("setup_type"),
            "close_vs_entry_pct": close_vs_entry,
            "close_vs_target_pct": close_vs_target,
            "bucket": stale_bucket,
            "scan_score": levels["scan_score"],
            "close_price_at_scan": levels["close_price_scan"],
        }
        table1.append(row1)
        table3.append({
            "symbol": sym,
            "date": d,
            "bucket": stale_bucket,
            **rebuild,
        })

        st = bucket_stats[stale_bucket]
        st["count"] += 1
        st["clean_winners"] += 1
        if level_age is not None:
            st["level_ages"].append(level_age)
        if close_vs_target is not None:
            st["close_vs_target"].append(close_vs_target)

    conn.close()

    table2 = []
    for bucket, st in sorted(bucket_stats.items(), key=lambda kv: kv[1]["count"], reverse=True):
        ages = st["level_ages"]
        cvt = st["close_vs_target"]
        decision = {
            "STALE_TARGET": "لا تحسب RR_TOO_LOW — MISSED_MOVE / WATCH_REENTRY",
            "CHASE_NOT_SWING": "CHASE_NOT_SWING — لا BUY",
            "ENTRY_ALREADY_GONE": "WATCH_PULLBACK فقط",
            "ENTRY_MODEL_MISMATCH": "صحح effective_entry حسب setup",
            "STALE_SCAN_LEVELS": "أعد بناء المستويات يوميًا أو أبطِل عند age>2",
            "INVALID_STOP": "إصلاح SL builder",
            "INVALID_MARKET_STOP": "إصلاح SL vs close",
            "NEWS/SPIKE_EXCEPTION": "لا يُعالج بالبوابات",
        }.get(bucket, "Review")
        table2.append({
            "bucket": bucket,
            "count": st["count"],
            "clean_winners": st["clean_winners"],
            "avg_level_age": round(mean(ages), 2) if ages else None,
            "avg_close_vs_target_pct": round(mean(cvt), 2) if cvt else None,
            "decision": decision,
        })

    rebuild_classes = defaultdict(int)
    rebuild_rr_ok = 0
    for t in table3:
        rebuild_classes[t["classification_after_rebuild"]] += 1
        if t["new_rr"] is not None and t["new_rr"] >= 1.3:
            rebuild_rr_ok += 1

    summary = {
        "total_cases": len(cohort),
        "level_source_counts": dict(source_counts),
        "root_cause_attribution": dict(sorted(root_causes.items(), key=lambda kv: kv[1], reverse=True)),
        "primary_diagnosis": (
            "مصدر المستويات: scans lookback حتى 10 أيام بدون invalidation عند close>=target"
            if root_causes["close_above_target_no_invalidation"] >= len(cohort) * 0.5
            else "مزيج من مصدر المستويات ونموذج effective_entry"
        ),
        "rebuild_simulation": {
            "classification_counts": dict(rebuild_classes),
            "rebuilt_rr_ge_1_3": rebuild_rr_ok,
            "pct_rebuilt_actionable": round(rebuild_rr_ok / max(len(cohort), 1), 3),
        },
        "verdict": "FIX_RISK_LEVEL_PIPELINE_BEFORE_RR_GATE",
        "fix_order": [
            "1) stale-level invalidation قبل Final Edge",
            "2) لا RR_TOO_LOW عند close>=target",
            "3) refresh أو rebuild levels يوم score_all",
            "4) effective_entry حسب setup (ليس max أعمى)",
            "5) بعدها أعد تقييم RR_TOO_LOW threshold",
        ],
        "no_patch_applied": True,
    }

    spotlight = {
        "ELSH": [x for x in table1 if x["symbol"] == "ELSH"],
        "FIRE": [x for x in table1 if x["symbol"] == "FIRE"],
        "ELSH_rebuild": [x for x in table3 if x["symbol"] == "ELSH"],
    }

    report = {
        "success": True,
        "phase": "2.2",
        "title": "Risk Level Lineage Audit",
        "period": {"start": start, "end": end, "cohort": period_filter},
        "summary": summary,
        "table_1_level_source_audit": table1,
        "table_2_stale_level_diagnosis": table2,
        "table_3_risk_rebuild_simulation": table3,
        "spotlight": spotlight,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{start}_{end}_{period_filter}"
    json_path = REPORT_DIR / f"risk_lineage_audit_{tag}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "Risk Level Lineage Audit — Phase 2.2",
        f"Cohort: {period_filter} | Cases: {len(cohort)}",
        f"VERDICT: {summary['verdict']}",
        f"Primary: {summary['primary_diagnosis']}",
        "",
        "=== Level Source ===",
    ]
    for k, v in sorted(source_counts.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {k:<24} {v:>4}")
    lines += ["", "=== Root Cause Attribution ==="]
    for k, v in summary["root_cause_attribution"].items():
        lines.append(f"  {k:<40} {v:>4}")
    lines += ["", "=== Table 2: Stale Diagnosis ===",
              f"{'Bucket':<24} {'Cnt':>4} {'AvgAge':>7} {'AvgC/T%':>8}  Decision"]
    for t in table2:
        lines.append(
            f"{t['bucket']:<24} {t['count']:>4} "
            f"{t['avg_level_age'] or 0:>7.1f} {t['avg_close_vs_target_pct'] or 0:>8.1f}  {t['decision']}"
        )
    lines += ["", "=== Rebuild Simulation ==="]
    for k, v in sorted(rebuild_classes.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {k:<32} {v:>4}")
    lines.append(f"  Rebuilt RR>=1.3: {rebuild_rr_ok}/{len(cohort)}")
    lines += ["", "=== ELSH Lineage ==="]
    for x in spotlight["ELSH"]:
        lines.append(
            f"{x['date']} src={x['level_source']} scan={x['scan_date']} age={x['level_age_days']} "
            f"entry={x['entry']} tgt={x['target']} close={x['close']} bucket={x['bucket']}"
        )

    txt_path = REPORT_DIR / f"risk_lineage_audit_{tag}.txt"
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_json"] = str(json_path)
    report["report_txt"] = str(txt_path)
    return report


COMMANDS = {
    "fill_outcomes": cmd_fill_outcomes,
    "check_pending_outcomes": cmd_check_pending_outcomes,
    "backfill": cmd_backfill,
    "rescore": cmd_rescore,
    "audit": cmd_audit,
    "audit_phase2": cmd_audit_phase2,
    "audit_post_p0": cmd_audit_post_p0,
    "audit_rr_forensic": cmd_audit_rr_forensic,
    "audit_risk_lineage": cmd_audit_risk_lineage,
    "false_blocks": cmd_false_blocks,
    "run": cmd_run,
}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({"error": f"Unknown command: {cmd}", "available": list(COMMANDS.keys())}))
        sys.exit(1)
    try:
        result = handler(params)
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    except Exception as e:
        import traceback
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()}))
        sys.exit(1)
