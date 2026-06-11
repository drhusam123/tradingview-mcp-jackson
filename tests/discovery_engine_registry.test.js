import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { planDiscoveryRun, DISCOVERY_ENGINES } from '../scripts/lib/discovery_engine_registry.mjs';

describe('discovery_engine_registry', () => {
  it('has core engines registered', () => {
    assert.ok(DISCOVERY_ENGINES.opportunity_v2);
    assert.ok(DISCOVERY_ENGINES.quant_rules);
    assert.ok(DISCOVERY_ENGINES.dmids);
  });

  it('planDiscoveryRun triggers promotion_audit on PROMOTION_GAP', () => {
    const { planned } = planDiscoveryRun({
      feedbackQueue: [{ type: 'PROMOTION_GAP', target: 'client_signal_promotion' }],
      forceDaily: false,
    });
    const ids = planned.map(p => p.id);
    assert.ok(ids.includes('promotion_audit'));
  });

  it('does not schedule closed_loop from perpetual', () => {
    const { planned } = planDiscoveryRun({ feedbackQueue: [], forceDaily: true });
    assert.ok(!planned.some(p => p.id === 'closed_loop'));
  });
});
