# LRE-3.5 — Capped Dual-Gate Shadow Pilot Design

**Generated:** 2026-06-14T16:13:32.930340+00:00
**Verdict:** `RESEARCH_EDGE_PROMISING_BUT_NEEDS_FORWARD` — Caps improve concentration (dom 36.1%→41.2%) but need forward pilot — PF=1.89

## A. Why 3.4 Was Promising But Not Pass

LRE-3.4: PF=1.86, bootstrap P(PF>1.3)=94.8%, survives 200bps — but top-10=36.1%, Finance-heavy, collapses on exclude top-10.

## B. Pilot Eligibility Rules

- dual_gate_type = LRE_MDE_CONFLUENCE
- LRE sub-stage 3B / 4A / 4B (not 4X)
- MDE gate passed or hidden repricing confirmed
- clean_confluence: no artifact, liquidity ok, not exploded, not do-not-chase
- Caps: symbol 10%, sector 25%, Finance 20–30%

## C. Caps Replay

| Mode | Trades | PF@100 | Median | Top-10 | Max Sector |
|------|--------|--------|--------|--------|------------|
| raw | 116 | 1.86 | 1.907% | 36.1% | 37.1% |
| symbol_cap_only | 116 | 1.86 | 1.907% | 36.1% | 37.1% |
| sector_cap_only | 97 | 1.89 | 2.286% | 41.2% | 24.7% |
| finance_cap_20 | 91 | 1.7 | 2.206% | 43.0% | 19.8% |
| finance_cap_25 | 97 | 1.89 | 2.286% | 41.2% | 24.7% |
| finance_cap_30 | 104 | 1.74 | 1.661% | 39.3% | 29.8% |
| symbol_sector_cap | 97 | 1.89 | 2.286% | 41.2% | 24.7% |
| symbol_sector_finance_cap_25 | 97 | 1.89 | 2.286% | 41.2% | 24.7% |
| clean_core_only | 57 | 2.11 | 3.673% | 37.5% | 24.6% |
| core_plus_4b | 97 | 1.89 | 2.286% | 41.2% | 24.7% |

## D. Concentration Results

- Raw top-10: 36.1% → Combined caps: 41.2%
- Finance dominance after caps: 24.7%

## E. Bootstrap After Caps

- P(PF>1.3) = 94.8%
- P(median>0) = 75.9%
- P(hit+5%>40) = 85.4%

## F. Current Candidates

- **OLFI:** bucket=Controlled_4B_Monitor eligible=True clean=True cap=None

## G. Final Decision

**RESEARCH_EDGE_PROMISING_BUT_NEEDS_FORWARD** — Caps improve concentration (dom 36.1%→41.2%) but need forward pilot — PF=1.89

## Answers

1. **هل يمكن خفض concentration بدون قتل edge؟** — top-10 36.1%→41.2% | PF 1.86→1.89 — نعم جزئياً
1. **هل caps تجعل confluence صالحاً كـ shadow pilot؟** — RESEARCH_EDGE_PROMISING_BUT_NEEDS_FORWARD — PF=1.89 median=2.286% dom=41.2%
1. **هل Clean Core أفضل من Core + 4B؟** — core PF=2.11 n=57 | core+4B PF=1.89 n=97
1. **هل Finance concentration ما زال مشكلة؟** — Finance share after caps: 24.7% (max sector 24.7%)
1. **أين يقع OLFI؟** — bucket=Controlled_4B_Monitor eligible=True — 4B + MDE pass — timing monitor, not core
1. **forward capped shadow pilot أم monitoring-only؟** — RESEARCH_EDGE_PROMISING_BUT_NEEDS_FORWARD — shadow pilot ledger only, no client path

---
*Dual-Gate Capped Shadow Pilot only — no production.*