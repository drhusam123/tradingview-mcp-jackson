/**
 * LRE forward OOS gate — tracks 40/40 for EGX_LRE_FEED_BOOST promotion.
 */
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

const DEFAULT_TARGET = parseInt(process.env.EGX_LRE_OOS_TARGET ?? '40', 10);

function readLreStatus() {
  const p = join(PROJECT_ROOT, 'data/lre_4_0_status_last.json');
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

export function evaluateLreOosGate() {
  const lre = readLreStatus();
  const progress = lre?.graduation?.progress ?? {};
  const checks = lre?.graduation?.checks ?? {};
  const closed = progress.live_oos_closed
    ?? lre?.forward_oos_closed
    ?? lre?.forward_shadow?.metrics?.n_closed
    ?? 0;
  const target = progress.target_oos ?? lre?.forward_oos_target ?? DEFAULT_TARGET;
  const pf = progress.live_pf_proxy ?? lre?.forward_shadow?.metrics?.pf_100bps_proxy;
  const dom = progress.live_dominance_pct ?? lre?.forward_shadow?.metrics?.top10_dominance_pct;
  const wfPf = progress.historical_wf_pf_100 ?? lre?.walk_forward_baseline?.primary_capped_pf_100;
  const lreBoot = process.env.EGX_LRE_OOS_BOOTSTRAP === '1';
  const wfBootstrapPass = lreBoot && closed < target && (wfPf ?? 0) >= 1.3;
  const pass = (closed >= target
    && Boolean(checks.pf_ge_1_3 ?? (pf != null && pf >= 1.3))
    && Boolean(checks.dominance_lt_35 ?? (dom != null && dom < 35)))
    || wfBootstrapPass;

  return {
    at: new Date().toISOString(),
    pass,
    oos_closed: closed,
    oos_target: target,
    pf_proxy: pf,
    dominance_pct: dom,
    verdict: lre?.graduation?.verdict ?? 'UNKNOWN',
    env: 'EGX_LRE_FEED_BOOST',
    recommended: pass ? '1' : '0',
    reason: pass
      ? (wfBootstrapPass && closed < target
        ? `LRE WF bootstrap PASS (PF ${wfPf}) — live OOS ${closed}/${target} accumulating`
        : `LRE OOS ${closed}/${target} + quality PASS`)
      : `LRE OOS ${closed}/${target} accumulating`,
    bootstrap_wf: lreBoot ? wfBootstrapPass : null,
    wf_pf_100: wfPf,
    trade_date: lre?.trade_date ?? null,
  };
}
