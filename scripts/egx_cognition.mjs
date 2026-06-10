/**
 * EGX Phase 16 — Autonomous Market Cognition Engine (standalone runner)
 * =======================================================================
 * Runs the full 7-stage cognition pipeline and optionally sends Telegram digest.
 *
 * Usage:
 *   node scripts/egx_cognition.mjs               # full cognition cycle
 *   node scripts/egx_cognition.mjs --notify      # + Telegram digest
 *   node scripts/egx_cognition.mjs --quick       # stock_dna + laws + evolve only
 *   node scripts/egx_cognition.mjs --report      # regenerate report only
 */

import { pythonCogFull, pythonCogStockDNA, pythonCogLaws,
         pythonCogEvolve, pythonCogReport, pythonCogStatus } from '../src/egx/index.js';
import { loadP6ResearchContext } from './lib/p6_research_context.mjs';
import { sendTelegram }                                       from '../src/egx/notify.js';
import { writeFileSync, readFileSync }                        from 'fs';
import { join, dirname }                                      from 'path';
import { fileURLToPath }                                      from 'url';

const __dirname  = dirname(fileURLToPath(import.meta.url));
const DATA_DIR   = join(__dirname, '../data');
const LOG_FILE   = join(DATA_DIR, 'cognition_log.json');

const NOTIFY = process.argv.includes('--notify');
const QUICK  = process.argv.includes('--quick');
const REPORT = process.argv.includes('--report');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (n = 65)  => wl('═'.repeat(n));

sep();
wl('  🧠 EGX AUTONOMOUS MARKET COGNITION ENGINE (Phase 16)');
wl(`  ${new Date().toISOString()} | mode: ${REPORT ? 'report-only' : QUICK ? 'quick' : 'full'}`);
sep();
wl('');

const t0 = Date.now();
let result;

if (REPORT) {
  wl('  📄 Regenerating cognition report...');
  result = await pythonCogReport();
} else if (QUICK) {
  wl('  ⚡ Quick mode — stock DNA + universal laws + self-evolution...');
  const [sd, ul, ev] = await Promise.all([
    pythonCogStockDNA(),
    pythonCogLaws(),
    pythonCogEvolve(),
  ]);
  result = {
    stock_dna:      sd,
    universal_laws: ul,
    self_evolution: ev,
    total_elapsed:  ((Date.now() - t0) / 1000).toFixed(1),
  };
} else {
  const p6 = loadP6ResearchContext();
  if (p6) {
    wl(`  📎 P6 context: ${p6.cognition_hints?.explosion_loss_count ?? 0} EXPLOSIVE ULTRA losses`);
    if (p6.cognition_hints?.prioritize_explosive_review) {
      wl('     Priority: EXPLOSIVE archetype review');
    }
  }
  wl('  🧪 Full pipeline — 7-stage cognition cycle');
  wl('  (Estimated: 30–120 seconds)\n');
  result = await pythonCogFull(p6 ? { p6_context: p6 } : {});
}

if (result.error) {
  wl(`  ❌ Cognition error: ${result.error}`);
  process.exit(1);
}

const elapsed = result.total_elapsed ?? ((Date.now() - t0) / 1000).toFixed(1);

// ── Display summary ──────────────────────────────────────────────────────────
if (REPORT) {
  wl(`  ✅ Report generated: ${result.report_file ?? '?'}`);
} else if (QUICK) {
  const sd = result.stock_dna    || {};
  const ul = result.universal_laws || {};
  const ev = result.self_evolution || {};
  wl(`  ✅ Quick cognition complete: ${elapsed}s`);
  wl(`  🧬 Stock DNA:     ${sd.profiles_built ?? 0} profiles | ${JSON.stringify(sd.archetype_dist ?? {})}`);
  wl(`  ⚖️  Universal Laws: ${ul.n_laws ?? 0} analyzed | ${ul.dominant ?? 0} DOMINANT`);
  wl(`  🔬 Self-Evolution: ${ev.variants_tested ?? 0} variants | ${(ev.best_variants||[]).length} improvements`);
} else {
  const sd = result.stock_dna         || {};
  const sc = result.sector_dna        || {};
  const ea = result.explosion_anatomy || {};
  const ul = result.universal_laws    || {};
  const mg = result.knowledge_graph   || {};
  const ev = result.self_evolution    || {};

  wl(`  ✅ Full cognition complete: ${elapsed}s (${result.stages_completed ?? '?'}/${result.stages_total ?? 7} stages)`);
  wl(`  🧬 Stock DNA:     ${sd.profiles_built ?? 0} profiles | ${JSON.stringify(sd.archetype_dist ?? {})}`);
  wl(`  🏭 Sector DNA:    ${sc.sectors_built ?? 0} sectors | ${sc.contagion_edges ?? 0} contagion edges`);
  wl(`  💥 Explosions:    ${(ea.total_explosions||0).toLocaleString()} studied | ${ea.n_universal ?? 0} universal signatures`);
  wl(`  ⚖️  Laws:          ${ul.n_laws ?? 0} analyzed | ${ul.dominant ?? 0} DOMINANT | ${ul.active ?? 0} ACTIVE`);
  wl(`  🕸️  Memory:        ${mg.nodes ?? 0} nodes | ${mg.edges ?? 0} edges`);
  wl(`  🔬 Evolution:     ${ev.variants_tested ?? 0} variants | ${(ev.best_variants||[]).length} improvements`);
  wl(`  📄 Report:        ${result.report_file ?? result.report?.report_file ?? '?'}`);

  if (result.key_findings?.length) {
    wl('');
    wl('  🔑 KEY DISCOVERIES:');
    for (const f of result.key_findings) wl(`    • ${f}`);
  }

  // Best evolution improvements
  const bestVariants = (ev.best_variants || []).filter(v =>
    v?.pattern_name && v?.direction && Number.isFinite(v?.variant_threshold) && Number.isFinite(v?.improvement_pp)
  );
  if (bestVariants.length) {
    wl('');
    wl('  📊 THRESHOLD IMPROVEMENTS:');
    for (const v of bestVariants.slice(0, 5))
      wl(`    ▲ ${v.pattern_name} (${v.direction})  thresh=${v.variant_threshold?.toFixed(5)}  +${v.improvement_pp?.toFixed(1)}pp`);
  }
}

// ── Telegram digest ──────────────────────────────────────────────────────────
if (NOTIFY && !REPORT) {
  wl('\n  📲 Sending cognition digest to Telegram...');
  const now     = new Date();
  const dateStr = now.toLocaleDateString('en-GB', { weekday:'short', day:'numeric', month:'short', year:'numeric' });

  const sd  = result.stock_dna         || {};
  const sc  = result.sector_dna        || {};
  const ea  = result.explosion_anatomy || {};
  const ul  = result.universal_laws    || {};
  const ev  = result.self_evolution    || {};

  const archText = Object.entries(sd.archetype_dist ?? {})
    .map(([k,v]) => `   ${k}: ${v}`).join('\n');

  const lawText = (ul.laws || [])
    .map(l => `   ${l.direction==='UP'?'▲':'▼'} ${l.pattern_name} (${l.direction}) P=${l.precision?.toFixed(3)} [${l.law_status}]`)
    .join('\n');

  const varText = (ev.best_variants || [])
    .filter(v => v?.pattern_name && Number.isFinite(v?.variant_threshold) && Number.isFinite(v?.improvement_pp))
    .slice(0, 3)
    .map(v => `   +${v.improvement_pp?.toFixed(1)}pp → ${v.pattern_name} @ ${v.variant_threshold?.toFixed(4)}`)
    .join('\n');

  const msg = `🧠 <b>EGX COGNITION ENGINE — Phase 16</b>
📅 <b>${dateStr}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
🧬 <b>STOCK DNA</b> (${sd.profiles_built ?? 0} stocks)
${archText || '   No data'}
━━━━━━━━━━━━━━━━━━━━━━━━
🏭 <b>SECTOR DNA</b>
   ${sc.sectors_built ?? 0} sectors | ${sc.contagion_edges ?? 0} contagion edges
━━━━━━━━━━━━━━━━━━━━━━━━
⚖️ <b>UNIVERSAL LAWS</b>
${lawText || '   No data'}
━━━━━━━━━━━━━━━━━━━━━━━━
🔬 <b>EVOLUTION IMPROVEMENTS</b>
${varText || '   No threshold improvements'}
━━━━━━━━━━━━━━━━━━━━━━━━
💥 <b>EXPLOSION ANATOMY</b>
   ${ea.n_universal ?? 0} universal signatures | ${(ea.total_explosions||0).toLocaleString()} explosions
━━━━━━━━━━━━━━━━━━━━━━━━
⏱ ${elapsed}s | ${now.toTimeString().slice(0,5)} UTC`;

  try {
    await sendTelegram(msg, { parseMode: 'HTML' });
    wl('  ✅ Telegram digest sent');
  } catch (e) {
    wl(`  ⚠️  Telegram failed: ${e.message}`);
  }
}

// ── Log run ──────────────────────────────────────────────────────────────────
try {
  let log = { runs: [] };
  try { log = JSON.parse(readFileSync(LOG_FILE, 'utf8')); } catch { /* first run */ }
  const sd = result.stock_dna         || {};
  const sc = result.sector_dna        || {};
  const ul = result.universal_laws    || {};
  const ev = result.self_evolution    || {};
  log.runs.push({
    timestamp:          new Date().toISOString(),
    mode:               REPORT ? 'report' : QUICK ? 'quick' : 'full',
    profiles_built:     sd.profiles_built,
    sectors_built:      sc.sectors_built,
    laws_analyzed:      ul.n_laws,
    dominant_laws:      ul.dominant,
    variants_tested:    ev.variants_tested,
    improvements:       (ev.best_variants || []).length,
    report_file:        result.report_file ?? result.report?.report_file,
    elapsed:            elapsed,
    notified:           NOTIFY,
  });
  if (log.runs.length > 90) log.runs = log.runs.slice(-90);
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
} catch (e) { wl(`  ⚠️  Log write failed: ${e.message}`); }

sep();
wl('  ✅ Phase 16 Autonomous Cognition complete');
sep();
