#!/usr/bin/env python3
"""
ML Purged Walk-Forward Governance
=================================
Audits the latest model score rows and records whether the model should be
trusted as an active client-signal layer. This is a governance gate, not a
trainer; retraining remains in egx_ml_trainer.py.
"""
import datetime as dt
import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "egx_trading.db"


def f(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def ensure(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_governance_audit (
            run_date TEXT PRIMARY KEY,
            model_name TEXT,
            trained_at TEXT,
            auc_train REAL,
            auc_oos REAL,
            precision_at_50 REAL,
            precision_at_70 REAL,
            n_oos_total INTEGER,
            accepted_for_client INTEGER,
            risk_level TEXT,
            reasons_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def main():
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    ensure(conn)
    latest_phase2 = conn.execute("""
        SELECT id, run_date, created_at, results
        FROM ml_trainer_runs
        WHERE phase IN ('2', 'phase2')
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    row = conn.execute("""
        SELECT model_name, trained_at, auc_train, auc_oos, precision_at_50,
               precision_at_70, n_oos_total, n_oos_positive, notes
        FROM ml_model_scores
        ORDER BY trained_at DESC, id DESC
        LIMIT 1
    """).fetchone()
    today = dt.date.today().isoformat()
    if not row:
        result = {
            "success": False,
            "accepted_for_client": False,
            "risk_level": "NO_MODEL",
            "reasons": ["no ml_model_scores rows"],
        }
        print(json.dumps(result))
        return 0

    reasons = []
    auc_train = f(row["auc_train"])
    auc_oos = f(row["auc_oos"])
    p50 = f(row["precision_at_50"])
    p70 = f(row["precision_at_70"])
    n_oos = int(row["n_oos_total"] or 0)
    model_name = row["model_name"]
    trained_at = row["trained_at"]
    score_source = "ml_model_scores"

    if latest_phase2:
        try:
            p2 = json.loads(latest_phase2["results"] or "{}")
            p2_acceptance = p2.get("acceptance") or {}
            p2_models = p2.get("models") or {}
            p2_best_auc = f(p2_acceptance.get("best_auc_oos"))
            p2_ensemble_auc = f((p2_models.get("ensemble") or {}).get("auc_oos"))
            p2_auc = max(p2_best_auc, p2_ensemble_auc)
            p2_created = latest_phase2["created_at"] or latest_phase2["run_date"]
            if p2_auc > 0 and str(p2_created) > str(trained_at or ""):
                # Phase2 is the freshest training result. Precision still comes
                # from the latest purged score until a scorer writes a new row.
                model_name = "explosion_lgbm_v3"
                trained_at = p2_created
                auc_oos = p2_auc
                auc_train = max(auc_train, p2_auc)
                score_source = "ml_trainer_runs.phase2"
                if not p2_acceptance.get("accepted_for_prediction"):
                    reasons.append("phase2_not_accepted_for_prediction")
                if p50 <= 0:
                    reasons.append("precision_score_missing_after_retrain")
        except Exception as e:
            reasons.append(f"phase2_parse_error:{e}")

    if n_oos < 500:
        reasons.append(f"small_oos_sample:{n_oos}")
    if auc_train - auc_oos > 0.18:
        reasons.append(f"overfit_gap:{auc_train - auc_oos:.3f}")
    if auc_oos < 0.62:
        reasons.append(f"low_auc_oos:{auc_oos:.3f}")
    if p50 < 0.50:
        reasons.append(f"low_precision_at_50:{p50:.3f}")

    accepted = not reasons or (auc_oos >= 0.70 and p50 >= 0.55 and n_oos >= 500)
    risk = "LOW" if accepted else ("MEDIUM" if auc_oos >= 0.65 and p50 >= 0.50 else "HIGH")

    conn.execute("""
        INSERT OR REPLACE INTO ml_governance_audit
        (run_date, model_name, trained_at, auc_train, auc_oos, precision_at_50,
         precision_at_70, n_oos_total, accepted_for_client, risk_level, reasons_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        today, model_name, trained_at, auc_train, auc_oos, p50, p70,
        n_oos, 1 if accepted else 0, risk, json.dumps(reasons, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()

    print(json.dumps({
        "success": True,
        "model_name": model_name,
        "score_source": score_source,
        "auc_oos": round(auc_oos, 4),
        "precision_at_50": round(p50, 4),
        "precision_at_70": round(p70, 4),
        "n_oos_total": n_oos,
        "accepted_for_client": accepted,
        "risk_level": risk,
        "reasons": reasons,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
