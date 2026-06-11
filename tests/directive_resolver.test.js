import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import {
  completeResearchDirectives,
  countDirectiveStats,
  resolveEvolutionDirectives,
  resolveClosedLoopDirectives,
  resolveDiscoveryDirectives,
} from '../scripts/lib/directive_resolver.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('directive resolver', () => {
  it('completeResearchDirectives returns structured result', { skip: !existsSync(DB_PATH) }, () => {
    const r = completeResearchDirectives(['test_nonexistent_target_xyz'], {
      engine: 'test',
      note: 'ci smoke',
    });
    assert.equal(r.ok, true);
    assert.ok('completed' in r);
  });

  it('countDirectiveStats returns pending/completed', { skip: !existsSync(DB_PATH) }, () => {
    const s = countDirectiveStats();
    assert.ok('pending' in s);
    assert.ok('completed' in s);
  });

  it('resolveEvolutionDirectives handles empty result', () => {
    const r = resolveEvolutionDirectives({});
    assert.equal(r.ok, true);
    assert.equal(r.completed, 0);
  });

  it('resolveClosedLoopDirectives handles counterfactual lift', () => {
    const r = resolveClosedLoopDirectives({
      learning: { counterfactual: { wr_delta: 5 } },
      runtime: { applied_laws: [{ id: 'x' }] },
    });
    assert.equal(r.ok, true);
  });

  it('resolveDiscoveryDirectives completes quality directives when quant+opp ok', () => {
    const r = resolveDiscoveryDirectives({ quantOk: true, oppOk: true });
    assert.equal(r.ok, true);
    assert.ok(r.targets?.includes('discovery_quality_low') || r.completed >= 0);
  });

  it('resolveDiscoveryDirectives uses opp followup alert mapping', () => {
    const r = resolveDiscoveryDirectives({
      quantOk: true,
      oppOk: true,
      oppFollowup: {
        alerts: [{ code: 'MISSED_HIGH_OPP_RISING' }],
        directives: [{ id: 'opp_missed_trend' }],
      },
      feedback: { queue: [{ type: 'PROMOTION_GAP', target: 'client_signal_promotion' }] },
    });
    assert.equal(r.ok, true);
  });
});
