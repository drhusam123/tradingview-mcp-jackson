# Phase 6 — Hygiene Automation & Prevention Report

**Audit date:** 2026-06-15  
**Status:** ✅ Complete — purge · failure tracking · cron · tests

---

## Executive summary

| Deliverable | Status |
|-------------|--------|
| Suspicious OHLCV tail auto-purge | ✅ `ohlcv_hygiene.mjs` |
| Chronic fetch-failure archive | ✅ `ohlcv_fetch_failures` table |
| Catch-up integration | ✅ purge → fetch → record → archive |
| Weekly cron wiring | ✅ via `egx_gap_repair.mjs` |
| Portfolio import dedup test | ✅ `portfolio_import_dedup.test.py` |
| Automation verify | ✅ **109/109 PASS** |

---

## Deliverable 1 — Suspicious tail purge (ROTO pattern)

### Module: `scripts/lib/ohlcv_hygiene.mjs`

Detects corrupt tail bars when:
- Close jump **>50%** between consecutive bars (configurable `EGX_OHLCV_SUSPICIOUS_JUMP_PCT`)
- OR latest `corporate_actions` row is `SUSPICIOUS`

Actions:
- `purgeOhlcvFromDate(symbol, fromDate)` — deletes `ohlcv_history` + `indicators_cache` from date onward
- Invoked automatically before each symbol fetch in `egx_ohlcv_catchup.mjs`

### CLI
```bash
npm run egx:ohlcv:hygiene -- --purge-symbols ROTO,ANFI
npm run egx:ohlcv:hygiene -- --dry-run
```

Dry-run scan (5 lagging symbols) found purge candidates:
- ANFI, MEGM, MMAT, NDRL, SAIB — suspicious tails from 2026-04/05

---

## Deliverable 2 — Chronic failure archive

### Table: `ohlcv_fetch_failures`
| Column | Purpose |
|--------|---------|
| `fail_count` | Consecutive / cumulative failures |
| `last_fail_at` | Timestamp |
| `last_success_at` | Reset on successful fetch |
| `last_reason` | TV error snippet |

### Archive rule
- Threshold: **5** failures (`EGX_OHLCV_FAIL_ARCHIVE_THRESHOLD`)
- **Protected:** symbols actionable in last 14 days (never archived)
- Sets `stock_universe.status = 'archived'` + `hygiene_reason`

```bash
npm run egx:ohlcv:catchup -- --archive-chronic
```

---

## Deliverable 3 — Cron / gap repair wiring

**`scripts/egx_gap_repair.mjs`** — new first step (optional, TV-dependent):
```javascript
ohlcv_hygiene_purge → egx_ohlcv_catchup.mjs --max-symbols 15 --archive-chronic
// Sunday full gap repair: --max-symbols 30 (when --full)
```

**Schedule (existing cron, enhanced behavior):**
| Cron | Time | Job |
|------|------|-----|
| `EGX-GAP-REPAIR-D` | Sun–Thu 7:20 AM | light gap repair + OHLCV catch-up (15 sym) |
| `EGX-GAP-REPAIR-W` | Sunday 8:10 AM | full gap repair + OHLCV catch-up (30 sym) |

`install_cron.mjs` log text updated to document OHLCV catch-up.

---

## Deliverable 4 — Unit test

**`tests/portfolio_import_dedup.test.py`**
- ✅ Skips import when symbol already `OPEN`
- ✅ Allows import when prior position `CLOSED_T1`

```bash
python3 tests/portfolio_import_dedup.test.py
# portfolio_import_dedup: OK
```

---

## Files added / changed (Phase 6)

| File | Change |
|------|--------|
| `scripts/lib/ohlcv_hygiene.mjs` | NEW — purge + failure tracking + archive |
| `scripts/egx_ohlcv_hygiene.mjs` | NEW — standalone hygiene runner |
| `scripts/egx_ohlcv_catchup.mjs` | Purge before fetch, record outcomes, `--archive-chronic` |
| `scripts/egx_gap_repair.mjs` | OHLCV catch-up step |
| `scripts/egx_automation_verify.mjs` | +7 checks (109 total) |
| `scripts/install_cron.mjs` | Doc string for catch-up |
| `tests/portfolio_import_dedup.test.py` | NEW |
| `package.json` | `egx:ohlcv:hygiene` |

---

## Verification

| Command | Result |
|---------|--------|
| `python3 tests/portfolio_import_dedup.test.py` | **OK** |
| `npm run egx:ohlcv:catchup -- --dry-run --max-symbols 5` | 5 purge candidates detected |
| `node scripts/egx_automation_verify.mjs` | **109/109 PASS** |

---

## Full audit arc (Phases 1–6)

| Phase | Focus | Outcome |
|-------|-------|---------|
| 1 | System map | `audit/SYSTEM_MAP.md` |
| 2 | Pipeline recovery | TV incremental fix, indicators_cache, 102/102 verify |
| 3 | Delivery | Portfolio unblock, live send id=375 |
| 4 | ROTO integrity + per-symbol lag | OHLCV repair, `getSymbolsLaggingOhlcv` |
| 5 | Catch-up tooling + client correction | `egx:ohlcv:catchup`, ROTO notice sent |
| 6 | Prevention automation | Purge, failure archive, cron, tests |

**Current production state:**
- Actionable: **EGCH, UEFM** (2)
- prepare-send: **GREEN**
- Reconcile: **6/6 sent** + ROTO correction
- Actionable OHLCV lag: **0**
- Automation: **109/109**

**Phase 6 complete. Audit cycle closed.**
