import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

describe('merge technical indicators', () => {
  it('merge script documents source column', () => {
    const src = readFileSync(join(ROOT, 'scripts/merge_technical_indicators.mjs'), 'utf8');
    assert.ok(src.includes("source='tv'"));
    assert.ok(src.includes('technical_indicators_cache'));
  });

  it('last merge report exists after run', { skip: !existsSync(join(ROOT, 'data/merge_technical_indicators_last.json')) }, () => {
    const report = JSON.parse(readFileSync(join(ROOT, 'data/merge_technical_indicators_last.json'), 'utf8'));
    assert.ok(typeof report.merged === 'number');
  });
});
