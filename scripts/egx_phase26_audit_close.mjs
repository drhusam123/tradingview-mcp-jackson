#!/usr/bin/env node
/** Phase 26 — Audit close sign-off. */
import { loadEnv } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { runAuditCloseApply } from './lib/phase26_audit_close.mjs';
loadEnv();
const p = runAuditCloseApply(latestOhlcvDate());
console.log(JSON.stringify(p, null, 2));
process.exit(p.audit_closed ? 0 : 1);
