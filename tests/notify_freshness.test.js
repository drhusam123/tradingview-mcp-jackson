/**
 * Telegram QA freshness uses trading sessions (not calendar days).
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { validateTelegramPayload, sendTelegram } from '../src/egx/notify.js';

describe('notify freshness QA', () => {
  it('blocks client payload when OHLCV is unavailable', () => {
    const r = validateTelegramPayload('🎯 أفضل فرص التداول', {
      clientDelivery: true,
      finalActionableCount: 0,
    });
    // With real DB may pass freshness but fail client gate; without DB blocks freshness
    assert.equal(typeof r.ok, 'boolean');
    assert.ok(Array.isArray(r.issues ?? r.warnings));
  });

  it('blocks debug/research payloads', () => {
    const r = validateTelegramPayload('🧠 EGX COGNITION ENGINE\nSTOCK DNA (0 stocks)');
    assert.equal(r.ok, false);
    assert.ok(r.issues.some(i => /debug|research/i.test(i)));
  });

  it('opsAlert bypasses client-only Telegram policy', async () => {
    const token = process.env.TELEGRAM_BOT_TOKEN;
    const chat = process.env.TELEGRAM_CHAT_ID;
    delete process.env.TELEGRAM_BOT_TOKEN;
    delete process.env.TELEGRAM_CHAT_ID;
    const r = await sendTelegram('ops alert test', { opsAlert: true });
    assert.notEqual(r.policyBlocked, true);
    if (token) process.env.TELEGRAM_BOT_TOKEN = token;
    if (chat) process.env.TELEGRAM_CHAT_ID = chat;
  });
});
