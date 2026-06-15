# Phase 4 — Data Integrity & Pipeline Hardening Report

**Audit date:** 2026-06-15  
**Status:** ✅ ROTO repaired · per-symbol lag wired · `egx_go_live --send` fixed

---

## Executive summary

| Item | Before | After | Status |
|------|--------|-------|--------|
| ROTO OHLCV @ 2026-06-14 | missing (stale ~5 EGP scale) | **33.77 EGP** (TV-aligned) | ✅ |
| ROTO `indicators_cache` | missing | **2026-06-14** rsi=48.22 | ✅ |
| ROTO actionable | yes (entry 5.06 — wrong scale) | **demoted** after rescore | ✅ Correct |
| Actionable lag (EGCH/UEFM/ROTO) | 2 behind | **0 behind** | ✅ |
| Per-symbol OHLCV audit | aggregate-only | **`getSymbolsLaggingOhlcv`** + catch-up | ✅ |
| `egx:prod:send` | always dry-run | **`--send` → live cron** | ✅ Fixed |
| Strict safety (post-rescore) | 2/3 | **EGCH + UEFM 2/2** | ✅ |

---

## Root cause #7 — ROTO corrupt OHLCV scale

### Symptom
- DB had ~5 EGP bars (2026-05-24/25) while TradingView returned ~33 EGP for June
- `daily_update` rejected: `suspicious new close jump ROTO: 5.06 -> 33.89`
- `corporate_actions` flagged `SUSPICIOUS` (-84%) — not a confirmed split

### Root cause (proven)
Erroneous low-scale bars inserted May 24–25; TV `EGX_DLY:ROTO` series remained at ~33 EGP.  
Scoring on corrupt data produced actionable ROTO with `entry_price=5.06` (invalid).

### Fix applied (ops + data)
```sql
DELETE FROM ohlcv_history WHERE symbol='ROTO' AND date(bar_time,'unixepoch') >= '2026-05-24';
DELETE FROM indicators_cache WHERE symbol='ROTO' AND bar_date >= '2026-05-24';
```
```bash
node scripts/daily_update.mjs --symbol ROTO   # +bars 2026-06-08 → 2026-06-14 @ ~33 EGP
node scripts/rebuild_indicators.mjs --symbol ROTO
npm run egx:prod:prepare-send               # rescore → ROTO demoted, 2 actionable
```

### Verification
```sql
SELECT date(bar_time,'unixepoch'), close FROM ohlcv_history
WHERE symbol='ROTO' ORDER BY bar_time DESC LIMIT 3;
-- 2026-06-14|33.77 | 2026-06-11|33.32 | 2026-06-10|33.6

SELECT symbol, actionable, entry_price FROM final_signals
WHERE trade_date='2026-06-14' AND symbol IN ('EGCH','UEFM','ROTO');
-- EGCH|1|13.3 | UEFM|1|468.99 | (ROTO not actionable)
```

**Note:** Live Telegram id=375 (Phase 3) included ROTO at wrong 5.06 entry — already sent; corrected data prevents recurrence.

---

## Root cause #8 — Aggregate OHLCV freshness masked per-symbol gaps

### Symptom
Pipeline logged "Daily OHLCV is fresh" while **43 symbols** (incl. UEFM, ROTO) lacked bar on `last_trading_day`.

### Fix applied

**`src/egx/database.js`** — new export:
```javascript
export function getSymbolsLaggingOhlcv(targetDate)
```

**`scripts/egx_tv_auto_update.mjs`** — after calendar freshness check:
- Query per-symbol lag vs `signalDate`
- If **actionable** symbols lag and TV connected → targeted `daily_update.mjs --symbol <SYM>` for each

### Verification
```javascript
getSymbolsLaggingOhlcv('2026-06-14')  // 42 symbols (universe-wide)
// actionable lag: []  (EGCH, UEFM, ROTO all current)
```

---

## Root cause #9 — `egx_go_live.mjs` ignored `--send`

### Symptom
`npm run egx:prod:send` → `egx_go_live.mjs --send --update` but only ran Telegram **dry-run**.

### Root cause
```javascript
// Before: TG_DRY true unless --skip-telegram (inverted logic)
const TG_DRY = includes('--telegram-dry-run') || !includes('--skip-telegram');
```

### Fix applied
```javascript
const TG_SKIP = includes('--skip-telegram');
const TG_SEND = includes('--send');
const TG_DRY = !TG_SKIP && (includes('--telegram-dry-run') || !TG_SEND);
// --send → live egx_telegram_cron.mjs (no --dry-run)
```

---

## Files changed (Phase 4)

| File | Change |
|------|--------|
| `src/egx/database.js` | `getSymbolsLaggingOhlcv()` |
| `src/egx/index.js` | export new helper |
| `scripts/egx_tv_auto_update.mjs` | per-symbol actionable catch-up |
| `scripts/egx_go_live.mjs` | honor `--send` for live Telegram |
| `data/egx_trading.db` | ROTO OHLCV purge + refetch; CA notes |
| `portfolio_positions` | ROTO #20 closed (`ROTO_DATA_REPAIR_DEMOTED`) |

---

## Verification matrix

| Command | Result |
|---------|--------|
| `node scripts/egx_decision_bot.mjs --verify` | **PASS** — EGCH, UEFM |
| `npm run egx:prod:prepare-send` | **GREEN** — 2 actionable, safety all pass |
| `getSymbolsLaggingOhlcv('2026-06-14')` actionable | **0 lag** |

---

## Remaining (Phase 5 optional)

1. Batch catch-up for 42 non-actionable lagging symbols (cron window / `--max-symbols`)
2. Auto-purge `SUSPICIOUS` OHLCV bars when TV reconcile disagrees >50%
3. Portfolio dedup on `import_signals` (prevent max-position cap recurrence)
4. Resend correction note for ROTO if client still holds wrong-entry message from id=375

**Phase 4 complete.**
