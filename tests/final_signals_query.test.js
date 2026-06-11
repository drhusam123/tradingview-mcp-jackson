import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import Database from 'better-sqlite3';
import { join } from 'path';
import { tmpdir } from 'os';
import { unlinkSync } from 'fs';
import {
  latestFinalSignalDate,
  purgeTestFinalSignals,
  finalActionableCountForDate,
} from '../scripts/lib/final_signals_query.mjs';

describe('final_signals_query', () => {
  it('ignores 2099 test dates in latest and counts', () => {
    const path = join(tmpdir(), `fsq_${Date.now()}.db`);
    const db = new Database(path);
    db.exec(`CREATE TABLE final_signals (
      symbol TEXT, trade_date TEXT, actionable INTEGER, veto_reason TEXT, score REAL
    )`);
    db.prepare(`INSERT INTO final_signals VALUES ('A','2026-06-10',1,NULL,80)`).run();
    db.prepare(`INSERT INTO final_signals VALUES ('B','2099-08-27',1,NULL,90)`).run();
    db.close();

    assert.equal(latestFinalSignalDate(new Database(path, { readonly: true })), '2026-06-10');
    assert.equal(finalActionableCountForDate('2026-06-10', path), 1);
    assert.equal(finalActionableCountForDate('2099-08-27', path), 0);

    const purge = purgeTestFinalSignals(path);
    assert.equal(purge.deleted, 1);
    assert.equal(purge.latest, '2026-06-10');

    unlinkSync(path);
  });
});
