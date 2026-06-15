# Troubleshooting

## HEALTH: FAIL

```bash
npm run egx:health
node scripts/egx_automation_verify.mjs
```

Fix P0 first: `db_integrity`, `db_migrations`, `env_readiness`.

## HEALTH: WARN — delivery_reconcile

Pending signal-day before live send. Normal pre-cron.

```bash
npm run egx:notify:reconcile
npm run egx:cron:telegram:dry
```

## Stale lock

```bash
ls -la logs/*.lock
# If >6h old:
rm logs/egx_tv_auto_update.lock   # only if no process running
```

Or run `npm run egx:full-cycle` — clears stale locks >6h automatically.

## CDP not connected

```bash
npm run egx:cdp:smoke
npm run tv:health
```

Launch TradingView with CDP on port 9222.

## Data layer FAIL

```bash
npm run egx:validate-data
node scripts/rebuild_indicators.mjs
node scripts/egx_data_layer_audit.mjs
```

## Full cycle hangs

Use fast mode:

```bash
npm run egx:full-cycle -- --fast
```

Full EOD without `--fast` runs `tv_auto_update` (20–40 min).

## Low disk space

Health warns if `<2GB` free in `data/`. Archive old `data/backups/` manually.
