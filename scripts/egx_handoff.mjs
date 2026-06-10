#!/usr/bin/env node
/**
 * Production handoff summary — one screen for Dr. Husam.
 * Usage: node scripts/egx_handoff.mjs
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { nextTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';
import { getProofLoopMetrics, formatProofLoopLine } from './lib/proof_loop.mjs';
import { runDailyQualityGate } from './lib/data_quality_gate.mjs';

loadEnv();

function readJson(rel) {
  const p = join(PROJECT_ROOT, rel);
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; }
}

const cairo = cairoDateParts();
const digest = buildDeliveryDigest();
const nxt = nextTradingDay(cairo.date);
const ready = readJson('data/prod_ready_last.json');
const verify = readJson('data/full_verify_last.json');
const proof = readJson('data/proof_loop_last.json') || getProofLoopMetrics();
let trustLine = '—';
try {
  const g = runDailyQualityGate();
  trustLine = g.blocked
    ? `❌ BLOCKED (${g.reason})`
    : `✅ ${g.latest_date} trust=${g.trust_score} (${g.trust_status})`;
} catch { /* optional */ }
const proofLine = formatProofLoopLine(proof);
const learn = readJson('data/learning_loop_last.json');
const closedLoop = readJson('data/closed_loop_last.json');
const learnLine = learn?.counterfactual?.projected_wr != null
  ? `  Learning:    WR ${learn.counterfactual.actual_wr}%→${learn.counterfactual.projected_wr}% (+${learn.counterfactual.wr_delta ?? 0}pp counterfactual)`
  : '';
const autopsy = learn?.loss_autopsy || readJson('data/loss_autopsy_last.json');
const autopsyLine = autopsy?.n_residual_losses != null
  ? `  Loss autopsy: ${autopsy.n_residual_losses} residual | rules ${autopsy.proposed_rules?.length ?? 0}`
  : '';
const p6Ctx = readJson('data/p6_research_context.json');
const oppFollow = readJson('data/opportunity_followup_last.json');
const closedLine = closedLoop?.discovery_feedback != null
  ? `  Closed loop: ${closedLoop.directives_ingested ?? 0} directives | ${closedLoop.discovery_feedback} discovery | opp Q ${closedLoop.opportunity_quality?.quality_score ?? '—'}%`
  : '';
const p6CtxLine = p6Ctx?.p6_gate
  ? `  P6 context:  ${p6Ctx.ultra_losses?.length ?? 0} ULTRA losses | downrank ${(p6Ctx.evolution_hints?.downrank_behavioral || []).join(',') || '—'}`
  : '';
const oppTrendLine = oppFollow?.alerts?.length
  ? `  Opp followup: ${oppFollow.alerts.length} alert(s) — ${oppFollow.alerts[0]?.code}`
  : '';

let gitLine = 'git: unknown';
try {
  const sb = execSync('git status -sb', { cwd: PROJECT_ROOT, encoding: 'utf8' }).trim().split('\n')[0];
  gitLine = sb.replace('## ', '');
} catch { /* */ }

console.log(`
╔══════════════════════════════════════════════════════════════╗
║  EGX PRODUCTION — HANDOFF SUMMARY                            ║
╚══════════════════════════════════════════════════════════════╝

  Cairo:        ${cairo.date} ${String(cairo.hour).padStart(2, '0')}:${String(cairo.minute).padStart(2, '0')}
  Last OHLCV:   ${digest.ohlcv ?? '—'}
  Deliverable:  ${digest.symbols?.join(', ') || 'none'} (${digest.deliverable})
  Reconcile:    ${digest.reconcile} | pending ${digest.pending}
  Next session: ${nxt.next_trading_day}

  Prod ready:   ${ready?.pass ? '✅ PASS' : ready ? '❌ FAIL' : '—'} ${ready?.at?.slice(0, 19) ?? ''}
  Full verify:  ${verify?.pass ? '✅ PASS' : verify ? '❌ FAIL' : '—'} ${verify?.at?.slice(0, 19) ?? ''}
  Data trust:   ${trustLine}
  ${proofLine}
${learnLine}
${autopsyLine}
${closedLine}
${p6CtxLine}
${oppTrendLine}
  Git:          ${gitLine}

── ONE COMMANDS ──
  npm run egx:prod:ready          # 7-step gate
  npm run egx:automation:status   # runbook + digest + logs
  npm run egx:runbook:next        # next session preview
  npm run egx:proof:forensic      # ULTRA WR breakdown (P6)
  npm run egx:closed:loop         # master closed loop (all stages)
  npm run egx:learning:loop       # forensic + counterfactual + laws
  npm run egx:loss:autopsy        # residual ULTRA loss patterns
  npm run egx:p6:status           # P6 samples + counterfactual
  npm run egx:cache:backfill      # historical indicators_cache gaps
  npm run egx:opportunity:followup # opp quality trend alerts
  npm run egx:loop:audit          # closed-loop health check

── DOCS ──
  docs/PRODUCTION_AUTOMATION.md

── SUNDAY 2026-06-14 (automated) ──
  06:45 prod:ready → 16:30 TV → 17:20 Telegram → 17:45 post-session
  Ops alerts: EGX_ALERT_TELEGRAM=1 | EGX_OPS_SUCCESS_ALERT=1

  No manual action required.

  Push to GitHub (optional backup):
    npm run egx:git:sync -- --push
`);
