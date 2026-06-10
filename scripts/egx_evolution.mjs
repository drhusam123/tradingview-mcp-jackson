/**
 * EGX Phase 15 — Self-Learning Market Evolution Engine (standalone runner)
 * =========================================================================
 * Runs the full self-learning pipeline and optionally sends a Telegram digest.
 *
 * Usage:
 *   node scripts/egx_evolution.mjs               # full evolution cycle
 *   node scripts/egx_evolution.mjs --notify      # + Telegram digest
 *   node scripts/egx_evolution.mjs --quick       # confidence + reinforcement only
 */

import { pythonEvoFull, pythonEvoConfidence,
         pythonEvoReinforce, pythonEvoStatus }      from '../src/egx/index.js';
import { sendTelegram }                             from '../src/egx/notify.js';
import { writeFileSync, readFileSync }              from 'fs';
import { join, dirname }                            from 'path';
import { fileURLToPath }                            from 'url';

const __dirname  = dirname(fileURLToPath(import.meta.url));
const DATA_DIR   = join(__dirname, '../data');
const LOG_FILE   = join(DATA_DIR, 'evolution_log.json');

const NOTIFY     = process.argv.includes('--notify');
const QUICK      = process.argv.includes('--quick');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (n = 65)  => wl('═'.repeat(n));

sep();
wl('  🧠 EGX SELF-LEARNING EVOLUTION ENGINE (Phase 15)');
wl(`  ${new Date().toISOString()} | mode: ${QUICK ? 'quick' : 'full'}`);
sep();
wl('');

const t0 = Date.now();
let result;

if (QUICK) {
  wl('  ⚡ Quick mode — confidence evolution + structural reinforcement...');
  const [conf, rf] = await Promise.all([pythonEvoConfidence(), pythonEvoReinforce()]);
  result = { confidence: conf, reinforcement: rf,
             total_elapsed: ((Date.now() - t0) / 1000).toFixed(1) };
} else {
  wl('  🧪 Full pipeline — 7-stage self-learning evolution cycle');
  wl('  (Estimated: 10–30 seconds)\n');
  result = await pythonEvoFull();
}

if (result.error) {
  wl(`  ❌ Evolution error: ${result.error}`);
  process.exit(1);
}

const elapsed = result.total_elapsed ?? ((Date.now() - t0) / 1000).toFixed(1);

const conf = result.confidence    || {};
const rf   = result.reinforcement || {};
const fr   = result.failures      || {};
const st   = result.stocks        || {};
const hyp  = result.hypotheses    || {};
const rc   = result.regime_models || {};
const by_s = rf.by_status         || {};
const REINFORCE_ICONS = { REINFORCED:'🟢', ACTIVE:'✅', DEGRADING:'🟡', ARCHIVED:'❌' };

wl(`  ✅ Evolution complete: ${elapsed}s`);
wl(`  📈 Confidence:     ${conf.laws_updated ?? 0} laws | ▲${conf.gaining ?? 0} gaining ▼${conf.losing ?? 0} losing`);
wl(`  ⚡ Reinforcement:  ${Object.entries(by_s).map(([k,v]) => `${REINFORCE_ICONS[k]??k}=${v}`).join(' ')}`);

if (!QUICK) {
  wl(`  ⚠️  Failures:      ${(fr.total_failures_analyzed??0).toLocaleString()} analyzed`);
  wl(`  🏭 Stocks:        ${st.stocks_profiled ?? 0} profiled | EXPLOSIVE=${(st.behavioral_distribution??{}).EXPLOSIVE??0}`);
  wl(`  🔬 Hypotheses:    ${hyp.new_candidates??0} new | ${hyp.promoted??0} validated`);
  wl(`  🌐 Regime models: ${rc.models_calibrated??0} calibrated | avg|err|=${((rc.avg_abs_error??0)*100).toFixed(1)}%`);
  wl(`  📄 Report:        ${result.report_file ?? '?'}`);
}

// Print key learnings
if (result.key_findings?.length) {
  wl('');
  wl('  🧪 KEY LEARNINGS:');
  for (const f of result.key_findings) wl(`    • ${f}`);
}

// Print confidence evolution detail
const updates = conf.updates ?? [];
if (updates.length) {
  wl('');
  wl('  📊 CONFIDENCE EVOLUTION:');
  for (const u of [...updates].sort((a, b) => Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))) {
    const arrow = (u.delta ?? 0) >= 0 ? '▲' : '▼';
    const rp = u.rolling_precision ?? u.rolling_sr ?? 0;
    const ap = u.alltime_precision ?? 0;
    wl(`    ${arrow} ${(u.pattern + ' (' + u.direction + ')').padEnd(34)} ${u.old_conf?.toFixed(3)} → ${u.new_conf?.toFixed(3)}  (${(rp*100).toFixed(1)}% vs ${(ap*100).toFixed(1)}% baseline)`);
  }
}

// ── Telegram digest ──────────────────────────────────────────────────────────
if (NOTIFY) {
  wl('\n  📲 Sending evolution digest to Telegram...');

  const CONF_ICONS = { REINFORCED:'🟢', ACTIVE:'✅', DEGRADING:'🟡', ARCHIVED:'❌' };
  const now     = new Date();
  const dateStr = now.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric'});

  const confText = (conf.updates ?? []).map(u => {
    const arrow = (u.delta ?? 0) >= 0 ? '▲' : '▼';
    return `   ${arrow} ${u.pattern} (${u.direction}) ${u.old_conf?.toFixed(3)}→${u.new_conf?.toFixed(3)}`;
  }).join('\n');

  const rfText = Object.entries(by_s)
    .map(([k, v]) => `   ${CONF_ICONS[k]??k} ${k}: ${v}`).join('\n');

  const failText = Object.entries(fr.global_cause_distribution ?? {})
    .slice(0, 3).map(([k, v]) => `   ${k}: ${v.pct}%`).join('\n');

  const msg = `🧠 <b>EGX EVOLUTION ENGINE</b>
📅 <b>${dateStr}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
📈 <b>CONFIDENCE EVOLUTION</b>
${confText || '   No updates'}
━━━━━━━━━━━━━━━━━━━━━━━━
⚡ <b>STRUCTURAL REINFORCEMENT</b>
${rfText || '   No data'}
━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ <b>FAILURE ROOT CAUSES</b>
${failText || '   No failures analyzed'}
━━━━━━━━━━━━━━━━━━━━━━━━
🔬 <b>HYPOTHESIS EVOLUTION</b>
   ${hyp.new_candidates??0} new | ${hyp.promoted??0} validated
━━━━━━━━━━━━━━━━━━━━━━━━
⏱ ${elapsed}s | ${now.toTimeString().slice(0, 5)} UTC`;

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
  log.runs.push({
    timestamp:       new Date().toISOString(),
    mode:            QUICK ? 'quick' : 'full',
    laws_updated:    conf.laws_updated,
    gaining:         conf.gaining,
    losing:          conf.losing,
    reinforced:      by_s.REINFORCED ?? 0,
    archived:        by_s.ARCHIVED ?? 0,
    stocks_profiled: st.stocks_profiled,
    hypotheses_new:  hyp.new_candidates,
    report_file:     result.report_file,
    elapsed:         elapsed,
    notified:        NOTIFY,
  });
  if (log.runs.length > 90) log.runs = log.runs.slice(-90);
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
} catch (e) { wl(`  ⚠️  Log write failed: ${e.message}`); }

sep();
wl('  ✅ Phase 15 Self-Learning Evolution complete');
sep();
