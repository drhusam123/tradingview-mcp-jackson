import { appendFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ALERT_LOG = join(dirname(fileURLToPath(import.meta.url)), '../../logs/notification_alerts.log');

async function pushTelegramAlert(event, detail) {
  if (process.env.EGX_ALERT_TELEGRAM === '0') return;
  try {
    const { sendTelegram, isTelegramConfigured } = await import('../../src/egx/notify.js');
    if (!isTelegramConfigured()) return;
    const body = typeof detail === 'object' ? JSON.stringify(detail).slice(0, 800) : String(detail || '');
    const msg = `⚠️ <b>EGX Ops Alert</b>\n<b>${event}</b>\n<pre>${body}</pre>`;
    await sendTelegram(msg, { parseMode: 'HTML', opsAlert: true });
  } catch {
    /* log-only fallback */
  }
}

function appendAlertLog(kind, event, detail) {
  mkdirSync(join(dirname(ALERT_LOG)), { recursive: true });
  const row = { ts: new Date().toISOString(), kind, event, ...detail };
  const line = `[NOTIFY_ALERT] ${JSON.stringify(row)}\n`;
  appendFileSync(ALERT_LOG, line);
  console.log(line.trim());
}

export function alertNotification(event, detail = {}) {
  appendAlertLog('failure', event, detail);
  void pushTelegramAlert(event, detail);
}

/** Compact success digest — off when EGX_OPS_SUCCESS_ALERT=0 */
export async function opsSuccessAlert(event, detail = {}) {
  if (process.env.EGX_ALERT_TELEGRAM === '0' || process.env.EGX_OPS_SUCCESS_ALERT === '0') return;
  appendAlertLog('success', event, detail);
  try {
    const { sendTelegram, isTelegramConfigured } = await import('../../src/egx/notify.js');
    if (!isTelegramConfigured()) return;
    const { formatOpsSuccessMessage } = await import('./ops_digest.mjs');
    const msg = formatOpsSuccessMessage(event, detail);
    await sendTelegram(msg, { parseMode: 'HTML', opsAlert: true });
  } catch {
    /* log-only */
  }
}
