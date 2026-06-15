# Gates & Actionable Audit

**Generated:** 2026-06-15T20:06:37.809Z

## Pipeline

```text
unified_signals → score_all → gates → gate_audit_snapshots
  → promotion → final_signals.actionable → telegram → notification_delivery_audit
```

## Notification pipeline

| Field | Value |
|-------|-------|
| Root cause | notification_not_run_or_failed |
| Actionable DB | 2 |
| Deliverable | 2 |
| Telegram configured | yes |

## Stage audit

| Stage | Expected Input | Actual Input | Expected Output | Actual Output | Pass/Fail | Silent Risk |
|-------|----------------|--------------|-----------------|---------------|-----------|-------------|
| score_all | unified signals | OHLCV+features | scored rows | gate snapshots | ✅ | low |
| gates | scored signals | gate_audit_snapshots | pass/fail+reason | 2 deliverable | ✅ | low — gate_doctor logs blockers |
| promotion | quality+edge pass | final_signals | actionable rows | DB=2 | ✅ | low |
| formatter | actionable SSOT | final_signals.actionable | telegram messages | deliverable=2 | ✅ | low — diagnostics JSON |
| send gate | deliverable+pre_send | safety veto | send or block+reason | root=notification_not_run_or_failed | ✅ | medium — safety blocks logged |

## Gate doctor

```json
{
  "success": true,
  "period": {
    "start": "2026-06-01",
    "end": "2026-06-15"
  },
  "n_snapshots": 3207,
  "n_evaluable_5d": 876,
  "n_dates": 12,
  "actionable_evaluable": 1,
  "actionable_winners_5d": 1,
  "actionable_losers_5d": 0,
  "sequential_funnel": {
    "2026-06-01": {
      "total": 254,
      "quality_pass": 4,
      "final_edge_pass": 2,
      "actionable": 0,
      "top_first_blocker": [
        [
          "low_volume_signal",
          73
        ],
        [
          "ml_too_low",
          67
        ],
        [
          "noisy_low_prox",
          43
        ],
        [
          "volatile_stock",
          35
        ],
        [
          "drift_throttle_ml_floor",
          19
        ]
      ]
    },
    "2026-06-02": {
      "total": 254,
      "quality_pass": 13,
      "final_edge_pass": 3,
      "actionable": 0,
      "top_first_blocker": [
        [
          "ml_too_low",
          71
        ],
        [
          "low_volume_signal",
          58
        ],
        [
          "noisy_low_prox",
          48
        ],
        [
          "volatile_stock",
          33
        ],
        [
          "drift_throttle_ml_floor",
          15
        ]
      ]
    },
    "2026-06-03": {
      "total": 254,
      "quality_pass": 12,
      "final_edge_pass": 4,
      "actionable": 0,
      "top_first_blocker": [
        [
          "ml_too_low",
          70
        ],
        [
          "low_volume_signal",
          64
        ],
       
```

## Verification

```bash
npm run egx:audit:gates
npm run egx:notification:audit
```
