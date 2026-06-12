# EGX Market Discovery Engine (MDE)

**Version:** 0.1 — Discovery Brain First  
**Status:** Phase 1 implemented — shadow output only (`egx_market_discovery_engine.py`)

---

## Identity (unchanged)

```text
MDE = additive discovery engine
    discovers opportunities BEFORE repricing / explosion
    does NOT replace UES, promotion, or Telegram path
    does NOT hard-veto existing signals in v1
```

**Golden rule:** Client delivery only via `final_signals.actionable=1` + safety + prep.

```text
Build the discovery brain → Prove in shadow → Connect safely to the machine
```

---

# Part 1 — Discovery Brain

## 1.1 What is a pre-explosion opportunity?

A stock is a **pre-explosion candidate** when the market has begun **hidden repricing** — a shift in the relationship between flow, liquidity, and price — **before** a visible breakout.

```text
Explosion ≠ cheap stock
Explosion = Fundamental Mispricing
          × Liquidity Shift
          × Impact Expansion
          × Supply Exhaustion
          × Institutional Accumulation
          × Catalyst Probability
```

### Pre-Explosion Probability (conceptual)

```text
Pre_Explosion_Probability =
    Mispricing
  × Liquidity_Shift
  × Impact_Expansion
  × Absorption
  × Supply_Exhaustion
  × Latent_Accumulation
  × Resilience_Failure
  × Sector_Confirmation
  × Catalyst_Probability
  ÷ Manipulation_Risk
  ÷ Balance_Sheet_Risk
  ÷ Extension_Risk
```

**Hidden Repricing** = at least two of:

- Kyle λ rising while price is flat or gently rising (same flow moves price more)
- Cumulative signed flow positive while range compresses
- Absorption: high turnover, small return, strong CLV
- Supply exhaustion: failed breakdowns, lower wicks, down-volume weakness
- Resilience failure of sellers: shock up, no follow-through down

---

## 1.2 Layer map (discovery logic, not pipeline)

| Layer | Question MDE answers | Core signals |
|-------|---------------------|--------------|
| L1 Fundamental Repricing | Why *might* the market reprice? | PE/PB/ROE/FCF vs sector, growth, debt |
| L2 Liquidity Regime | Is money entering? | Turnover, relative turnover, EGP volume |
| L3 Kyle λ / Impact | Does each EGP move price more? | λ_daily, impact expansion λ_5/λ_60 |
| L4 Absorption | Is offer being absorbed? | Turnover/|return|, CLV, close near high |
| L5 VPIN / Toxicity proxy | Is flow one-sided & stressful? | Volume imbalance from CLV split |
| L6 Resilience | After shock, do sellers recover? | Shock day + next-day recovery test |
| L7 Latent Accumulation | Hidden build before breakout? | CSF, OBV slope, rising lows, compression |
| L8 Sector | Leader or follower with sector tailwind? | RS vs EGX100, sector breadth, peer RS |
| L9 Technical Trigger | Entry timing (not discovery alone) | Breakout, pullback, spring, follower |
| L10 Risk / Trap filters | Is this real or a trap? | Liquidity, debt, manipulation, extension |

---

## 1.3 How we read liquidity–price relationship

### Daily return & turnover

```text
Return_d     = (Close_d - Close_{d-1}) / Close_{d-1}
Turnover_d   = Close_d × Volume_d
Rel_Turn_d   = Turnover_d / Avg(Turnover_20D)
```

| Rel_Turn | Reading |
|----------|---------|
| > 1.5 | Notable liquidity entry |
| > 2.0 | Unusual activity |
| > 3.0 | Possible liquidity shock |

**Hidden repricing signal:** Rel_Turn rising 3–5 days, |Return| still small, CLV improving.

---

## 1.4 Kyle Lambda & Impact Expansion

### Kyle proxy (EGX daily)

```text
Kyle_Lambda_Scaled_d = |Return_d| / (Turnover_d / 1,000,000)
```

Interpretation: *how much price moves per 1M EGP traded*.

| Pattern | Meaning |
|---------|---------|
| Price ↑ + Turnover ↑ + λ ↑ | Each buy unit moves price more → weak offer / repricing |
| Price ↓ + λ ↑ | Downside fragility / distribution |
| Price flat + λ ↑ + CSF ↑ | **Classic hidden repricing** |

### Impact expansion

```text
Impact_Expansion = Kyle_Lambda_5D / Kyle_Lambda_60D
```

| Value | Reading |
|-------|---------|
| > 1.2 | Impact beginning to expand |
| > 1.5 | Strong fragility / repricing |
| > 2.0 | High liquidity shock risk |

Direction matters: expansion **with** positive signed flow = opportunity; **with** negative flow = trap.

---

## 1.5 Signed flow (without tick data)

```text
VWAP_proxy_d   = (High_d + Low_d + Close_d) / 3
Signed_Flow_d  = sign(Close_d - VWAP_proxy_d) × Turnover_d
CSF_N          = Σ Signed_Flow over N days  (N = 5, 10, 20, 60)
```

Enhancement when available: TV `cvd`, `vwap_reclaim`, `absorption` from `tv_discovery_features`.

| Pattern | Reading |
|---------|---------|
| CSF ↑, price flat | Absorption / accumulation |
| CSF ↑, price breaks | Markup beginning |
| CSF ↓, price flat | Hidden distribution |

---

## 1.6 Absorption engine

```text
CLV_d = (Close_d - Low_d) / max(High_d - Low_d, ε)
Absorption_Ratio_d = Turnover_d / max(|Return_d|, 0.002)
```

**Institutional absorption signal:**

```text
Rel_Turn > 1.5
AND |Return| < ATR_normalized_small
AND CLV > 0.60
AND Price > 20D_Low
```

Distinguish from fake liquidity: high turnover + CLV < 0.35 near lows = distribution, not absorption.

---

## 1.7 Supply exhaustion

**Downside follow-through failure:**

```text
Low_d < Low_{d-1}
AND Close_d > Close_{d-1}
AND CLV_d > 0.60
```

**Wick absorption:**

```text
Lower_Wick_Ratio = (min(Open,Close) - Low) / (High - Low)
Signal: Lower_Wick_Ratio > 0.45 AND Rel_Turn > 1.3 AND Close > Open
```

Supply exhaustion = sellers fail to continue; often precedes EGX breakouts.

---

## 1.8 VPIN proxy & fake vs real liquidity

```text
Buy_Vol_d  = Volume_d × CLV_d
Sell_Vol_d = Volume_d × (1 - CLV_d)
Vol_Imb_d  = |Buy_Vol_d - Sell_Vol_d| / Volume_d
VPIN_Proxy_N = Avg(Vol_Imb_d over N days)
```

| VPIN + context | Reading |
|----------------|---------|
| Rising + price up + CLV strong | Buy pressure stressing liquidity providers |
| Rising + price down | Sell toxicity |
| High + flat price | Battle — needs confirmation |

**Fake liquidity trap:** wide spread (DOM), high turnover on tiny range, VPIN high but no CSF persistence → penalize in risk layer.

---

## 1.9 Resilience failure (Obizhaeva-Wang spirit)

**Shock day:**

```text
Rel_Turn > 2.0 OR True_Range > 1.5 × ATR_20
```

**Positive resilience failure (bullish):**

```text
Shock up day
AND next day Low > mid-range of shock day
AND Close_{d+1} >= Close_d × 0.98
→ sellers could not regain control
```

**Negative resilience (trap):**

```text
Shock up day
AND Close_{d+1} < Low of shock day
→ breakout failure / distribution
```

---

## 1.10 Latent accumulation

```text
Accumulation_Score_raw =
  Z(CSF_20D) + Z(Rel_Turn_20D) + Z(OBV_Slope_20D) + Z(CLV_Avg_20D)
  - Z(Downside_Volatility_20D)
```

Strong accumulation conditions:

```text
CSF_20D > 0
OBV_Slope_20D > 0
Price > MA20
Price not extended > 20% from MA50
Drawdown from 60D high < 15%
```

**Real vs fake accumulation:**

| Real | Fake |
|------|------|
| CSF persistent, range compresses | One-day volume spike, no CSF |
| Down days on low volume | Up days on declining volume |
| λ rises with flat price | λ spikes only on manipulation days |
| Sector peers stabilizing | Isolated thin name |

---

## 1.11 Compression before expansion

```text
BB_Width = (Upper_BB - Lower_BB) / MA20
ATR_Compression = ATR_14 / Avg(ATR_14, 60D)
```

Best pre-explosion state:

```text
ATR compressed (< 0.75)
+ Turnover rising
+ OBV rising
+ Price sideways
= quiet accumulation, not dead stock
```

---

## 1.12 Sector confirmation

```text
RS_Stock_vs_EGX100  = Return_20D_Stock - Return_20D_EGX100
RS_Sector_vs_EGX100 = Return_20D_Sector - Return_20D_EGX100
```

Strong: Stock RS > 0, Sector RS > 0, Stock RS improving.

**Leader–follower:** sector leader broke out; follower has accumulation but still below prior high.

---

## 1.13 Fundamentals & catalyst (partial in v1)

**Fundamental repricing (where `financial_data` exists):**

```text
Value:   sector-relative PE, PB, FCF yield, dividend
Quality: ROE, margin stability, OCF stability, net debt
Growth:  revenue/EPS/FCF growth, margin expansion
```

**Catalyst (v1 placeholder, expand later):**

```text
earnings surprise, dividend, index inclusion, FX benefit,
sector tailwind, strategic/insider action, asset revaluation
```

Fundamentals answer *why* repricing may be justified; microstructure answers *whether it has started*.

---

## 1.14 Trap & risk exclusion (discovery-side only)

These filters decide **MDE discovery eligibility** — they do **not** block symbols from the legacy UES path.

| Gate | Rule (v1 defaults) |
|------|-------------------|
| Liquidity | Avg Turnover 20D ≥ 2M EGP (medium portfolio) |
| Dead stock | Too many zero-volume days; erratic turnover |
| Balance sheet | Severe D/E, chronic negative OCF (when data exists) |
| Manipulation | Thin float + spike without CSF; vague catalyst only |
| Extension | Price > MA50 +25%; 20D return > 40% parabolic |

MDE gate fail → symbol not discovered **by MDE**; may still qualify elsewhere.

---

# Part 2 — Scoring

Scoring comes **after** the discovery brain is defined.

## 2.1 Layer scores (0–100 each)

Computed from Part 1 logic:

```text
fundamental_repricing_score
liquidity_regime_score
price_impact_score          # Kyle + impact expansion
absorption_score
supply_exhaustion_score
vpin_proxy_score
resilience_score
latent_accumulation_score
sector_rotation_score
catalyst_score              # partial v1
technical_trigger_score
```

## 2.2 Discovery Score (v1 manual weights)

```text
Discovery_Score =
  0.18 × Fundamental
+ 0.14 × Accumulation
+ 0.12 × Impact
+ 0.10 × Absorption
+ 0.10 × Supply_Exhaustion
+ 0.08 × VPIN
+ 0.08 × Resilience
+ 0.08 × Sector
+ 0.07 × Catalyst
+ 0.05 × Technical
```

Weights are **v1 manual** → v2 OOS-calibrated → v3 sector/regime-adjusted.  
Stored as `weights_version` per run.

## 2.3 Confidence Score (evidence quality)

```text
Confidence =
  Data_Completeness         # OHLCV depth, financial_data, TV features
+ Liquidity_Stability
+ OOS_Sample_Size         # when atom-linked
+ Sector_Confirmation
+ Signal_Repeatability    # multi-day persistence
- Missing_Fundamental
- Thin_Trading_Penalty
```

```text
Effective_Score = Discovery_Score × (Confidence_Score / 100)
```

| Discovery | Confidence | Action in shadow |
|-----------|------------|------------------|
| 84 | 52 | Log only — weak evidence |
| 78 | 88 | Strong shadow candidate |
| 82 | 75 | Institutional shadow candidate |

## 2.4 MDE Stage classification

| Stage | Conditions |
|-------|------------|
| **INSTITUTIONAL_DISCOVERY** | Discovery ≥ 80, Effective ≥ 70, all MDE gates pass |
| **WATCH_TO_BUY** | Discovery 70–79, Effective ≥ 60, trigger pending |
| **EARLY_ACCUMULATION** | Discovery 60–69, accumulation layer ≥ 65 |
| **REJECT** | Discovery < 60 OR critical gate fail |

Stages are **discovery labels** — not client buy signals.

---

# Part 3 — Setups

Setups translate brain logic into testable patterns → fabric atoms.

| Setup ID | Logic summary | Key conditions |
|----------|---------------|----------------|
| `accum_breakout` | Compression → expansion | Close > 60D high, Rel_Turn > 1.5, CLV > 0.70, OBV 20D high |
| `pullback_accum` | Trend + digest | Above MA50, pullback to MA20/50, vol down on pullback, CSF > 0 |
| `failed_breakdown` | Spring | Low breaks support, close recovers, Rel_Turn > 1.3, lower wick > 0.45 |
| `sector_follower` | Peer lag | Leader broke out, follower accum ≥ 70, below prior high, RS improving |
| `absorption_pre_break` | Absorption before move | Absorption signal + compression + CSF ↑, no breakout yet |
| `impact_expansion` | Hidden repricing | Impact_Expansion > 1.2, CSF ↑, price not extended |

Each setup → `mde_setup_*` atom → OOS validation before any system influence.

---

# Part 4 — Integration (last)

Integration connects the proven brain to the existing machine.

## 4.1 Data outputs

```text
Table:  egx_market_discovery_daily
JSON:   data/egx_market_discovery_last.json
Shadow: data/mde_shadow_last.json
```

## 4.2 Pipeline position (append only)

```text
tv_micro → MDE engine → counterfactual → fabric light → opp_v2 → … → promotion → Telegram
```

Feature flags:

```text
EGX_MDE_ENABLED=1       # run engine
EGX_MDE_SHADOW=1        # log / store only (default at launch)
EGX_MDE_OPP_BOOST=0     # no opp influence until proven
```

## 4.3 Fabric atoms (after brain is built)

```text
mde_institutional_discovery
mde_kyle_impact_expansion
mde_latent_accumulation
mde_resilience_buyers
mde_absorption_institutional
mde_setup_*  (from Part 3)
```

## 4.4 OOS gate tiers (MDE atoms only)

| Tier | Thresholds | System effect |
|------|------------|---------------|
| **Research / Shadow** | n≥40, lift≥1.03, PF≥1.15 | Log + manifest `mde_watch_atoms` — **zero client impact** |
| **Production Boost** | n≥50, lift≥1.07, PF≥1.25 | Optional opp boost (+5 max) if `EGX_MDE_OPP_BOOST=1` |
| **Client-Influence** | PF≥1.40, lift≥1.10, stability | Future — only after shadow proof |

Global fabric gate (PF≥1.1) remains for non-MDE atoms. MDE uses stricter tiers.

## 4.5 What integration does NOT do (v1)

```text
✗ No UES / score_all changes
✗ No promotion policy changes
✗ No hard veto on existing signals
✗ No parallel Telegram path
✗ No opp boost until Production tier + shadow proof
```

## 4.6 Full client path (unchanged)

```text
MDE discovers (shadow)
  → fabric validates atoms
  → opp_v2 ranks (optional boost later)
  → UES / score_all confirms
  → arbitration
  → client_signal_promotion
  → safety + Telegram prep
  → final_signals.actionable=1
```

## 4.7 Implementation phases (reordered)

| Phase | Focus | Part |
|-------|-------|------|
| **0** | This document + contract | All |
| **1** | `egx_market_discovery_engine.py` — Part 1–3 + shadow JSON + DB | **DONE** |
| **2** | `mine_egx_mde` + OOS attribution + fabric atoms | Part 3 → Part 4 prove |
| **3** | Fabric + 3-tier OOS + shadow attribution | Part 4 (prove) |
| **4** | Optional opp boost + report + weight calibration v2 | Part 4 (connect) |

---

## Summary

```text
MDE Core     = how we detect hidden repricing before explosion
Integration  = how we plug that brain into fabric / opp / UES / promotion

Order: Brain → Score → Setups → Shadow proof → Safe integration
```
