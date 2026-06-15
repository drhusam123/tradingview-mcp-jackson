# Dependency Audit

**Generated:** 2026-06-15T20:50:19.577277+00:00

| Package | Current | Latest | Used By | Risk | Recommendation | Action Taken |
|---------|---------|--------|---------|------|----------------|--------------|
| @modelcontextprotocol/sdk | 1.27.1 | 1.29.0 | npm | Optional | Skip major bump unless tested | pending |
| better-sqlite3 | 12.9.0 | 12.11.1 | npm | Optional | Skip major bump unless tested | pending |
| chrome-remote-interface | 0.33.3 | 0.34.0 | npm | Optional | Skip major bump unless tested | pending |
| dotenv | 17.4.1 | 17.4.2 | npm | Optional | Skip major bump unless tested | pending |
| puppeteer-core | 24.43.1 | 25.1.0 | npm | Risky | Skip major bump unless tested | pending |
| simple-statistics | 7.8.9 | 7.9.0 | npm | Optional | Skip major bump unless tested | pending |

## Python (requirements-python.txt)

| Package | Role | Memory Impact | Recommendation |
|---------|------|---------------|----------------|
| lightgbm | ML scoring | Medium CPU | Keep — CPU-only, no GPU |
| duckdb | Analytics | Low | Keep |
| tensorflow | Legacy paths | High RAM | Avoid in daily cron |
| mlflow | MLOps | Medium | Weekly/manual only |
| tsfresh | Features | High | Manual research only |

**Action:** No mass upgrade this wave — document only. Run `npm run test:smoke` after any bump.
