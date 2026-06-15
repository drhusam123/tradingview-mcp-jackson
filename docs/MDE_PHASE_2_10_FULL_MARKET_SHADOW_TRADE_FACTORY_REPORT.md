# MDE Phase 2.10 — Full-Market Shadow Trade Factory Report

**Generated:** 2026-06-13T00:51:34.142504+00:00
**History:** 2022-04-05 → 2026-06-10
**Universe:** 248 symbols | 58405 MDE events | 49167 trade candidates

## Executive Answers

**1. هل MDE ينتج صفقات Shadow مربحة تاريخيًا؟** — نعم — 49167 صفقة، أفضل PF=35.3 (TF_CONF_ANALOG_PF2)
**2. ما أفضل عائلات الصفقات؟** — TF_CONF_ANALOG_PF2 PF=35.3, TF_OUTSIDE_OPP PF=35.3, TF_CONF_LIQUIDITY PF=23.22
**3. ما أفضل الفريمات؟** — Daily (MDE native)
**4. ما أفضل holding windows؟** — EXIT_TARGET_5PCT
**5. ما أفضل triggers؟** — TF_CONF_ANALOG_PF2
**6. ما أفضل exits؟** — EXIT_TARGET_5PCT
**7. ما القطاعات الأكثر استجابة؟** — Consumer Non-Durables, Miscellaneous, Energy Minerals, Health Technology, Unknown
**8. أين يفشل MDE؟** — TF_WATCH بدون confirmation
**9. الفرص الحالية؟** — 2 ready, 0 waiting
**10. فرص MDE-only؟** — 0
**11. هل تصمد بعد التكلفة؟** — PF net @50bps=33.44
**12. ACCEPT_SHADOW_TRADE_FAMILY؟** — TF_CONF_ANALOG_PF2, TF_OUTSIDE_OPP

## Best Trade Families

- **TF_CONF_ANALOG_PF2** (WATCH + conf + analog PF>2): trades=1969 WR=34.1% PF=35.3 status=ACCEPT_SHADOW_TRADE_FAMILY
- **TF_OUTSIDE_OPP** (Outside-opp + conf + analog PF>2): trades=1947 WR=34.5% PF=35.3 status=ACCEPT_SHADOW_TRADE_FAMILY
- **TF_CONF_LIQUIDITY** (WATCH + conf + REAL_LIQUIDITY): trades=7559 WR=28.8% PF=23.22 status=WATCH_TRADE_FAMILY
- **TF_CONF_HIDDEN_CAUSE** (WATCH + conf + hidden_cause_conf>=50): trades=7686 WR=28.9% PF=23.02 status=WATCH_TRADE_FAMILY
- **TF_WATCH_CONF** (WATCH + confirmation): trades=7689 WR=28.9% PF=23.01 status=WATCH_TRADE_FAMILY
- **TF_WATCH** (WATCH only): trades=49167 WR=26.5% PF=22.4 status=WATCH_TRADE_FAMILY
- **TF_CONF_METAORDER** (WATCH + conf + metaorder>=50): trades=5281 WR=28.5% PF=15.49 status=WATCH_TRADE_FAMILY
- **TF_MID_LIQ_HR** (Mid-liquidity HR + conf): trades=789 WR=26.7% PF=9.43 status=WATCH_TRADE_FAMILY

## Timeframe Coverage

- Daily: 269 symbols, MDE overlap 100.0%
- Weekly: 269 symbols, MDE overlap 100.0%
- 60m: 95 symbols, MDE overlap 37.9%
- 15m: 95 symbols, MDE overlap 37.9%

## Current Shadow Plans

- AIFI: WATCH_ONLY family=TF_CONF_LIQUIDITY score=62.5
- AIH: WATCH_ONLY family=TF_CONF_LIQUIDITY score=62.5
- FCMD: WATCH_ONLY family=TF_CONF_LIQUIDITY score=62.5
- OLFI: WATCH_ONLY family=TF_CONF_LIQUIDITY score=62.5
- PRDC: WATCH_ONLY family=TF_CONF_LIQUIDITY score=62.5
- ALCN: WATCH_ONLY family=TF_CONF_LIQUIDITY score=58.8
- EFIC: SHADOW_TRADE_READY family=TF_CONF_ANALOG_PF2 score=55.6
- ISMQ: WATCH_ONLY family=TF_CONF_LIQUIDITY score=42.5
- EOSB: SHADOW_TRADE_READY family=TF_CONF_ANALOG_PF2 score=40.0
- SDTI: WATCH_ONLY family=TF_CONF_LIQUIDITY score=38.5

```text
Shadow only. No client path. No real trades.
```
