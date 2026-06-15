/**
 * Deep institutional audit scan — orphan scripts/tables + code smell grep.
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync, readdirSync, writeFileSync, mkdirSync } from 'fs';
import { join, basename } from 'path';
import Database from 'better-sqlite3';
import { PROJECT_ROOT } from './load_env.mjs';
import { DB_PATH } from './delivery_audit.mjs';
import { DISCOVERY_ENGINES } from './discovery_engine_registry.mjs';

const SKIP_DIRS = new Set(['node_modules', '.git', 'screenshots', 'data', 'dist', 'build', '.cursor']);

function listScripts() {
  const out = [];
  function walk(dir) {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      if (SKIP_DIRS.has(ent.name)) continue;
      const p = join(dir, ent.name);
      if (ent.isDirectory()) walk(p);
      else if (/\.(mjs|js|py)$/.test(ent.name)) out.push(p);
    }
  }
  walk(join(PROJECT_ROOT, 'scripts'));
  return out;
}

function referencedScripts() {
  const refs = new Set();
  const pkg = JSON.parse(readFileSync(join(PROJECT_ROOT, 'package.json'), 'utf8'));
  for (const cmd of Object.values(pkg.scripts ?? {})) {
    for (const m of String(cmd).matchAll(/scripts\/[^\s"']+/g)) refs.add(m[0]);
    for (const m of String(cmd).matchAll(/egx_[a-z0-9_]+\.mjs/gi)) refs.add(`scripts/${m[0]}`);
  }
  for (const eng of Object.values(DISCOVERY_ENGINES)) {
    if (eng.npm) {
      const script = pkg.scripts?.[eng.npm.replace('egx:', 'egx:')];
      if (script) for (const m of String(script).matchAll(/scripts\/[^\s"']+/g)) refs.add(m[0]);
    }
  }
  try {
    const cron = readFileSync(join(PROJECT_ROOT, 'scripts/install_cron.mjs'), 'utf8');
    for (const m of cron.matchAll(/join\(ROOT,\s*'scripts',\s*'([^']+)'\)/g)) refs.add(`scripts/${m[1]}`);
    for (const m of cron.matchAll(/scripts\/([a-z0-9_./-]+\.(mjs|py))/gi)) refs.add(`scripts/${m[1]}`);
  } catch { /* */ }
  return refs;
}

export function scanOrphanScripts({ limit = 40 } = {}) {
  const all = listScripts().map(p => p.replace(PROJECT_ROOT + '/', ''));
  const refs = referencedScripts();
  const orphans = all.filter(p => {
    const base = basename(p);
    if (p.includes('/lib/') || p.includes('/python/') || p.includes('/migrations/')) return false;
    if (refs.has(p)) return false;
    if ([...refs].some(r => p.endsWith(basename(r)))) return false;
    return true;
  });
  return { total: all.length, referenced: refs.size, orphans: orphans.slice(0, limit), orphan_count: orphans.length };
}

export function scanOrphanTables() {
  if (!existsSync(DB_PATH)) return { error: 'NO_DB' };
  const db = new Database(DB_PATH, { readonly: true });
  const tables = db.prepare(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
  ).all().map(r => r.name);

  const codeRoots = ['scripts', 'src'];
  let corpus = '';
  for (const root of codeRoots) {
    const p = join(PROJECT_ROOT, root);
    if (!existsSync(p)) continue;
    try {
      corpus += execSync(`rg -l "${tables.slice(0, 50).join('|')}" "${p}" 2>/dev/null || true`, {
        encoding: 'utf8', maxBuffer: 5_000_000,
      });
    } catch { /* */ }
  }
  // Simpler: grep each table name in scripts+src via node read
  const used = new Set();
  const scanDirs = [join(PROJECT_ROOT, 'scripts'), join(PROJECT_ROOT, 'src')];
  function walkCode(dir) {
    for (const ent of readdirSync(dir, { withFileTypes: true })) {
      if (SKIP_DIRS.has(ent.name)) continue;
      const fp = join(dir, ent.name);
      if (ent.isDirectory()) walkCode(fp);
      else if (/\.(mjs|js|py|ts)$/.test(ent.name)) {
        let text = '';
        try { text = readFileSync(fp, 'utf8'); } catch { continue; }
        for (const t of tables) {
          if (text.includes(t)) used.add(t);
        }
      }
    }
  }
  for (const d of scanDirs) if (existsSync(d)) walkCode(d);

  const empty = [];
  const orphan = [];
  for (const t of tables) {
    const n = db.prepare(`SELECT COUNT(*) n FROM [${t}]`).get()?.n ?? 0;
    if (n === 0) empty.push(t);
    if (!used.has(t) && n > 0) orphan.push({ table: t, rows: n });
  }
  db.close();
  return {
    table_count: tables.length,
    used_count: used.size,
    empty_count: empty.length,
    orphan_tables: orphan.slice(0, 30),
    empty_tables_sample: empty.slice(0, 20),
  };
}

export function scanCodeSmells() {
  let raw = '';
  try {
    raw = execSync(
      `rg -n "TODO|FIXME|NotImplemented|placeholder|stub" scripts src audit --glob '!node_modules' 2>/dev/null | head -200`,
      { cwd: PROJECT_ROOT, encoding: 'utf8', maxBuffer: 2_000_000 },
    );
  } catch (e) {
    raw = (e.stdout || '').toString();
  }
  const lines = raw.split('\n').filter(Boolean);
  const buckets = { TODO: 0, FIXME: 0, NotImplemented: 0, placeholder: 0, stub: 0 };
  for (const line of lines) {
    for (const k of Object.keys(buckets)) {
      if (line.includes(k)) buckets[k] += 1;
    }
  }
  return { total_matches: lines.length, buckets, sample: lines.slice(0, 30) };
}

export function reconcileExclusionOrphans() {
  if (!existsSync(DB_PATH)) return { ok: false, error: 'NO_DB' };
  const db = new Database(DB_PATH);
  db.pragma('busy_timeout = 10000');
  const before = db.prepare(
    "SELECT COUNT(*) n FROM data_quality_bar_exclusions WHERE status='ACTIVE'"
  ).get().n;
  const purged = db.prepare(`
    DELETE FROM data_quality_bar_exclusions
    WHERE status='ACTIVE'
      AND NOT EXISTS (
        SELECT 1 FROM ohlcv_history h
        WHERE h.symbol = data_quality_bar_exclusions.symbol
          AND h.bar_time = data_quality_bar_exclusions.bar_time
      )
  `).run().changes;
  const after = db.prepare(
    "SELECT COUNT(*) n FROM data_quality_bar_exclusions WHERE status='ACTIVE'"
  ).get().n;
  db.close();
  return { ok: true, before, after, purged };
}

export function writeDeepScanArtifacts() {
  const scriptScan = scanOrphanScripts();
  const tableScan = scanOrphanTables();
  const codeScan = scanCodeSmells();
  const exclusion = reconcileExclusionOrphans();

  const payload = {
    at: new Date().toISOString(),
    script_scan: scriptScan,
    table_scan: tableScan,
    code_scan: codeScan,
    exclusion_reconcile: exclusion,
  };
  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  mkdirSync(join(PROJECT_ROOT, 'audit'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/audit_deep_scan_last.json'), JSON.stringify(payload, null, 2));

  const md = [
    '# Code Scan Summary',
    '',
    `**Generated:** ${payload.at}`,
    '',
    '## Grep buckets',
    '',
    ...Object.entries(codeScan.buckets).map(([k, v]) => `- **${k}:** ${v}`),
    '',
    '## Sample matches (first 30)',
    '',
    ...codeScan.sample.map(l => `- \`${l.slice(0, 120)}\``),
    '',
    '## Exclusion reconcile',
    '',
    `- Before: ${exclusion.before} | Purged: ${exclusion.purged} | After: ${exclusion.after}`,
  ].join('\n');
  writeFileSync(join(PROJECT_ROOT, 'audit/CODE_SCAN_SUMMARY.md'), md + '\n');

  return payload;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const p = writeDeepScanArtifacts();
  console.log(JSON.stringify(p, null, 2));
}
