import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('cron log check', () => {
  it('exits 0 when no recent failures in logs', () => {
    execSync('node scripts/egx_cron_log_check.mjs --hours 1', {
      cwd: ROOT,
      stdio: 'pipe',
      timeout: 30_000,
    });
    assert.ok(true);
  });
});
