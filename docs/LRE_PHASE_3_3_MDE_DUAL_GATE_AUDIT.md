# LRE-3.3 — LRE × MDE Dual-Gate Observe-Only Audit

**Generated:** 2026-06-13T03:38:05.780886+00:00
**Verdict:** `RESEARCH_EDGE_MONITOR_ONLY` — Confluence OOS strong (PF=1.86, median=1.907%) but top-10 dominance 36.1% > 35%

## A. Why LRE Alone Failed

LRE-3.0: PF@100=0.96, stop=61% — not trade gate. LRE-3.1: conservative PF=1.15 OOS but top-10 dominance blocked. LRE-3.2: RESEARCH_EDGE_MONITOR_ONLY — 3B/4A strongest; 4X misleading; 56% stop hits had MFE≥5% later; pair LRE radar with MDE confirmation.

## B. Dual-Gate Design

- **LRE** = liquidity rotation radar (3A/3B/4A/4B/4X sub-stages)
- **MDE** = hidden repricing / absorption confirmation (COMP_001B, hidden_repricing)
- **Why combine:** LRE sees transition early; MDE confirms repricing — hypothesis from LRE-3.2
- **Observe-only:** No promotion, actionable, Telegram, boost, veto, or final_signals changes

## C. Group Comparison (OOS 2025+)

| Group | Trades | PF@100 | Median | Stop% | Hit+5% |
|-------|--------|--------|--------|-------|--------|
| LRE_ONLY | 2758 | 1.77 | -1.336% | 38.7% | 27.3% |
| MDE_ONLY | 522 | 1.47 | -1.0% | 12.6% | 38.5% |
| LRE_MDE_CONFLUENCE | 116 | 1.86 | 1.907% | 31.0% | 44.0% |
| LRE_REJECTED_MDE_PASS | 122 | 733.11 | 2.027% | 17.2% | 43.4% |
| LRE_PASS_MDE_REJECTED | 938 | 15.09 | -1.798% | 44.6% | 30.6% |

## D. Sequence Audit (OOS)

| Sequence | Trades | PF@100 | Median | Stop% |
|----------|--------|--------|--------|-------|
| LRE_LEADS_MDE_CONFIRMATION | 377 | 169.6 | 2.118% | 17.8% |
| LRE_FIRST_THEN_MDE | 377 | 169.6 | 2.118% | 17.8% |
| MDE_FIRST_THEN_LRE | 86 | 5.95 | -2.087% | 38.4% |
| SAME_DAY_CONFLUENCE | 116 | 1.86 | 1.907% | 31.0% |
| LRE_WITHOUT_MDE | 1374 | 1.18 | -2.421% | 49.8% |
| MDE_WITHOUT_LRE | 509 | 101.66 | -0.524% | 11.6% |

**Best sequence (OOS, sanitized top10<35%):** SAME_DAY_CONFLUENCE

> **تحذير:** LRE→MDE sequence يظهر PF=169.6 لكن top-10 dominance=99.1% — outlier contamination، لا يُعتمد.

## E. OOS Results

- **same_day_close:** confluence trades=116 PF=1.86 median=1.907%
- **next_day_not_extended:** confluence trades=99 PF=1.27 median=-0.258%

## F. Timing & Stop Diagnostic

- MDE confirmation entry vs LRE same-day: —
- no_stop 20d edge at MDE confirmation: —
- stop_hit change: —

## G. Candidate Review (2026-06-11)

- **OLFI:** sub=4B type=LRE_MDE_CONFLUENCE score=74.0 MDE=1 confluence=True monitoring_only=False
- **HBCO:** sub=3A type=LRE_ONLY score=41.8 MDE=0 confluence=False monitoring_only=True
- **EFIC:** sub=4X type=LRE_ONLY score=18.5 MDE=0 confluence=False monitoring_only=True
- **EGAS:** sub=4B type=LRE_ONLY score=34.1 MDE=0 confluence=False monitoring_only=True

## H. Final Decision

**RESEARCH_EDGE_MONITOR_ONLY** — Confluence يتحسن vs singles لكن top-10 dominance يمنع shadow pilot graduation

## Answers

1. **هل اجتماع LRE و MDE أفضل من كل واحد وحده؟** — Confluence OOS PF=1.86 median=+1.907% vs LRE_ONLY PF=1.77 median=-1.336% vs MDE_ONLY PF=1.47 median=-1.0% — **نعم على PF و median، لكن ليس بفارق كافٍ للبوابة**
1. **هل LRE يجب أن يسبق MDE أم العكس؟** — same-day confluence PF=1.86 (موثوق) | LRE→MDE PF=169.6 dom=99.1% (غير موثوق) | MDE→LRE PF=5.95 dom=79.3% — **same-day confluence هو الأفضل الموثوق**
1. **هل الدخول عند MDE confirmation يحل مشكلة timing؟** — LRE_LEADS_MDE stop=17.8% vs same-day stop=31.0% — **تحسن stop لكن PF مضلل بسبب outliers**
1. **هل stop hits تنخفض؟** — Confluence stop=31.0% | LRE_ONLY=38.7% | MDE_ONLY=12.6% — **نعم vs LRE_ONLY، لكن MDE_ONLY أقل**
1. **هل OLFI/HBCO/EFIC/EGAS يملكون confluence حقيقي؟** — **OLFI فقط** (4B+MDE) | HBCO=3A exploded | EFIC=4X | EGAS=4B exploded
1. **هل dual-gate يبقى monitoring-only أم يستحق shadow pilot؟** — **monitoring-only** — لا PASS_DUAL_GATE_SHADOW (dominance 36.1%)

---
*Shadow dual-gate pilot only — no production step.*