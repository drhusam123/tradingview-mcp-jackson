#!/usr/bin/env bash
# Install EGX Python dependencies on cron + dev interpreters.
# Usage: bash scripts/install_python_deps.sh [--pyenv-only|--system-only]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REQ="$ROOT/requirements-python.txt"
SYSTEM_PY="/usr/bin/python3"
PYENV_PY="${HOME}/.pyenv/shims/python"

# libomp for lightgbm on macOS
for p in /opt/homebrew/opt/libomp/lib /usr/local/opt/libomp/lib; do
  if [[ -f "$p/libomp.dylib" ]]; then
    export DYLD_LIBRARY_PATH="${p}${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
    break
  fi
done

install_one() {
  local py="$1"
  local label="$2"
  echo ""
  echo "════════════════════════════════════════════════════════"
  echo "▶  $label"
  echo "    $py"
  echo "════════════════════════════════════════════════════════"
  if [[ ! -x "$py" ]] && ! command -v "$py" &>/dev/null; then
    echo "  ⚠️  تخطي — غير موجود"
    return 1
  fi
  "$py" --version
  "$py" -c "import ssl; print('  SSL:', ssl.OPENSSL_VERSION)"
  "$py" -m pip install --upgrade pip setuptools wheel
  "$py" -m pip install -r "$REQ"
  echo "  ✅  تم التثبيت"
}

verify_one() {
  local py="$1"
  local label="$2"
  echo ""
  echo "── تحقق: $label ──"
  "$py" -c "
import json, sys
req = [
  'numpy','pandas','lightgbm','sklearn','scipy','optuna','shap','mlflow',
  'yfinance','statsmodels','tsfresh','joblib','xgboost','lifelines',
  'duckdb','pyarrow','networkx','requests','yaml','dotenv','matplotlib',
]
missing = []
versions = {}
for p in req:
    mod = 'sklearn' if p == 'sklearn' else ('yaml' if p == 'yaml' else ('dotenv' if p == 'dotenv' else p))
    try:
        m = __import__(mod)
        versions[p] = getattr(m, '__version__', 'ok')
    except ImportError:
        missing.append(p)
print(json.dumps({'label': '$label', 'python': sys.version.split()[0], 'missing': missing, 'versions': versions}, indent=2))
if missing:
    sys.exit(1)
"
}

MODE="${1:-all}"
FAIL=0

case "$MODE" in
  --system-only) install_one "$SYSTEM_PY" "System Python (cron)" || FAIL=1 ;;
  --pyenv-only)  install_one "$PYENV_PY"  "Pyenv Python (dev)"  || FAIL=1 ;;
  *)
    install_one "$SYSTEM_PY" "System Python (cron)" || FAIL=1
    if [[ -x "$PYENV_PY" ]]; then
      install_one "$PYENV_PY" "Pyenv Python (dev)" || FAIL=1
    else
      echo "  ℹ️  pyenv غير موجود — تخطي"
    fi
    ;;
esac

echo ""
echo "════════════════════════════════════════════════════════"
echo "▶  Smoke tests"
echo "════════════════════════════════════════════════════════"
verify_one "$SYSTEM_PY" "system" || FAIL=1
if [[ -x "$PYENV_PY" ]]; then
  verify_one "$PYENV_PY" "pyenv" || FAIL=1
fi

# lightgbm runtime smoke
for py in "$SYSTEM_PY" "$PYENV_PY"; do
  [[ -x "$py" ]] || continue
  echo ""
  echo "── lightgbm smoke: $py ──"
  "$py" -c "import lightgbm as lgb; import numpy as np; d=lgb.Dataset(np.random.rand(20,3), label=np.random.randint(0,2,20)); lgb.train({'objective':'binary','verbose':-1}, d, num_boost_round=2); print('  ✅ lightgbm train OK')"
done

# lifelines smoke (ml_advanced survival)
for py in "$SYSTEM_PY" "$PYENV_PY"; do
  [[ -x "$py" ]] || continue
  echo ""
  echo "── lifelines smoke: $py ──"
  "$py" -c "from lifelines import CoxPHFitter; print('  ✅ lifelines', CoxPHFitter.__module__)"
done

echo ""
if [[ $FAIL -eq 0 ]]; then
  echo "═══ PASS — Python deps جاهزة على كل المسارات ═══"
else
  echo "═══ FAIL — راجع الأخطاء أعلاه ═══"
  exit 1
fi
