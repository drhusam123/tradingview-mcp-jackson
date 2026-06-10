#!/usr/bin/env node
/**
 * Master closed loop — measure → learn → apply → discover → monitor.
 *
 * Stages:
 *   1. sync delivered outcomes (audit → recommendation_outcomes)
 *   2. proof snapshot + forensic
 *   3. learning loop (counterfactual + autopsy + laws)
 *   4. runtime rules merge (delivery_laws → egx_rules_runtime.json)
 *   5. P6 directives → research_directives
 *   6. opportunity quality (high-opp → actionable → delivered)
 *   7. discovery feedback queue
 *   8. opportunity followup (trend alerts)
 *   9. p6_research_context → evolution + cognition
 *
 * Usage: node scripts/egx_closed_loop.mjs [--json] [--date YYYY-MM-DD]
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { getProofLoopMetrics, writeProofLoopSnapshot, PROOF_MIN_N, PROOF_MIN_WR } from './lib/proof_loop.mjs';
import { syncDeliveredOutcomes } from './lib/delivered_outcomes.mjs';
import { mergeRuntimeRules } from './lib/runtime_rules_merge.mjs';
import { ingestP6Directives } from './lib/p6_directives_ingest.mjs';
import { runOpportunityQualityLoop } from './lib/opportunity_quality_loop.mjs';
import { buildDiscoveryFeedback } from './lib/discovery_feedback.mjs';
import { buildP6ResearchContext, writeP6ResearchContext } from './lib/p6_research_context.mjs';
import { analyzeOpportunityTrend } from './lib/opportunity_followup.mjs';
import { resolveClosedLoopDirectives } from './lib/directive_resolver.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';

loadEnv();

const NODE = process.execPath;
const AS_JSON = process.argv.includes('--json');
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const signalDate = dateArg || latestOhlcvDate() || cairoDateParts().date;

const stages = [];

function stage(name, fn) {
  const t0 = Date.now();
  try {
    const result = fn();
    stages.push({ name, ok: true, ms: Date.now() - t0, result });
    return result;
  } catch (e) {
    stages.push({ name, ok: false, ms: Date.now() - t0, error: e.message?.slice(0, 200) });
    return null;
  }
}

console.log('\n═══ EGX Closed Loop (master) ═══');
console.log(`  Signal date: ${signalDate}\n`);

const delivered = stage('sync_delivered_outcomes', () => syncDeliveredOutcomes());

const proofAll = stage('proof_snapshot_all', () => writeProofLoopSnapshot());
const proofDelivered = stage('proof_snapshot_delivered', () => {
  const m = getProofLoopMetrics({ deliveredOnly: true });
  return m;
});

let forensic = null;
stage('proof_forensic', () => {
  const out = execSync(`"${NODE}" scripts/egx_proof_forensic.mjs --json`, {
    cwd: PROJECT_ROOT,
    encoding: 'utf8',
    timeout: 120_000,
  });
  forensic = JSON.parse(out);
  return { n_losses: forensic.n_losses, by_class: Object.keys(forensic.by_class || {}) };
});

let learning = null;
stage('learning_loop', () => {
  execSync(`"${NODE}" scripts/egx_learning_loop.mjs`, {
    cwd: PROJECT_ROOT,
    stdio: 'pipe',
    timeout: 300_000,
  });
  const p = join(PROJECT_ROOT, 'data/learning_loop_last.json');
  learning = existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : null;
  return {
    p6_wr: learning?.proof_loop?.win_rate,
    counterfactual_wr: learning?.counterfactual?.projected_wr,
    directives: learning?.directives?.length ?? 0,
  };
});

const runtime = stage('runtime_rules_merge', () => mergeRuntimeRules({ learningReport: learning }));

const opportunity = stage('opportunity_quality', () => runOpportunityQualityLoop(signalDate));
const oppFollowup = stage('opportunity_followup', () => analyzeOpportunityTrend());
const allDirectives = [
  ...(learning?.directives || []),
  ...(opportunity?.directives || []),
  ...(oppFollowup?.directives || []),
];
const ingested = stage('p6_directives_ingest', () => ingestP6Directives(allDirectives));
const discovery = stage('discovery_feedback', () => buildDiscoveryFeedback({
  forensic,
  autopsy: learning?.loss_autopsy,
  opportunity,
}));
const resolved = stage('directive_resolve', () => resolveClosedLoopDirectives({
  learning,
  runtime,
  oppFollowup,
}));
const p6Context = stage('p6_research_context', () => {
  const ctx = buildP6ResearchContext({
    signalDate,
    learning,
    forensic,
    discovery,
    opportunity,
    oppFollowup,
    ingested,
  });
  return writeP6ResearchContext(ctx);
});

const report = {
  at: new Date().toISOString(),
  cairo_date: cairoDateParts().date,
  signal_date: signalDate,
  stages,
  delivered_sync: delivered,
  proof_all: proofAll,
  proof_delivered: proofDelivered,
  learning_summary: learning ? {
    p6: learning.proof_loop,
    counterfactual: {
      projected_wr: learning.counterfactual?.projected_wr,
      would_block_losses: learning.counterfactual?.would_block_losses,
      would_block_wins: learning.counterfactual?.would_block_wins,
    },
    residual_losses: learning.loss_autopsy?.n_residual_losses,
  } : null,
  runtime_rules: { applied: runtime?.applied_laws?.length ?? 0, at: runtime?.at },
  directives_ingested: ingested?.ingested ?? 0,
  opportunity_quality: opportunity,
  discovery_feedback: discovery?.n_items ?? 0,
  opportunity_followup: oppFollowup,
  p6_research_context: p6Context,
  directives_resolved: resolved?.completed ?? 0,
  loops_closed: [
    'delivery_audit → recommendation_outcomes.client_delivered',
    'outcomes → proof → counterfactual → delivery_laws',
    'delivery_laws → egx_rules_runtime.json → safety_check',
    'directives → research_directives',
    'opportunity_score_v2 → promotion → safety → delivered',
    'forensic/autopsy → discovery_feedback → quant_discovery + score_all',
    'telegram_cron → syncDeliveredOutcomes after live send',
    'opportunity_quality → opportunity_quality_history.json',
    'opportunity_history → opportunity_followup → directives',
    'closed_loop → p6_research_context.json → evolution + cognition',
    'directives PENDING → engines → COMPLETED (directive_resolver)',
  ],
};

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/closed_loop_last.json'), JSON.stringify(report, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(report, null, 2));
  process.exit(0);
}

const fail = stages.filter(s => !s.ok).length;
console.log('  Stages:');
for (const s of stages) {
  console.log(`    ${s.ok ? '✅' : '❌'} ${s.name} (${s.ms}ms)`);
}
console.log(`\n  P6 all:        ${proofAll?.n_completed}/${PROOF_MIN_N} @ ${proofAll?.win_rate ?? '—'}%`);
console.log(`  P6 delivered:  ${proofDelivered?.n_completed ?? 0} @ ${proofDelivered?.win_rate ?? '—'}%`);
console.log(`  Runtime laws:  ${runtime?.applied_laws?.length ?? 0} applied`);
console.log(`  Directives:    ${ingested?.ingested ?? 0} ingested → research_directives`);
console.log(`  Discovery Q:   ${discovery?.n_items ?? 0} feedback items`);
console.log(`  Opportunity:   ${opportunity?.n_delivered ?? 0} delivered / ${opportunity?.n_top_opportunity ?? 0} high-opp`);
console.log(`  Opp followup:  ${oppFollowup?.alerts?.length ?? 0} alerts`);
console.log(`  P6 context:    ${p6Context?.path ?? '—'}\n`);
console.log('  Saved: data/closed_loop_last.json\n');

process.exit(fail ? 1 : 0);
