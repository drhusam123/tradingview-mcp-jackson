# MDE Full-History Behavioral Mining Report (Phase 2.6)

**Generated:** 2026-06-12T21:34:35.941686+00:00

## 1. Full-History Coverage

- **total_events:** 58405
- **symbols:** 248
- **trade_dates:** 723
- **date_range:** 2022-04-05 → 2026-06-11
- **full_history:** True

## 2. Events Per Setup

- `absorption_pre_break`: **1250** events
- `impact_expansion`: **4784** events
- `sector_follower`: **6216** events
- `pullback_accum`: **1659** events
- `failed_breakdown`: **242** events
- `hidden_repricing`: **10596** events

## 3. Symbol-Level Repeated Behavior (top)

| symbol | setup | n | hit% | avg_5d% | PF |
|---|---|---:|---:|---:|---:|
| ANFI | sector_follower | 5 | 100.0 | 20.21 | 2.0 |
| SCFM | impact_expansion | 5 | 100.0 | 15.66 | 2.0 |
| ALEX | sector_follower | 4 | 100.0 | 20.17 | 2.0 |
| NBKE | pullback_accum | 4 | 100.0 | 15.58 | 2.0 |
| EPCO | impact_expansion | 4 | 100.0 | 13.32 | 2.0 |
| GRCA | absorption_pre_break | 4 | 100.0 | 13.0 | 2.0 |
| CEFM | impact_expansion | 4 | 100.0 | 50.0 | 2.0 |
| HBCO | absorption_pre_break | 3 | 100.0 | 8.44 | 2.0 |
| CPCI | impact_expansion | 3 | 100.0 | 7.95 | 2.0 |
| SCTS | impact_expansion | 8 | 87.5 | 21.71 | 48.48 |
| SMPP | impact_expansion | 6 | 83.3 | 27.32 | 30.5 |
| CAED | absorption_pre_break | 5 | 80.0 | 21.94 | 13.73 |
| PRMH | pullback_accum | 5 | 80.0 | 4.32 | 3.95 |
| SAIB | absorption_pre_break | 4 | 75.0 | 12.49 | 53.97 |
| FNAR | absorption_pre_break | 4 | 75.0 | 21.4 | 48.32 |
| MIPH | impact_expansion | 11 | 72.7 | 6.57 | 4.86 |
| ADRI | absorption_pre_break | 7 | 71.4 | 11.14 | 7.73 |
| EBSC | impact_expansion | 18 | 66.7 | 7.12 | 6.61 |
| NIPH | impact_expansion | 6 | 66.7 | 24.5 | 16.47 |
| LKGP | failed_breakdown | 3 | 66.7 | 4.26 | 3.96 |

## 4. Sector Clustering

| sector | syms | events | dominant | hit% | PF | bias |
|---|---:|---:|---|---:|---:|---|
| Finance | 80 | 18497 | sector_follower | 19.5 | 1.04 | liquidity_artifact_check |
| Process Industries | 40 | 9412 | sector_follower | 18.7 | 0.93 | normal |
| Non-Energy Minerals | 14 | 3336 | sector_follower | 24.1 | 1.1 | normal |
| Consumer Non-Durables | 12 | 2920 | sector_follower | 16.2 | 0.68 | normal |
| Industrial Services | 11 | 2669 | sector_follower | 22.9 | 1.04 | normal |
| Health Technology | 11 | 2668 | sector_follower | 24.2 | 1.39 | normal |
| Distribution Services | 11 | 2664 | sector_follower | 19.2 | 0.98 | normal |
| Producer Manufacturing | 10 | 2431 | sector_follower | 16.1 | 0.7 | normal |
| Consumer Services | 11 | 2403 | impact_expansion | 16.7 | 0.96 | normal |
| Unknown | 8 | 1733 | impact_expansion | 17.0 | 0.86 | normal |
| Consumer Durables | 7 | 1700 | sector_follower | 20.2 | 0.83 | normal |
| Technology Services | 6 | 1452 | impact_expansion | 23.3 | 1.1 | normal |
| Health Services | 6 | 1440 | sector_follower | 23.3 | 1.21 | normal |
| Retail Trade | 5 | 1216 | sector_follower | 17.9 | 0.94 | normal |
| Commercial Services | 4 | 958 | sector_follower | 20.0 | 1.09 | normal |

## 5. Behavior Families

- **A) Impact-sensitive stocks** (62): ['SMPP', 'OBRI', 'ICLE', 'MPCI', 'AMIA', 'NIPH']
- **B) Absorption-driven stocks** (73): ['GGRN', 'VERT', 'ADRI', 'SAIB', 'ISMA', 'GPIM']
- **C) Sector-follower stocks** (59): ['ANFI', 'MCQE', 'SUCE', 'CRST', 'ADCI', 'AMES']
- **D) Pullback-accumulation stocks** (46): ['TWSA', 'NBKE', 'PRMH', 'DCRC', 'APPC', 'RKAZ']
- **E) Spring / failed-breakdown stocks** (8): ['LKGP', 'EITP', 'EPPK', 'ELNA', 'BINV', 'EFIC']

## 6. Behavioral Rules

- **R1_hr_impact_rs_combo**: hidden_repricing + impact_expansion + positive sector RS outperforms hidden_repricing alone (enabled=False)
- **R2_impact_needs_volume**: impact_expansion without rel_turnover >= 1.0 is often an artifact (enabled=False)
- **R3_absorption_finance_cap**: absorption_before_breakout in Finance needs sector cap or confirmation (enabled=True)
- **R4_no_setup_hr_reject**: Family F: hidden_repricing without setup — reject or persistence gate (enabled=False)
- **R5_persistence_gate_global**: Require hidden_repricing on 2+ consecutive sessions before watch tier (enabled=True)

## 9. Recommended Engine Changes

- Store full history daily in egx_market_discovery_daily (done by this phase)
- Enable EGX_MDE_BEHAVIOR_MEMORY=1 only after 2+ weeks shadow with memory file stable
- Apply R1 combo boost in confidence when HR+impact+RS>0
- Apply R2 rel_turn gate on impact_expansion
- Apply R4/R5 persistence gates before watch tier
- Keep mde_boost_atoms=[] and EGX_MDE_OPP_BOOST=0

## Architectural Reminder

```text
Behavior memory is OFF by default (EGX_MDE_BEHAVIOR_MEMORY=0).
No veto. No suppression. No opp_v2/UES/promotion changes.
Phase 3 / EGX_MDE_OPP_BOOST remains OFF.
```
