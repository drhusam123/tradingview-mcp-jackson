# MDE Candidate Deep Dossiers

**Date:** 2026-06-11

## PRDC — HIGH_QUALITY_SHADOW_CANDIDATE_PENDING_CONFIRMATION

**Thesis:** Latent accumulation / institutional absorption — analog hit 43.8% PF 3.78
**OQS v2:** 66.7 | Bucket: A_High_Quality_Pending_Confirmation

### Hidden Cause
- Primary: `B_latent_accumulation` (65.0%)
- Top hypothesis: Latent accumulation / institutional absorption

### Impact & Metaorder
- Dominant impact: inventory_dealer
- Metaorder: 85% stage=mid
- Liquidity: REAL_LIQUIDITY
- Assimilation: DELAYED_DISCOVERY

### Analogs & Outcome Path
- Analog hit 5d/10d/20d: 43.8% / 46.8% / 67.4%
- Dominant path: late_signal
- Holding: 5d

### Triggers
- **Confirm:** CLV>0.6 + volume follow-through AND effective_score remains >55
- **Invalidate:** effective_score drops below 50
- **Upgrade:** OQS_v2>65 + confirmation + metaorder early/mid
- **Downgrade:** effective<50 OR ON_TIME_DISCOVERY

### Adversarial
- Bull: Hidden repricing with analog hit 43.8% PF 3.78
- Bear: Mean reversion after volume spike

## OLFI — HIGH_QUALITY_SHADOW_CANDIDATE_PENDING_CONFIRMATION

**Thesis:** Supply exhaustion / seller finished — analog hit 45.5% PF 3.15
**OQS v2:** 64.8 | Bucket: A_High_Quality_Pending_Confirmation

### Hidden Cause
- Primary: `C_supply_exhaustion` (65.0%)
- Top hypothesis: Supply exhaustion / seller finished

### Impact & Metaorder
- Dominant impact: inventory_dealer
- Metaorder: 70% stage=late
- Liquidity: REAL_LIQUIDITY
- Assimilation: DELAYED_DISCOVERY

### Analogs & Outcome Path
- Analog hit 5d/10d/20d: 45.5% / 50.0% / 65.9%
- Dominant path: late_signal
- Holding: 5d

### Triggers
- **Confirm:** effective_score remains >55
- **Invalidate:** effective_score drops below 50
- **Upgrade:** OQS_v2>65 + confirmation + metaorder early/mid
- **Downgrade:** effective<50 OR ON_TIME_DISCOVERY

### Adversarial
- Bull: Hidden repricing with analog hit 45.5% PF 3.15
- Bear: Mean reversion after volume spike

## TAQA — REJECT

**Thesis:** Sector spillover only — analog hit 46.3% PF 3.98
**OQS v2:** 45.1 | Bucket: F_Liquidity_Artifact

### Hidden Cause
- Primary: `F_sector_rotation_spillover` (57.5%)
- Top hypothesis: Sector spillover only

### Impact & Metaorder
- Dominant impact: inventory_dealer
- Metaorder: 50% stage=early
- Liquidity: DISTRIBUTION_LIQUIDITY
- Assimilation: DELAYED_DISCOVERY

### Analogs & Outcome Path
- Analog hit 5d/10d/20d: 46.3% / 50.0% / 55.0%
- Dominant path: fast_winner
- Holding: 5d

### Triggers
- **Confirm:** rel_turn>1.2 + close in upper half of range
- **Invalidate:** effective_score drops below 50
- **Upgrade:** OQS_v2>65 + confirmation + metaorder early/mid
- **Downgrade:** effective<50 OR ON_TIME_DISCOVERY

### Adversarial
- Bull: Hidden repricing with analog hit 46.3% PF 3.98
- Bear: Mean reversion after volume spike

## ARAB — REJECT

**Thesis:** Noise / liquidity artifact — analog hit 45.7% PF 3.49
**OQS v2:** 16.5 | Bucket: G_Reject

### Hidden Cause
- Primary: `H_noise_overfit` (50.0%)
- Top hypothesis: Noise / liquidity artifact

### Impact & Metaorder
- Dominant impact: inventory_dealer
- Metaorder: 0% stage=exhausted
- Liquidity: GHOST_LIQUIDITY
- Assimilation: FAST_ASSIMILATION

### Analogs & Outcome Path
- Analog hit 5d/10d/20d: 45.7% / 55.9% / 70.6%
- Dominant path: late_signal
- Holding: 5d

### Triggers
- **Confirm:** effective_score remains >55
- **Invalidate:** effective_score drops below 50
- **Upgrade:** OQS_v2>65 + confirmation + metaorder early/mid
- **Downgrade:** effective<50 OR ON_TIME_DISCOVERY

### Adversarial
- Bull: Hidden repricing with analog hit 45.7% PF 3.49
- Bear: Mean reversion after volume spike
