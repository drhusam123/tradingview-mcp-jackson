#!/bin/bash
# ============================================================
# EGX ML — Daily Operations Script
# Run every day after EGX market close (Thu ~15:30 Cairo time)
# ============================================================
# Usage:
#   bash egx_daily_run.sh              # full daily run
#   bash egx_daily_run.sh predict_only # skip retrain, just predict
#   bash egx_daily_run.sh report_only  # just show today's report
# ============================================================

ROOT="/Users/dr.husam/tradingview-mcp-jackson"
PY=$(which python3)
TRAINER="$ROOT/scripts/python/egx_ml_trainer.py"
REPORTER="$ROOT/scripts/python/egx_client_report.py"
TRACKER="$ROOT/scripts/python/egx_outcome_tracker.py"
LOG="$ROOT/logs/daily_$(date +%Y%m%d).log"

mkdir -p "$ROOT/logs"
ts() { date "+%Y-%m-%d %H:%M:%S"; }
sep() { echo "════════════════════════════════════════════════" | tee -a $LOG; }

MODE="${1:-full}"

sep
echo "[$(ts)] EGX ML DAILY RUN — mode=$MODE" | tee -a $LOG
sep

# ── 1. Fill outcome tracker (update previous predictions) ────────────────
echo "[$(ts)] Filling outcome tracker..." | tee -a $LOG
$PY $TRACKER >> $LOG 2>&1
echo "[$(ts)] Outcomes updated" | tee -a $LOG

if [ "$MODE" = "report_only" ]; then
    $PY $REPORTER
    exit 0
fi

# ── 2. Generate today's predictions ──────────────────────────────────────
echo "[$(ts)] Running predict_ensemble..." | tee -a $LOG
$PY $TRAINER predict_ensemble > /tmp/daily_predict.log 2>&1
if [ $? -eq 0 ]; then
    echo "[$(ts)] ✅ Predictions generated" | tee -a $LOG
    tail -1 /tmp/daily_predict.log | tee -a $LOG
else
    echo "[$(ts)] ❌ predict_ensemble FAILED" | tee -a $LOG
    tail -5 /tmp/daily_predict.log | tee -a $LOG
fi

if [ "$MODE" = "predict_only" ]; then
    $PY $REPORTER
    exit 0
fi

# ── 3. Generate and display client report ────────────────────────────────
echo "[$(ts)] Generating client report..." | tee -a $LOG
$PY $REPORTER | tee -a $LOG

sep
echo "[$(ts)] DAILY RUN COMPLETE" | tee -a $LOG
sep
