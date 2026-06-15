# Device Limits — MacBook Pro Intel i9 / 16GB

**Profile:** `config/performance.json` → `macbook_i9_16gb`

## Hard Limits

| Setting | Value | Reason |
|---------|-------|--------|
| `max_workers` | 4 | 8-core CPU — avoid RAM thrashing |
| `enable_heavy_research` | false | No GPU; heavy jobs manual only |
| `chunk_size` | 50000 | Bounded memory per read |
| `daily_timeout_minutes` | 20 | Fast cycle must finish |
| `deep_research_timeout_minutes` | 120 | Weekly/manual cap |

## Do Not Run in Daily Automation

- Full `tv_auto` + all discovery engines together
- TensorFlow training loops
- `tsfresh` full universe
- `egx:evolution.mjs` full (use `--quick` in cron)
- Parallel symbol fetch > 4 workers

## Recommended Daily Path

```bash
npm run egx:full-cycle -- --fast    # CDP smoke + validate + session + dry-run
npm run egx:health -- --quick
```

## Full EOD (cron or manual)

```bash
npm run egx:full-cycle              # without --fast, TV connected
```

## Heavy Research (manual)

```bash
ENABLE_HEAVY_RESEARCH=1 npm run egx:discovery:strategy:sweep
```

Only when `config/performance.json` has `enable_heavy_research: true` or env override.
