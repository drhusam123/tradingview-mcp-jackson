# Tools Recommendation

**Generated:** 2026-06-15T20:50:19.613614+00:00
**Device:** Intel i9 / 16GB — balanced mode

| Tool/Library | Layer | Why Needed | Alternative | Memory/CPU | Decision |
|--------------|-------|------------|-------------|------------|----------|
| better-sqlite3 | Node DB | Fast sync SQLite | sqlite3 | Low | **Keep** |
| duckdb | Python analytics | SQL on parquet | pandas only | Medium | **Keep** |
| lightgbm | ML | CPU booster | sklearn | Medium CPU | **Keep** |
| polars | Data | Faster than pandas | pandas | Medium RAM | **Later** |
| pandera | Validation | Schema checks | custom SQL | Low | **Later** |
| pino | Node logging | Structured logs | run_logger.mjs | Low | **Skip** — added lightweight logger |
| great-expectations | Data QA | Heavy | validate_market_data.py | High | **Skip** |
| redis/postgres | Infra | — | SQLite | High ops | **Skip** |

## Installed This Wave

- `config/performance.json` — device limits
- `scripts/python/validate_market_data.py` — lightweight validation
- `scripts/lib/run_logger.mjs` + `system_logger.py` — JSONL logging
- `scripts/python/db_optimize.py` — indexes + backup
