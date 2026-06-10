import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildDeliveryDigest, formatOpsSuccessMessage, reconcileCounts } from '../scripts/lib/ops_digest.mjs';

describe('ops_digest', () => {
  it('buildDeliveryDigest returns structured fields', () => {
    const d = buildDeliveryDigest('2099-01-01');
    assert.equal(typeof d, 'object');
    assert.ok('reconcile' in d);
    assert.ok('pending' in d);
    assert.match(d.reconcile, /^\d+\/\d+ sent$/);
  });

  it('formatOpsSuccessMessage includes event and date', () => {
    const msg = formatOpsSuccessMessage('TEST', { signal_date: '2026-06-10', symbols: ['NARE'] });
    assert.match(msg, /EGX Ops OK/);
    assert.match(msg, /2026-06-10/);
    assert.match(msg, /NARE/);
  });

  it('reconcileCounts returns non-negative totals', () => {
    const r = reconcileCounts(7);
    assert.ok(r.total >= 0);
    assert.ok(r.sent >= 0);
    assert.ok(r.pending >= 0);
    assert.equal(r.pending, r.total - r.sent);
  });
});
