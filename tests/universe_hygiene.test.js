import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import {
  RENAME_MAP,
  buildHygieneReport,
  ensureHygieneColumns,
} from '../scripts/lib/universe_hygiene.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(ROOT, 'data/egx_trading.db');

describe('universe hygiene', () => {
  it('RENAME_MAP includes known EGX ticker changes', () => {
    assert.equal(RENAME_MAP.QNBA, 'QNBE');
    assert.equal(RENAME_MAP.ESRS, 'TAQA');
    assert.equal(RENAME_MAP.EKHW, 'ARVA');
  });

  it('buildHygieneReport runs on live DB', { skip: !existsSync(DB) }, () => {
    const db = new Database(DB, { readonly: true });
    ensureHygieneColumns(db);
    const report = buildHygieneReport(db);
    db.close();
    assert.ok(report.universe_total >= 200);
    assert.ok(typeof report.ghosts_no_ohlcv === 'number');
    assert.ok(report.weekly_gap);
  });
});
