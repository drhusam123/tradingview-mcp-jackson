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

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR  = join(__dirname, '../data');
const LOG_FILE  = join(DATA_DIR, 'discovery_log.json');
const QUANT_SCRIPT = join(__dirname, 'python', 'quant_discovery.py');
const OPP_SCRIPT   = join(__dirname, 'python', 'opportunity_score_v2.py');

const NOTIFY = process.argv.includes('--notify');
const QUICK  = process.argv.includes('--quick');

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

let quant = null;
if (!QUICK) {
  wl('  🧪 Quant discovery — mining OOS entry rules...');
  try {
    quant = JSON.parse(execFileSync('python3', [QUANT_SCRIPT, 'run', '{}'], {
      cwd: join(__dirname, '..'),
      encoding: 'utf8',
      timeout: 1000 * 60 * 20,
    }));
    if (quant?.success) {
      wl(`  ✅ Quant rules: ${quant.rules_kept}/${quant.rules_tested} kept | baseline=${(quant.baseline_precision * 100).toFixed(1)}%`);
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
  opportunity = JSON.parse(execFileSync('python3', [OPP_SCRIPT, 'run'], {
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
