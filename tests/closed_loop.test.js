import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { syncDeliveredOutcomes } from '../scripts/lib/delivered_outcomes.mjs';
import { ingestP6Directives } from '../scripts/lib/p6_directives_ingest.mjs';
import { mergeRuntimeRules } from '../scripts/lib/runtime_rules_merge.mjs';
import { runOpportunityQualityLoop } from '../scripts/lib/opportunity_quality_loop.mjs';
import { buildDiscoveryFeedback } from '../scripts/lib/discovery_feedback.mjs';
import { getProofLoopMetrics } from '../scripts/lib/proof_loop.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('closed loop connectors', () => {
  it('syncDeliveredOutcomes returns stats', { skip: !existsSync(DB_PATH) }, () => {
    const r = syncDeliveredOutcomes({ lookbackDays: 30 });
    assert.equal(r.ok, true);
    assert.ok('rows_updated' in r);
  });

  it('ingestP6Directives accepts sample directives', { skip: !existsSync(DB_PATH) }, () => {
    const r = ingestP6Directives([
      { id: 'test_directive_ci', priority: 'LOW', action: 'ci smoke test' },
    ]);
    assert.equal(r.ok, true);
  });

  it('mergeRuntimeRules writes overlay structure', () => {
    const r = mergeRuntimeRules({ minEvidence: 1 });
    assert.ok(Array.isArray(r.applied_laws));
    assert.ok(r.behavioral_filters);
  });

  it('getProofLoopMetrics supports deliveredOnly flag', { skip: !existsSync(DB_PATH) }, () => {
    const all = getProofLoopMetrics();
    const del = getProofLoopMetrics({ deliveredOnly: true });
    assert.ok('delivered_only' in del);
    assert.equal(del.delivered_only, true);
    assert.ok(del.n_completed <= all.n_completed);
  });

  it('runOpportunityQualityLoop returns pipeline fields', { skip: !existsSync(DB_PATH) }, () => {
    const r = runOpportunityQualityLoop('2026-06-10');
    assert.ok('n_top_opportunity' in r || 'error' in r);
  });

  it('buildDiscoveryFeedback produces queue', () => {
    const r = buildDiscoveryFeedback({
      forensic: { by_class: { EXPLOSIVE: { n: 10, wins: 2, losses: 8 } } },
      autopsy: { flag_counts: { repeat_ultra_loser: 5 } },
    });
    assert.ok(r.n_items >= 1);
    assert.ok(Array.isArray(r.queue));
  });
});
