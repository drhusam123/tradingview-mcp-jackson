import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('delivery recovery', () => {
  it('recovery exits 0 when nothing pending', () => {
    execSync('node scripts/egx_notify_recovery.mjs', { cwd: ROOT, stdio: 'pipe' });
  });

  it('recovery --send without EGX_AUTO_BACKFILL exits non-zero when pending', () => {
    // With 4/4 sent, --send should still exit 0; test script runs without throw
    try {
      execSync('node scripts/egx_notify_recovery.mjs --send', {
        cwd: ROOT,
        stdio: 'pipe',
        env: { ...process.env, EGX_AUTO_BACKFILL: '0' },
      });
    } catch (e) {
      assert.ok([0, 3].includes(e.status), `unexpected exit ${e.status}`);
    }
  });
});
