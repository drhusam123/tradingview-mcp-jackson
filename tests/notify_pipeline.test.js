import { describe, it, after } from 'node:test';
import assert from 'node:assert/strict';
import Database from 'better-sqlite3';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  ensureDeliveryAuditTable, logDeliveryAttempt, wasAlreadySent,
  normalizeDeliverableSignals, countActionable, closeDeliveryAuditDb,
} from '../scripts/lib/delivery_audit.mjs';
import { purgeTestFinalSignals } from '../scripts/lib/final_signals_query.mjs';
import { runPreSendCheck } from '../scripts/lib/pre_send_check.mjs';
import { runEgxSafetyCheck } from '../scripts/lib/egx_safety_check.mjs';
import { validateTelegramPayload } from '../src/egx/notify.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(ROOT, 'data/egx_trading.db');

function cleanupTestDb(...pairs) {
  const d = new Database(DB);
  for (const [sql, ...args] of pairs) d.prepare(sql).run(...args);
  d.prepare("DELETE FROM final_signals WHERE trade_date LIKE '2099-%'").run();
  d.prepare("DELETE FROM notification_delivery_audit WHERE signal_date LIKE '2099-%'").run();
  d.close();
}

describe('notification pipeline', () => {
  after(() => {
    purgeTestFinalSignals(DB);
    const d = new Database(DB);
    d.prepare("DELETE FROM notification_delivery_audit WHERE signal_date LIKE '2099-%'").run();
    d.close();
    closeDeliveryAuditDb();
  });

  it('Test A — normalize promoted actionable sets quality_gate_passed', () => {
    const d = new Database(DB);
    const n = Date.now();
    const testDate = `2099-${String(1 + (n % 11)).padStart(2, '0')}-${String(1 + ((n >> 5) % 28)).padStart(2, '0')}`;
    const sym = `TST${n}`;
    d.prepare('DELETE FROM final_signals WHERE trade_date=?').run(testDate);
    d.prepare(`
      INSERT OR REPLACE INTO final_signals
      (trade_date, symbol, actionable, veto_reason, source_breakdown, entry_price, entry_high, stop_loss, t1_target, r_ratio, score)
      VALUES (?, ?, 1, NULL, ?, 10, 10.5, 9, 11, 2.0, 80)
    `).run(testDate, sym, JSON.stringify({ promoted: true }));
    d.close();

    const norm = normalizeDeliverableSignals(testDate);
    assert.ok(norm.fixed >= 1, 'should fix missing quality_gate_passed');
    const after = countActionable(testDate, { allowTestDates: true });
    assert.equal(after.deliverable, 1, 'normalized row should be deliverable');
    assert.ok(after.symbols.includes(sym), 'symbol should be deliverable');

    cleanupTestDb(
      ['DELETE FROM final_signals WHERE trade_date=? AND symbol=?', testDate, sym],
    );
  });

  it('Test B — ML stale blocks pre-send for future date', () => {
    const r = runPreSendCheck('2099-12-31', { dryRun: true, skipMlRemediate: true, logBlock: false });
    assert.equal(r.ok, false, 'future date should fail upstream');
    assert.ok(
      r.blockers.some(b => b.includes('ml_prediction') || b.includes('ML') || b.includes('upstream')),
      `expected ML/upstream blocker, got: ${r.blockers.join('; ')}`,
    );
  });

  it('Test D/E — dedup detects prior live send audit row', () => {
    ensureDeliveryAuditTable();
    const key = `dedup_test:${Date.now()}`;
    const signalDate = '2099-11-11';
    logDeliveryAttempt({
      signal_date: signalDate,
      symbol: 'DEDUP',
      actionable: 1,
      deliverable: 1,
      send_attempted: 1,
      send_success: 1,
      pipeline_stage: 'telegram_send',
      dedup_key: key,
    });
    const dup = wasAlreadySent(signalDate);
    assert.equal(dup.duplicate, true);
    assert.equal(dup.reason, 'already_sent_live');
    cleanupTestDb(
      ["DELETE FROM notification_delivery_audit WHERE signal_date='2099-11-11' AND symbol='DEDUP'"],
    );
    closeDeliveryAuditDb();
  });

  it('Test C — backfillMode relaxes ISO date QA on overview', () => {
    const qa = validateTelegramPayload('summary 2026-06-10', {
      clientDelivery: true,
      reportDate: '2026-06-06',
      finalActionableCount: 1,
      backfillMode: true,
    });
    const issues = qa.issues || [];
    assert.ok(!issues.some(i => i.includes('non-report ISO')), issues.join('; '));
  });

  it('Test F — dry-run pre_send returns structured checks', () => {
    const r = runPreSendCheck('2099-12-31', { dryRun: true, skipMlRemediate: true, logBlock: false });
    assert.ok(Array.isArray(r.checks) && r.checks.length > 0);
    assert.ok('blockers' in r);
    assert.ok('actionable' in r);
    assert.ok('ml_latest_date' in r);
  });

  it('Test G — Near ATH blocked when vol_ratio below lesson threshold', () => {
    const d = new Database(DB);
    const testDate = `2099-08-${String((Date.now() % 27) + 1).padStart(2, '0')}`;
    const sym = `ATH${Date.now() % 100000}`;
    d.prepare(`
      INSERT OR REPLACE INTO final_signals
      (trade_date, symbol, setup_type, actionable, veto_reason, source_breakdown,
       entry_price, entry_high, stop_loss, t1_target, r_ratio, score)
      VALUES (?, ?, 'Near ATH Continuation', 1, NULL, ?, 10, 10.2, 9.5, 11, 2.5, 85)
    `).run(testDate, sym, JSON.stringify({ quality_gate_passed: true }));
    d.prepare(`
      INSERT OR REPLACE INTO indicators_cache (symbol, bar_date, vol_ratio_20, close_position)
      VALUES (?, ?, 1.2, 0.3)
    `).run(sym, testDate);
    d.close();

    normalizeDeliverableSignals(testDate);
    const safety = runEgxSafetyCheck(testDate, { veto: true, allowTestDates: true });
    const dec = safety.decisions.find(x => x.symbol === sym);
    assert.ok(dec, 'decision row for test symbol');
    assert.equal(dec.decision, 'BLOCKED');
    assert.ok(dec.failed_conditions.includes('near_ath_volume'));

    cleanupTestDb(
      ['DELETE FROM final_signals WHERE trade_date=? AND symbol=?', testDate, sym],
      ['DELETE FROM indicators_cache WHERE symbol=? AND bar_date=?', sym, testDate],
    );
  });

  it('Test H — VOLATILE blocked at delivery without optimal vol band', () => {
    const d = new Database(DB);
    const testDate = `2099-09-${String((Date.now() % 27) + 1).padStart(2, '0')}`;
    const sym = `VOL${Date.now() % 100000}`;
    d.prepare(`
      INSERT OR REPLACE INTO final_signals
      (trade_date, symbol, setup_type, actionable, veto_reason, source_breakdown,
       entry_price, entry_high, stop_loss, t1_target, r_ratio, score)
      VALUES (?, ?, 'Power Breakout', 1, NULL, ?, 10, 10.2, 9.5, 11, 2.5, 85)
    `).run(testDate, sym, JSON.stringify({ quality_gate_passed: true, behavioral_class: 'VOLATILE' }));
    d.prepare(`
      INSERT OR REPLACE INTO indicators_cache (symbol, bar_date, vol_ratio_20, rsi14, close_position)
      VALUES (?, ?, 1.8, 55, 0.4)
    `).run(sym, testDate);
    d.prepare(`
      INSERT OR REPLACE INTO stock_behavioral_memory (symbol, behavioral_class, false_signal_rate)
      VALUES (?, 'VOLATILE', 0.4)
    `).run(sym);
    d.close();

    const safety = runEgxSafetyCheck(testDate, { veto: true, allowTestDates: true });
    const dec = safety.decisions.find(x => x.symbol === sym);
    assert.ok(dec, 'decision row for volatile symbol');
    assert.equal(dec.decision, 'BLOCKED');
    assert.ok(dec.failed_conditions.includes('behavioral_volatile'));

    cleanupTestDb(
      ['DELETE FROM final_signals WHERE trade_date=? AND symbol=?', testDate, sym],
      ['DELETE FROM indicators_cache WHERE symbol=? AND bar_date=?', sym, testDate],
      ['DELETE FROM stock_behavioral_memory WHERE symbol=?', sym],
    );
  });
});
