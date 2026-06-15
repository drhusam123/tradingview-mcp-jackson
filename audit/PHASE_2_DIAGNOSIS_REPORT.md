# Phase 2 — Diagnosis, Fixes & Verification Report

**Audit date:** 2026-06-15  
**Status:** Fixes applied + partial pipeline recovery verified  
**Evidence logs:** `logs/audit_catchup_20260615.log`, `logs/tv_auto_daily.log`

---

## Executive summary

| Area | Before | After | Status |
|------|--------|-------|--------|
| Automation verify | 101/102 FAIL | **102/102 PASS** | ✅ Fixed |
| ML/Scan/Meta freshness | Stuck at 2026-06-11 | **2026-06-14** | ✅ Recovered |
| Actionable signals | 0 since 2026-06-11 | **2** (EGCH, ROTO) | ✅ Recovered |
| indicators_cache | Broken save (252 errors) | **2026-06-14** | ✅ Fixed |
| Client delivery | Pending 2026-06-14 | Still pending | ⚠️ Manual send needed |
| P6 proof loop WR | 37.8% | 37.8% | ⚠️ Research gate (not infra) |

---

## Root cause #1 — Pipeline abort (2026-06-12 → 2026-06-14)

### Symptom
- `final_signals` stopped at 2026-06-11
- `logs/tv_auto_daily.log` line 80: `[tv-auto] FAILED: TradingView daily OHLCV sync: spawnSync /bin/sh ETIMEDOUT`
- Telegram cron 2026-06-11: `CRON_DELIVERY_FAILED` (upstream stale)

### Root cause (proven)
1. **`egx_tv_auto_update.mjs` always passed `--force` to `daily_update.mjs`** when OHLCV was stale → full 269-symbol TV fetch (~107 min / 6462s)
2. Parent `execSync` timed out → **entire EOD pipeline aborted** before `rebuild_indicators` / `score_all`
3. Subsequent sessions (2026-06-12–14) never scored

### Fix applied
**File:** `scripts/egx_tv_auto_update.mjs`

```javascript
// Before: needsDaily = FORCE || stale > 0; daily_update always --force
// After:
const needsDaily = stale.staleness_trading_days > 0;
const dailyFlags = FORCE ? '--force' : '';  // incremental by default
```

- `--force` on `egx_tv_auto_update` now only **skips trading-day check** (weekend catch-up), not full OHLCV re-fetch
- Explicit `--force` still forces full OHLCV when user intends it

### Verification
```bash
node scripts/egx_tv_auto_update.mjs --force   # Sunday catch-up
# Log: "Daily OHLCV is fresh by EGX trading calendar; no daily sync needed."
# Completed scoring through proof-pack (hung later on fetch_alerts — see RC#4)
```

```bash
sqlite3 data/egx_trading.db \
  "SELECT trade_date, COUNT(*), SUM(actionable) FROM final_signals WHERE trade_date>='2026-06-11' GROUP BY 1;"
# 2026-06-11|337|4
# 2026-06-14|254|2
```

---

## Root cause #2 — Automation verify false negative (MED wiring)

### Symptom
`npm run egx:verify:fast` → **101/102 PASS** — `❌ eod med daily chain`

### Root cause
Verify scripts checked for obsolete `med_0_2_daily_chain.py`; pipeline uses **`med_0_3_daily_chain.py`** since MED-0.3.

### Fix applied
| File | Change |
|------|--------|
| `scripts/egx_automation_verify.mjs` | `med_0_2` → `med_0_3_daily_chain.py` |
| `scripts/egx_discovery_verify.mjs` | `med_0_2` → `med_0_3_daily_chain.py` + `med_0_3_status.py` |

### Verification
```bash
node scripts/egx_automation_verify.mjs
# === Automation Verify: 102/102 PASS ===
```

---

## Root cause #3 — indicators_cache save broken (252/268 errors)

### Symptom
- `rebuild_indicators.mjs`: **252 errors**, 0 success, cache stuck at 2026-06-11
- `saveIndicatorsCache`: `39 values for 40 columns`
- Decision bot blocked actionable: `BLOCKED (indicator_cache)` for EGCH, ROTO

### Root cause
**File:** `src/egx/database.js` — `saveIndicatorsCache()`  
SQL `VALUES` clause had **38 `?` placeholders** but **39 bound columns** when `source` column exists (migration 006).  
Also field-name drift: `ind.stoch` vs `ind.stochastic`, `ind.bb` vs `ind.bollingerBands`.

### Fix applied
- Dynamic placeholder builder: 38 + optional `source` + `datetime('now')`
- Fallback field names for stoch/bb/williams

### Verification
```bash
node scripts/rebuild_indicators.mjs --symbol EGCH
# ✅ ناجح: 1 | ❌ خطأ: 0

node scripts/rebuild_indicators.mjs
# آخر تحديث: 2026-06-14

sqlite3 data/egx_trading.db \
  "SELECT bar_date FROM indicators_cache WHERE symbol='EGCH' ORDER BY bar_date DESC LIMIT 1;"
# 2026-06-14
```

---

## Root cause #4 — fetch_alerts hang (TV offline tail)

### Symptom
Catch-up pipeline stopped after `tv_proof_pack.mjs` for ~45 min with no output.

### Root cause
`fetch_alerts.mjs` calls `tv_health_check` / `alert_create` via CDP — **blocks indefinitely** when TradingView not running.

### Status
**Fixed in Phase 2** — `fetch_alerts` now runs only when `tvReady` (CDP connected); logs skip otherwise.

---

## Root cause #5 — Missing `.env.example`

### Fix applied
Created `.env.example` with placeholders (no secrets).

---

## Session ready matrix (post-recovery)

```bash
npm run egx:session:ready
```

| Check | Result |
|-------|--------|
| OHLCV date | ✅ 2026-06-14 |
| ML pred date | ✅ 2026-06-14 |
| Scan date | ✅ 2026-06-14 |
| Meta date | ✅ 2026-06-14 |
| Data freshness | ✅ 0 stale sessions |
| Actionable | ✅ 2 deliverable (EGCH, ROTO) |
| Proof loop P6 | ❌ WR5 37.8% < 60% (research graduation gate) |
| Last full verify | ❌ stale timestamp until re-run |

**Score: 16/18** — infra gates pass; P6 WR is research policy, not pipeline failure.

---

## Delivery state

```bash
npm run egx:notify:reconcile
```

| Date | Symbols | Status |
|------|---------|--------|
| 2026-06-14 | EGCH, ROTO | **NOT_SENT** (pending) |
| 2026-06-11 | EGCH, NARE, POUL, UEFM | SENT |

**Action required (manual, no auto-send in audit):**
```bash
npm run egx:telegram:cron -- --dry-run   # QA first
npm run egx:prod:prepare-send            # then live if QA passes
```

---

## Decision bot blockers (non-infra)

| Blocker | Type | Notes |
|---------|------|-------|
| max open positions 12/6 | Portfolio policy | `EGX_MAX_OPEN_POSITIONS=6` in `.env` |
| P6 proof loop WR < 60% | Research gate | Expected until more ULTRA wins |

---

## Files changed (Phase 2)

| File | Change |
|------|--------|
| `scripts/egx_automation_verify.mjs` | MED 0.3 wiring check |
| `scripts/egx_discovery_verify.mjs` | MED 0.3 wiring check |
| `scripts/egx_tv_auto_update.mjs` | Incremental daily_update; decouple FORCE from needsDaily |
| `src/egx/database.js` | Fix `saveIndicatorsCache` placeholder count + field aliases |
| `.env.example` | New deploy template |
| `audit/PHASE_2_DIAGNOSIS_REPORT.md` | This report |

---

## Verification commands (runbook)

```bash
# Structural gates
npm run egx:verify:fast          # expect Automation 102/102
node scripts/egx_automation_verify.mjs

# Data + signals
npm run egx:status
npm run egx:session:ready

# Indicators
node scripts/rebuild_indicators.mjs
sqlite3 data/egx_trading.db "SELECT MAX(bar_date) FROM indicators_cache;"

# Delivery
npm run egx:notify:reconcile
npm run egx:telegram:cron -- --dry-run
```

---

## Phase 3 backlog

1. ~~Skip `fetch_alerts` when `!tvReady` (prevent pipeline hang)~~ ✅ Done
2. Backfill scoring for 2026-06-12 and 2026-06-13 if needed for audit trail
3. Portfolio position cleanup (12 > 6 max open)
4. P6 proof loop — research track (not automation)
5. `indicators_cache` row model: one row per symbol vs per date (currently REPLACE overwrites — verify design)
6. Cron ETIMEDOUT monitoring alert when daily_update > 90 min

---

## Audit trail

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1 | `audit/SYSTEM_MAP.md` | ✅ |
| 2 | `audit/PHASE_2_DIAGNOSIS_REPORT.md` + fixes | ✅ |
| 3 | Pipeline hardening + delivery | Pending |
