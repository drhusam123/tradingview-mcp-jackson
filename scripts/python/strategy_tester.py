#!/usr/bin/env python3
"""
Phase 59: EGX Strategy Tester
Generates Pine Script v5 strategy code from discovered laws, parses backtest results
from TradingView Strategy Tester, and updates law confidence scores.
"""

import os, sys, json, math, sqlite3, collections
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

# ── Schema bootstrap ──────────────────────────────────────────────────────────

def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS strategy_backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            law_id TEXT NOT NULL,
            law_name TEXT,
            symbol TEXT,
            timeframe TEXT DEFAULT 'D',
            period_start TEXT,
            period_end TEXT,
            n_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            sharpe_ratio REAL,
            max_drawdown_pct REAL,
            net_pnl_pct REAL,
            avg_trade_pct REAL,
            expectancy REAL,
            pine_code TEXT,
            raw_results TEXT,
            tested_at TEXT
        )
    """)
    conn.commit()

# ── Pine Script generation ────────────────────────────────────────────────────

def _pine_for_law(law_id, law_name, direction, extra_meta=""):
    """Build a robust Pine Script v5 strategy for a given direction."""
    direction_upper = str(direction).upper() if direction else "NEUTRAL"

    if "BULL" in direction_upper or direction_upper in ("LONG", "UP", "BUY"):
        entry_label = "Long Entry"
        entry_cond  = "ta.crossover(rsi, 40) and close > sma20"
        exit_label  = "Long Exit"
        trade_dir   = "strategy.long"
        sl_price    = "strategy.position_avg_price * 0.95"
        tp_price    = "strategy.position_avg_price * 1.10"
    else:
        entry_label = "Short Entry"
        entry_cond  = "ta.crossunder(rsi, 60) and close < sma20"
        exit_label  = "Short Exit"
        trade_dir   = "strategy.short"
        sl_price    = "strategy.position_avg_price * 1.05"
        tp_price    = "strategy.position_avg_price * 0.90"

    safe_name = str(law_name or law_id).replace('"', "'")[:80]

    pine = f"""//@version=5
// ─────────────────────────────────────────────────────
// EGX Autonomous Quant System — Phase 59 Strategy Tester
// Law ID   : {law_id}
// Law Name : {safe_name}
// Direction: {direction_upper}
// Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
{('// ' + extra_meta) if extra_meta else '//'}
// ─────────────────────────────────────────────────────
strategy(
    title           = "EGX Law [{law_id}] {safe_name}",
    overlay         = true,
    default_qty_type  = strategy.percent_of_equity,
    default_qty_value = 10,
    initial_capital   = 100000,
    commission_type   = strategy.commission.percent,
    commission_value  = 0.15
)

// ── Inputs ────────────────────────────────────────────
rsiLen  = input.int(14,  "RSI Length",   minval=2)
smaLen  = input.int(20,  "SMA Length",   minval=5)
slPct   = input.float(5.0,  "Stop Loss %",    minval=0.1, maxval=50)
tpPct   = input.float(10.0, "Take Profit %",  minval=0.1, maxval=200)

// ── Indicators ────────────────────────────────────────
rsi   = ta.rsi(close, rsiLen)
sma20 = ta.sma(close, smaLen)

// ── Entry / Exit conditions ───────────────────────────
entryCondition = {entry_cond}
inTrade        = strategy.position_size != 0

// ── Strategy execution ────────────────────────────────
if entryCondition and not inTrade
    strategy.entry("{entry_label}", {trade_dir})

if inTrade
    strategy.exit(
        id       = "Exit",
        from_entry = "{entry_label}",
        stop     = {sl_price},
        limit    = {tp_price}
    )

// ── Plots ─────────────────────────────────────────────
plot(sma20, "SMA20", color=color.new(color.blue, 0), linewidth=1)
bgcolor(entryCondition ? color.new(color.green, 90) : na, title="Entry Signal")
"""
    return pine

# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_generate_law_strategy(params):
    law_id   = params.get("law_id", "")
    law_type = params.get("law_type", "universal")

    if not law_id:
        return {"error": "law_id is required"}

    conn = get_db()
    ensure_tables(conn)

    if law_type == "structural":
        row = conn.execute(
            "SELECT id, law_number, title, statement, confidence_level, directions FROM structural_laws WHERE id=? OR law_number=?",
            [law_id, law_id]
        ).fetchone()
        if not row:
            return {"error": f"Structural law '{law_id}' not found"}
        direction = str(row["directions"] or "NEUTRAL")
        law_name  = row["title"] or f"Law {row['law_number']}"
        extra_meta = f"Confidence: {row['confidence_level']} | Statement: {str(row['statement'] or '')[:100]}"
    else:
        row = conn.execute(
            "SELECT pattern_id, pattern_name, direction, precision, recall, f1_score, n_activations, beats_random FROM universal_laws_p16 WHERE pattern_id=?",
            [law_id]
        ).fetchone()
        if not row:
            return {"error": f"Universal law '{law_id}' not found"}
        direction = str(row["direction"] or "NEUTRAL")
        law_name  = row["pattern_name"] or law_id
        extra_meta = (
            f"Precision: {row['precision']:.3f} | Recall: {row['recall']:.3f} | "
            f"F1: {row['f1_score']:.3f} | Activations: {row['n_activations']}"
        )

    conn.close()

    pine_code = _pine_for_law(law_id, law_name, direction, extra_meta)

    return {
        "law_id":     law_id,
        "law_name":   law_name,
        "direction":  direction,
        "pine_code":  pine_code,
        "description": f"Pine Script v5 strategy for {law_type} law '{law_name}' ({direction}). "
                       f"Uses RSI + SMA20 proxy signal. SL=5%, TP=10% (2:1 RR)."
    }


def cmd_list_laws_for_testing(params):
    law_type      = params.get("law_type", "universal")
    min_precision = float(params.get("min_precision", 0.6))
    limit         = int(params.get("limit", 20))

    conn = get_db()
    ensure_tables(conn)

    # Fetch already-tested IDs
    tested_ids = set(
        r[0] for r in conn.execute("SELECT DISTINCT law_id FROM strategy_backtest_results").fetchall()
    )

    if law_type == "structural":
        rows = conn.execute("""
            SELECT id AS law_id, title AS law_name, directions AS direction,
                   support_pct AS precision, confidence_level AS status
            FROM structural_laws
            WHERE confidence_level IN ('MEDIUM', 'HIGH', 'VERY_HIGH')
            ORDER BY
                CASE confidence_level
                    WHEN 'VERY_HIGH' THEN 0
                    WHEN 'HIGH'      THEN 1
                    WHEN 'MEDIUM'    THEN 2
                    ELSE 3
                END,
                support_pct DESC
            LIMIT ?
        """, [limit]).fetchall()
    else:
        rows = conn.execute("""
            SELECT pattern_id AS law_id, pattern_name AS law_name, direction,
                   precision, law_status AS status
            FROM universal_laws_p16
            WHERE beats_random = 1 AND precision >= ?
            ORDER BY f1_score DESC, precision DESC
            LIMIT ?
        """, [min_precision, limit]).fetchall()

    conn.close()

    laws = []
    for r in rows:
        lid = str(r["law_id"])
        laws.append({
            "law_id":        lid,
            "law_name":      r["law_name"],
            "direction":     r["direction"],
            "precision":     r["precision"],
            "status":        r["status"],
            "already_tested": lid in tested_ids
        })

    return {"laws": laws, "total": len(laws), "already_tested_count": sum(1 for l in laws if l["already_tested"])}


def cmd_parse_backtest_results(params):
    law_id      = params.get("law_id", "")
    symbol      = params.get("symbol", "")
    raw_results = params.get("raw_results", {})
    pine_code   = params.get("pine_code", "")
    timeframe   = params.get("timeframe", "D")

    if not law_id:
        return {"error": "law_id is required"}

    # Extract fields from TV strategy results payload
    r = raw_results if isinstance(raw_results, dict) else {}

    def _f(key, default=0.0):
        v = r.get(key, default)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _i(key, default=0):
        v = r.get(key, default)
        try:
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    n_trades       = _i("total_closed_trades")
    win_rate       = _f("percent_profitable") / 100.0 if _f("percent_profitable") > 1 else _f("percent_profitable")
    profit_factor  = _f("profit_factor")
    sharpe_ratio   = _f("sharpe_ratio")
    max_drawdown   = _f("max_drawdown_percent")
    net_pnl_pct    = _f("net_profit_percent")
    avg_trade_pct  = _f("avg_trade_percent")
    period_start   = str(r.get("period_start", ""))
    period_end     = str(r.get("period_end", ""))

    # Expectancy = win_rate * avg_win - (1-win_rate) * avg_loss
    # Approximate from gross profit / loss
    gross_profit = _f("gross_profit")
    gross_loss   = abs(_f("gross_loss"))
    if n_trades > 0 and win_rate > 0:
        n_wins   = max(1, round(n_trades * win_rate))
        n_losses = max(0, n_trades - n_wins)
        avg_win  = gross_profit / n_wins   if n_wins   > 0 else 0.0
        avg_loss = gross_loss   / n_losses if n_losses > 0 else 0.0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    else:
        expectancy = 0.0

    # Fetch law_name
    conn = get_db()
    ensure_tables(conn)

    law_name_row = conn.execute(
        "SELECT pattern_name FROM universal_laws_p16 WHERE pattern_id=?", [law_id]
    ).fetchone()
    if not law_name_row:
        law_name_row = conn.execute(
            "SELECT title AS pattern_name FROM structural_laws WHERE id=? OR law_number=?", [law_id, law_id]
        ).fetchone()
    law_name = law_name_row["pattern_name"] if law_name_row else law_id

    conn.execute("""
        INSERT INTO strategy_backtest_results
            (law_id, law_name, symbol, timeframe, period_start, period_end,
             n_trades, win_rate, profit_factor, sharpe_ratio, max_drawdown_pct,
             net_pnl_pct, avg_trade_pct, expectancy, pine_code, raw_results, tested_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [
        law_id, law_name, symbol, timeframe, period_start, period_end,
        n_trades, win_rate, profit_factor, sharpe_ratio, max_drawdown,
        net_pnl_pct, avg_trade_pct, expectancy,
        pine_code, json.dumps(raw_results),
        datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    ])
    conn.commit()
    conn.close()

    metrics = {
        "law_id":          law_id,
        "law_name":        law_name,
        "symbol":          symbol,
        "n_trades":        n_trades,
        "win_rate":        round(win_rate, 4),
        "profit_factor":   round(profit_factor, 4),
        "sharpe_ratio":    round(sharpe_ratio, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "net_pnl_pct":     round(net_pnl_pct, 4),
        "avg_trade_pct":   round(avg_trade_pct, 4),
        "expectancy":      round(expectancy, 4),
        "saved":           True
    }
    return metrics


def cmd_validate_law(params):
    law_id = params.get("law_id", "")
    if not law_id:
        return {"error": "law_id is required"}

    conn = get_db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT win_rate, profit_factor, sharpe_ratio, max_drawdown_pct, symbol
        FROM strategy_backtest_results
        WHERE law_id = ? AND n_trades > 0
    """, [law_id]).fetchall()
    conn.close()

    if not rows:
        return {"law_id": law_id, "n_tests": 0, "validation_grade": "N/A",
                "recommendation": "No backtest results available."}

    def _avg(vals):
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else 0.0

    avg_win_rate      = _avg([r["win_rate"]          for r in rows])
    avg_profit_factor = _avg([r["profit_factor"]      for r in rows])
    avg_sharpe        = _avg([r["sharpe_ratio"]        for r in rows])
    avg_drawdown      = _avg([r["max_drawdown_pct"]    for r in rows])
    symbols_tested    = list({r["symbol"] for r in rows if r["symbol"]})

    # Grading
    if avg_win_rate > 0.55 and avg_profit_factor > 1.5 and avg_sharpe > 0.8:
        grade = "A"
        rec   = "Strong law — integrate into live signals."
    elif avg_win_rate > 0.5 and avg_profit_factor > 1.2:
        grade = "B"
        rec   = "Solid law — use with confirmation filters."
    elif avg_win_rate > 0.45 and avg_profit_factor > 1.0:
        grade = "C"
        rec   = "Marginal edge — use in high-conviction setups only."
    elif avg_profit_factor > 0.8:
        grade = "D"
        rec   = "Weak law — further testing needed before use."
    else:
        grade = "F"
        rec   = "Law lacks edge — do not use in production."

    return {
        "law_id":          law_id,
        "n_tests":         len(rows),
        "symbols_tested":  symbols_tested,
        "avg_win_rate":    round(avg_win_rate, 4),
        "avg_profit_factor": round(avg_profit_factor, 4),
        "avg_sharpe":      round(avg_sharpe, 4),
        "avg_drawdown":    round(avg_drawdown, 4),
        "validation_grade": grade,
        "recommendation":  rec
    }


def cmd_rank_laws(params):
    min_tests = int(params.get("min_tests", 1))

    conn = get_db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT law_id, law_name,
               AVG(win_rate)       AS avg_win_rate,
               AVG(profit_factor)  AS avg_pf,
               AVG(sharpe_ratio)   AS avg_sharpe,
               AVG(max_drawdown_pct) AS avg_dd,
               COUNT(*)            AS n_tests
        FROM strategy_backtest_results
        WHERE n_trades > 0
        GROUP BY law_id
        HAVING n_tests >= ?
        ORDER BY avg_pf DESC
    """, [min_tests]).fetchall()
    conn.close()

    def _safe(v):
        return float(v) if v is not None else 0.0

    ranked = []
    for r in rows:
        wr   = _safe(r["avg_win_rate"])
        pf   = _safe(r["avg_pf"])
        sh   = _safe(r["avg_sharpe"])
        dd   = _safe(r["avg_dd"])

        # Normalize sharpe to 0-1 range (cap at 3)
        sh_norm = min(sh / 3.0, 1.0) if sh > 0 else 0.0
        # Drawdown component: penalise high drawdown (cap at 50)
        dd_comp = 1.0 - min(dd / 50.0, 1.0)

        score = (
            0.4 * wr
            + 0.3 * (pf / 3.0)
            + 0.2 * sh_norm
            + 0.1 * dd_comp
        )

        ranked.append({
            "law_id":     r["law_id"],
            "law_name":   r["law_name"],
            "n_tests":    r["n_tests"],
            "avg_win_rate":     round(wr, 4),
            "avg_profit_factor": round(pf, 4),
            "avg_sharpe":       round(sh, 4),
            "avg_drawdown":     round(dd, 4),
            "composite_score":  round(score, 4)
        })

    ranked.sort(key=lambda x: x["composite_score"], reverse=True)
    for i, item in enumerate(ranked[:20], 1):
        item["rank"] = i

    return {"ranked_laws": ranked[:20], "total_laws_with_results": len(ranked)}


def cmd_build_full(params):
    law_type = params.get("law_type", "universal")
    symbol   = params.get("symbol", "COMI")
    n_laws   = int(params.get("n_laws", 5))

    list_result = cmd_list_laws_for_testing({"law_type": law_type, "min_precision": 0.6, "limit": n_laws})

    laws = list_result.get("laws", [])
    already_tested_count = list_result.get("already_tested_count", 0)

    # Generate strategy for the top untested law, falling back to top overall
    top_strategy = None
    for law in laws:
        if not law["already_tested"]:
            top_strategy = cmd_generate_law_strategy({"law_id": law["law_id"], "law_type": law_type})
            break
    if top_strategy is None and laws:
        top_strategy = cmd_generate_law_strategy({"law_id": laws[0]["law_id"], "law_type": law_type})

    return {
        "top_law_strategy":          top_strategy,
        "laws_ready_for_testing":    laws,
        "already_tested_count":      already_tested_count,
        "symbol":                    symbol,
        "law_type":                  law_type
    }

# ── Dispatch ──────────────────────────────────────────────────────────────────

COMMANDS = {
    "generate_law_strategy":  cmd_generate_law_strategy,
    "list_laws_for_testing":  cmd_list_laws_for_testing,
    "parse_backtest_results": cmd_parse_backtest_results,
    "validate_law":           cmd_validate_law,
    "rank_laws":              cmd_rank_laws,
    "build_full":             cmd_build_full,
}

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: strategy_tester.py <command> [params_json]"}))
        sys.exit(1)

    command = sys.argv[1]
    params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    if command not in COMMANDS:
        print(json.dumps({
            "error": f"Unknown command '{command}'",
            "available_commands": sorted(COMMANDS.keys())
        }))
        sys.exit(1)

    try:
        result = COMMANDS[command](params)
        print(json.dumps(result, indent=2, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()}))
        sys.exit(1)

if __name__ == "__main__":
    main()
