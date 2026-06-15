# EGX Commands — Operator Reference

## Daily (5 minutes)

| Command | Purpose |
|---------|---------|
| `npm run egx:health -- --quick` | System health PASS/WARN/FAIL |
| `npm run egx:validate-data` | Market data validation |
| `npm run egx:full-cycle -- --fast` | Fast DAG with CDP smoke |
| `npm run egx:cron:telegram:dry` | Telegram dry-run |

## Full Production

| Command | Purpose |
|---------|---------|
| `npm run egx:full-cycle` | Full EOD DAG (slow) |
| `npm run egx:audit:e2e` | Institutional E2E chain |
| `npm run egx:post:session` | Post-close engines + health |

## Audit & Upgrade

| Command | Purpose |
|---------|---------|
| `npm run egx:audit:all` | Regenerate audit/*.md |
| `npm run egx:upgrade:audit` | Environment/deps/security audits |
| `npm run egx:db:optimize` | Indexes + backup |
| `npm run test:smoke` | Lightweight smoke tests |

## Automation

| Command | Purpose |
|---------|---------|
| `npm run egx:cron:show` | List installed cron jobs |
| `npm run egx:cron:install` | Install/update cron |
| `node scripts/egx_automation_verify.mjs` | 182 automation checks |

See [AUTOMATION_RUNBOOK.md](./AUTOMATION_RUNBOOK.md) and [DEVICE_LIMITS.md](./DEVICE_LIMITS.md).
