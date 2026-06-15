# Automation Audit

**Generated:** 2026-06-15T20:06:44.494Z

## Daily DAG

1. 05:15 full_verify
1. 07:00 prod:status
1. 07:10 session_ready
1. 16:30 tv_auto_update
1. 17:20 telegram_cron
1. 17:45 post_session_ops → health

## Cron status

```
مهام cron الحالية:
# ══════════════════════════════════════════════════════════════════
# EGX Trading System — Full Automation Crontab
# توقيت القاهرة = UTC+3 (صيف/EEST)
# caffeinate -i: يمنع النوم أثناء التشغيل
# ══════════════════════════════════════════════════════════════════
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

# ── 🌅 MORNING HEALTH CHECK — كل يوم 07:00 صباحاً بتوقيت القاهرة (04:00 UTC)
0 4 * * *   cd /Users/dr.husam/tradingview-mcp-jackson && /usr/bin/caffeinate -i /usr/bin/python3 scripts/python/health_monitor.py check >> logs/health.log 2>&1 # EGX-HEALTH-MONITOR

# ── 📊 DAILY RUN — بعد إغلاق البورصة 17:30 القاهرة (14:30 UTC) أحد-خميس

# ── 📤 TELEGRAM DAILY — 18:00 القاهرة (15:00 UTC) أحد-خميس

# ── ⚡ EVOLUTION QUICK — 17:45 القاهرة (14:45 UTC) أحد-خميس

# ── 🧠 COGNITION QUICK — 18:30 القاهرة (15:30 UTC) أحد-خميس

# ── 🌙 NIGHT LAB — 22:00 القاهرة (19:00 UTC) أحد-خميس
#    يُشغّل: per_stock → cycles → cross_stock → evolution → signal_integration → orchestrator
0 19 * * 0-4   cd /Users/dr.husam/tradingview-mcp-jackson && /usr/bin/caffeinate -i /usr/bin/python3 scripts/python/night_lab.py run >> logs/night_lab.log 2>&1 # EGX-NIGHT-LAB

# ── 🔬 WEEKEND DEEP LAB — الجمعة 21:00 القاهرة (18:00 UTC): إعادة تدريب كاملة
0 18 * * 5     cd /Users/dr.husam/tradingview-mcp-jackson && /usr/bin/caffeinate -i /usr/bin/python3 scripts/python/night_lab.py run >> logs/night_lab.log 2>&1 # EGX-WEEKEND-DEEPLAB

# ── 🔄 CYCLE HUNTER — الأحد 04:00 القاهرة (01:00 UTC): تحديث الدورات
0 1 * * 0      cd /Users/dr.husam/tradingview-mcp-jackson && /usr/bin/caffeinate -i /usr/bin/python3 scripts/python/cycle_hunter.py run >> logs/cycles.log 2>&1 # EGX-CYCLE-HUNTER

# ── 🕸 CROSS-STOCK BRAIN — الأحد 06:00 القاهرة (03:00 UTC)
0 3 * * 0      cd /Users/dr.husam/tradingview-mcp-jackson && /usr/bin/caffeinate -i /usr/bin/python3 scripts/python/cross_stock_brain.py run >> logs/cross_stock.log 2>&1 # EGX-CROSS-STOCK

# ── 📈 EVOLUTION FULL WEEKLY — الأحد 22:00 القاهرة (19:00 UTC
```

## Cron log check (48h)

```

═══ EGX Cron Log Check ═══
Window: last 48h | Cairo: 2026-06-15
❌ 5 issue(s):
  [telegram] ═══ Telegram Cron FAILED ═══
  [telegram] ═══ Telegram Cron FAILED ═══
  [post_session] [NOTIFY_ALERT] {"ts":"2026-06-15T12:58:08.517Z","kind":"failure","event":"POST_SESSION_VERIFY_FAIL","date":"2026-06-15"}
  [full_verify] === Full Verify: 6/7 PASS ===
  [session_ready] === Session Ready (2026-06-11): 14/18 ===
[NOTIFY_ALERT] {"ts":"2026-06-15T20:06:44.479Z","kind":"failure","event":"CRON_LOG_FAILURES","hours":48,"count":5,"samples":[{"log":"telegram","line":"═══ Telegram Cron FAILED ═══"},{"log":"telegram","line":"═══ Telegram Cron FAILED ═══"},{"log":"post_session","line":"[NOTIFY_ALERT] {\"ts\":\"2026-06-15T12:58:08.517Z\",\"kind\":\"failure\",\"event\":\"POST_SESSION_VERIFY_FAIL\",\"date\":\"2026-06-15\"}"},{"log":"full_verify","line":"=== Full Verify: 6/7 PASS ==="},{"log":"session_ready","line":"=== Session Ready (2026-06-11): 14/18 ==="}]}


```

## Last runs

| Job | Status |
|-----|--------|
| health | WARN |
| full_cycle | PASS |

## Lock policy

- Separate locks per long job via `with_lock.mjs`
- Stale locks >6h flagged in `egx:health`
