import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { runLossAutopsy } from '../scripts/lib/loss_autopsy.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('loss autopsy', () => {
  it('runLossAutopsy returns proposed rules structure', { skip: !existsSync(DB_PATH) }, () => {
    const r = runLossAutopsy();
    assert.ok('n_residual_losses' in r);
    assert.ok(Array.isArray(r.proposed_rules));
    assert.ok(Array.isArray(r.cases));
  });
});
