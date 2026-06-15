import Database from 'better-sqlite3';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import {
  DB_PATH, getUpstreamDates, latestOhlcvDate, countActionable, getAuditForDate,
} from './delivery_audit.mjs';
import { PROJECT_ROOT } from './load_env.mjs';

/** LRE-4.0 research feed snapshot from status artifact. */
export function loadLreResearchDigest() {
  const p = join(PROJECT_ROOT, 'data/lre_4_0_status_last.json');
  if (!existsSync(p)) return null;
  try {
    const s = JSON.parse(readFileSync(p, 'utf8'));
    const g = s.graduation || {};
    const pgr = g.progress || {};
    return {
      trade_date: s.trade_date,
      feed_rows: s.feed?.rows ?? 0,
      confluence: s.feed?.confluence_symbols ?? 0,
      max_boost: s.feed?.max_opp_boost ?? 0,
      forward_oos_closed: pgr.live_oos_closed ?? 0,
      forward_oos_target: pgr.target_oos ?? 40,
      graduation_verdict: g.verdict,
      integration: s.automation?.integration_verdict,
      acceptance: s.automation?.acceptance_verdict,
      wf_pf_100: s.walk_forward_baseline?.primary_capped_pf_100,
    };
  } catch {
    return null;
  }
}

/** P6 live KPI + Phase 14 pilot summary for ops digest. */
export function loadP6LiveKpiDigest() {
  const p = join(PROJECT_ROOT, 'data/p6_live_kpi_last.json');
  if (!existsSync(p)) return null;
  try {
    const k = JSON.parse(readFileSync(p, 'utf8'));
    const p14 = join(PROJECT_ROOT, 'data/phase14_graduation_last.json');
    let phase14 = null;
    if (existsSync(p14)) {
      try { phase14 = JSON.parse(readFileSync(p14, 'utf8')); } catch { /* */ }
    }
    return {
      status_line: k.status_line,
      ultra_n: k.ultra_safe?.n,
      ultra_target: k.ultra_safe?.target,
      ultra_wr: k.ultra_safe?.wr,
      bootstrap_pass: k.bootstrap_pass,
      med_shadow_sessions: k.phase13?.med_client_shadow?.sessions,
      med_probe: phase14?.med_probe?.probe_active ?? null,
      med_client_signal: phase14?.effective_env?.MED_CLIENT_SIGNAL ?? null,
    };
  } catch {
    return null;
  }
}

function loadClientBetaSignoffDigest() {
  const p = join(PROJECT_ROOT, 'data/client_beta_signoff_last.json');
  if (!existsSync(p)) return null;
  try {
    const s = JSON.parse(readFileSync(p, 'utf8'));
    return {
      signed_off: s.client_beta_signed_off,
      required: `${s.required_pass}/${s.required_total}`,
      blockers: s.blockers?.length ?? 0,
    };
  } catch {
    return null;
  }
}

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
    lre: loadLreResearchDigest(),
    p6_kpi: loadP6LiveKpiDigest(),
    client_beta: loadClientBetaSignoffDigest(),
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
  if (detail.lre?.feed_rows) {
    lines.push(
      `🔬 LRE feed: ${detail.lre.feed_rows} sym | conf ${detail.lre.confluence} | OOS ${detail.lre.forward_oos_closed}/${detail.lre.forward_oos_target}`,
    );
  }
  if (detail.p6_kpi?.status_line) {
    lines.push(`📈 P6 KPI: ${detail.p6_kpi.status_line}`);
    if (detail.p6_kpi.med_client_signal === '1') {
      lines.push('🧪 MED_CLIENT_SIGNAL probe active (shadow)');
    }
  }
  if (detail.client_beta) {
    lines.push(
      `🎓 Client beta: ${detail.client_beta.signed_off ? 'SIGNED OFF' : 'accumulating'} (${detail.client_beta.required} required)`,
    );
  }
  return `✅ <b>EGX Ops OK</b>\n${lines.join('\n')}`;
}
