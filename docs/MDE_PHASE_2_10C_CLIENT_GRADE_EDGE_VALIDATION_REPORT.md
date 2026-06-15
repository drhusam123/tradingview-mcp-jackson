# MDE Phase 2.10C — Client-Grade Edge Validation Report

**Generated:** 2026-06-15T01:51:07.214035+00:00

## Executive Verdict

RESEARCH EDGE ONLY — not yet client-grade

## Answers

**1. هل PF العالي حقيقي؟** — TF_CONF_ANALOG_PF2 gross_PF=35.3 top10_wins=94.9% → SUSPECT
**2. هل يصمد بعد dedup/costs/liquidity؟** — dedup@10d PF=73.38
**3. هل يوجد lookahead؟** — failures=0
**4. Client-grade families؟** — none fully passed
**5. أفضل triggers؟** — CLV>0.6
**6. أفضل exits؟** — EXIT_TARGET_5PCT
**7. أفضل hidden causes؟** — ['latent_accumulation', 'delayed_information_assimilation', 'supply_exhaustion']
**8. PRDC/OLFI بعد fusion؟** — PRDC=HIGH_QUALITY_PENDING_CONFIRMATION fusion=46.2 | OLFI=WATCH_ONLY fusion=34.7
**9. EFIC vs PRDC؟** — EFIC=WATCH_ONLY score=14.0 | PRDC score=27.5
**10. قابلية التنفيذ؟** — {'trades': 1032, 'net_PF_100bps': 1.04, 'win_rate': 28.2, 'median_return': -0.2}
**11. max capacity؟** — 0.5% ADV proxy — see execution audit
**12. نوع العميل؟** — conservative/balanced per suitability layer
**13. محفظة shadow؟** — {'total_return_proxy': 51409.67, 'months_active': 236, 'monthly_hit_rate': 60.2, 'avg_monthly_return': 217.84, 'Sharpe_proxy': 0.07}
**14. Edge قابل للعملاء لاحقًا؟** — CONDITIONAL — analog fusion + dedup must hold

## Family Acceptance

- TF_CONF_ANALOG_PF2: ACCEPT_SHADOW_TRADE_FAMILY net_PF@100bps=31.89
- TF_OUTSIDE_OPP: ACCEPT_SHADOW_TRADE_FAMILY net_PF@100bps=31.97
- TF_COMP_001A: WATCH_TRADE_FAMILY net_PF@100bps=1.81
- TF_COMP_001B: ACCEPT_SHADOW_TRADE_FAMILY net_PF@100bps=3.17

## Candidate Re-Rank

- VALU: None → REJECT fusion=50.9
- OFH: None → REJECT fusion=49.6
- PHDC: None → HIGH_QUALITY_PENDING_CONFIRMATION fusion=48.9
- KRDI: None → REJECT fusion=42.6
- MASR: None → REJECT fusion=39.8
- EHDR: WATCH_ONLY → WATCH_ONLY fusion=52.8
- ORHD: WATCH_ONLY → HIGH_QUALITY_PENDING_CONFIRMATION fusion=51.9
- ARVA: WATCH_ONLY → HIGH_QUALITY_PENDING_CONFIRMATION fusion=49.8
- AIH: WATCH_ONLY → HIGH_QUALITY_PENDING_CONFIRMATION fusion=48.2
- ASCM: WATCH_ONLY → HIGH_QUALITY_PENDING_CONFIRMATION fusion=47.6

```text
Shadow only. No client path.
```