#!/usr/bin/env node
/** Phase 21 — Live anchor + t5 closure (standalone refresh). */
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { latestOhlcvDate } from './lib/delivery_audit.mjs';
import { cairoDateParts } from './lib/egx_calendar.mjs';
import { evaluatePhase21LiveAnchor } from './lib/phase21_live_anchor.mjs';
import { execSync } from 'child_process';

loadEnv();
const PYTHON = process.env.PYTHON_BIN || 'python3';
const d = latestOhlcvDate() || cairoDateParts().date;
for (const s of ['p6_watch_t5_closure.py', 'p6_delivered_wr_dashboard.py']) {
  try {
    execSync(`"${PYTHON}" scripts/python/${s} '${JSON.stringify({ trade_date: d, as_of_date: d })}'`, { cwd: PROJECT_ROOT, encoding: 'utf8' });
  } catch { /* */ }
}
const p = evaluatePhase21LiveAnchor(d);
console.log(JSON.stringify(p, null, 2));
process.exit(p.phase21_ready ? 0 : 1);
