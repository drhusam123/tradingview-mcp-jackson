/**
 * EGX Phase 13 — Deep Historical Validation (standalone runner)
 * ==============================================================
 * Runs the full DHVD pipeline and optionally sends a validation
 * digest to Telegram.
 *
 * Usage:
 *   node scripts/egx_dhvd.mjs               # full validation
 *   node scripts/egx_dhvd.mjs --notify      # + Telegram digest
 *   node scripts/egx_dhvd.mjs --laws-only   # walk-forward only (fast)
 *   node scripts/egx_dhvd.mjs --report      # report from cached DB
 */

import { pythonDhvdFull, pythonDhvdValidateLaws,
         pythonDhvdReport, pythonDhvdStatus }    from '../src/egx/index.js';
import { sendTelegram }                           from '../src/egx/notify.js';
import { writeFileSync, readFileSync, existsSync } from 'fs';
import { join, dirname }                          from 'path';
import { fileURLToPath }                          from 'url';

const __dirname  = dirname(fileURLToPath(import.meta.url));
const DATA_DIR   = join(__dirname, '../data');
const LOG_FILE   = join(DATA_DIR, 'dhvd_log.json');

const NOTIFY     = process.argv.includes('--notify');
const LAWS_ONLY  = process.argv.includes('--laws-only');
const REPORT_ONLY= process.argv.includes('--report');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (n = 65)  => wl('═'.repeat(n));

sep();
wl('  🔬 EGX DEEP HISTORICAL VALIDATION SYSTEM');
wl(`  ${new Date().toISOString()} | mode: ${REPORT_ONLY?'report':LAWS_ONLY?'laws-only':'full'}`);
sep();
wl('');

const t0 = Date.now();
let result;

if (REPORT_ONLY) {
  wl('  📄 Generating report from cached validation data...');
  result = await pythonDhvdReport();
} else if (LAWS_ONLY) {
  wl('  ⚡ Laws-only mode — walk-forward validation only...');
  result = await pythonDhvdValidateLaws();
} else {
  wl('  🧪 Full pipeline — validation → clustering → regimes → false breakouts → report');
  wl('  (Estimated: 60–120 seconds)\n');
  result = await pythonDhvdFull();
}

if (result.error) {
  wl(`  ❌ Validation error: ${result.error}`);
  process.exit(1);
}

const elapsed = result.total_elapsed ?? ((Date.now() - t0) / 1000).toFixed(1);

if (!REPORT_ONLY && !LAWS_ONLY) {
  const lv = result.law_validation    || {};
  const pf = result.precursor_families|| {};
  const rh = result.regime_history    || {};
  const fb = result.false_breakouts   || {};
  const rd = rh.regime_distribution  || {};

  wl(`  ✅ Validation complete: ${elapsed}s`);
  wl(`  📐 Laws:           ${lv.n_patterns ?? '?'} patterns | OOS n=${lv.oos_n_target ?? 0} | sig=${lv.n_significant_fdr ?? '?'}`);
  wl(`  🧬 Families:       ${pf.n_families ?? '?'} clusters | silhouette=${pf.silhouette_score ?? '?'}`);
  wl(`  🌐 Regimes:        ${rh.n_days ?? '?'}d | BULL:${rd.BULL ?? 0} BEAR:${rd.BEAR ?? 0} CHOPPY:${rd.CHOPPY ?? 0}`);
  wl(`  💀 False breakouts:${fb.n_false_breakouts ?? '?'} | false rate ${((fb.false_rate ?? 0) * 100).toFixed(1)}%`);
  wl(`  📄 Report:         ${result.report_file ?? '?'}`);

  // Print hypothesis lifecycle
  const hyps = (lv.results || []);
  if (hyps.length) {
    const STATUS_ICONS = { CONFIRMED:'✅',STRONG:'💪',VALIDATED:'🟢',DEGRADING:'🔶',WEAK:'🟡',REJECTED:'❌',DISCOVERED:'🔍' };
    wl('');
    wl('  HYPOTHESIS LIFECYCLE:');
    for (const h of hyps) {
      const icon = STATUS_ICONS[h.status] ?? '?';
      wl(`    ${icon} ${(h.pattern || '').padEnd(32)} conf=${(h.confidence ?? 0).toFixed(2)}  ${h.status}`);
    }
  }
} else if (LAWS_ONLY) {
  const STATUS_ICONS = { CONFIRMED:'✅',STRONG:'💪',VALIDATED:'🟢',DEGRADING:'🔶',WEAK:'🟡',REJECTED:'❌' };
  wl(`  ✅ Walk-forward complete: ${elapsed}s`);
  wl(`  ${result.n_patterns ?? '?'} patterns | OOS cutoff: ${result.oos_date_cutoff ?? '?'}\n`);
  for (const r of (result.results || [])) {
    const icon = STATUS_ICONS[r.status] ?? '?';
    wl(`  ${icon} ${r.pattern} (${r.direction}) | conf=${(r.confidence ?? 0).toFixed(2)} | ${r.status}`);
    wl(`      ${r.n_periods_passed}/${r.n_periods_tested} years | OOS: ${((r.oos_support ?? 0) * 100).toFixed(1)}% (n=${r.oos_n ?? 0})`);
  }
} else {
  wl(`  ✅ ${result.report_file ?? 'Report generated'}`);
}

// ── Telegram digest ─────────────────────────────────────────────
if (NOTIFY) {
  wl('\n  📲 Sending validation digest to Telegram...');

  const lv   = result.law_validation     || {};
  const pf   = result.precursor_families || {};
  const fb   = result.false_breakouts    || {};
  const hyps = lv.results || [];
  const STATUS_ICONS_TG = {
    CONFIRMED:'✅', STRONG:'💪', VALIDATED:'🟢',
    DEGRADING:'🔶', WEAK:'🟡', REJECTED:'❌',
  };

  const now     = new Date();
  const dateStr = now.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric'});

  const hypsText = hyps.map(h => {
    const icon = STATUS_ICONS_TG[h.status] ?? '❓';
    return `   ${icon} ${h.pattern} (${h.direction}) conf=${(h.confidence ?? 0).toFixed(2)}`;
  }).join('\n');

  const familiesText = (pf.families || []).slice(0, 4).map(f =>
    `   ${f.icon ?? '🔬'} ${f.name}: ${f.n} events | ${(f.recurrence * 100).toFixed(0)}%`
  ).join('\n');

  const msg = `🔬 <b>EGX HISTORICAL VALIDATION</b>
📅 <b>${dateStr}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
⚗️ <b>HYPOTHESIS LIFECYCLE</b>
${hypsText || '   No results yet'}
━━━━━━━━━━━━━━━━━━━━━━━━
🧬 <b>PRECURSOR FAMILIES (${pf.n_families ?? '?'})</b>
${familiesText || '   No clusters yet'}
━━━━━━━━━━━━━━━━━━━━━━━━
💀 <b>FALSE BREAKOUTS</b>
   ${fb.n_false_breakouts ?? '?'} detected | false rate ${((fb.false_rate ?? 0) * 100).toFixed(1)}%
━━━━━━━━━━━━━━━━━━━━━━━━
⏱ ${elapsed}s | ${now.toTimeString().slice(0, 5)} UTC`;

  try {
    await sendTelegram(msg, { parseMode: 'HTML' });
    wl('  ✅ Telegram digest sent');
  } catch (e) {
    wl(`  ⚠️  Telegram failed: ${e.message}`);
  }
}

// ── Log run ─────────────────────────────────────────────────────
try {
  let log = { runs: [] };
  try { log = JSON.parse(readFileSync(LOG_FILE, 'utf8')); } catch { /* first run */ }
  const lv = result.law_validation || {};
  const pf = result.precursor_families || {};
  const fb = result.false_breakouts || {};
  log.runs.push({
    timestamp:        new Date().toISOString(),
    mode:             REPORT_ONLY ? 'report' : LAWS_ONLY ? 'laws-only' : 'full',
    n_patterns:       lv.n_patterns,
    oos_cutoff:       lv.oos_date_cutoff,
    n_families:       pf.n_families,
    silhouette:       pf.silhouette_score,
    n_false_breakouts:fb.n_false_breakouts,
    elapsed:          elapsed,
    notified:         NOTIFY,
  });
  if (log.runs.length > 60) log.runs = log.runs.slice(-60);
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
} catch (e) { wl(`  ⚠️  Log write failed: ${e.message}`); }

sep();
wl('  ✅ Phase 13 DHVD complete');
sep();
