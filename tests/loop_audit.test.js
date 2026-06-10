import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { auditClosedLoops } from '../scripts/lib/loop_audit.mjs';

describe('loop audit', () => {
  it('auditClosedLoops returns checks array', () => {
    const r = auditClosedLoops({ maxAgeHours: 9999 });
    assert.ok(Array.isArray(r.checks));
    assert.ok('pass' in r);
    assert.ok('directives' in r);
  });
});
