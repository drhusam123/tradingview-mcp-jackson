/**
 * Learning loop + counterfactual safety.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { runCounterfactualSafety } from '../scripts/lib/counterfactual_safety.mjs';
import { evaluateSignalAtDate } from '../scripts/lib/egx_safety_check.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('learning loop counterfactual', () => {
  it('runCounterfactualSafety returns projected WR fields', { skip: !existsSync(DB_PATH) }, () => {
    const r = runCounterfactualSafety();
    assert.ok(r.n_historical >= 0);
    if (r.n_historical > 0) {
      assert.ok('actual_wr' in r);
      assert.ok('projected_wr' in r);
      assert.ok('would_block_losses' in r);
    }
  });

  it('evaluateSignalAtDate supports historical replay', { skip: !existsSync(DB_PATH) }, () => {
    const r = runCounterfactualSafety();
    const sample = r.sample_blocked_losses?.[0];
    if (!sample) return;
    const ev = evaluateSignalAtDate(sample.symbol, sample.date, { historical: true });
    assert.ok(ev.symbol);
    assert.ok('decision' in ev);
  });
});
