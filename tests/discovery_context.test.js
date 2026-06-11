import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildDiscoveryParams,
  discoveryContextSummary,
  readP6ResearchContext,
  readOpportunityFollowup,
} from '../scripts/lib/discovery_context.mjs';

describe('discovery context', () => {
  it('buildDiscoveryParams returns params with feedback_queue array', () => {
    const ctx = buildDiscoveryParams();
    assert.ok(Array.isArray(ctx.params.feedback_queue));
    assert.ok(Array.isArray(ctx.params.p6_priorities));
    assert.ok(Array.isArray(ctx.params.p6_directives));
    assert.ok('opportunity_followup' in ctx.params);
  });

  it('discoveryContextSummary returns numeric fields', () => {
    const ctx = buildDiscoveryParams();
    const s = discoveryContextSummary(ctx);
    assert.ok(typeof s.feedback_items === 'number');
    assert.ok(typeof s.pending_directives === 'number');
    assert.ok(typeof s.opp_alerts === 'number');
    assert.ok('discovery_quality_score' in s);
    assert.ok('discovery_grade' in s);
  });

  it('readP6ResearchContext returns object or null', () => {
    const p6 = readP6ResearchContext();
    assert.ok(p6 === null || typeof p6 === 'object');
  });

  it('readOpportunityFollowup returns object or null', () => {
    const f = readOpportunityFollowup();
    assert.ok(f === null || typeof f === 'object');
  });
});
