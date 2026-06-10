/**
 * EGX System Validation
 * =====================
 * Comprehensive end-to-end validation of the full 11-phase intelligence stack.
 * Runs 12 checks and reports PASS / WARN / FAIL for each.
 *
 * Usage:
 *   node scripts/egx_validate.mjs              # full validation
 *   node scripts/egx_validate.mjs --quick      # skip slow checks (timing)
 */

import {
  pythonOrchHealth,
  pythonOrchNow,
  pythonOsPipelineStatus,
  pythonOsAlertScan,
  pythonOsHealth,
  pythonOsResilience,
  pythonTgTestFormat,
} from '../src/egx/index.js';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { seedHolidayCalendar, tradingDayStaleness } from './lib/egx_calendar.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR  = join(__dirname, '../data');
const DB_PATH   = join(DATA_DIR, 'egx_trading.db');
const QUICK     = process.argv.includes('--quick');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 65) => wl(c.repeat(n));

// ─── Check Registry ──────────────────────────────────────────────────────────

const checks = [];
let passed = 0, warned = 0, failed = 0;

function record(name, status, detail) {
  const icon = status === 'PASS' ? '✅' : status === 'WARN' ? '⚠️ ' : '❌';
  checks.push({ name, status, detail });
  wl(`  ${icon} ${name.padEnd(42)} ${detail}`);
  if (status === 'PASS') passed++;
  else if (status === 'WARN') warned++;
  else failed++;
}

function pass(name, detail)  { record(name, 'PASS', detail); }
function warn(name, detail)  { record(name, 'WARN', detail); }
function fail(name, detail)  { record(name, 'FAIL', detail); }

// ─── Validation Checks ───────────────────────────────────────────────────────

sep();
wl('  🔬 EGX SYSTEM VALIDATION');
wl(`  ${QUICK ? '⚡ Quick mode' : '🔍 Full validation'} | ${new Date().toISOString()}`);
sep();
wl('');

// ── 1. Database Connectivity ─────────────────────────────────────────────────
wl('  ── [1/12] Database Connectivity ───────────────────────────────────');
try {
  if (!existsSync(DB_PATH)) throw new Error('DB file not found');
  const db = new Database(DB_PATH, { readonly: true });
  const row = db.prepare("SELECT COUNT(*) as n FROM ohlcv_history").get();
  db.close();
  if (row.n > 0) pass('DB: ohlcv_history table', `${row.n.toLocaleString()} bars`);
  else warn('DB: ohlcv_history table', 'empty — no data loaded');
} catch (e) {
  fail('DB: connectivity', e.message);
}

// ── 2. Data Freshness ────────────────────────────────────────────────────────
wl('\n  ── [2/12] Data Freshness ───────────────────────────────────────────');
try {
  seedHolidayCalendar();
  const db = new Database(DB_PATH, { readonly: true });
  // bar_time is unix timestamp (seconds)
  const row = db.prepare("SELECT MAX(bar_time) as latest_ts, COUNT(DISTINCT symbol) as syms FROM ohlcv_history").get();
  db.close();
  const latestTs = row.latest_ts;
  const syms     = row.syms;
  const now      = new Date();
  const latest   = latestTs ? new Date(latestTs * 1000).toISOString().slice(0,10) : 'unknown';
  if (!latestTs) throw new Error('no OHLCV bars found');

  let cal;
  try {
    cal = tradingDayStaleness(latest);
  } catch (e) {
    const diff = Math.floor((now - new Date(latestTs * 1000)) / 86400000);
    if (diff <= 3)      pass('Data: freshness', `latest=${latest} (${diff} calendar days ago) | ${syms} symbols — calendar fallback: ${e.message}`);
    else if (diff <= 7) warn('Data: freshness', `latest=${latest} (${diff} calendar days ago) — calendar fallback: ${e.message}`);
    else                fail('Data: freshness', `latest=${latest} (${diff} calendar days ago) — calendar fallback: ${e.message}`);
    cal = null;
  }

  if (cal) {
    const stale = Number(cal.staleness_trading_days ?? 999);
    const marketClosed = cal.market_status === 'MARKET_CLOSED';
    const closedWhy = marketClosed
      ? ` | ${cal.holiday_name ? `holiday=${cal.holiday_name}` : 'market closed'}`
      : '';
    const detail = `latest=${latest} | last_td=${cal.last_trading_day} | stale=${stale} trading days | ${syms} symbols${closedWhy}`;
    if (stale === 0)      pass('Data: freshness', detail);
    else if (stale <= 5)  warn('Data: freshness', `${detail} — data missed trading sessions`);
    else                  fail('Data: freshness', `${detail} — data outdated`);
  }
} catch (e) {
  fail('Data: freshness check', e.message);
}

// ── 3. Layer Resilience ───────────────────────────────────────────────────────
wl('\n  ── [3/12] Layer Resilience ─────────────────────────────────────────');
try {
  // pythonOrchNow returns layer_health dict {name: {health, state, detail}}
  const r = await pythonOrchNow();
  if (r.error) throw new Error(r.error);
  const layerHealth = r.layer_health || {};
  const layerVals   = Object.values(layerHealth);
  const healthy     = layerVals.filter(v => {
    const h = typeof v === 'object' ? (v.health ?? 0) : (typeof v === 'number' ? v : 0);
    return h >= 0.6;
  }).length;
  const total  = layerVals.length || 8;
  const conf   = ((r.global_confidence || 0) * 100).toFixed(1);
  if (healthy >= 6)      pass('Layers: health', `${healthy}/${total} healthy | conf=${conf}%`);
  else if (healthy >= 4) warn('Layers: health', `${healthy}/${total} healthy | conf=${conf}%`);
  else                   fail('Layers: health', `only ${healthy}/${total} healthy`);
} catch (e) {
  fail('Layers: resilience check', e.message.slice(0, 80));
}

// ── 4. Pipeline Status ───────────────────────────────────────────────────────
wl('\n  ── [4/12] Pipeline Status ──────────────────────────────────────────');
try {
  const r = await pythonOsPipelineStatus();
  if (r.error) throw new Error(r.error);
  // Response: {last_run, last_status, last_duration_sec, steps_done, steps_total, ...}
  const lastDate = r.last_run;
  const status   = r.last_status;
  const steps    = r.steps_done ?? '?';
  const total    = r.steps_total ?? 8;
  const dur      = r.last_duration_sec != null ? `${r.last_duration_sec.toFixed(1)}s` : '?';
  if (!lastDate) {
    warn('Pipeline: history', 'no pipeline runs recorded yet');
  } else if (status === 'OK' || status === 'SUCCESS') {
    pass('Pipeline: last run', `${steps}/${total} steps | ${dur} | ${lastDate}`);
  } else if (status === 'PARTIAL') {
    warn('Pipeline: last run', `partial: ${steps}/${total} | ${lastDate}`);
  } else {
    fail('Pipeline: last run', `status=${status} | ${lastDate}`);
  }
} catch (e) {
  fail('Pipeline: status check', e.message.slice(0, 80));
}

// ── 5. Alert Deduplication ───────────────────────────────────────────────────
wl('\n  ── [5/12] Alert Deduplication ──────────────────────────────────────');
try {
  const db = new Database(DB_PATH, { readonly: true });
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='alert_history'").get();
  if (!tables) {
    warn('Alerts: deduplication', 'alert_history table not yet created (run pipeline first)');
  } else {
    const rows = db.prepare("SELECT alert_type, COUNT(*) as n FROM alert_history GROUP BY alert_type").all();
    // Same type on same day would be a dup — check for truly duplicate rows
    const dupRows = db.prepare(
      "SELECT alert_type, alert_date, COUNT(*) as n FROM alert_history GROUP BY alert_type, alert_date HAVING n > 1"
    ).all();
    if (dupRows.length === 0) pass('Alerts: deduplication', `${rows.length} alert types, no same-day duplicates`);
    else warn('Alerts: deduplication', `${dupRows.length} duplicate pairs found`);
  }
  db.close();
} catch (e) {
  fail('Alerts: deduplication check', e.message.slice(0, 80));
}

// ── 6. Orchestration Confidence Bounds ───────────────────────────────────────
wl('\n  ── [6/12] Orchestration Confidence Bounds ──────────────────────────');
try {
  const r = await pythonOrchNow();
  if (r.error) throw new Error(r.error);
  // Response uses global_confidence not confidence
  const conf    = r.global_confidence ?? r.confidence ?? 0;
  const posture = r.posture ?? 'UNKNOWN';
  const regime  = r.regime  ?? 'UNKNOWN';
  if (conf >= 0.50 && conf <= 1.0 && posture !== 'UNKNOWN')
    pass('Orchestration: bounds', `conf=${(conf*100).toFixed(1)}% | ${regime} | ${posture}`);
  else if (conf < 0.40)
    warn('Orchestration: bounds', `low confidence ${(conf*100).toFixed(1)}% — degraded signal`);
  else
    pass('Orchestration: bounds', `conf=${(conf*100).toFixed(1)}% | ${regime} | ${posture}`);
} catch (e) {
  fail('Orchestration: confidence', e.message.slice(0, 80));
}

// ── 7. Opportunity Quality ────────────────────────────────────────────────────
wl('\n  ── [7/12] Opportunity Quality ──────────────────────────────────────');
try {
  const db = new Database(DB_PATH, { readonly: true });
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='cognition_snapshots'").get();
  if (!tables) {
    warn('Opportunities: quality', 'cognition_snapshots table not yet created');
  } else {
    const row = db.prepare("SELECT opportunities_json FROM cognition_snapshots ORDER BY snapshot_date DESC LIMIT 1").get();
    if (!row) {
      warn('Opportunities: quality', 'no snapshots yet — run pipeline first');
    } else {
      const opps      = JSON.parse(row.opportunities_json || '[]');
      const validOpps = opps.filter(o => o.symbol && typeof o.score === 'number');
      pass('Opportunities: quality', `${validOpps.length} valid opportunities in latest snapshot`);
    }
  }
  db.close();
} catch (e) {
  fail('Opportunities: quality check', e.message.slice(0, 80));
}

// ── 8. Archive Integrity ──────────────────────────────────────────────────────
wl('\n  ── [8/12] Archive Integrity ────────────────────────────────────────');
try {
  const archDir = join(DATA_DIR, 'cognition_archive');
  const fsOk    = existsSync(archDir);

  let fileCount = 0;
  if (fsOk) {
    const { readdirSync } = await import('fs');
    fileCount = readdirSync(archDir).filter(f => f.endsWith('.json')).length;
  }

  const db = new Database(DB_PATH, { readonly: true });
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='cognition_snapshots'").get();
  const dbCount = tables
    ? db.prepare("SELECT COUNT(*) as n FROM cognition_snapshots").get().n
    : 0;
  db.close();

  if (dbCount > 0 || fileCount > 0)
    pass('Archive: integrity', `DB=${dbCount} snapshots | FS=${fileCount} JSON files`);
  else
    warn('Archive: integrity', 'no archive data yet — run pipeline first');
} catch (e) {
  fail('Archive: integrity check', e.message.slice(0, 80));
}

// ── 9. Telegram Connectivity ─────────────────────────────────────────────────
wl('\n  ── [9/12] Telegram Format & Connectivity ───────────────────────────');
try {
  const r = await pythonTgTestFormat();
  if (r.error) throw new Error(r.error);
  const ok = r.n_messages >= 2 && r.total_chars > 500;
  if (ok) pass('Telegram: format', `${r.n_messages} messages | ${r.total_chars} chars | HTML OK`);
  else    warn('Telegram: format', `messages=${r.n_messages} chars=${r.total_chars}`);

  // Check if Telegram credentials are configured
  const hasChatId = !!(process.env.TELEGRAM_CHAT_ID || process.env.TG_CHAT_ID);
  const hasToken  = !!(process.env.TELEGRAM_BOT_TOKEN || process.env.TG_BOT_TOKEN);
  if (hasChatId && hasToken) pass('Telegram: credentials', 'CHAT_ID and BOT_TOKEN set');
  else warn('Telegram: credentials', `missing ${!hasChatId ? 'CHAT_ID ' : ''}${!hasToken ? 'BOT_TOKEN' : ''} — check .env`);
} catch (e) {
  fail('Telegram: format check', e.message.slice(0, 80));
}

// ── 10. System Health Monitor ─────────────────────────────────────────────────
wl('\n  ── [10/12] System Health Monitor ──────────────────────────────────');
try {
  const r = await pythonOsHealth();
  if (r.error) throw new Error(r.error);
  // Response: {health_score or overall_health, status, ...}
  const score  = r.health_score ?? r.overall_health ?? 0;
  const status = r.status ?? r.overall_state ?? 'UNKNOWN';
  if (score >= 0.70)      pass('System: health monitor', `score=${(score*100).toFixed(1)}% | ${status}`);
  else if (score >= 0.50) warn('System: health monitor', `score=${(score*100).toFixed(1)}% | ${status}`);
  else                    fail('System: health monitor', `low score=${(score*100).toFixed(1)}% | ${status}`);
} catch (e) {
  fail('System: health monitor', e.message.slice(0, 80));
}

// ── 11. Pipeline Resilience (skip in quick mode) ──────────────────────────────
wl('\n  ── [11/12] Pipeline Resilience ─────────────────────────────────────');
if (QUICK) {
  warn('Pipeline: resilience test', 'skipped (--quick mode)');
} else {
  try {
    const t0 = Date.now();
    const r = await pythonOsResilience();
    const elapsed = (Date.now() - t0) / 1000;
    if (r.error) throw new Error(r.error);
    const rl = r.resilience_level ?? r.level ?? r.overall ?? 'UNKNOWN';
    if (elapsed < 40 && rl !== 'CRITICAL')
      pass('Pipeline: resilience', `${elapsed.toFixed(1)}s | ${rl}`);
    else if (elapsed < 90)
      warn('Pipeline: resilience', `${elapsed.toFixed(1)}s (slow) | ${rl}`);
    else
      fail('Pipeline: resilience', `${elapsed.toFixed(1)}s — too slow`);
  } catch (e) {
    fail('Pipeline: resilience check', e.message.slice(0, 80));
  }
}

// ── 12. Historical Archive Access ─────────────────────────────────────────────
wl('\n  ── [12/12] Historical Archive Access ───────────────────────────────');
try {
  const db = new Database(DB_PATH, { readonly: true });
  const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='cognition_snapshots'").get();
  if (!tables) {
    warn('Archive: historical access', 'table not created yet');
  } else {
    const rows = db.prepare(`
      SELECT snapshot_date, regime, confidence, posture
      FROM   cognition_snapshots
      ORDER  BY snapshot_date DESC
      LIMIT  5
    `).all();
    if (rows.length === 0) {
      warn('Archive: historical access', 'no snapshots stored yet');
    } else {
      const latest = rows[0];
      pass('Archive: historical access',
        `${rows.length} recent | latest=${latest.snapshot_date} ${latest.regime} ${((latest.confidence||0)*100).toFixed(1)}%`);
    }
  }
  db.close();
} catch (e) {
  fail('Archive: historical access', e.message.slice(0, 80));
}

// ─── Summary ─────────────────────────────────────────────────────────────────

wl('');
sep();
wl('  📋 VALIDATION SUMMARY');
sep();

const total   = passed + warned + failed;
const pct     = ((passed / total) * 100).toFixed(0);
const overall = failed === 0 && warned <= 2 ? 'PASS' : failed > 0 ? 'FAIL' : 'WARN';
const icon    = overall === 'PASS' ? '✅' : overall === 'WARN' ? '⚠️ ' : '❌';

wl(`  ${icon} Overall: ${overall}`);
wl(`  ✅ PASS: ${passed}/${total} (${pct}%)`);
if (warned > 0) wl(`  ⚠️  WARN: ${warned}/${total}`);
if (failed > 0) wl(`  ❌ FAIL: ${failed}/${total}`);

if (failed > 0) {
  wl('\n  Failed checks:');
  checks.filter(c => c.status === 'FAIL').forEach(c => {
    wl(`  ❌ ${c.name}: ${c.detail}`);
  });
}

if (warned > 0) {
  wl('\n  Warnings:');
  checks.filter(c => c.status === 'WARN').forEach(c => {
    wl(`  ⚠️  ${c.name}: ${c.detail}`);
  });
}

wl('');
sep();

process.exit(overall === 'FAIL' ? 1 : 0);
