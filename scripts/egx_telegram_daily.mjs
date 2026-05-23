/**
 * EGX Telegram Daily Briefing
 * ============================
 * Runs the Market OS pipeline → formats institutional Telegram report
 * → sends all messages via the configured Telegram bot.
 *
 * Usage:
 *   node scripts/egx_telegram_daily.mjs             # live delivery
 *   node scripts/egx_telegram_daily.mjs --dry-run   # format only, no send
 *   node scripts/egx_telegram_daily.mjs --force     # skip pipeline, format+send only
 */

import { pythonOsPipelineRun, pythonTgFormatDaily } from '../src/egx/index.js';
import { sendTelegram } from '../src/egx/notify.js';
import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR  = join(__dirname, '../data');
const LOG_FILE  = join(DATA_DIR, 'telegram_delivery_log.json');
const DB_PATH   = join(DATA_DIR, 'egx_trading.db');

// ─── Ph30: TV MCP Live Price Validation ──────────────────────────────────────
// Checks if top signals are still in valid entry zone before sending.
// Gracefully degrades — if TV is unavailable, validation is skipped.

async function validateSignalPrices(topSymbols) {
  /** Returns { symbol → { current_price, entry_price, entry_high, pct_from_entry, stale } } */
  const result = {};
  if (!topSymbols || topSymbols.length === 0) return result;

  // 1. Get entry prices from DB
  let db;
  try {
    db = new Database(DB_PATH, { readonly: true });
    const today = new Date().toISOString().slice(0, 10);
    for (const sym of topSymbols) {
      const row = db.prepare(
        `SELECT entry_price, entry_high, stop_loss
         FROM unified_signals
         WHERE symbol=? AND signal_date<=?
         ORDER BY signal_date DESC LIMIT 1`
      ).get(sym, today);
      if (row) result[sym] = { entry_price: row.entry_price, entry_high: row.entry_high, stop_loss: row.stop_loss };
    }
  } catch { /* DB read failed — continue */ }
  finally { try { db?.close(); } catch {} }

  // 2. Try TV MCP live quotes via batch_run
  let callTV = null;
  try {
    const bridge = await import('../src/egx/tv_bridge.js').catch(() => null);
    if (bridge?.callMCPTool) callTV = bridge.callMCPTool;
  } catch { /* TV not available */ }

  if (callTV && topSymbols.length > 0) {
    try {
      const tvSyms = topSymbols.map(s => `EGX:${s}`);
      const batch  = await callTV('batch_run', { symbols: tvSyms, action: 'quote_get' });
      const quotes = batch?.results || [];
      for (const q of quotes) {
        const sym   = (q.symbol || '').replace('EGX:', '');
        const price = q.data?.last_price ?? q.data?.close ?? null;
        if (!price || !result[sym]) continue;
        const entry      = result[sym].entry_price;
        const entryHigh  = result[sym].entry_high;
        const stop       = result[sym].stop_loss;
        const pct        = entry ? ((price - entry) / entry * 100) : null;
        // Stale = price moved >5% above entry_high (missed) or below stop_loss
        const missed = entryHigh && price > entryHigh * 1.05;
        const stopped = stop && price < stop;
        result[sym] = { ...result[sym], current_price: price, pct_from_entry: pct, stale: missed || stopped, missed, stopped };
      }
    } catch (e) {
      wl(`  ⚠️  TV price validation error: ${e.message}`);
    }
  }

  return result;
}

const DRY_RUN = process.argv.includes('--dry-run');
const FORCE   = process.argv.includes('--force');

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 65) => wl(c.repeat(n));

// ─── Delivery Log ────────────────────────────────────────────────────────────

function appendDeliveryLog(entry) {
  let log = { deliveries: [] };
  try { log = JSON.parse(readFileSync(LOG_FILE, 'utf8')); } catch { /* first run */ }
  log.deliveries.push(entry);
  if (log.deliveries.length > 90) log.deliveries = log.deliveries.slice(-90);
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
}

// ── Same-day duplicate-send guard ────────────────────────────────────────────
// Prevents accidental double-delivery if the script is run twice in one day.
// Override with --force flag.
if (!DRY_RUN && !FORCE) {
  try {
    const _log = JSON.parse(readFileSync(LOG_FILE, 'utf8'));
    const _today = new Date().toISOString().slice(0, 10);
    const _lastSent = (_log.deliveries || []).slice().reverse()
      .find(d => d.messages_sent > 0);
    if (_lastSent && _lastSent.date === _today) {
      sep();
      wl(`  ⛔ Already delivered today (${_today} — ${_lastSent.time || '?'})`);
      wl(`  ℹ️  Use --force to override or --dry-run to preview`);
      sep();
      process.exit(0);
    }
  } catch { /* log missing or malformed — first run, proceed */ }
}

// ─── Main ─────────────────────────────────────────────────────────────────────

const t0 = Date.now();
sep();
wl(`  📲 EGX TELEGRAM DAILY BRIEFING`);
wl(`  ${DRY_RUN ? '🔍 DRY RUN — no messages sent' : '🚀 LIVE — will send to Telegram'}`);
wl(`  ${new Date().toISOString()}`);
sep();

// ── Step 1: Run Market OS Pipeline ───────────────────────────────────────────
let pipelineOk = true;
if (!FORCE) {
  wl('\n  ⚙️  Step 1: Running Market OS pipeline...');
  try {
    const pr = await pythonOsPipelineRun();
    if (pr.error) {
      wl(`  ⚠️  Pipeline warning: ${pr.error}`);
      wl('  ℹ️  Continuing with latest cached data...');
      pipelineOk = false;
    } else {
      const steps = pr.steps_done ?? '?';
      const total = pr.steps_total ?? 8;
      const dur   = pr.duration_sec != null ? `${pr.duration_sec.toFixed(1)}s` : '?';
      wl(`  ✅ Pipeline: ${steps}/${total} steps | ${dur} | status: ${pr.status || 'OK'}`);
    }
  } catch (e) {
    wl(`  ⚠️  Pipeline threw: ${e.message} — continuing with cached data`);
    pipelineOk = false;
  }
} else {
  wl('  ⏭  Step 1: Skipped (--force flag)');
}

// ── Step 2: Format Telegram Report ──────────────────────────────────────────
wl('\n  📝 Step 2: Formatting Telegram report...');
let formatResult;
try {
  formatResult = await pythonTgFormatDaily();
} catch (e) {
  wl(`  ❌ Format error: ${e.message}`);
  process.exit(1);
}

if (formatResult.error) {
  wl(`  ❌ Format error: ${formatResult.error}`);
  if (formatResult.stderr) wl(formatResult.stderr.slice(0, 300));
  process.exit(1);
}

let messages = formatResult.messages || [];
wl(`  ✅ Formatted: ${messages.length} messages | ${formatResult.total_chars} chars total`);
messages.forEach((msg, i) => wl(`     Msg ${i+1}: ${msg.length} chars`));

// ── Step 2b: Ph30 — TV MCP Live Price Validation ─────────────────────────────
wl('\n  🔍 Step 2b: Validating signal prices via TradingView...');
const topSymbols = formatResult.top_symbols || [];
let priceValidation = {};
let staleCount = 0;
if (topSymbols.length > 0) {
  try {
    priceValidation = await validateSignalPrices(topSymbols);
    const staleSyms = Object.entries(priceValidation)
      .filter(([, v]) => v.stale)
      .map(([s]) => s);
    staleCount = staleSyms.length;
    if (staleCount > 0) {
      wl(`  ⚠️  ${staleCount} signals may be stale (price moved): ${staleSyms.join(', ')}`);
      // Append a staleness note to the second message if it exists
      const staleNote = `\n⚠️ <i>ملاحظة: ${staleCount} إشارة قد تجاوزت منطقة الدخول — راجع الأسعار قبل التنفيذ</i>`;
      if (messages.length >= 2) {
        messages[messages.length - 1] += staleNote;
      } else if (messages.length === 1) {
        messages[0] += staleNote;
      }
    } else if (Object.keys(priceValidation).length > 0) {
      wl(`  ✅ All ${topSymbols.length} signals in valid price zone`);
    } else {
      wl('  ℹ️  Price validation skipped (TV not available)');
    }
  } catch (e) {
    wl(`  ⚠️  Price validation failed: ${e.message} — continuing`);
  }
} else {
  wl('  ℹ️  No top symbols to validate');
}

// ── Step 2c: Ph33 — Model Drift Monitor ──────────────────────────────────────
// Runs model_drift check — if win rate < 45% alert, prepend warning to Msg1
wl('\n  📊 Step 2c: Checking model drift (Ph33)...');
try {
  const { execFileSync: _execFs } = await import('child_process');
  const _python3c = (() => {
    for (const p of ['/usr/bin/python3', '/usr/local/bin/python3', 'python3']) {
      try { _execFs(p, ['--version'], { stdio: 'ignore' }); return p; } catch {}
    }
    return 'python3';
  })();
  const _driftRaw = _execFs(
    _python3c,
    ['scripts/python/signal_integration.py', 'model_drift',
     JSON.stringify({ window_days: 30, min_filled: 10, alert_threshold_wr: 45.0 })],
    { cwd: join(__dirname, '..'), timeout: 15_000 }
  ).toString();
  const _drift = JSON.parse(_driftRaw.trim());
  if (_drift.success) {
    if (_drift.pending_outcomes) {
      // hit_t5 not yet resolved for recent signals — not a real drift
      wl(`  ℹ️  Drift: ${_drift.message}`);
    } else if (_drift.n_filled < _drift.min_filled) {
      wl(`  ℹ️  Drift: ${_drift.n_filled}/${_drift.min_filled} صفقات مكتملة — لا يكفي للفحص`);
    } else if (_drift.drift_detected) {
      wl(`  ⚠️  DRIFT DETECTED: ${_drift.drift_reason}`);
      // Prepend a visible drift alert to the first Telegram message
      const driftAlert = `⚠️ <b>تحذير: تدهور في دقة النموذج</b>\n<i>${_drift.drift_reason}</i>\n<i>WR آخر 30 يوم: ${_drift.win_rate}% | إجراء: إعادة تدريب مجدولة</i>\n\n`;
      if (messages.length > 0) messages[0] = driftAlert + messages[0];
    } else {
      wl(`  ✅ Drift OK: WR=${_drift.win_rate}% | gated=${_drift.gated_win_rate ?? 'N/A'}% | calibration=${_drift.calibration_ok ?? 'N/A'}`);
    }
  }
} catch (e) {
  wl(`  ℹ️  Drift check skipped: ${e.message}`);
}

// ── Step 2d: Ph39 — ML Score Delta (أسهم قفزت/هبطت >15pt) ─────────────────
wl('\n  📊 Step 2d: Checking ML score delta (Ph39)...');
try {
  const { execFileSync: _execFsd } = await import('child_process');
  const _py3d = (() => {
    for (const p of ['/usr/bin/python3', '/usr/local/bin/python3', 'python3']) {
      try { _execFsd(p, ['--version'], { stdio: 'ignore' }); return p; } catch {}
    }
    return 'python3';
  })();
  const _deltaRaw = _execFsd(
    _py3d,
    ['scripts/python/signal_integration.py', 'ml_score_delta', '{"min_delta":15}'],
    { cwd: join(__dirname, '..'), timeout: 15_000 }
  ).toString();
  const _delta = JSON.parse(_deltaRaw.trim());
  if (_delta.success && _delta.n_surging > 0) {
    wl(`  ⚡ ${_delta.n_surging} أسهم قفز ML بـ ≥15pt اليوم`);
    // Append ML momentum note to last message
    const _topSurge = _delta.surging.slice(0, 3)
      .map(s => `${s.symbol} +${s.delta}pt (${s.ml_yesterday}%→${s.ml_today}%)`)
      .join(' | ');
    const _surgeNote = `\n🔥 <b>زخم ML اليوم:</b> <i>${_topSurge}</i>`;
    if (messages.length > 0) messages[messages.length - 1] += _surgeNote;
  } else {
    wl(`  ℹ️  ML delta: ${_delta.n_surging} surging | ${_delta.n_dropping} dropping`);
  }
} catch (e) {
  wl(`  ℹ️  ML delta skipped: ${e.message.slice(0, 80)}`);
}

// ── Step 2e: Ph45 — Stop-Loss Hit Detector ───────────────────────────────────
wl('\n  🛑 Step 2e: Checking stop-loss hits (Ph45)...');
try {
  const { execFileSync: _execFse } = await import('child_process');
  const _py3e = (() => {
    for (const p of ['/usr/bin/python3', '/usr/local/bin/python3', 'python3']) {
      try { _execFse(p, ['--version'], { stdio: 'ignore' }); return p; } catch {}
    }
    return 'python3';
  })();
  const _slRaw = _execFse(
    _py3e,
    ['scripts/python/signal_integration.py', 'stop_loss_hits', '{"lookback_days":7}'],
    { cwd: join(__dirname, '..'), timeout: 20_000 }
  ).toString();
  const _sl = JSON.parse(_slRaw.trim());
  if (_sl.success) {
    if (_sl.n_hit_stop > 0) {
      wl(`  🛑 ${_sl.n_hit_stop} إشارة وصل وقفها: ${_sl.hit_stop.slice(0,3).map(s=>s.symbol).join(', ')}`);
      const _stopNote = `\n🛑 <b>تحذير وقف خسارة:</b> <i>${_sl.hit_stop.slice(0,3).map(s => `${s.symbol} (${s.pct_from_stop}% من الوقف)`).join(' | ')}</i>`;
      if (messages.length > 0) messages[0] = _stopNote + '\n\n' + messages[0];
    } else if (_sl.n_near_stop > 0) {
      wl(`  ⚠️  ${_sl.n_near_stop} إشارة قريبة من الوقف: ${_sl.near_stop.slice(0,3).map(s=>s.symbol).join(', ')}`);
      const _nearNote = `\n⚠️ <i>تنبيه: ${_sl.near_stop.slice(0,3).map(s => `${s.symbol} على بُعد ${s.pct_from_stop}% من الوقف`).join(' | ')}</i>`;
      if (messages.length >= 2) messages[messages.length - 1] += _nearNote;
    } else {
      wl(`  ✅ ${_sl.n_tracked} إشارة مُتتبَّعة — لا وقوف مُكسَّرة`);
    }
  }
} catch (e) {
  wl(`  ℹ️  Stop-loss check skipped: ${e.message.slice(0, 80)}`);
}

// ── Step 3: Dry Run — show preview ──────────────────────────────────────────
if (DRY_RUN) {
  wl('\n  🔍 DRY RUN — Message Preview:');
  messages.forEach((msg, i) => {
    wl(`\n  ──── Message ${i+1} ─────────────────────────────────────────────`);
    msg.split('\n').forEach(l => wl('  ' + l));
  });
  wl(`\n${'═'.repeat(65)}`);
  wl(`  ⏱  ${((Date.now()-t0)/1000).toFixed(1)}s | 0 messages sent (dry run)`);
  process.exit(0);
}

// ── Step 4: Send messages ────────────────────────────────────────────────────
wl('\n  📤 Step 3: Sending to Telegram...');
const sent   = [];
const failed = [];

for (let i = 0; i < messages.length; i++) {
  const msg = messages[i];
  try {
    await sendTelegram(msg, { parseMode: 'HTML' });
    sent.push(i + 1);
    wl(`  ✅ Message ${i+1} sent (${msg.length} chars)`);
    if (i < messages.length - 1) {
      await new Promise(r => setTimeout(r, 1500));
    }
  } catch (e) {
    failed.push({ index: i + 1, error: e.message });
    wl(`  ❌ Message ${i+1} failed: ${e.message}`);
  }
}

// ── Step 4b: Send Visual Cards (Pillow-based image cards) ────────────────────
wl('\n  🎨 Step 4b: Sending visual cards...');
try {
  const { execFileSync } = await import('child_process');
  const cardScript = join(__dirname, '../scripts/python/telegram_send_cards.py');
  const reportDate = formatResult.date || new Date().toISOString().slice(0, 10);
  const cardResult = execFileSync('python3', [cardScript, reportDate], {
    timeout: 60_000,
    env: { ...process.env },
    cwd: join(__dirname, '..'),
  });
  wl(`  ✅ Visual cards: ${cardResult.toString().trim().split('\n').pop()}`);
} catch (cardErr) {
  wl(`  ⚠️  Visual cards skipped: ${cardErr.message?.slice(0, 100) || cardErr}`);
}

// ── Step 5: Log Delivery ─────────────────────────────────────────────────────
const elapsed = ((Date.now()-t0)/1000).toFixed(1);
try {
  appendDeliveryLog({
    date:             formatResult.date,
    time:             formatResult.time,
    timestamp:        new Date().toISOString(),
    messages_sent:    sent.length,
    messages_failed:  failed.length,
    total_chars:      formatResult.total_chars,
    pipeline_ran:     !FORCE,
    pipeline_ok:      pipelineOk,
    duration_sec:     parseFloat(elapsed),
    errors:           failed,
    stale_signals:    staleCount,           // Ph30
    validated_symbols: topSymbols.length,   // Ph30
  });
  wl(`  📋 Delivery logged to ${LOG_FILE}`);
} catch (e) {
  wl(`  ⚠️  Log write failed: ${e.message}`);
}

// ── Summary ───────────────────────────────────────────────────────────────────
sep();
const allOk = failed.length === 0;
wl(`  ${allOk ? '✅' : '⚠️ '} Delivery: ${sent.length}/${messages.length} messages sent`);
if (failed.length > 0) failed.forEach(f => wl(`  ❌ Msg ${f.index}: ${f.error}`));
wl(`  ⏱  Total: ${elapsed}s`);
sep();

process.exit(allOk ? 0 : 1);
