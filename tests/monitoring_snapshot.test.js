import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { buildMonitoringSnapshot, writeMonitoringSnapshot } from '../scripts/lib/monitoring_snapshot.mjs';

describe('monitoring snapshot', () => {
  it('buildMonitoringSnapshot returns unified fields', () => {
    const s = buildMonitoringSnapshot();
    assert.ok(s.p6);
    assert.ok(s.closed_loops);
    assert.ok(s.directives);
    assert.ok('counterfactual' in s);
    assert.ok('discovery' in s);
    assert.ok('quality_score' in s.discovery);
  });

  it('writeMonitoringSnapshot persists file', () => {
    const r = writeMonitoringSnapshot();
    assert.ok(r.path);
    assert.ok(r.at);
  });
});
