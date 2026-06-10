#!/usr/bin/env python3
"""
Phase 85 — Hydra Experiment Runner
Loads EGX experiment config via Hydra and runs a configurable pipeline.

Usage:
    python hydra_runner.py                           # run with default config
    python hydra_runner.py +experiment=regime_ml    # regime-specific training
    python hydra_runner.py model.global.n_estimators=300
    python hydra_runner.py walk_forward.oos_months=6

Config file: configs/egx_experiment.yaml
"""
import sys
import os
import datetime
from pathlib import Path

# ── Hydra / OmegaConf ────────────────────────────────────────────────────────
try:
    import hydra
    from omegaconf import DictConfig, OmegaConf
    HYDRA_OK = True
except ImportError:
    HYDRA_OK = False

# ── MLflow ───────────────────────────────────────────────────────────────────
try:
    import mlflow
    MLFLOW_OK = True
except ImportError:
    MLFLOW_OK = False

# ── Repo root (two levels up from this script) ───────────────────────────────
REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = str(REPO_ROOT / "configs")


# ══════════════════════════════════════════════════════════════════════════════
#  Pipeline steps
# ══════════════════════════════════════════════════════════════════════════════

def run_feature_refresh(cfg) -> dict:
    """Refresh today's features via the feature store."""
    print("[hydra_runner] Running feature store refresh...")
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "python"))
    from feature_store import cmd_refresh
    result = cmd_refresh({
        "version": f"v{datetime.date.today().isoformat()}"
    })
    print(f"  → {result.get('n_symbols', 0)} symbols, "
          f"{result.get('n_features', 0)} features, "
          f"version={result.get('version')}")
    return result


def run_explosion_ml(cfg) -> dict:
    """Train explosion ML model using config parameters."""
    print("[hydra_runner] Running explosion ML training...")
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "python"))

    # Build CLI-style params from config
    model_cfg = cfg.model.get("global", {})
    params = {
        "n_estimators":      model_cfg.get("n_estimators", 200),
        "learning_rate":     model_cfg.get("learning_rate", 0.05),
        "num_leaves":        model_cfg.get("num_leaves", 20),
        "min_child_samples": model_cfg.get("min_child_samples", 15),
        "is_end":            cfg.data.get("is_end", "2025-12-31"),
        "oos_start":         cfg.data.get("oos_start", "2026-01-30"),
        "purge_days":        cfg.data.get("purge_days", 30),
    }

    try:
        from explosion_ml import cmd_train
        result = cmd_train(params)
    except Exception as e:
        result = {"success": False, "error": str(e), "params": params}
        print(f"  → Training skipped: {e}")

    return result


def run_regime_ml(cfg) -> dict:
    """Train regime-specific models using config parameters."""
    print("[hydra_runner] Running regime-specific ML training...")
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "python"))

    regime_cfg = cfg.model.get("regime_specific", {})
    params = {
        "min_samples":   regime_cfg.get("min_samples", 80),
        "n_estimators":  regime_cfg.get("n_estimators", 200),
        "learning_rate": regime_cfg.get("learning_rate", 0.05),
        "n_regimes":     cfg.regime.get("n_regimes", 4),
    }

    try:
        from regime_specific_ml import cmd_train
        result = cmd_train(params)
    except Exception as e:
        result = {"success": False, "error": str(e), "params": params}
        print(f"  → Regime training skipped: {e}")

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  MLflow logging
# ══════════════════════════════════════════════════════════════════════════════

def log_to_mlflow(cfg, results: dict, experiment_name: str = None):
    """Log experiment config and results to MLflow."""
    if not MLFLOW_OK:
        print("[hydra_runner] MLflow not installed — skipping tracking")
        return None

    tracking_uri = str(REPO_ROOT / cfg.mlflow.get("tracking_uri", "mlruns"))
    exp_name     = experiment_name or cfg.mlflow.get("experiment_name", "EGX-Explosion-ML")

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(exp_name)

    with mlflow.start_run(run_name=f"hydra-{datetime.date.today().isoformat()}") as run:
        # Log config as params (flatten one level)
        for section, section_cfg in cfg.items():
            if hasattr(section_cfg, "items"):
                for k, v in section_cfg.items():
                    if not hasattr(v, "items"):   # skip nested dicts
                        mlflow.log_param(f"{section}.{k}", v)

        # Log result metrics
        for step, result in results.items():
            if isinstance(result, dict):
                for k, v in result.items():
                    if isinstance(v, (int, float)):
                        mlflow.log_metric(f"{step}.{k}", v)

        run_id = run.info.run_id
        print(f"[hydra_runner] MLflow run logged: {run_id}")
        return run_id


# ══════════════════════════════════════════════════════════════════════════════
#  Hydra entry point
# ══════════════════════════════════════════════════════════════════════════════

if HYDRA_OK:
    @hydra.main(version_base=None,
                config_path=CONFIG_DIR,
                config_name="egx_experiment")
    def main(cfg: DictConfig) -> None:
        print("=" * 60)
        print("EGX Experiment Runner (Phase 85)")
        print("=" * 60)
        print(OmegaConf.to_yaml(cfg))

        # Determine experiment type from cfg override or default
        experiment = OmegaConf.select(cfg, "experiment", default="explosion_ml")

        results: dict = {}

        # Step 1: Feature refresh (if configured)
        if cfg.feature_store.get("refresh_daily", True):
            results["feature_refresh"] = run_feature_refresh(cfg)

        # Step 2: Train appropriate model
        if experiment == "regime_ml":
            results["regime_ml"] = run_regime_ml(cfg)
        else:
            results["explosion_ml"] = run_explosion_ml(cfg)

        # Step 3: Log to MLflow
        run_id = log_to_mlflow(cfg, results, experiment_name=experiment)
        results["mlflow_run_id"] = run_id

        print("\n[hydra_runner] Pipeline complete.")
        for step, res in results.items():
            if isinstance(res, dict):
                status = "OK" if res.get("success", True) else "FAILED"
                print(f"  {step}: {status}")
            else:
                print(f"  {step}: {res}")

else:
    # Fallback: run without Hydra (load YAML manually via PyYAML or plain dict)
    def main():
        print("[hydra_runner] Hydra not installed. Install with: pip install hydra-core")
        print("Running with default config values...")

        try:
            import yaml
            cfg_path = REPO_ROOT / "configs" / "egx_experiment.yaml"
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
        except ImportError:
            cfg = {}

        results: dict = {}

        # Feature refresh
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "python"))
        from feature_store import cmd_refresh
        results["feature_refresh"] = cmd_refresh({})

        print("\n[hydra_runner] Fallback pipeline complete.")
        for step, res in results.items():
            if isinstance(res, dict):
                print(f"  {step}: success={res.get('success')}")


if __name__ == "__main__":
    main()
