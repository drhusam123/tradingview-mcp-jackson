/**
 * P6 proof loop metrics.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import {
  getProofLoopMetrics,
  formatProofLoopLine,
  PROOF_MIN_N,
  PROOF_MIN_WR,
} from '../scripts/lib/proof_loop.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('proof_loop P6 metrics', () => {
  it('exports gate constants', () => {
    assert.equal(PROOF_MIN_N, 30);
    assert.equal(PROOF_MIN_WR, 60);
  });

  it('getProofLoopMetrics returns structured fields', { skip: !existsSync(DB_PATH) }, () => {
    const m = getProofLoopMetrics();
    assert.equal(m.tier, 'ULTRA_CONVICTION');
    assert.ok(m.n_completed >= 0);
    assert.ok('gate_pass' in m);
    assert.ok('samples_needed' in m);
  });

  it('getProofLoopMetrics supports safetyFiltered track', { skip: !existsSync(DB_PATH) }, () => {
    const raw = getProofLoopMetrics();
    const filtered = getProofLoopMetrics({ safetyFiltered: true });
    assert.equal(filtered.safety_filtered, true);
    assert.ok(filtered.n_completed <= raw.n_completed);
  });

  it('formatProofLoopLine is human-readable', () => {
    const line = formatProofLoopLine({ n_completed: 26, win_rate: 61.5, gate_pass: false, samples_needed: 4 });
    assert.match(line, /26\/30/);
    assert.match(line, /61\.5%/);
  });
});
