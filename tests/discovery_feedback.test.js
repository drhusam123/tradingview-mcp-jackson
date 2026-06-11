import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { loadDiscoveryFeedback, readPendingResearchDirectives } from '../scripts/lib/load_discovery_feedback.mjs';
import { buildDiscoveryParams } from '../scripts/lib/discovery_context.mjs';
import { latestStructuralLawsFile } from '../scripts/lib/structural_laws_bridge.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('discovery feedback bridge', () => {
  it('loadDiscoveryFeedback returns queue array', () => {
    const r = loadDiscoveryFeedback();
    assert.ok(Array.isArray(r.queue));
    assert.ok('n_items' in r);
  });

  it('readPendingResearchDirectives returns array', { skip: !existsSync(DB_PATH) }, () => {
    const rows = readPendingResearchDirectives(5);
    assert.ok(Array.isArray(rows));
  });

  it('buildDiscoveryParams merges P6 priorities into queue', () => {
    const ctx = buildDiscoveryParams();
    assert.ok(ctx.feedback.n_items >= 0);
    assert.ok(ctx.params.p6_priorities !== undefined);
  });

  it('latestStructuralLawsFile returns path or null', () => {
    const f = latestStructuralLawsFile();
    assert.ok(f === null || f.includes('structural_laws_'));
  });
});
