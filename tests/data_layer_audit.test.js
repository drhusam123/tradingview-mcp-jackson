import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { getOHLCV } from '../src/egx/index.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(ROOT, 'data/egx_trading.db');

describe('data layer L0/L1', () => {
  it('getOHLCV uses execution view when present', { skip: !existsSync(DB) }, () => {
    const db = new Database(DB, { readonly: true });
    const hasView = db.prepare(
      "SELECT 1 ok FROM sqlite_master WHERE type='view' AND name='ohlcv_history_execution'",
    ).get()?.ok === 1;
    db.close();
    if (!hasView) return;
    const bars = getOHLCV('COMI', 20, { execution: true });
    assert.ok(bars.length > 0);
    assert.ok(bars.every(b => b.volume > 0 && b.close > 0));
  });

  it('discovery hydrate wires L0 and checks exit codes', () => {
    const src = readFileSync(join(ROOT, 'scripts/python/discovery_data_hydrate.py'), 'utf8');
    assert.ok(src.includes('"stock_universe"') || src.includes("'stock_universe'"));
    assert.ok(src.includes('proc.returncode'));
  });

  it('batch_run delegates get_ohlcv to getOhlcv', () => {
    const src = readFileSync(join(ROOT, 'src/core/batch.js'), 'utf8');
    assert.ok(src.includes("import { getQuote, getOhlcv }"));
    assert.ok(src.includes('await getOhlcv'));
  });
});
