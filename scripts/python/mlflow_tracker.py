"""
Phase 81 — MLflow Experiment Tracking
Track all ML experiments, compare runs, maintain model registry.
"""

import sys
import json
import sqlite3
import pickle
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH    = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
MLRUNS_PATH = Path(__file__).parent.parent.parent / 'mlruns'
MODELS_DIR = Path(__file__).parent / 'models'
REGIME_DIR = MODELS_DIR / 'regime_models'

# ── MLflow ────────────────────────────────────────────────────────────────────
try:
    import mlflow
    import mlflow.lightgbm
    import mlflow.sklearn
    mlflow.set_tracking_uri(str(MLRUNS_PATH))
    MLFLOW_OK = True
except ImportError as _e:
    MLFLOW_OK = False
    _MLFLOW_ERR = str(_e)

# ── LightGBM ──────────────────────────────────────────────────────────────────
try:
    import lightgbm as lgb
    LGB_OK = True
except ImportError:
    LGB_OK = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('mlflow_tracker')

# ── Constants ─────────────────────────────────────────────────────────────────
FEATURE_COLS = [
    'pre1_bb_width', 'pre3_bb_width', 'pre5_bb_width',
    'pre1_vol_ratio', 'pre3_vol_ratio', 'pre5_vol_ratio',
    'pre1_rsi', 'pre3_rsi', 'pre5_rsi',
    'pre3_momentum_5d', 'pre5_momentum_5d',
    'pre5_bb_position', 'pre5_compression_days',
]

EXP_GLOBAL  = 'EGX-Explosion-ML'
EXP_REGIME  = 'EGX-Regime-Models'

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _require_mlflow():
    if not MLFLOW_OK:
        raise RuntimeError(f'MLflow not available: {_MLFLOW_ERR}')


def _get_or_create_experiment(name: str) -> str:
    """Return experiment ID, creating it if absent."""
    exp = mlflow.get_experiment_by_name(name)
    if exp is None:
        exp_id = mlflow.create_experiment(name)
    else:
        exp_id = exp.experiment_id
    return exp_id


def _load_features() -> pd.DataFrame:
    """Load explosive_moves with all 13 feature columns + label."""
    conn = _get_conn()
    cols = ', '.join(FEATURE_COLS)
    query = f"""
        SELECT explosion_date, {cols}, return_3d
        FROM explosive_moves
        WHERE {' AND '.join(f'{c} IS NOT NULL' for c in FEATURE_COLS)}
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['label'] = (df['return_3d'] >= 0.05).astype(int)
    return df


def _precision(y_true, y_pred_proba, threshold=0.5):
    """Compute precision at threshold."""
    preds = (y_pred_proba >= threshold).astype(int)
    signals = preds.sum()
    if signals == 0:
        return 0.0, 0
    tp = ((preds == 1) & (y_true == 1)).sum()
    return float(tp / signals), int(signals)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_init(params: dict) -> dict:
    """Initialize MLflow experiments."""
    try:
        _require_mlflow()
        MLRUNS_PATH.mkdir(parents=True, exist_ok=True)

        global_id = _get_or_create_experiment(EXP_GLOBAL)
        regime_id = _get_or_create_experiment(EXP_REGIME)

        global_exp = mlflow.get_experiment(global_id)
        regime_exp = mlflow.get_experiment(regime_id)

        return {
            'success': True,
            'experiments': {
                EXP_GLOBAL: {
                    'id': global_id,
                    'artifact_location': global_exp.artifact_location,
                },
                EXP_REGIME: {
                    'id': regime_id,
                    'artifact_location': regime_exp.artifact_location,
                },
            },
            'mlruns_path': str(MLRUNS_PATH),
        }
    except Exception as e:
        log.error('cmd_init failed: %s', e)
        return {'success': False, 'error': str(e)}


def cmd_log_run(params: dict) -> dict:
    """Re-train global LightGBM model and log as an MLflow run."""
    try:
        _require_mlflow()
        if not LGB_OK:
            raise RuntimeError('LightGBM not installed')

        is_end   = params.get('is_end',   '2025-12-31')
        oos_start = params.get('oos_start', '2026-01-30')

        df = _load_features()
        if df.empty:
            return {'success': False, 'error': 'No feature data in explosive_moves'}

        is_df  = df[df['explosion_date'] <= is_end]
        oos_df = df[df['explosion_date'] >= oos_start]

        if len(is_df) < 10:
            return {'success': False, 'error': f'Insufficient IS data: {len(is_df)} rows'}

        X_is, y_is = is_df[FEATURE_COLS].values, is_df['label'].values
        n_estimators = int(params.get('n_estimators', 200))
        lr           = float(params.get('lr', 0.05))
        num_leaves   = int(params.get('num_leaves', 20))

        model = lgb.LGBMClassifier(
            n_estimators=n_estimators,
            learning_rate=lr,
            num_leaves=num_leaves,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_is, y_is)

        is_proba = model.predict_proba(X_is)[:, 1]
        is_prec, n_is_signals = _precision(y_is, is_proba)

        n_is  = len(is_df)
        n_oos = len(oos_df)

        oos_prec    = 0.0
        n_oos_signals = 0
        if n_oos >= 5:
            X_oos, y_oos = oos_df[FEATURE_COLS].values, oos_df['label'].values
            oos_proba = model.predict_proba(X_oos)[:, 1]
            oos_prec, n_oos_signals = _precision(y_oos, oos_proba)

        exp_id   = _get_or_create_experiment(EXP_GLOBAL)
        run_name = f"global_{datetime.now().strftime('%Y%m%d_%H%M')}"

        with mlflow.start_run(experiment_id=exp_id, run_name=run_name) as run:
            mlflow.log_params({
                'n_estimators': n_estimators,
                'lr': lr,
                'num_leaves': num_leaves,
                'is_end': is_end,
                'oos_start': oos_start,
                'n_features': len(FEATURE_COLS),
            })
            mlflow.log_metric('is_precision',   is_prec)
            mlflow.log_metric('oos_precision',  oos_prec)
            mlflow.log_metric('is_samples',     n_is)
            mlflow.log_metric('oos_samples',    n_oos)
            mlflow.log_metric('oos_signals',    n_oos_signals)

            try:
                mlflow.lightgbm.log_model(model, 'model')
            except Exception:
                mlflow.sklearn.log_model(model, 'model')

            run_id = run.info.run_id

        return {
            'success': True,
            'run_id': run_id,
            'run_name': run_name,
            'experiment': EXP_GLOBAL,
            'metrics': {
                'is_precision':  round(is_prec, 4),
                'oos_precision': round(oos_prec, 4),
                'is_samples':    n_is,
                'oos_samples':   n_oos,
                'oos_signals':   n_oos_signals,
            },
        }
    except Exception as e:
        log.error('cmd_log_run failed: %s', e)
        return {'success': False, 'error': str(e)}


def cmd_log_regime_run(params: dict) -> dict:
    """Log regime-specific model runs."""
    try:
        _require_mlflow()

        if not REGIME_DIR.exists():
            return {'success': False, 'error': f'Regime models dir not found: {REGIME_DIR}'}

        pkl_files = list(REGIME_DIR.glob('*.pkl'))
        if not pkl_files:
            return {'success': False, 'error': 'No .pkl files in regime_models/'}

        exp_id = _get_or_create_experiment(EXP_REGIME)
        results = {}
        date_str = datetime.now().strftime('%Y%m%d_%H%M')

        for pkl_path in pkl_files:
            regime = pkl_path.stem.replace('explosion_model_', '')
            try:
                with open(pkl_path, 'rb') as f:
                    model = pickle.load(f)

                run_name = f"regime_{regime}_{date_str}"
                with mlflow.start_run(experiment_id=exp_id, run_name=run_name) as run:
                    mlflow.log_param('regime', regime)
                    mlflow.log_param('model_file', pkl_path.name)

                    # Feature importances
                    feat_imp = {}
                    if hasattr(model, 'feature_importances_'):
                        imp = model.feature_importances_
                        feat_names = getattr(model, 'feature_name_', FEATURE_COLS)
                        if len(feat_names) == len(imp):
                            pairs = sorted(zip(feat_names, imp), key=lambda x: -x[1])
                            for i, (fname, fval) in enumerate(pairs[:5]):
                                mlflow.log_param(f'top_feat_{i+1}', fname)
                                mlflow.log_metric(f'imp_{fname[:20]}', float(fval))
                                feat_imp[fname] = float(fval)

                    try:
                        mlflow.sklearn.log_model(model, 'model')
                    except Exception:
                        pass

                    results[regime] = {
                        'run_id': run.info.run_id,
                        'run_name': run_name,
                        'top_features': feat_imp,
                    }
            except Exception as e:
                results[regime] = {'error': str(e)}

        return {'success': True, 'regime_runs': results}
    except Exception as e:
        log.error('cmd_log_regime_run failed: %s', e)
        return {'success': False, 'error': str(e)}


def cmd_compare(params: dict) -> dict:
    """Compare last N runs from EGX-Explosion-ML."""
    try:
        _require_mlflow()
        n = int(params.get('n', 10))

        runs_df = mlflow.search_runs(
            experiment_names=[EXP_GLOBAL],
            max_results=n,
            order_by=['metrics.oos_precision DESC'],
        )

        if runs_df.empty:
            return {'success': True, 'runs': [], 'message': 'No runs found'}

        records = []
        for _, row in runs_df.iterrows():
            records.append({
                'run_id':        row.get('run_id', ''),
                'run_name':      row.get('tags.mlflow.runName', ''),
                'oos_precision': round(float(row.get('metrics.oos_precision', 0) or 0), 4),
                'is_precision':  round(float(row.get('metrics.is_precision', 0) or 0), 4),
                'is_samples':    int(row.get('metrics.is_samples', 0) or 0),
                'oos_samples':   int(row.get('metrics.oos_samples', 0) or 0),
                'oos_signals':   int(row.get('metrics.oos_signals', 0) or 0),
                'start_time':    str(row.get('start_time', '')),
                'status':        row.get('status', ''),
            })

        return {'success': True, 'runs': records, 'total': len(records)}
    except Exception as e:
        log.error('cmd_compare failed: %s', e)
        return {'success': False, 'error': str(e)}


def cmd_register(params: dict) -> dict:
    """Register best model to MLflow Model Registry."""
    try:
        _require_mlflow()

        runs_df = mlflow.search_runs(
            experiment_names=[EXP_GLOBAL],
            max_results=50,
            order_by=['metrics.oos_precision DESC'],
        )

        if runs_df.empty:
            return {'success': False, 'error': 'No runs to register — run cmd_log_run first'}

        best = runs_df.iloc[0]
        run_id   = best['run_id']
        oos_prec = float(best.get('metrics.oos_precision', 0) or 0)

        model_uri = f'runs:/{run_id}/model'
        mv = mlflow.register_model(model_uri, 'EGXExplosionModel')

        return {
            'success': True,
            'model_name':    'EGXExplosionModel',
            'model_version': mv.version,
            'run_id':        run_id,
            'oos_precision': round(oos_prec, 4),
            'model_uri':     model_uri,
        }
    except Exception as e:
        log.error('cmd_register failed: %s', e)
        return {'success': False, 'error': str(e)}


def cmd_report(params: dict) -> dict:
    """Full report: init + compare top runs."""
    try:
        init_result    = cmd_init(params)
        compare_result = cmd_compare(params)

        return {
            'success':  True,
            'init':     init_result,
            'compare':  compare_result,
            'summary': {
                'total_runs':       compare_result.get('total', 0),
                'best_oos_precision': compare_result['runs'][0]['oos_precision']
                    if compare_result.get('runs') else None,
                'experiments': list(init_result.get('experiments', {}).keys()),
            },
        }
    except Exception as e:
        log.error('cmd_report failed: %s', e)
        return {'success': False, 'error': str(e)}


# ── Dispatch ──────────────────────────────────────────────────────────────────
COMMANDS = {
    'init':            cmd_init,
    'log_run':         cmd_log_run,
    'log_regime_run':  cmd_log_regime_run,
    'compare':         cmd_compare,
    'register':        cmd_register,
    'report':          cmd_report,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'success': False, 'error': 'Usage: mlflow_tracker.py <command> [json_params]'}))
        sys.exit(1)

    command = sys.argv[1]
    params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    handler = COMMANDS.get(command)
    if handler is None:
        print(json.dumps({'success': False, 'error': f'Unknown command: {command}',
                          'available': list(COMMANDS)}))
        sys.exit(1)

    result = handler(params)
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
