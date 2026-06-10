#!/usr/bin/env node
/** Print or send ops delivery digest. Usage: node scripts/egx_ops_digest.mjs [--send] */
import { loadEnv } from './lib/load_env.mjs';
import { buildDeliveryDigest } from './lib/ops_digest.mjs';
import { opsSuccessAlert } from './lib/notification_alert.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';

loadEnv();

const SEND = process.argv.includes('--send');
const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const digest = buildDeliveryDigest(dateArg || latestOhlcvDate());

console.log('\n═══ EGX Ops Digest ═══');
console.log(JSON.stringify(digest, null, 2));

if (SEND) {
  await opsSuccessAlert('OPS_DIGEST_MANUAL', digest);
  console.log('\nSent ops success alert (if EGX_ALERT_TELEGRAM + EGX_OPS_SUCCESS_ALERT enabled)\n');
}
