/**
 * EGX Deep Market Intelligence Discovery
 * ========================================
 * Runs the full DMIDS discovery pipeline and optionally sends
 * a structural intelligence digest to Telegram.
 *
 * Usage:
 *   node scripts/egx_discover.mjs                # full discovery
 *   node scripts/egx_discover.mjs --notify       # + send digest to Telegram
 *   node scripts/egx_discover.mjs --quick        # skip explosion scan (use cached)
 */

import { pythonDmidsFull, pythonDmidsReport, pythonDmidsStatus } from '../src/egx/index.js';
import { sendTelegram } from '../src/egx/notify.js';
import { writeFileSync, readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execFileSync } from 'child_process';
import { buildDiscoveryParams, discoveryContextSummary } from './lib/discovery_context.mjs';
import { resolveDiscoveryDirectives } from './lib/directive_resolver.mjs';
import { mergeStructuralLawsIntoRuntime } from './lib/structural_laws_bridge.mjs';
import { runDiscoveryQualityLoop } from './lib/discovery_quality_loop.mjs';
import { parsePythonJson } from './lib/parse_python_json.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR  = join(__dirname, '../data');
const LOG_FILE  = join(DATA_DIR, 'discovery_log.json');
const QUANT_SCRIPT = join(__dirname, 'python', 'quant_discovery.py');
const OPP_SCRIPT   = join(__dirname, 'python', 'opportunity_score_v2.py');

const NOTIFY = process.argv.includes('--notify');
const QUICK  = process.argv.includes('--quick');
const RESCORE = process.argv.includes('--rescore');
const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';

const wl  = (s='') => process.stdout.write(s+'\n');
const sep = (n=65)  => wl('═'.repeat(n));

sep();
wl('  🔬 EGX DEEP MARKET INTELLIGENCE DISCOVERY');
wl(`  ${new Date().toISOString()} | ${QUICK ? 'quick' : 'full'} mode`);
sep();
wl('');

const t0 = Date.now();

let result;
if (QUICK) {
  wl('  ⚡ Quick mode — regenerating report from cached data...');
  result = await pythonDmidsReport();
} else {
  wl('  🔭 Full discovery — analyzing 252 stocks...');
  result = await pythonDmidsFull();
}

if (result.error) {
  wl(`  ❌ Discovery error: ${result.error}`);
  process.exit(1);
}

const discoveryCtx = buildDiscoveryParams({ includeDirectives: true });
const { feedback, p6, oppFollowup, directives: p6Directives, params: discoveryParams } = discoveryCtx;
const ctxSummary = discoveryContextSummary(discoveryCtx);
wl(`  🔁 Discovery context: feedback=${ctxSummary.feedback_items} | p6=${ctxSummary.p6_signal_date || 'n/a'} | directives=${ctxSummary.pending_directives} | opp_alerts=${ctxSummary.opp_alerts}`);
if (feedback.n_items) {
  feedback.queue.slice(0, 4).forEach(item => {
    wl(`     • [${item.type}] ${item.target} — ${item.rationale}`);
  });
}
if (p6Directives.length) {
  p6Directives.slice(0, 3).forEach(d => {
    wl(`     • ${d.target} (p=${d.priority})`);
  });
}
if (oppFollowup?.alerts?.length) {
  wl(`  📈 Opp followup: ${oppFollowup.alerts.length} alert(s) — ${oppFollowup.alerts[0].code}`);
}
if (p6?.p6_gate && !p6.p6_gate.gate_pass) {
  wl(`  🎯 P6 gate: ${p6.p6_gate.n_completed}/${p6.p6_gate.min_n} @ ${p6.p6_gate.win_rate}% WR — quant prioritizes counterfactual atoms`);
}

let quant = null;
if (!QUICK) {
  wl('  🧪 Quant discovery — mining OOS entry rules...');
  const quantParams = JSON.stringify(discoveryParams);
  try {
    quant = JSON.parse(execFileSync(PYTHON3, [QUANT_SCRIPT, 'run', quantParams], {
      cwd: join(__dirname, '..'),
      encoding: 'utf8',
      timeout: 1000 * 60 * 20,
    }));
    if (quant?.success) {
      const fb = quant.feedback_applied?.n_items ?? 0;
      wl(`  ✅ Quant rules: ${quant.rules_kept}/${quant.rules_tested} kept | baseline=${(quant.baseline_precision * 100).toFixed(1)}% | feedback=${fb}`);
      (quant.top_rules || []).slice(0, 3).forEach((r, i) => {
        wl(`     ${i + 1}. ${r.rule} | OOS=${(r.precision * 100).toFixed(1)}% | lift=${r.lift}x | exp=${r.expectancy_pct}%`);
      });
    } else {
      wl(`  ⚠️ Quant discovery skipped: ${quant?.error ?? 'unknown error'}`);
    }
  } catch (e) {
    wl(`  ⚠️ Quant discovery failed: ${e.message}`);
  }
}

let opportunity = null;
wl('  🎯 Opportunity v2 — ranking market/sector/liquidity discovery map...');
try {
  const oppParams = JSON.stringify(discoveryParams);
  opportunity = JSON.parse(execFileSync(PYTHON3, [OPP_SCRIPT, 'run', oppParams], {
    cwd: join(__dirname, '..'),
    encoding: 'utf8',
    timeout: 1000 * 60 * 10,
  }));
  if (opportunity?.success) {
    const sc = opportunity.stage_counts || {};
    wl(`  ✅ Opportunity v2: ${opportunity.symbols_scored} scored | qualified=${sc.QUALIFIED_DISCOVERY ?? 0} | near=${sc.NEAR_BREAKOUT ?? 0} | early=${sc.EARLY_ACCUMULATION ?? 0}`);
    (opportunity.top || []).slice(0, 5).forEach((r, i) => {
      wl(`     ${i + 1}. ${r.symbol} | ${r.score} | ${r.stage} | ${r.sector}`);
    });
  } else {
    wl(`  ⚠️ Opportunity v2 skipped: ${opportunity?.error ?? 'unknown error'}`);
  }
} catch (e) {
  wl(`  ⚠️ Opportunity v2 failed: ${e.message}`);
}

// Extract key metrics
const rr = QUICK ? result : (result.research_report || result);
const sp = result.stock_profiles || {};
const es = result.explosion_scan || {};
const pd = result.precursor_discovery || {};
const ku = result.knowledge_update || {};

wl(`  ✅ Discovery complete: ${result.total_elapsed || ((Date.now()-t0)/1000).toFixed(1)}s`);
if (!QUICK) {
  wl(`  📊 Profiles: ${sp.n_profiled || '?'} | Explosions: ${es.total_explosions || '?'}`);
  wl(`  🧬 Patterns: ${pd.patterns_found || '?'} | Laws: ${ku.laws_generated || '?'}`);
}
wl(`  📄 Report: ${rr.report_file || '?'}`);

let structural = null;
if (!QUICK && (ku.laws_generated || 0) > 0) {
  try {
    structural = mergeStructuralLawsIntoRuntime({ minSupportPct: 30 });
    if (structural?.n_merged) {
      wl(`  ⚖️  Structural laws → runtime overlay: ${structural.n_merged} UP laws merged`);
    }
  } catch (e) {
    wl(`  ⚠️ Structural laws bridge: ${e.message}`);
  }
}

let rescore = null;
if (RESCORE && opportunity?.success) {
  wl('  🔄 Rescore — opportunity-aware score_all after weekly discovery...');
  try {
    const scoreScript = join(__dirname, 'python', 'signal_integration.py');
    const promoScript = join(__dirname, 'python', 'client_signal_promotion.py');
    const tradeDate = opportunity.trade_date;
    const scoreParams = JSON.stringify({ date: tradeDate });
    const promoParams = JSON.stringify({ date: tradeDate, ...discoveryParams });
    rescore = parsePythonJson(execFileSync(PYTHON3, [scoreScript, 'score_all', scoreParams], {
      cwd: join(__dirname, '..'),
      encoding: 'utf8',
      timeout: 1000 * 60 * 15,
    }));
    const promo = parsePythonJson(execFileSync(PYTHON3, [promoScript, promoParams], {
      cwd: join(__dirname, '..'),
      encoding: 'utf8',
      timeout: 1000 * 60 * 5,
    }));
    wl(`  ✅ Rescore: n_scored=${rescore?.n_scored ?? '?'} | promoted=${promo?.promoted ?? 0}`);
  } catch (e) {
    wl(`  ⚠️ Rescore failed: ${e.message}`);
  }
}

const discoveryQuality = runDiscoveryQualityLoop(opportunity?.trade_date);
if (discoveryQuality?.discovery_quality_score != null) {
  wl(`  📊 Discovery quality: ${discoveryQuality.discovery_quality_score}% (grade ${discoveryQuality.grade})`);
  if (quant?.discovery_quality?.discovery_quality_score) {
    wl(`     quant grade: ${quant.discovery_quality.grade} | rules kept: ${quant.rules_kept}`);
  }
}

const resolved = resolveDiscoveryDirectives({
  quantOk: !!quant?.success,
  oppOk: !!opportunity?.success,
  oppFollowup,
  feedback,
  structuralOk: !!structural?.n_merged,
});
if (resolved.completed) {
  wl(`  📋 Directives:  ${resolved.completed} marked COMPLETED`);
}

// Send Telegram digest if --notify
if (NOTIFY) {
  wl('\n  📲 Sending intelligence digest to Telegram...');

  // Build a concise Telegram message from the report
  const now = new Date();
  const dateStr = now.toLocaleDateString('en-GB', { weekday:'short', day:'numeric', month:'short', year:'numeric' });

  // Get sector and pattern highlights
  const laws    = rr.n_laws        || pd.patterns_found || 0;
  const expTotal= rr.total_explosions || es.total_explosions || 0;
  const nSectors= result.sector_cycles?.n_sectors || '?';
  const nProf   = rr.n_stocks || sp.n_profiled || '?';

  const archParts = Object.entries(sp.archetypes || {})
    .sort((a,b)=>b[1]-a[1])
    .slice(0,4)
    .map(([k,v]) => `${k}: ${v}`)
    .join(' | ');

  const lawsText = rr.n_up_patterns > 0 || rr.n_down_patterns > 0
    ? `\n🧬 <b>PRECURSOR LAWS (${laws}):</b>\n` +
      (rr.report_preview || '').split('\n')
        .filter(l => l.includes('❝') || l.includes('support='))
        .slice(0,6)
        .map(l => `   ${l.trim()}`)
        .join('\n')
    : '';

  const sectorLeader = result.sector_cycles?.most_synchronized || '?';
  const oppCounts = opportunity?.stage_counts || {};
  const oppTop = (opportunity?.top || [])
    .slice(0, 6)
    .map(r => `${r.symbol} ${r.score}`)
    .join(', ');

  const msg = `🔬 <b>EGX INTELLIGENCE DISCOVERY</b>
📅 <b>${dateStr}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
📊 <b>MARKET STRUCTURE</b>
   ${nProf} stocks profiled | ${expTotal} explosive moves
   ${archParts}
━━━━━━━━━━━━━━━━━━━━━━━━
🏭 <b>SECTOR INTELLIGENCE</b>
   ${nSectors} sectors | most synchronized: <code>${sectorLeader}</code>
   EGX is <b>FRAGMENTED</b> — moves are stock-specific, not sector-driven
━━━━━━━━━━━━━━━━━━━━━━━━
🎯 <b>OPPORTUNITY DISCOVERY v2</b>
   qualified: ${oppCounts.QUALIFIED_DISCOVERY ?? 0} | near: ${oppCounts.NEAR_BREAKOUT ?? 0} | early: ${oppCounts.EARLY_ACCUMULATION ?? 0}
   ${oppTop || 'No ranked symbols'}
━━━━━━━━━━━━━━━━━━━━━━━━${lawsText}
━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ System: ${result.total_elapsed || '?'}s | ${new Date().toTimeString().slice(0,5)} UTC`;

  try {
    await sendTelegram(msg, { parseMode: 'HTML' });
    wl('  ✅ Telegram digest sent');
  } catch (e) {
    wl(`  ⚠️  Telegram failed: ${e.message}`);
  }
}

// Log discovery
try {
  let log = { discoveries: [] };
  try { log = JSON.parse(readFileSync(LOG_FILE, 'utf8')); } catch { /* first run */ }
  log.discoveries.push({
    timestamp: new Date().toISOString(),
    n_profiles: sp.n_profiled,
    n_explosions: es.total_explosions,
    n_patterns: pd.patterns_found,
    n_laws: ku.laws_generated,
    n_quant_rules: quant?.rules_kept ?? null,
    opportunity_trade_date: opportunity?.trade_date ?? null,
    opportunity_scored: opportunity?.symbols_scored ?? null,
    opportunity_stage_counts: opportunity?.stage_counts ?? null,
    discovery_feedback_items: feedback.n_items,
    p6_directives_pending: p6Directives.length,
    quant_feedback_applied: quant?.feedback_applied ?? null,
    p6_context: ctxSummary,
    structural_laws_merged: structural?.n_merged ?? null,
    discovery_quality_score: discoveryQuality?.discovery_quality_score ?? null,
    discovery_grade: discoveryQuality?.grade ?? null,
    rescore_n: rescore?.n_scored ?? null,
    elapsed: result.total_elapsed || ((Date.now()-t0)/1000).toFixed(1),
    notified: NOTIFY,
  });
  if (log.discoveries.length > 90) log.discoveries = log.discoveries.slice(-90);
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
} catch (e) { wl(`  ⚠️  Log write failed: ${e.message}`); }

sep();
wl(`  ✅ Phase 12 DMIDS complete`);
sep();
process.exit(0);
