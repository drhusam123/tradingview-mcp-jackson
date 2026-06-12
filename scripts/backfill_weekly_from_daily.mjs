#!/usr/bin/env node
/**
 * Backfill ohlcv_weekly from daily ohlcv_history for symbols missing weekly data.
 * No TradingView required — aggregates Fri-ending EGX weeks from daily bars.
 *
 * Usage:
 *   node scripts/backfill_weekly_from_daily.mjs
 *   node scripts/backfill_weekly_from_daily.mjs --symbols COMI,ACFR
 */
import Database from 'better-sqlite3';
import { loadEnv } from './lib/load_env.mjs';
import { DB_PATH } from './lib/delivery_audit.mjs';
import { getDB, saveOHLCVTimeframe } from '../src/egx/index.js';

loadEnv();

const args = process.argv.slice(2);
const symArg = (() => {
  const i = args.indexOf('--symbols');
  return i >= 0 ? args[i + 1]?.split(',').map(s => s.trim()).filter(Boolean) : null;
})();

/** Cairo Friday 14:30 ≈ week end for EGX. Returns YYYY-Www key. */
function weekKey(unixSec) {
  const d = new Date(unixSec * 1000);
  const utc = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = utc.getUTCDay() || 7;
  utc.setUTCDate(utc.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(utc.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((utc - yearStart) / 86400000) + 1) / 7);
  return `${utc.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

function aggregateWeekly(bars) {
  const weeks = new Map();
  for (const b of bars) {
    const wk = weekKey(b.bar_time);
    if (!weeks.has(wk)) {
      weeks.set(wk, {
        bar_time: b.bar_time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume ?? 0,
      });
    } else {
      const w = weeks.get(wk);
      w.high = Math.max(w.high, b.high);
      w.low = Math.min(w.low, b.low);
      w.close = b.close;
      w.volume += b.volume ?? 0;
      if (b.bar_time > w.bar_time) w.bar_time = b.bar_time;
    }
  }
  return [...weeks.values()].sort((a, b) => a.bar_time - b.bar_time);
}

function main() {
  getDB();
  const db = new Database(DB_PATH);

  const targets = symArg ?? db.prepare(`
    SELECT symbol FROM (SELECT DISTINCT symbol FROM ohlcv_history)
    WHERE symbol NOT IN (SELECT DISTINCT symbol FROM ohlcv_weekly)
    ORDER BY symbol
  `).all().map(r => r.symbol);

  console.log(`\n═══ Weekly Backfill from Daily ═══`);
  console.log(`  Targets: ${targets.length} symbols\n`);

  let totalBars = 0;
  for (const symbol of targets) {
    const daily = db.prepare(`
      SELECT bar_time, open, high, low, close, volume
      FROM ohlcv_history
      WHERE symbol = ? AND volume > 0
      ORDER BY bar_time ASC
    `).all(symbol);

    if (daily.length < 3) {
      console.log(`  ⏭ ${symbol}: only ${daily.length} daily bars — skip`);
      continue;
    }

    const weekly = aggregateWeekly(daily);
    const bars = weekly.map(w => ({
      time: w.bar_time,
      open: w.open,
      high: w.high,
      low: w.low,
      close: w.close,
      volume: w.volume,
    }));

    const n = saveOHLCVTimeframe('ohlcv_weekly', symbol, bars);
    totalBars += n;
    console.log(`  ✅ ${symbol}: ${n} weekly bars from ${daily.length} daily`);
  }

  const weeklyN = db.prepare('SELECT COUNT(DISTINCT symbol) n FROM ohlcv_weekly').get()?.n ?? 0;
  const gap = db.prepare(`
    SELECT COUNT(*) n FROM (SELECT DISTINCT symbol FROM ohlcv_history)
    WHERE symbol NOT IN (SELECT DISTINCT symbol FROM ohlcv_weekly)
  `).get()?.n ?? 0;

  db.close();
  console.log(`\n  Total weekly bars written: ${totalBars}`);
  console.log(`  Weekly symbols now: ${weeklyN} | remaining gap: ${gap}\n`);
}

main();
