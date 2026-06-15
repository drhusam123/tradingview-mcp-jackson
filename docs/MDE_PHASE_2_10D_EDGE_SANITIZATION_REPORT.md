# MDE Phase 2.10D — Edge Sanitization Report

**Generated:** 2026-06-13T01:04:33.476620+00:00

**Verdict:** RESEARCH EDGE — client-grade not yet proven

## Answers

**1. كم PF كان artifact؟** — 3.3% trades flagged; raw_PF=22.4 → sanitized_PF=1.13
**2. PF الحقيقي بعد التنظيف؟** — sanitized=1.13, winsorized_95=1.17, tradeable=1.02
**3. COMP_001B يصمد؟** — True — PF=2.03
**4. PRDC-class يصمد؟** — False median=-0.159
**5. family client-grade؟** — none
**6. research أم قابل للإنقاذ؟** — RESEARCH EDGE — client-grade not yet proven
**7. أفضل مرشح؟** — PRDC (HIGH_QUALITY_PENDING_CONFIRMATION) score=74.6
**8. رفض نهائي؟** — ARAB/EOSB ghost liquidity, extreme artifact trades
**9. Shadow فقط؟** — TF_CONF_ANALOG_PF2 raw PF — REJECT_AS_BACKTEST_ARTIFACT after sanitize
**10. الخطوة التالية؟** — Forward paper-trading COMP_001B + PRDC-class 60d

## Family Rescue

- TF_CONF_ANALOG_PF2: WATCH_RESCUE sanitized_PF=1.37
- TF_OUTSIDE_OPP: WATCH_RESCUE sanitized_PF=1.37
- TF_COMP_001A: ACCEPT_RESEARCH_SHADOW_FAMILY sanitized_PF=2.16
- TF_COMP_001B: ACCEPT_RESEARCH_SHADOW_FAMILY sanitized_PF=2.03
- PRDC_CLASS: REJECT_AS_BACKTEST_ARTIFACT sanitized_PF=0.96
- SAME_SYMBOL_ANALOG: WATCH_RESCUE sanitized_PF=1.37

## Top Candidates

- PRDC: HIGH_QUALITY_PENDING_CONFIRMATION fusion=52.2
- OLFI: REJECT fusion=33.9
- AIH: HIGH_QUALITY_PENDING_CONFIRMATION fusion=45.7
- AIFI: RESEARCH_ONLY fusion=41.8
- ISMQ: RESEARCH_ONLY fusion=41.3
- FCMD: RESEARCH_ONLY fusion=40.3
- ALCN: RESEARCH_ONLY fusion=38.2
- SDTI: RESEARCH_ONLY fusion=38.4

```text
Shadow only. No client path.
```