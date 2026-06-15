# Environment Audit

**Generated:** 2026-06-15T20:50:17.516443+00:00

| Field | Value |
|-------|-------|
| Node Version | v24.15.0 |
| NPM Version | 11.12.1 |
| Python Version | Python 3.11.9 |
| Pip Version | pip 26.1.2 from /Users/dr.husam/.pyenv/versions/3.11.9/lib/python3.11/site-packages/pip (python 3.11) |
| Python Path | /Users/dr.husam/.pyenv/versions/3.11.9/bin/python3 |
| Detected DB | data/egx_trading.db |
| Device Profile | MacBook Pro Intel i9 / 16GB RAM |

## Device Constraint Notes

- No CUDA / Metal-heavy ML training in daily automation
- `config/performance.json`: max_workers=4, enable_heavy_research=false
- Prefer SQLite + chunked reads over in-memory full loads

## Heavy Python Packages (installed)

- xgboost
- lightgbm
- mlflow
- tsfresh

## Main Commands

- `npm run egx:health` — system health
- `npm run egx:validate-data` — market data validation
- `npm run egx:full-cycle -- --fast` — daily fast DAG
- `npm run egx:audit:e2e` — institutional E2E
- `npm run test:smoke` — lightweight smoke tests

## Broken Commands

Run `node scripts/egx_automation_verify.mjs` — expected 182/182 PASS.

## Recommended Updates

- Keep Node 24 LTS track; patch npm deps only after smoke test
- Python 3.11.9 via pyenv — do not jump to 3.12 without ML wheel check
