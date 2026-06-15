# MDE Phase 2.9B — Hidden Cause Validation Report

**Generated:** 2026-06-13T00:01:37.917286+00:00
**Latest:** 2026-06-11

## COMP_001 Stress Test

- **COMP_001A**: hit=68.2% PF=6.19 n=44 symbols=32 cause=C_supply_exhaustion
- **COMP_001B**: hit=51.9% PF=3.92 n=81 symbols=52 cause=C_supply_exhaustion
- **COMP_001C**: hit=51.4% PF=5.78 n=105 symbols=66 cause=C_supply_exhaustion
- **COMP_001D**: hit=33.3% PF=2.24 n=3 symbols=3 cause=C_supply_exhaustion

## Candidate Buckets v2

- **A_High_Quality_Pending_Confirmation**: PRDC, OLFI
- **F_Liquidity_Artifact**: TAQA, ASCM, TWSA, ARAB, AJWA, AMER, AMIA
- **B_Analog_Strong_Needs_Effective_Upgrade**: ACAMD, AIFI
- **C_Hidden_Cause_Strong_Weak_Score**: AIH

## OQS v2 Top

- PRDC: OQS_v2=66.7 bucket=A_High_Quality_Pending_Confirmation cause=B_latent_accumulation
- OLFI: OQS_v2=64.8 bucket=A_High_Quality_Pending_Confirmation cause=C_supply_exhaustion
- AIFI: OQS_v2=55.5 bucket=B_Analog_Strong_Needs_Effective_Upgrade cause=C_supply_exhaustion
- TAQA: OQS_v2=45.1 bucket=F_Liquidity_Artifact cause=F_sector_rotation_spillover
- AJWA: OQS_v2=43.6 bucket=F_Liquidity_Artifact cause=H_noise_overfit
- TWSA: OQS_v2=41.8 bucket=F_Liquidity_Artifact cause=H_noise_overfit

## Outside-Opp Verdict

MDE discovers opportunities outside opp universe

```text
Shadow only. No client path.
```
