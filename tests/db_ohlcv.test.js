import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('db_ohlcv python helper', () => {
  it('defaults to execution view', () => {
    const out = execFileSync('python3', ['-c',
      'import sys; sys.path.insert(0,"scripts/python"); from db_ohlcv import OHLCV_TABLE, OHLCV_FEATURES; print(OHLCV_TABLE, OHLCV_FEATURES)',
    ], { cwd: ROOT, encoding: 'utf8' });
    assert.ok(out.includes('ohlcv_history_execution'));
    assert.ok(out.includes('ohlcv_history_features'));
  });
});
