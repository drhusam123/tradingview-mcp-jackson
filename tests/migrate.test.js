/**
 * Migration runner smoke test — uses temp DB.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'child_process';
import { mkdtempSync, rmSync } from 'fs';
import { join, dirname } from 'path';
import { tmpdir } from 'os';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MIGRATE = join(ROOT, 'scripts/migrations/migrate.mjs');

describe('schema migrations', () => {
  it('applies all SQL migrations to a fresh database', () => {
    const dir = mkdtempSync(join(tmpdir(), 'egx-migrate-'));
    const dbPath = join(dir, 'test.db');
    new Database(dbPath).close();

    execFileSync('node', [MIGRATE], {
      cwd: ROOT,
      env: { ...process.env, EGX_DB_PATH: dbPath },
      stdio: 'pipe',
    });

    const db = new Database(dbPath);
    const rows = db.prepare('SELECT version FROM schema_migrations ORDER BY version').all();
    db.close();
    rmSync(dir, { recursive: true, force: true });

    assert.ok(rows.length >= 2);
    assert.equal(rows[0].version, '001');
    assert.equal(rows[1].version, '002');
  });
});
