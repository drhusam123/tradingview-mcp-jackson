import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  ensureDeliveryAuditTable, logDeliveryAttempt, getLatestAuditRows, closeDeliveryAuditDb,
} from '../scripts/lib/delivery_audit.mjs';

describe('notification delivery audit', () => {
  it('records smoke-style delivery row', () => {
    ensureDeliveryAuditTable();
    const key = `test:${Date.now()}`;
    logDeliveryAttempt({
      signal_date: '2099-12-31',
      symbol: 'TEST',
      actionable: 1,
      message_generated: 1,
      send_attempted: 1,
      send_success: 1,
      pipeline_stage: 'unit_test',
      dedup_key: key,
    });
    const rows = getLatestAuditRows(5);
    assert.ok(rows.some(r => r.dedup_key === key));
    closeDeliveryAuditDb();
  });
});
