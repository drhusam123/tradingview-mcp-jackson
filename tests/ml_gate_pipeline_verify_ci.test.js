import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('ML+gate pipeline verify CI mode', () => {
  it('--ci exits 0 without crontab', () => {
    const out = execSync('node scripts/egx_ml_gate_pipeline_verify.mjs --ci', {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 60_000,
      env: { ...process.env, TELEGRAM_BOT_TOKEN: '', TELEGRAM_CHAT_ID: '' },
    });
    assert.match(out, /ML\/Gate Pipeline Verify: \d+\/\d+ PASS/);
  });
});
