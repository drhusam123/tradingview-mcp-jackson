#!/usr/bin/env node
/** Purge 2099-* test fixtures from production DB after test runs. */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { purgeTestFinalSignals } from './lib/final_signals_query.mjs';

const DB = join(dirname(fileURLToPath(import.meta.url)), '../data/egx_trading.db');

const fs = purgeTestFinalSignals(DB);
let auditDeleted = 0;
let indicatorsDeleted = 0;
if (existsSync(DB)) {
  const db = new Database(DB);
  auditDeleted = db.prepare(
    "DELETE FROM notification_delivery_audit WHERE signal_date LIKE '2099-%'",
  ).run().changes;
  indicatorsDeleted = db.prepare(
    "DELETE FROM indicators_cache WHERE bar_date LIKE '2099-%'",
  ).run().changes;
  db.close();
}

console.log(JSON.stringify({
  final_signals: fs,
  audit_deleted: auditDeleted,
  indicators_deleted: indicatorsDeleted,
}));
