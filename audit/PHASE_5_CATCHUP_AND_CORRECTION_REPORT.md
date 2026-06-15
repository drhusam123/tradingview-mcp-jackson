# Phase 5 — Catch-up Automation & Client Correction Report

**Audit date:** 2026-06-15  
**Status:** ✅ ROTO correction sent · catch-up tooling · portfolio dedup

---

## Executive summary

| Item | Status |
|------|--------|
| `egx:ohlcv:catchup` script | ✅ New batch per-symbol sync |
| `egx:notify:correction` (ROTO) | ✅ **Live sent** to client |
| Portfolio `import_signals` dedup | ✅ Skip if symbol already OPEN |
| Actionable OHLCV lag | ✅ **0** (EGCH, UEFM current) |
| Universe-wide lag (42→…) | ⚠️ Catch-up running — mostly illiquid symbols |

---

## Deliverable 1 — OHLCV catch-up automation

### New files / commands
```bash
npm run egx:ohlcv:catchup                    # all symbols behind last trading day
npm run egx:ohlcv:catchup -- --max-symbols 20
npm run egx:ohlcv:catchup -- --date 2026-06-14 --dry-run
```

**`scripts/egx_ohlcv_catchup.mjs`**
- Uses `getSymbolsLaggingOhlcv(targetDate)`
- Runs `daily_update.mjs --symbol <SYM>` per lagging row
- Reports remaining lag after batch

### Observation (first batch)
42 lagging symbols are predominantly **low-liquidity / stale-universe** names (ANFI, MEGM, MMAT, …) last bar 2026-05-17–2026-06-10. Many fail TV fetch (no bars / halted).  
**Actionable path is protected** via Phase 4 `egx_tv_auto_update.mjs` actionable catch-up.

---

## Deliverable 2 — ROTO client correction

### Problem
Phase 3 Telegram id=375 included ROTO at **entry 5.06 EGP** (corrupt OHLCV). Phase 4 demoted ROTO; client needed explicit notice.

### Fix
```bash
npm run egx:notify:correction -- --symbol ROTO --date 2026-06-14 --send
```

**`scripts/egx_notify_data_correction.mjs`**
- Sends HTML correction via `sendTelegram({ clientDelivery: true })`
- Logs `pipeline_stage: data_correction` in `notification_delivery_audit`

### Verification
```
✅ Correction sent
```

Message states ROTO withdrawn; valid signals **EGCH**, **UEFM**.

---

## Deliverable 3 — Portfolio import dedup

### Problem (Phase 3)
17 open positions vs max 6 — duplicate imports across dates for same symbol.

### Fix — `portfolio_tracker.py` `import_gate_passed_signals`
Skip import when symbol already has status `OPEN | PARTIAL_T1 | PARTIAL_T2` (any date), in addition to same-date check.

---

## Files changed (Phase 5)

| File | Change |
|------|--------|
| `scripts/egx_ohlcv_catchup.mjs` | NEW — batch per-symbol OHLCV sync |
| `scripts/egx_notify_data_correction.mjs` | NEW — client data correction notices |
| `scripts/python/portfolio_tracker.py` | Open-position dedup on import |
| `package.json` | `egx:ohlcv:catchup`, `egx:notify:correction` |

---

## Current system state

| Metric | Value |
|--------|-------|
| Actionable 2026-06-14 | EGCH, UEFM (2) |
| Delivery reconcile | 6/6 sent + ROTO correction |
| prepare-send | GREEN |
| Decision bot | PASS 2/2 |
| Per-symbol actionable lag | 0 |

---

## Remaining (Phase 6 optional)

1. Mark chronically failing lag symbols `archived` in `stock_universe` after N fetch failures
2. Auto-purge `SUSPICIOUS` OHLCV tail before per-symbol catch-up (ROTO pattern)
3. Wire `egx:ohlcv:catchup` into weekly cron (Sunday gap repair window)
4. Unit test for `import_signals` open-dedup

**Phase 5 complete.**
