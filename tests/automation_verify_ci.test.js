import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('automation verify CI mode', () => {
  it('--ci exits 0 without crontab or Telegram secrets', () => {
    const out = execSync('node scripts/egx_automation_verify.mjs --ci', {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 30_000,
      env: { ...process.env, TELEGRAM_BOT_TOKEN: '', TELEGRAM_CHAT_ID: '' },
    });
    assert.match(out, /Automation Verify: \d+\/\d+ PASS/);
  });
});
