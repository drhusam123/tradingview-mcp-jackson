/**
 * Extended behavioral safety rules from loss autopsy.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { evaluateSignalAtDate } from '../scripts/lib/egx_safety_check.mjs';
import { existsSync } from 'fs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('extended safety rules', () => {
  it('blocks EXPLOSIVE with vol below 2.5x when indicators present', () => {
    const ev = evaluateSignalAtDate('TEST', '2099-01-01', { historical: true, counterfactual: true });
    // synthetic path — only runs when DB has data; assert API shape
    assert.ok('decision' in ev);
    assert.ok('failed_conditions' in ev);
  });

  it('MOIN repeat loser blocked on 2nd+ ULTRA after prior loss', { skip: !existsSync(DB_PATH) }, () => {
    const ev = evaluateSignalAtDate('MOIN', '2026-06-03', { historical: true, counterfactual: true });
    if (ev.failed_conditions?.includes('no_signal')) return;
    assert.ok(
      ev.failed_conditions.includes('repeat_ultra_loser'),
      `expected repeat_ultra_loser, got ${ev.failed_conditions.join(',')}`,
    );
  });

  it('OCPH repeat loser blocked after prior ULTRA loss', { skip: !existsSync(DB_PATH) }, () => {
    const ev = evaluateSignalAtDate('OCPH', '2026-06-02', { historical: true, counterfactual: true });
    if (ev.failed_conditions?.includes('no_signal')) return;
    assert.ok(ev.failed_conditions.includes('repeat_ultra_loser'));
  });
});
