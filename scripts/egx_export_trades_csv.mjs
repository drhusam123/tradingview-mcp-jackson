#!/usr/bin/env node
/**
 * Export EGX portfolio trades to trades.csv (Jackson bot.js tax pattern).
 * Usage:
 *   node scripts/egx_export_trades_csv.mjs
 *   node scripts/egx_export_trades_csv.mjs --summary
 */
import Database from 'better-sqlite3';
import { existsSync, writeFileSync, mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { loadEnv } from './lib/load_env.mjs';

loadEnv();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DB = join(ROOT, 'data/egx_trading.db');
const OUT = join(ROOT, 'data/trades.csv');
const SUMMARY = process.argv.includes('--summary');

const HEADER = 'Date,Time,Exchange,Symbol,Side,Quantity,Price,Total_EGP,Fee_Est,Net_Amount,Order_ID,Mode,Status,Exit_Reason,PnL_Pct\n';

function esc(v) {
  const s = String(v ?? '');
  return s.includes(',') ? `"${s.replace(/"/g, '""')}"` : s;
}

if (!existsSync(DB)) {
  console.log('No egx_trading.db — nothing to export.');
  process.exit(0);
}

const d = new Database(DB, { readonly: true });
const rows = d.prepare(`
  SELECT entry_date, entry_price, shares, symbol, stop_loss, t1_target, t2_target,
         exit_date, exit_price, exit_reason, current_pnl_pct, status, signal_type, id
  FROM portfolio_positions
  WHERE status NOT IN ('OPEN','PARTIAL_T1','PARTIAL_T2')
     OR exit_date IS NOT NULL
  ORDER BY COALESCE(exit_date, entry_date) DESC, id DESC
`).all();
d.close();

const paper = process.env.EGX_PAPER_TRADING === 'true' ? 'Paper' : 'Live';
const lines = [HEADER.trim()];

let totalVol = 0;
let wins = 0;
let losses = 0;

for (const r of rows) {
  const date = r.exit_date || r.entry_date;
  const price = r.exit_price || r.entry_price || 0;
  const qty = r.shares || 0;
  const total = price * qty;
  const fee = total * 0.00125;
  const net = total - fee;
  const pnl = r.current_pnl_pct ?? 0;
  if (pnl > 0) wins++;
  else if (pnl < 0) losses++;
  totalVol += total;

  lines.push([
    date,
    '12:00:00',
    'EGX',
    r.symbol,
    'Buy',
    qty.toFixed(0),
    price.toFixed(3),
    total.toFixed(2),
    fee.toFixed(2),
    net.toFixed(2),
    `pos-${r.id}`,
    paper,
    r.status,
    r.exit_reason || '',
    pnl.toFixed(2),
  ].map(esc).join(','));
}

if (SUMMARY) {
  console.log('\n=== EGX Trade Summary ===');
  console.log(`Closed/exported rows: ${rows.length}`);
  console.log(`Total volume (EGP): ${totalVol.toFixed(2)}`);
  console.log(`Wins: ${wins} | Losses: ${losses}`);
  console.log(`CSV path: ${OUT}`);
  process.exit(0);
}

mkdirSync(join(ROOT, 'data'), { recursive: true });
writeFileSync(OUT, `${lines.join('\n')}\n`);
console.log(`✅ Exported ${rows.length} rows → ${OUT}`);
