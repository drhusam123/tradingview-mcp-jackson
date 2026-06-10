import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import {
  checkIndicatorCacheCoverage,
  verifyActionableIndicatorCache,
} from '../scripts/lib/indicator_cache_gate.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('indicator cache gate', () => {
  it('checkIndicatorCacheCoverage returns structured fields', { skip: !existsSync(DB_PATH) }, () => {
    const r = checkIndicatorCacheCoverage('2026-06-10');
    assert.ok('symbols_on_date' in r);
    assert.ok('min_required' in r);
  });

  it('verifyActionableIndicatorCache handles empty actionable', { skip: !existsSync(DB_PATH) }, () => {
    const r = verifyActionableIndicatorCache('2099-01-01');
    assert.equal(r.ok, true);
    assert.deepEqual(r.missing, []);
  });
});
