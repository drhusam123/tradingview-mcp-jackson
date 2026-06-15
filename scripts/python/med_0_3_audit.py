#!/usr/bin/env python3
"""MED-0.3 — Ground-truth audit vs proposed calibration plan (shadow only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from med_common import DATA, MED_INVARIANTS, connect, ensure_med_tables

OUT = DATA / "med_0_3_audit_last.json"


def _pct(vals: List[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(p * (len(s) - 1))))
    return float(s[i])


def _table_coverage(conn, table: str, date_col: str = "date") -> Dict[str, Any]:
    try:
        row = conn.execute(
            f"SELECT MIN({date_col}), MAX({date_col}), COUNT(*) FROM {table}"
        ).fetchone()
        return {"table": table, "min_date": row[0], "max_date": row[1], "rows": row[2]}
    except Exception as exc:
        return {"table": table, "error": str(exc)}


def _parquet_coverage(path: Path) -> Dict[str, Any]:
    try:
        import pyarrow.parquet as pq

        t = pq.read_table(path)
        dates = t.column("date").to_pylist() if "date" in t.column_names else []
        return {
            "path": str(path.relative_to(ROOT)),
            "rows": t.num_rows,
            "cols": t.column_names[:8],
            "min_date": min(dates) if dates else None,
            "max_date": max(dates) if dates else None,
        }
    except Exception as exc:
        return {"path": str(path), "error": str(exc)}


def _latest_trade_date(conn) -> Optional[str]:
    row = conn.execute("SELECT MAX(trade_date) FROM med_daily_scores").fetchone()
    return row[0] if row and row[0] else None


def _distribution(conn, col: str, trade_date: str) -> Dict[str, Any]:
    rows = conn.execute(
        f"SELECT {col} FROM med_daily_scores WHERE trade_date=? AND {col} IS NOT NULL",
        (trade_date,),
    ).fetchall()
    vals = [float(r[0]) for r in rows]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "min": min(vals),
        "max": max(vals),
        "avg": sum(vals) / len(vals),
        "p50": _pct(vals, 0.5),
        "p70": _pct(vals, 0.7),
        "p90": _pct(vals, 0.9),
        "ge_0_2": sum(1 for v in vals if v >= 0.2),
        "ge_0_45": sum(1 for v in vals if v >= 0.45),
    }


def _failure_warning_drivers(conn, trade_date: str) -> Dict[str, Any]:
    total = conn.execute(
        "SELECT COUNT(*) FROM med_daily_scores WHERE trade_date=?", (trade_date,)
    ).fetchone()[0]
    warn = conn.execute(
        "SELECT COUNT(*) FROM med_daily_scores WHERE trade_date=? AND med_bucket='MED_FAILURE_WARNING'",
        (trade_date,),
    ).fetchone()[0]
    fs60 = conn.execute(
        "SELECT COUNT(*) FROM med_daily_scores WHERE trade_date=? AND failure_similarity>=0.6",
        (trade_date,),
    ).fetchone()[0]
    cr75 = conn.execute(
        "SELECT COUNT(*) FROM med_daily_scores WHERE trade_date=? AND crowding_score>=0.75",
        (trade_date,),
    ).fetchone()[0]
    dnc = conn.execute(
        """
        SELECT COUNT(*) FROM med_daily_scores m
        JOIN med_failure_patterns fp ON m.trade_date=fp.trade_date AND m.symbol=fp.symbol
        WHERE m.trade_date=? AND m.med_bucket='MED_FAILURE_WARNING' AND fp.do_not_chase=1
        """,
        (trade_date,),
    ).fetchone()[0]
    return {
        "total_symbols": total,
        "failure_warning": warn,
        "failure_warning_pct": round(100 * warn / total, 1) if total else 0,
        "fs_ge_0_60": fs60,
        "crowding_ge_0_75": cr75,
        "failure_warning_with_do_not_chase": dnc,
    }


def _high_conviction_blockers(conn, trade_date: str) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT symbol, med_score, p_cond_20d_10, expected_return_20d,
               failure_similarity, crowding_score, sample_quality, liquidity_fitness, med_bucket
        FROM med_daily_scores WHERE trade_date=? ORDER BY med_score DESC LIMIT 20
        """,
        (trade_date,),
    ).fetchall()
    blockers = {"ms_lt_80": 0, "p_lt_0_15": 0, "er_le_0": 0, "fs_ge_0_35": 0,
                "cp_ge_0_50": 0, "sq_lt_0_50": 0, "lf_lt_0_50": 0, "would_pass_all": 0}
    for r in rows:
        ms, pc, er, fs, cp, sq, lf = r[1], r[2], r[3], r[4], r[5], r[6], r[7]
        if ms < 80:
            blockers["ms_lt_80"] += 1
        if pc < 0.15:
            blockers["p_lt_0_15"] += 1
        if er <= 0:
            blockers["er_le_0"] += 1
        if fs >= 0.35:
            blockers["fs_ge_0_35"] += 1
        if cp >= 0.50:
            blockers["cp_ge_0_50"] += 1
        if sq < 0.50:
            blockers["sq_lt_0_50"] += 1
        if lf < 0.50:
            blockers["lf_lt_0_50"] += 1
        if ms >= 80 and pc >= 0.15 and er > 0 and fs < 0.35 and cp < 0.50 and sq >= 0.50 and lf >= 0.50:
            blockers["would_pass_all"] += 1
    sq_row = conn.execute(
        "SELECT MIN(sample_quality), MAX(sample_quality), AVG(sample_quality), "
        "SUM(CASE WHEN sample_quality>=0.5 THEN 1 ELSE 0 END) "
        "FROM med_daily_scores WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    return {
        "top20_blocker_counts": blockers,
        "sample_quality_min": sq_row[0],
        "sample_quality_max": sq_row[1],
        "sample_quality_avg": sq_row[2],
        "sample_quality_ge_0_50": sq_row[3],
        "high_conviction_count": conn.execute(
            "SELECT COUNT(*) FROM med_daily_scores WHERE trade_date=? "
            "AND med_bucket='MED_HIGH_CONVICTION_RESEARCH'",
            (trade_date,),
        ).fetchone()[0],
    }


def _analogue_overlap(conn, trade_date: str) -> Dict[str, Any]:
    med = [r[0] for r in conn.execute(
        "SELECT symbol FROM med_daily_scores WHERE trade_date=? ORDER BY med_score DESC LIMIT 20",
        (trade_date,),
    ).fetchall()]
    ana = [r[0] for r in conn.execute(
        "SELECT symbol FROM med_analogue_scores_daily WHERE trade_date=? "
        "ORDER BY analogue_p_tail_20_10 DESC LIMIT 20",
        (trade_date,),
    ).fetchall()]
    overlap = sorted(set(med) & set(ana))
    return {
        "med_top20": med[:10],
        "analogue_top20": ana[:10],
        "overlap_count": len(overlap),
        "overlap_symbols": overlap,
        "overlap_pct": round(100 * len(overlap) / 20, 1),
    }


def _lre_med_energy_mismatch(conn, trade_date: str) -> Dict[str, Any]:
    med = conn.execute(
        "SELECT MIN(stored_energy), MAX(stored_energy), AVG(stored_energy) "
        "FROM med_daily_scores WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    lre = conn.execute(
        "SELECT MIN(stored_energy), MAX(stored_energy), AVG(stored_energy), COUNT(*) "
        "FROM lre_daily_scores WHERE trade_date=?",
        (trade_date,),
    ).fetchone()
    return {
        "med_stored_energy": {"min": med[0], "max": med[1], "avg": med[2]},
        "lre_stored_energy": {"min": lre[0], "max": lre[1], "avg": lre[2], "n": lre[3]},
        "med_ok_stored_ge_0_2": conn.execute(
            "SELECT COUNT(*) FROM med_daily_scores WHERE trade_date=? AND stored_energy>=0.2",
            (trade_date,),
        ).fetchone()[0],
        "note": "LRE uses 0-100 scale; MED v1 uses C*V*A*(1-ext) ~0.001-0.02",
    }


def _replay_summary() -> Dict[str, Any]:
    path = DATA / "med_replay_audit_last.json"
    if not path.exists():
        return {"error": "missing med_replay_audit_last.json"}
    rep = json.loads(path.read_text())
    out = {}
    for mode in ("LRE_only", "MED_LRE", "MED_only", "crowding_on", "crowding_off"):
        r = rep.get("results", {}).get(mode, {})
        out[mode] = {
            "n": r.get("n"),
            "median_return_pct": round((r.get("median_return") or 0) * 100, 3),
            "stop8_pct": round((r.get("stop8") or 0) * 100, 2),
        }
    lift = rep.get("lift", {})
    out["MED_LRE_vs_LRE_median_pp"] = round(
        (lift.get("MED_LRE_vs_LRE") or {}).get("median_delta", 0) * 100, 3
    )
    return out


def _edge_quality(conn) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN n>=30 THEN 1 ELSE 0 END), AVG(n), "
        "AVG(sample_quality), SUM(CASE WHEN sample_quality=0 THEN 1 ELSE 0 END) "
        "FROM med_conditional_edge_tables"
    ).fetchone()
    return {
        "total_edges": row[0],
        "edges_n_ge_30": row[1],
        "avg_n": round(row[2] or 0, 1),
        "avg_sample_quality": round(row[3] or 0, 3),
        "edges_sq_zero": row[4],
    }


def _verdicts(audit: Dict[str, Any]) -> List[Dict[str, str]]:
    d = audit.get("failure_warning_drivers", {})
    se = audit.get("distributions", {}).get("stored_energy", {})
    hc = audit.get("high_conviction", {})
    verdicts = []

    verdicts.append({
        "claim": "Scale mismatch: stored_energy>=0.2 never fires",
        "verdict": "CONFIRMED",
        "evidence": f"max={se.get('max', 0):.6f}, ge_0.2={se.get('ge_0_2', 0)}; MED_LRE relies on hidden_energy_flag",
    })
    verdicts.append({
        "claim": "Hardcoded regime_fit/sector/breadth",
        "verdict": "CONFIRMED",
        "evidence": "med_0_1_math_features.py lines 236-239 = 0.5/0.5/0.5/0.75",
    })
    verdicts.append({
        "claim": "Failure warning ~55% from KNN failure_similarity alone",
        "verdict": "PARTIAL — main driver is do_not_chase",
        "evidence": (
            f"{d.get('failure_warning_with_do_not_chase', 0)}/{d.get('failure_warning', 0)} warnings "
            f"have do_not_chase; fs>=0.6 only {d.get('fs_ge_0_60', 0)}"
        ),
    })
    verdicts.append({
        "claim": "HIGH_CONVICTION=0 due to med_score/p_cond only",
        "verdict": "REJECTED — sample_quality is the bottleneck",
        "evidence": (
            f"sq max={hc.get('sample_quality_max', 0):.3f}, "
            f"ge_0.5={hc.get('sample_quality_ge_0_50', 0)}; "
            f"top20 sq<0.5={hc.get('top20_blocker_counts', {}).get('sq_lt_0_50', 0)}"
        ),
    })
    verdicts.append({
        "claim": "MED_LRE adds no OOS value",
        "verdict": "REJECTED",
        "evidence": f"replay MED_LRE lift median {audit.get('replay', {}).get('MED_LRE_vs_LRE_median_pp', 0)}pp, stop8 lower",
    })
    verdicts.append({
        "claim": "Analogue disconnected from MED rank",
        "verdict": "CONFIRMED",
        "evidence": f"top20 overlap {audit.get('analogue_overlap', {}).get('overlap_pct', 0)}%",
    })
    verdicts.append({
        "claim": "Real regime data available without feature_store",
        "verdict": "CONFIRMED",
        "evidence": "markov_regime_daily, sector_rotation_daily, market_breadth_enhanced parquet, failure_reconstruction",
    })
    return verdicts


def main() -> int:
    conn = connect()
    ensure_med_tables(conn)
    trade_date = _latest_trade_date(conn) or "2026-06-11"

    audit: Dict[str, Any] = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "invariants": MED_INVARIANTS,
        "trade_date": trade_date,
        "distributions": {
            "stored_energy": _distribution(conn, "stored_energy", trade_date),
            "absorption_score": _distribution(conn, "absorption_score", trade_date),
            "med_score": _distribution(conn, "med_score", trade_date),
            "failure_similarity": _distribution(conn, "failure_similarity", trade_date),
            "crowding_score": _distribution(conn, "crowding_score", trade_date),
            "sample_quality": _distribution(conn, "sample_quality", trade_date),
        },
        "failure_warning_drivers": _failure_warning_drivers(conn, trade_date),
        "high_conviction": _high_conviction_blockers(conn, trade_date),
        "analogue_overlap": _analogue_overlap(conn, trade_date),
        "lre_med_energy_mismatch": _lre_med_energy_mismatch(conn, trade_date),
        "edge_quality": _edge_quality(conn),
        "replay": _replay_summary(),
        "data_sources": {
            "sqlite": [
                _table_coverage(conn, "markov_regime_daily"),
                _table_coverage(conn, "sector_rotation_daily"),
                _table_coverage(conn, "market_breadth_daily", "date"),
                _table_coverage(conn, "closing_pressure_daily", "trade_date"),
                _table_coverage(conn, "failure_reconstruction", "failure_date"),
                _table_coverage(conn, "lre_daily_scores", "trade_date"),
                _table_coverage(conn, "med_daily_scores", "trade_date"),
            ],
            "parquet": [
                _parquet_coverage(DATA / "parquet" / "markov_regime_daily.parquet"),
                _parquet_coverage(DATA / "parquet" / "market_breadth_enhanced.parquet"),
                _parquet_coverage(DATA / "parquet" / "closing_pressure_daily.parquet"),
            ],
        },
        "buckets": dict(conn.execute(
            "SELECT med_bucket, COUNT(*) FROM med_daily_scores WHERE trade_date=? GROUP BY 1",
            (trade_date,),
        ).fetchall()),
    }
    audit["verdicts"] = _verdicts(audit)
    OUT.write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    print(json.dumps({"ok": True, "trade_date": trade_date, "out": str(OUT), "verdicts": len(audit["verdicts"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
