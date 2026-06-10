/**
 * Layer-2 gate_daily — fast pre-ML enforce path.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { runDailyQualityGate, enforceDailyQualityGate } from '../scripts/lib/data_quality_gate.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('data_quality_gate gate_daily', () => {
  it('runDailyQualityGate returns structured result when DB exists', { skip: !existsSync(DB_PATH) }, () => {
    const gate = runDailyQualityGate();
    assert.equal(gate.command, 'gate_daily');
    assert.ok('blocked' in gate);
    assert.ok('trust_score' in gate);
    assert.ok(gate.latest_date);
  });

  it('enforceDailyQualityGate throws when blocked', { skip: !existsSync(DB_PATH) }, () => {
    const gate = runDailyQualityGate();
    if (gate.blocked) {
      assert.throws(() => enforceDailyQualityGate({}, { exitOnBlock: true }), /BLOCKED/);
    } else {
      const ok = enforceDailyQualityGate({}, { exitOnBlock: true });
      assert.equal(ok.blocked, false);
    }
  });
});
