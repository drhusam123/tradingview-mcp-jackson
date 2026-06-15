#!/usr/bin/env node
/**
 * Institutional audit orchestrator — generates audit/*.md + system_audit_snapshot.json
 *
 * Usage:
 *   npm run egx:audit:snapshot
 *   npm run egx:audit:all
 *   node scripts/egx_system_audit_orchestrator.mjs --engines|--gates|--automation
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate, DB_PATH } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { DISCOVERY_ENGINES } from './lib/discovery_engine_registry.mjs';
import { writeDeepScanArtifacts } from './lib/audit_deep_scan.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const ALL = process.argv.includes('--all') || !process.argv.some(a => a.startsWith('--'));
const AS_JSON = process.argv.includes('--json');

function readJson(name) {
  const p = join(PROJECT_ROOT, 'data', name);
  if (!existsSync(p)) return null;
  try { return JSON.parse(readFileSync(p, 'utf8')); } catch { return null; }
}

function runCapture(cmd, timeout = 120_000) {
  try {
    const out = execSync(cmd, { cwd: PROJECT_ROOT, encoding: 'utf8', timeout, stdio: ['pipe', 'pipe', 'pipe'] });
    return { ok: true, out: out.slice(0, 8000) };
  } catch (e) {
    return { ok: false, error: e.message?.slice(0, 200), out: (e.stdout || e.stderr || '').slice(0, 2000) };
  }
}

function writeMd(path, lines) {
  mkdirSync(join(PROJECT_ROOT, 'audit'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'audit', path), lines.join('\n') + '\n');
}

function pipelineDbStats(signalDate) {
  if (!existsSync(DB_PATH)) return {};
  const db = new Database(DB_PATH, { readonly: true });
  const latest = signalDate ?? db.prepare(
    "SELECT MAX(date(bar_time,'unixepoch')) d FROM ohlcv_history",
  ).get()?.d;
  const universe = latest
    ? db.prepare(
      "SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history WHERE date(bar_time,'unixepoch') = ?",
    ).get(latest)?.n ?? 0
    : db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history').get()?.n ?? 0;
  const expected = (() => {
    try {
      return db.prepare(
        "SELECT COUNT(*) n FROM symbol_universe WHERE status IN ('ACTIVE','WATCH')",
      ).get()?.n;
    } catch {
      try {
        return db.prepare("SELECT COUNT(DISTINCT symbol) n FROM ohlcv_history").get()?.n;
      } catch { return universe; }
    }
  })() ?? universe;
  const badRows = db.prepare(
    "SELECT COUNT(*) n FROM ohlcv_history WHERE close <= 0 OR volume < 0",
  ).get()?.n ?? 0;
  const dupes = db.prepare(`
    SELECT COUNT(*) n FROM (
      SELECT symbol, date(bar_time,'unixepoch') d, COUNT(*) c FROM ohlcv_history
      GROUP BY symbol, d HAVING c > 1
    )
  `).get()?.n ?? 0;
  db.close();
  return { latest, universe, expected, badRows, dupes };
}

// ── DATA PIPELINE AUDIT ─────────────────────────────────────────────────────
function generateDataPipelineAudit(dataLayer) {
  const signalDate = latestOhlcvDate();
  const stats = pipelineDbStats(signalDate);
  const missing = Math.max(0, (stats.expected ?? 0) - (stats.universe ?? 0));
  const lines = [
    '# Data Pipeline Audit',
    '',
    `**Generated:** ${new Date().toISOString()}`,
    `**Signal date:** ${signalDate ?? stats.latest ?? '—'}`,
    '',
    '## Pipeline fields',
    '',
    `| Field | Value |`,
    `|-------|-------|`,
    `| Data Source | TradingView CDP → \`egx_tv_auto_update\` → \`ohlcv_history\` |`,
    `| Universe Size | ${stats.universe ?? '—'} |`,
    `| Expected Symbols | ${stats.expected ?? '—'} |`,
    `| Actual Symbols | ${stats.universe ?? '—'} |`,
    `| Missing Symbols | ${missing} |`,
    `| Latest Date | ${stats.latest ?? '—'} |`,
    `| Freshness Status | ${dataLayer?.pass ? 'PASS' : 'WARN/FAIL'} |`,
    `| Bad Rows | ${stats.badRows ?? 0} |`,
    `| Duplicates | ${stats.dupes ?? 0} |`,
    `| Artifacts | See L0/L1 checks |`,
    '',
    '## L0/L1 checks',
    '',
    `| Check | Status | Detail |`,
    `|-------|--------|--------|`,
  ];
  for (const c of dataLayer?.checks ?? []) {
    lines.push(`| ${c.id} | ${c.ok ? '✅' : '❌'} | ${c.detail ?? ''} |`);
  }
  const deep = readJson('audit_deep_scan_last.json');
  if (deep?.exclusion_reconcile?.purged) {
    lines.push('', '## Fixes Applied', '', `- Purged ${deep.exclusion_reconcile.purged} orphan bar exclusions`, '');
  } else {
    lines.push('', '## Fixes Applied', '', '- Automated via `egx_data_layer_audit.mjs` + `audit_deep_scan`', '');
  }
  lines.push('## Remaining Risks', '', dataLayer?.pass ? '- None critical' : `- Failed: ${(dataLayer?.failed ?? []).join(', ')}`, '');
  writeMd('DATA_PIPELINE_AUDIT.md', lines);
  return { file: 'DATA_PIPELINE_AUDIT.md', pass: Boolean(dataLayer?.pass) };
}

// ── ENGINES AUDIT ───────────────────────────────────────────────────────────
function generateEnginesAudit() {
  const core = [
    { name: 'LRE 4.0', cmd: 'egx:lre:status', artifact: 'lre_4_0_status_last.json', layer: 'client-shadow' },
    { name: 'MED 0.3', cmd: 'egx:med:run', artifact: 'med_0_3_status_last.json', layer: 'client' },
    { name: 'MDE shadow', cmd: 'egx:mde:shadow', artifact: 'mde_pilot_shadow_last.json', layer: 'shadow' },
    { name: 'Fabric', cmd: 'egx:fabric:status', artifact: 'fabric_status_last.json', layer: 'research' },
    { name: 'P6 delivered', cmd: 'egx:phase22:p6-delivered', artifact: 'phase22_p6_delivered_last.json', layer: 'KPI' },
    { name: 'LRE OOS acc', cmd: 'lre_oos_accumulator.py', artifact: 'lre_oos_accumulator_last.json', layer: 'shadow' },
    { name: 'Graduation', cmd: 'egx:graduation:complete', artifact: 'graduation_final_last.json', layer: 'ops' },
    { name: 'Score all', cmd: 'egx:score:all', artifact: 'score_all_last.json', layer: 'scoring' },
    { name: 'Gates', cmd: 'gate_doctor_audit.py', artifact: 'gate_doctor_last.json', layer: 'gates' },
  ];

  const discovery = Object.values(DISCOVERY_ENGINES).map(e => ({
    name: e.id,
    cmd: e.npm ?? '—',
    artifact: (e.outputs?.[0] ?? `${e.id}_last.json`).replace(/\.json$/, '') + '_last.json',
    layer: e.layer ?? 'discovery',
    runnable: e.runnable !== false,
  }));

  const lines = [
    '# Engines Audit',
    '',
    `**Generated:** ${new Date().toISOString()}`,
    '',
    '## Core production engines',
    '',
    '| Engine | Command | Layer | Output artifact | Last Run | Status |',
    '|--------|---------|-------|-----------------|----------|--------|',
  ];

  const results = [];
  for (const e of core) {
    const art = readJson(e.artifact);
    const status = art ? (art.success === false ? 'FAIL' : 'PASS') : 'NOT_RUN';
    const runAt = art?.at ?? art?.run_at ?? '—';
    lines.push(`| ${e.name} | \`${e.cmd}\` | ${e.layer} | \`${e.artifact}\` | ${runAt} | ${status} |`);
    results.push({ engine: e.name, status, artifact: e.artifact });
  }

  lines.push('', '## Discovery / research registry', '');
  lines.push('| Engine | Command | Layer | Runnable | Outputs |', '|--------|---------|-------|----------|---------|');
  for (const e of discovery) {
    const eng = DISCOVERY_ENGINES[e.name];
    lines.push(`| ${e.name} | \`${e.cmd}\` | ${e.layer} | ${e.runnable ? 'yes' : 'no'} | ${(eng?.outputs ?? []).join(', ')} |`);
    results.push({ engine: e.name, status: e.runnable ? 'REGISTERED' : 'CHAIN_ONLY', artifact: e.artifact });
  }

  writeMd('ENGINES_AUDIT.md', lines);
  return { file: 'ENGINES_AUDIT.md', engines: results };
}

function appendSystemMapOrphans(deep) {
  const mapPath = join(PROJECT_ROOT, 'audit/SYSTEM_MAP.md');
  if (!existsSync(mapPath)) return;
  let text = readFileSync(mapPath, 'utf8');
  const marker = '## 25. Orphan & Registry Analysis';
  if (text.includes(marker)) {
    text = text.split(marker)[0].trimEnd();
  }
  const ss = deep?.script_scan ?? {};
  const ts = deep?.table_scan ?? {};
  const block = [
    '',
    '---',
    '',
    marker,
    '',
    `**Generated:** ${deep?.at ?? new Date().toISOString()}`,
    '',
    '### 8. Duplicate / unlinked layers',
    '',
    '- Discovery engines with `runnable: false` (e.g. `closed_loop`) are invoked only from `post_session_ops` — not orphans.',
    '- Shadow engines (MDE, LRE OOS) write research tables only; no client promotion without graduation gates.',
    '',
    '### 9. Scripts present but not in package.json/npm',
    '',
    `- Total scripts: **${ss.total ?? '—'}** | Referenced: **${ss.referenced ?? '—'}** | Orphans: **${ss.orphan_count ?? 0}**`,
    ...(ss.orphans ?? []).slice(0, 15).map(p => `- \`${p}\` (utility/lib or manual-only)`),
    '',
    '### 10. Tables / registry gaps',
    '',
    `- DB tables: **${ts.table_count ?? '—'}** | Referenced in code: **${ts.used_count ?? '—'}**`,
    `- Empty tables: **${ts.empty_count ?? 0}** (schema placeholders / seasonal)`,
    ...(ts.orphan_tables ?? []).slice(0, 10).map(t => `- \`${t.table}\` (${t.rows} rows, no code reference)`),
    '',
    'See `audit/CODE_SCAN_SUMMARY.md` and `data/audit_deep_scan_last.json`.',
  ].join('\n');
  writeFileSync(mapPath, text + block + '\n');
}

// ── GATES & ACTIONABLE ────────────────────────────────────────────────────────
function generateGatesAudit(notifAudit) {
  const gate = runCapture(`${PYTHON} scripts/python/gate_doctor_audit.py audit '{}'`, 180_000);
  const lines = [
    '# Gates & Actionable Audit',
    '',
    `**Generated:** ${new Date().toISOString()}`,
    '',
    '## Pipeline',
    '',
    '```text',
    'unified_signals → score_all → gates → gate_audit_snapshots',
    '  → promotion → final_signals.actionable → telegram → notification_delivery_audit',
    '```',
    '',
    '## Notification pipeline',
    '',
    `| Field | Value |`,
    `|-------|-------|`,
    `| Root cause | ${notifAudit?.root_cause_category ?? notifAudit?.root_cause ?? '—'} |`,
    `| Actionable DB | ${notifAudit?.actionable?.db ?? '—'} |`,
    `| Deliverable | ${notifAudit?.actionable?.deliverable ?? '—'} |`,
    `| Telegram configured | ${notifAudit?.telegram_configured ? 'yes' : 'no'} |`,
    '',
    '## Gate doctor',
    '',
    '```json',
    (gate.out || gate.error || '{}').slice(0, 1500),
    '```',
    '',
    '## Verification',
    '',
    '```bash',
    'npm run egx:audit:gates',
    'npm run egx:notification:audit',
    '```',
  ];
  writeMd('GATES_AND_ACTIONABLE_AUDIT.md', lines);
  return { file: 'GATES_AND_ACTIONABLE_AUDIT.md', gate_ok: gate.ok };
}

// ── NOTIFICATION AUDIT ──────────────────────────────────────────────────────
function generateNotificationAudit(notifAudit) {
  const lines = [
    '# Notification Audit',
    '',
    `**Generated:** ${new Date().toISOString()}`,
    `**Report date:** ${notifAudit?.report_date ?? latestOhlcvDate()}`,
    '',
    '## Status',
    '',
    `- **Root cause:** ${notifAudit?.root_cause_category ?? notifAudit?.root_cause ?? 'unknown'}`,
    `- **Actionable:** ${notifAudit?.actionable?.db ?? 0} | **Deliverable:** ${notifAudit?.actionable?.deliverable ?? 0}`,
    `- **Telegram configured:** ${notifAudit?.telegram_configured ? '✅' : '❌'}`,
    '',
    '## Diagnosis',
    '',
    ...(notifAudit?.diagnosis ?? ['No diagnosis available']).map(d => `- ${d}`),
    '',
    '## Commands',
    '',
    '```bash',
    'npm run egx:prod:prepare-send -- --dry-run',
    'npm run egx:notify:reconcile',
    'npm run egx:cron:telegram:dry',
    '```',
  ];
  writeMd('NOTIFICATION_AUDIT.md', lines);
  return { file: 'NOTIFICATION_AUDIT.md' };
}

// ── AUTOMATION AUDIT ─────────────────────────────────────────────────────────
function generateAutomationAudit() {
  const cronShow = runCapture(`"${NODE}" scripts/install_cron.mjs --show`, 30_000);
  const cronLog = runCapture(`"${NODE}" scripts/egx_cron_log_check.mjs --hours 48`, 60_000);
  const health = readJson('system_health_last.json');
  const fullCycle = readJson('full_cycle_last.json');

  const dag = [
    '05:15 full_verify',
    '07:00 prod:status',
    '07:10 session_ready',
    '16:30 tv_auto_update',
    '17:20 telegram_cron',
    '17:45 post_session_ops → health',
  ];

  const lines = [
    '# Automation Audit',
    '',
    `**Generated:** ${new Date().toISOString()}`,
    '',
    '## Daily DAG',
    '',
    ...dag.map(d => `1. ${d}`),
    '',
    '## Cron status',
    '',
    '```',
    (cronShow.out || cronShow.error || 'not available').slice(0, 2000),
    '```',
    '',
    '## Cron log check (48h)',
    '',
    '```',
    (cronLog.out || cronLog.error || '').slice(0, 1000),
    '```',
    '',
    '## Last runs',
    '',
    `| Job | Status |`,
    `|-----|--------|`,
    `| health | ${health?.status ?? 'not run'} |`,
    `| full_cycle | ${fullCycle?.pass ? 'PASS' : 'pending'} |`,
    '',
    '## Lock policy',
    '',
    '- Separate locks per long job via `with_lock.mjs`',
    '- Stale locks >6h flagged in `egx:health`',
  ];
  writeMd('AUTOMATION_AUDIT.md', lines);
  return { file: 'AUTOMATION_AUDIT.md', cron_ok: cronShow.ok };
}

// ── MAIN ──────────────────────────────────────────────────────────────────────
console.log('\n═══ System Audit Orchestrator ═══\n');

const snapshot = {
  at: new Date().toISOString(),
  cairo_date: cairoDateParts().date,
  sections: {},
};

let deepScan = null;
if (ALL || process.argv.includes('--deep')) {
  deepScan = writeDeepScanArtifacts();
  snapshot.sections.deep_scan = { ok: true, exclusion_purged: deepScan.exclusion_reconcile?.purged ?? 0 };
  appendSystemMapOrphans(deepScan);
  console.log(`  ✅ Deep scan (exclusions purged: ${deepScan.exclusion_reconcile?.purged ?? 0})`);
}

if (ALL || process.argv.includes('--db')) {
  const db = runCapture(`${PYTHON} scripts/python/audit_db_report.py`, 120_000);
  snapshot.sections.db = { ok: db.ok, file: 'DB_AUDIT.md' };
  console.log(`  ${db.ok ? '✅' : '❌'} DB audit`);
}

let dataLayer = null;
if (ALL || process.argv.includes('--data-pipeline')) {
  if (!deepScan) writeDeepScanArtifacts();
  const dlRun = runCapture(`"${NODE}" scripts/egx_data_layer_audit.mjs`, 90_000);
  dataLayer = readJson('data_layer_audit_last.json');
  snapshot.sections.data_pipeline = generateDataPipelineAudit(
    dataLayer ?? { pass: dlRun.ok, checks: [{ id: 'data_layer', ok: dlRun.ok, detail: dlRun.error ?? 'run complete' }] },
  );
  console.log(`  ${dataLayer?.pass !== false ? '✅' : '⚠️'} DATA_PIPELINE_AUDIT.md`);
}

if (ALL || process.argv.includes('--engines')) {
  snapshot.sections.engines = generateEnginesAudit();
  console.log('  ✅ ENGINES_AUDIT.md');
}

let notifAudit = null;
if (ALL || process.argv.includes('--notification') || process.argv.includes('--gates')) {
  const signalDate = latestOhlcvDate();
  const nRun = runCapture(`"${NODE}" scripts/egx_notification_pipeline_audit.mjs`, 90_000);
  const rp = join(PROJECT_ROOT, 'data/research_reports', `notification_pipeline_audit_${signalDate}.json`);
  if (existsSync(rp)) {
    try { notifAudit = JSON.parse(readFileSync(rp, 'utf8')); } catch { /* */ }
  }
  if (!notifAudit && nRun.out) {
    try {
      const m = nRun.out.match(/\{[\s\S]*"signals_today"[\s\S]*\}/);
      if (m) notifAudit = { diagnosis: [], actionable: JSON.parse(m[0]), root_cause: 'parsed' };
    } catch { /* */ }
  }
  notifAudit = notifAudit ?? { root_cause: 'unknown', diagnosis: [], actionable: {} };
  writeFileSync(join(PROJECT_ROOT, 'data/notification_pipeline_audit_last.json'), JSON.stringify(notifAudit, null, 2));
}

if (ALL || process.argv.includes('--gates')) {
  snapshot.sections.gates = generateGatesAudit(notifAudit);
  console.log('  ✅ GATES_AND_ACTIONABLE_AUDIT.md');
}

if (ALL || process.argv.includes('--notification')) {
  snapshot.sections.notification = generateNotificationAudit(notifAudit);
  console.log('  ✅ NOTIFICATION_AUDIT.md');
}

if (ALL || process.argv.includes('--automation')) {
  snapshot.sections.automation = generateAutomationAudit();
  console.log('  ✅ AUTOMATION_AUDIT.md');
}

// Architecture + loop (snapshot only)
if (ALL) {
  snapshot.architecture = runCapture(`"${NODE}" scripts/egx_architecture_audit.mjs`, 60_000).ok;
  snapshot.loop = runCapture(`"${NODE}" scripts/egx_loop_audit.mjs`, 60_000).ok;
  snapshot.health = readJson('system_health_last.json');
  snapshot.audit_closed = readJson('audit_close_last.json');
}

mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/system_audit_snapshot.json'), JSON.stringify(snapshot, null, 2));

if (AS_JSON) {
  console.log(JSON.stringify(snapshot, null, 2));
} else {
  console.log('\n  Saved: data/system_audit_snapshot.json');
  console.log('  Reports: audit/DB_AUDIT.md … AUTOMATION_AUDIT.md\n');
}
