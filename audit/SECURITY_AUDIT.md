# Security Audit

**Generated:** 2026-06-15T20:50:19.612651+00:00

| Finding | Severity | File | Risk | Fix | Status |
|---------|----------|------|------|-----|--------|
| `.env` gitignored | High | .gitignore | Secret leak | Present | Fixed |

## Rules enforced

- Tokens read from `.env` only
- Logs must not print secrets
- Dry-run default for telegram cron
