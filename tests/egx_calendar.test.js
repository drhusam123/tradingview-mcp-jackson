/**
 * Unit tests for EGX trading calendar bridge (no DB required for date helpers).
 * Run: node --test tests/egx_calendar.test.js
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  cairoDateParts,
  addDaysIso,
  freshnessReferenceDate,
  nextTradingDay,
} from '../scripts/lib/egx_calendar.mjs';

describe('egx_calendar date helpers', () => {
  it('cairoDateParts returns YYYY-MM-DD', () => {
    const p = cairoDateParts(new Date('2026-05-27T10:00:00Z'));
    assert.match(p.date, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(p.hour >= 0 && p.hour <= 23);
  });

  it('addDaysIso shifts calendar days', () => {
    assert.equal(addDaysIso('2026-05-27', -1), '2026-05-26');
    assert.equal(addDaysIso('2026-05-27', 1), '2026-05-28');
  });

  it('freshnessReferenceDate before 15:30 Cairo uses previous day', () => {
    // 12:00 Cairo on 2026-06-09 ≈ 09:00 UTC
    const ref = freshnessReferenceDate(new Date('2026-06-09T09:00:00Z'));
    assert.equal(ref, '2026-06-08');
  });

  it('nextTradingDay returns a future ISO date after ref', () => {
    const r = nextTradingDay('2026-06-05'); // Thu — next should be Sun 2026-06-08 or later
    assert.ok(r.next_trading_day);
    assert.match(r.next_trading_day, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(r.next_trading_day > r.ref_date);
  });
});
