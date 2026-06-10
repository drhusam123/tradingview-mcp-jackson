#!/usr/bin/env node
/**
 * EGX schema migrations — ordered SQL files in scripts/migrations/
 *
 * Usage:
 *   node scripts/migrations/migrate.mjs           # apply pending
 *   node scripts/migrations/migrate.mjs --check  # list status only
 *   node scripts/migrations/migrate.mjs --status
 */
import { readdirSync, readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';

const __dirname = dirname(fileURLToPath(import.meta.url));
const MIGRATIONS_DIR = __dirname;
const ROOT = join(__dirname, '../..');
const DB_PATH = process.env.EGX_DB_PATH || join(ROOT, 'data/egx_trading.db');
const CHECK_ONLY = process.argv.includes('--check') || process.argv.includes('--status');

function listMigrationFiles() {
  return readdirSync(MIGRATIONS_DIR)
    .filter(f => /^\d{3}_.+\.sql$/.test(f))
    .sort();
}

function ensureMetaTable(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      version     TEXT NOT NULL UNIQUE,
      filename    TEXT NOT NULL,
      applied_at  TEXT DEFAULT (datetime('now'))
    );
  `);
}

function appliedVersions(db) {
  ensureMetaTable(db);
  return new Set(
    db.prepare('SELECT version FROM schema_migrations ORDER BY version').all().map(r => r.version)
  );
}

function versionFromFilename(filename) {
  return filename.split('_')[0];
}

function main() {
  if (!existsSync(DB_PATH)) {
    console.error(`[migrate] database not found: ${DB_PATH}`);
    process.exit(CHECK_ONLY ? 0 : 1);
  }

  const db = new Database(DB_PATH);
  db.pragma('journal_mode = WAL');
  const applied = appliedVersions(db);
  const files = listMigrationFiles();
  let pending = 0;

  for (const file of files) {
    const version = versionFromFilename(file);
    const status = applied.has(version) ? 'applied' : 'pending';
    console.log(`  ${version}  ${file.padEnd(40)} ${status}`);
    if (applied.has(version)) continue;
    pending += 1;
    if (CHECK_ONLY) continue;

    const sql = readFileSync(join(MIGRATIONS_DIR, file), 'utf8');
    const apply = db.transaction(() => {
      db.exec(sql);
      db.prepare(
        'INSERT INTO schema_migrations (version, filename) VALUES (?, ?)'
      ).run(version, file);
    });
    apply();
    console.log(`[migrate] applied ${file}`);
  }

  db.close();

  if (CHECK_ONLY) {
    console.log(`[migrate] ${files.length - pending}/${files.length} applied, ${pending} pending`);
    process.exit(0);
  }

  console.log(pending === 0
    ? '[migrate] database is up to date'
    : `[migrate] applied ${pending} migration(s)`);
}

main();
