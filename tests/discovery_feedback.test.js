import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { loadDiscoveryFeedback, readPendingResearchDirectives } from '../scripts/lib/load_discovery_feedback.mjs';
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
});
