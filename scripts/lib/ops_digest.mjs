import Database from 'better-sqlite3';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import {
  DB_PATH, getUpstreamDates, latestOhlcvDate, countActionable, getAuditForDate,
} from './delivery_audit.mjs';
import { PROJECT_ROOT } from './load_env.mjs';

/** Reconcile counts for recent actionable signal-days. */
export function reconcileCounts(days = 14) {
  if (!existsSync(DB_PATH)) return { total: 0, sent: 0, pending: 0 };
  const db = new Database(DB_PATH, { readonly: true });
  const signals = db.prepare(`
    SELECT DISTINCT trade_date AS date
    FROM final_signals
    WHERE actionable=1 AND veto_reason IS NULL
      AND trade_date >= date('now', ?)
      AND trade_date NOT LIKE '2099-%'
  `).all(`-${days} days`);
  db.close();

  let sent = 0;
  for (const { date } of signals) {
    const audit = getAuditForDate(date);
    const live = audit.find(a =>
      a.send_success === 1
      && ['telegram_send', 'backfill_send', 'live_send'].includes(a.pipeline_stage),
    );
    if (live) sent += 1;
  }
  return { total: signals.length, sent, pending: signals.length - sent };
}

export function buildDeliveryDigest(signalDate = latestOhlcvDate()) {
  const upstream = getUpstreamDates();
  const act = signalDate ? countActionable(signalDate) : { deliverable: 0, symbols: [] };
  const recon = reconcileCounts(14);
  let verifyPass = null;
  const vPath = join(PROJECT_ROOT, 'data/full_verify_last.json');
  if (existsSync(vPath)) {
    try {
      const v = JSON.parse(readFileSync(vPath, 'utf8'));
      verifyPass = v.pass;
    } catch { /* */ }
  }
  return {
    signal_date: signalDate,
    symbols: act.symbols,
    deliverable: act.deliverable,
    ohlcv: upstream.ohlcv,
    ml_pred: upstream.ml_pred,
    scan: upstream.scan,
    reconcile: `${recon.sent}/${recon.total} sent`,
    pending: recon.pending,
    verify_pass: verifyPass,
  };
}

export function formatOpsSuccessMessage(event, detail) {
  const lines = [`<b>${event}</b>`];
  if (detail.signal_date) lines.push(`📅 ${detail.signal_date}`);
  if (detail.symbols?.length) lines.push(`📈 ${detail.symbols.join(', ')}`);
  else if (detail.deliverable === 0) lines.push('📭 no actionable signals');
  if (detail.reconcile) lines.push(`✉️ reconcile: ${detail.reconcile}`);
  if (detail.ohlcv) lines.push(`📊 OHLCV ${detail.ohlcv} | ML ${detail.ml_pred || '—'}`);
  if (detail.verify_pass != null) lines.push(`🔍 verify: ${detail.verify_pass ? 'PASS' : 'FAIL'}`);
  return `✅ <b>EGX Ops OK</b>\n${lines.join('\n')}`;
}
