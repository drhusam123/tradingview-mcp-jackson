import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'fs';
import { buildIndicatorPayload } from '../scripts/lib/indicator_snapshot.mjs';

describe('indicator snapshot', () => {
  it('buildIndicatorPayload returns null for insufficient bars', () => {
    assert.equal(buildIndicatorPayload([]), null);
    assert.equal(buildIndicatorPayload(new Array(10).fill({ time: 1, open: 1, high: 2, low: 1, close: 1, volume: 100 })), null);
  });
});

describe('indicator backfill report', () => {
  it('indicator_backfill_last.json exists after manual run', { skip: !existsSync('data/indicator_backfill_last.json') }, () => {
    const r = JSON.parse(readFileSync('data/indicator_backfill_last.json', 'utf8'));
    assert.ok('ok' in r);
  });
});
