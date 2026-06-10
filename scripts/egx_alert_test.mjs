#!/usr/bin/env node
/** Test ops Telegram alerts (not client signals). */
import { loadEnv } from './lib/load_env.mjs';
import { alertNotification, opsSuccessAlert } from './lib/notification_alert.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';

loadEnv();
const mode = process.argv.includes('--success') ? 'success' : 'failure';

if (mode === 'success') {
  console.log('Sending test ops SUCCESS alert...');
  await opsSuccessAlert('OPS_ALERT_TEST_OK', buildDeliveryDigest());
} else {
  console.log('Sending test ops FAILURE alert...');
  alertNotification('OPS_ALERT_TEST_FAIL', {
    message: 'EGX ops failure alert pipeline OK',
    ts: new Date().toISOString(),
  });
}
console.log('Check Telegram + logs/notification_alerts.log');
