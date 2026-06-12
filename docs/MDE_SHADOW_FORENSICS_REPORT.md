# MDE Shadow Forensics Report (Phase 2.5)

**Generated:** 2026-06-12T21:16:38.291782+00:00
**Latest MDE trade date:** 2026-06-11

## Executive Summary

- **mde_ran_on_symbols:** 201
- **ohlcv_universe:** 268
- **hidden_repricing_latest_day:** 35
- **stored_mde_dates_in_db:** 1
- **outside_opp_universe:** ['ARAB']
- **overlap_with_actionable:** 0
- **persistence_one_day_only_pct:** 1.6
- **phase3_ready:** False
- **reason:** mde_boost_atoms empty; single-day DB snapshot; persistence not proven
- **additive_guarantee:** zero changes to opp_v2/UES/promotion/Telegram/final_signals

## Data Coverage

| data_source | available | used | missing | coverage % |
|---|---:|---:|---:|---:|
| ohlcv_history | 268 | 201 | 0 | 100.0 |
| ohlcv_80plus_bars | 248 | 201 | 0 | 100.0 |
| stock_universe | 301 | 201 | 0 | 100.0 |
| financial_data | 267 | 201 | 0 | 100.0 |
| tv_discovery_features | 41 | 41 | 160 | 20.4 |
| pine_analytics | 203 | 201 | 0 | 100.0 |
| sector_stock_universe | 301 | 201 | 0 | 100.0 |
| closing_pressure_daily | 203 | 201 | 0 | 100.0 |

## Top Discoveries (Hidden Repricing)

- **ISMQ** (Non-Energy Minerals) eff=69.62 disc=69.62 setups=['impact_expansion'] flags=['in_opp_universe', 'in_final_signals']
- **OLFI** (Consumer Non-Durables) eff=66.37 disc=66.37 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **AJWA** (Consumer Non-Durables) eff=65.98 disc=65.98 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **FCMD** (Distribution Services) eff=65.66 disc=65.66 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **PRDC** (Finance) eff=64.97 disc=64.97 setups=['sector_follower', 'absorption_pre_break'] flags=['in_opp_universe', 'in_final_signals']
- **HBCO** (None) eff=64.77 disc=64.77 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **ACAMD** (Finance) eff=64.17 disc=64.17 setups=['pullback_accum'] flags=['in_opp_universe', 'in_final_signals']
- **AIFI** (Process Industries) eff=63.75 disc=63.75 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **BINV** (Finance) eff=62.47 disc=62.47 setups=['pullback_accum', 'sector_follower', 'impact_expansion'] flags=['in_opp_universe', 'in_final_signals']
- **RACC** (Commercial Services) eff=60.9 disc=60.9 setups=['pullback_accum', 'impact_expansion'] flags=['in_opp_universe', 'in_final_signals']
- **RAYA** (Technology Services) eff=60.43 disc=60.43 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **ASCM** (Non-Energy Minerals) eff=60.16 disc=60.16 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **AMIA** (Finance) eff=58.02 disc=62.38 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **MBSC** (Non-Energy Minerals) eff=57.46 disc=57.46 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **KABO** (Consumer Non-Durables) eff=57.45 disc=57.45 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **MPRC** (Consumer Services) eff=57.45 disc=57.45 setups=['pullback_accum', 'sector_follower'] flags=['in_opp_universe', 'in_final_signals']
- **EFIC** (Process Industries) eff=57.07 disc=57.07 setups=['failed_breakdown', 'absorption_pre_break'] flags=['in_opp_universe', 'in_final_signals']
- **ICID** (Finance) eff=56.21 disc=56.21 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **TWSA** (None) eff=56.11 disc=56.11 setups=[] flags=['in_opp_universe', 'in_final_signals']
- **AMER** (Finance) eff=54.8 disc=54.8 setups=['impact_expansion'] flags=['in_opp_universe', 'in_final_signals']

## Persistence Analysis

- Backfill window: **2026-03-08** → **2026-06-11** (60 dates)
- Unique hidden-repricing symbols in backfill: **246**
- One day only: **4** | 2–3 days: **22** | 4+ days: **220**

| symbol | days | consecutive | first | last | setups |
|---|---:|---:|---|---|---|
| CIRA | 28 | 4 | 2026-03-09 | 2026-06-01 | absorption_pre_break, impact_expansion, pullback_accum, sector_follower |
| RAKT | 26 | 3 | 2026-03-09 | 2026-05-21 | absorption_pre_break, impact_expansion, pullback_accum, sector_follower |
| PRDC | 24 | 5 | 2026-03-24 | 2026-06-11 | absorption_pre_break, impact_expansion, pullback_accum, sector_follower |
| SAUD | 22 | 2 | 2026-03-08 | 2026-06-04 | failed_breakdown, impact_expansion, sector_follower |
| ROTO | 22 | 5 | 2026-03-10 | 2026-05-10 | absorption_pre_break, impact_expansion, sector_follower |
| FNAR | 21 | 5 | 2026-03-08 | 2026-06-04 | absorption_pre_break, impact_expansion, pullback_accum, sector_follower |
| LKGP | 21 | 2 | 2026-03-09 | 2026-06-08 | absorption_pre_break, failed_breakdown, impact_expansion, pullback_accum, sector_follower |
| CERA | 21 | 5 | 2026-03-11 | 2026-05-19 | absorption_pre_break, impact_expansion, sector_follower |
| KABO | 21 | 4 | 2026-03-11 | 2026-06-11 | absorption_pre_break, impact_expansion, sector_follower |
| SPIN | 21 | 5 | 2026-03-12 | 2026-05-24 | absorption_pre_break, failed_breakdown, impact_expansion, pullback_accum, sector_follower |
| WCDF | 20 | 5 | 2026-03-08 | 2026-05-03 | absorption_pre_break, impact_expansion, pullback_accum, sector_follower |
| AMPI | 19 | 5 | 2026-03-08 | 2026-06-10 | absorption_pre_break, impact_expansion, pullback_accum, sector_follower |
| EFIC | 18 | 3 | 2026-03-08 | 2026-06-11 | absorption_pre_break, failed_breakdown, impact_expansion, pullback_accum, sector_follower |
| MOIN | 18 | 3 | 2026-03-08 | 2026-05-03 | absorption_pre_break, failed_breakdown, impact_expansion, pullback_accum, sector_follower |
| NEDA | 18 | 4 | 2026-03-08 | 2026-05-21 | impact_expansion, pullback_accum, sector_follower |

## Sector Clustering

| sector | count | avg_eff | dominant_setup | avg_turnover_20d |
|---|---:|---:|---|---:|
| Finance | 15 | 52.62 | pullback_accum | 77,715,709 |
| Unknown | 4 | 50.48 | impact_expansion | 7,977,842 |
| Non-Energy Minerals | 3 | 62.41 | impact_expansion | 67,815,359 |
| Consumer Non-Durables | 3 | 63.27 | - | 21,362,440 |
| Process Industries | 3 | 54.07 | failed_breakdown | 13,288,193 |
| Distribution Services | 1 | 65.66 | - | 21,793,549 |
| Commercial Services | 1 | 60.9 | pullback_accum | 16,039,634 |
| Technology Services | 1 | 60.43 | - | 90,938,953 |
| Consumer Services | 1 | 57.45 | pullback_accum | 24,033,013 |
| Producer Manufacturing | 1 | 52.81 | pullback_accum | 36,142,782 |
| Industrial Services | 1 | 49.53 | - | 22,940,117 |
| Miscellaneous | 1 | 40.6 | absorption_pre_break | 2,806,138 |

## Family Classification

- **A) Impact Expansion Family** (7): ['ISMQ', 'BINV', 'RACC', 'AMER', 'FAIT'] — follow=watch
- **B) Sector Follower Family** (2): ['PRDC', 'MPRC'] — follow=watch
- **C) Absorption Before Breakout Family** (3): ['EFIC', 'EGREF', 'EOSB'] — follow=watch
- **D) Pullback Accumulation Family** (2): ['ACAMD', 'ARVA'] — follow=watch
- **E) Hidden Repricing Multi-Signal Family** (3): ['OLFI', 'NARE', 'EASB'] — follow=watch
- **F) False/Weak Discovery Family** (18): ['AJWA', 'FCMD', 'HBCO', 'AIFI', 'RAYA'] — follow=reject_or_gate

## Risks / Biases

- **[low]** thin_liquidity_bias: 4/35 hidden repricing near/below 2M EGP liquidity gate; 4 micro-cap by turnover proxy
- **[medium]** impact_expansion_low_volume: 7 names show impact_expansion>1.2 with rel_turn<0.8 (possible illiquidity artifact)
- **[high]** single_day_snapshot: DB has 1 stored MDE date; backfill shows 1.6% hidden-repricing symbols appear only 1 day
- **[medium]** finance_sector_pf_concentration: Atom OOS hits skewed to Finance sector (see atom concentration finance_sector_share_pct)
- **[low]** confidence_score_ceiling: Many hidden_repricing names at confidence=100 despite missing TV/pine on subset
- **[medium]** hidden_repricing_no_persistence_gate: hidden_repricing fires on 2+ intraday signals same day — no multi-day confirmation in v1

## Overlap With Existing System

- Hidden repricing: **35**
- Overlap opp universe: **34**
- Outside opp universe: **['ARAB']**
- New vs actionable: **35** symbols

## Atom Performance

- **mde_hidden_repricing** n=410 lift=1.278 PF=1.29 hit=28.5% | balanced_symbols=True finance_share=46.2%
- **mde_impact_expansion_candidate** n=244 lift=1.413 PF=1.36 hit=31.6% | balanced_symbols=True finance_share=49.4%
- **mde_sector_follower** n=186 lift=1.156 PF=1.27 hit=25.8% | balanced_symbols=True finance_share=43.8%
- **mde_absorption_before_breakout** n=73 lift=1.288 PF=1.75 hit=28.8% | balanced_symbols=True finance_share=61.9%

## Recommendations Before Phase 3

- **[high]** track_outside_opp_symbols: Paper-track outside-opp discoveries: ['ARAB']
- **[high]** persistence_gate: Require hidden_repricing on 2+ consecutive sessions before watch-tier promotion
- **[medium]** sector_concentration_cap: Cap Finance-sector MDE watch entries to ≤40% per day until cross-sector stability proven
- **[medium]** liquidity_floor_review: Consider raising avg_turnover_20d floor to 3M EGP for hidden_repricing flag
- **[low]** confidence_minimum: Require confidence≥85 only when fundamentals+TV present; penalize missing data harder
- **[high]** exclude_one_day_only: Filter backfill one-day-only symbols (4 symbols) from watch manifest
- **[medium]** split_liquid_vs_small_cap_tracks: Tag discoveries as liquid_track vs small_cap_track before any future boost
- **[critical]** no_phase3_until: mde_boost_atoms=[] — do not enable EGX_MDE_OPP_BOOST until Production tier + multi-week stability

## Architectural Reminder

```text
MDE remains strictly additive. No veto. No suppression. No negative boost.
No opp_v2 / UES / promotion / Telegram / final_signals changes.
mde_priority_atoms = shadow evidence only. mde_boost_atoms = [] → Phase 3 OFF.
```
