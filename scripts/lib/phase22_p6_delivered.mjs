/**
 * Phase 22 — P6 delivered live graduation (30/30 @ 60% WR).
 */
import { getLiveKpiDashboard } from './p6_live_kpi.mjs';
import { readJson, writeJson } from './graduation_phases.mjs';

export function evaluatePhase22P6Delivered() {
  const mode = process.env.EGX_P6_DELIVERED_MODE ?? 'live_accumulate';
  const wr = readJson('p6_delivered_wr_dashboard_last.json');
  const kpi = getLiveKpiDashboard();
  const bootstrapPass = Boolean(wr?.bootstrap?.pass) || Boolean(kpi?.bootstrap?.pass);
  const fullPass = Boolean(wr?.full_live?.pass);
  const useBootstrap = mode === 'historical_bootstrap' || mode === 'bootstrap';
  const pass = useBootstrap ? bootstrapPass : fullPass;

  const snap = {
    at: new Date().toISOString(),
    phase22_ready: pass,
    mode,
    gates: {
      delivered_wr: {
        pass,
        n: wr?.closed_n ?? kpi?.delivered?.safe_n ?? 0,
        wr: wr?.wr_pct ?? kpi?.delivered?.safe_wr,
        bootstrap_pass: bootstrapPass,
        full_pass: fullPass,
        detail: wr?.status_line ?? kpi?.delivered,
      },
    },
    wr_dashboard: wr,
    live_kpi: kpi,
    recommended: pass ? 'P6 delivered gate PASS' : `Need ${wr?.full_live?.samples_needed ?? 30} more delivered samples`,
  };
  writeJson('phase22_p6_delivered_last.json', snap);
  return snap;
}
