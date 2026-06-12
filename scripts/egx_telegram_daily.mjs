/**
 * EGX Telegram Daily Briefing
 * ============================
 * Runs the Market OS pipeline → formats institutional Telegram report
 * → sends all messages via the configured Telegram bot.
 *
 * Usage:
 *   node scripts/egx_telegram_daily.mjs             # live delivery
 *   node scripts/egx_telegram_daily.mjs --dry-run   # format only, no send
 *   node scripts/egx_telegram_daily.mjs --force     # skip pipeline only; QA/freshness still enforced
 */

import { pythonOsPipelineRun, pythonTgFormatDaily } from '../src/egx/index.js';
import { sendTelegram, validateTelegramPayload } from '../src/egx/notify.js';
import { writeFileSync, readFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { toTvSymbol, fromTvSymbol } from '../src/egx/tv_symbols.js';
import {
  logDeliveryAttempt, countActionable, ensureDeliveryAuditTable,
  normalizeDeliverableSignals, wasAlreadySent,
} from './lib/delivery_audit.mjs';
import { runPreSendCheck } from './lib/pre_send_check.mjs';
import { buildClientFormatParams, resolvePrepMode } from './lib/client_message_prep.mjs';
import { loadEnv } from './lib/load_env.mjs';

loadEnv();

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_DIR  = join(__dirname, '../data');
const LOG_FILE  = join(DATA_DIR, 'telegram_delivery_log.json');
const DB_PATH   = join(DATA_DIR, 'egx_trading.db');

// ─── Ph30: TV MCP Live Price Validation ──────────────────────────────────────
// Checks if top signals are still in valid entry zone before sending.
// Gracefully degrades — if TV is unavailable, validation is skipped.

async function validateSignalPrices(topSymbols, reportDate = null) {
  /** Returns { symbol → { current_price, entry_price, entry_high, pct_from_entry, stale } } */
  const result = {};
  if (!topSymbols || topSymbols.length === 0) return result;

  // 1. Get entry prices from DB
  let db;
  try {
    db = new Database(DB_PATH, { readonly: true });
    const today = reportDate || getLatestOhlcvDate() || new Date().toISOString().slice(0, 10);
    for (const sym of topSymbols) {
      const row = db.prepare(
        `SELECT entry_price, entry_high, stop_loss
         FROM final_signals
         WHERE symbol=? AND trade_date<=? AND actionable=1 AND veto_reason IS NULL
         ORDER BY trade_date DESC LIMIT 1`
      ).get(sym, today) ?? db.prepare(
        `SELECT entry_price, entry_high, stop_loss
         FROM unified_signals
         WHERE symbol=? AND signal_date<=? AND quality_gate_passed=1
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
      const tvSyms = topSymbols.map(s => toTvSymbol(s));
      const batch  = await callTV('batch_run', { symbols: tvSyms, action: 'quote_get' });
      const quotes = batch?.results || [];
      for (const q of quotes) {
        const sym   = fromTvSymbol(q.symbol || q.request?.symbol || q.input?.symbol || '');
        const quote = q.result || q.data || {};
        const price = quote.last_price ?? quote.last ?? quote.close ?? null;
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
const PREP    = resolvePrepMode();
const requestedReportDate = getLatestOhlcvDate() || new Date().toISOString().slice(0, 10);
const prepBundle = buildClientFormatParams(requestedReportDate, { prep: PREP });

ensureDeliveryAuditTable();

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (c = '═', n = 65) => wl(c.repeat(n));

function sanitizeTelegramHtml(text = '') {
  return String(text)
    // Keep Telegram-supported tags, escape everything else that looks like HTML.
    .replace(/<(?!\/?(b|strong|i|em|u|ins|s|strike|del|code|pre|a)(\s+href="[^"]*")?\s*>)/gi, '&lt;');
}

function stripTelegramHtml(text = '') {
  return String(text)
    .replace(/<[^>]*>/g, '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

// ─── Delivery Log ────────────────────────────────────────────────────────────

function appendDeliveryLog(entry) {
  let log = { deliveries: [] };
  try { log = JSON.parse(readFileSync(LOG_FILE, 'utf8')); } catch { /* first run */ }
  log.deliveries.push(entry);
  if (log.deliveries.length > 90) log.deliveries = log.deliveries.slice(-90);
  if (!existsSync(DATA_DIR)) mkdirSync(DATA_DIR, { recursive: true });
  writeFileSync(LOG_FILE, JSON.stringify(log, null, 2));
}

function getLatestOhlcvDate() {
  let db;
  try {
    db = new Database(DB_PATH, { readonly: true });
    const row = db.prepare(
      "SELECT MAX(date(bar_time, 'unixepoch')) AS latest FROM ohlcv_history"
    ).get();
    return row?.latest ?? null;
  } catch {
    return null;
  } finally {
    try { db?.close(); } catch {}
  }
}

function getFinalActionableCount(reportDate) {
  let db;
  try {
    db = new Database(DB_PATH, { readonly: true });
    const row = db.prepare(
      `SELECT COUNT(*) AS n
       FROM final_signals
       WHERE trade_date=? AND actionable=1 AND veto_reason IS NULL`
    ).get(reportDate);
    return Number(row?.n || 0);
  } catch {
    return 0;
  } finally {
    try { db?.close(); } catch {}
  }
}

function scrubClientText(text) {
  return String(text ?? '')
    .replace(/\b(undefined|null|NaN)\b/gi, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{4,}/g, '\n\n\n')
    .trim();
}

function validateClientMessages(messages, { reportDate, finalActionableCount, prepMode = false, targetSessionDate = null }) {
  const issues = [];
  const body = messages.join('\n\n');
  if (!body.trim()) issues.push('empty Telegram payload');
  if (/\b(undefined|null|NaN)\b/i.test(body)) {
    issues.push('payload contains undefined/null/NaN');
  }
  if (/0 stocks|No data|@ undefined|None @/i.test(body)) {
    issues.push('payload contains debug/research placeholder text');
  }
  if (finalActionableCount <= 0) {
    const forbiddenWhenNoFinal = [
      /أفضل فرص التداول/,
      /فرص مؤهلة/,
      /منطقة الدخول/,
      /(?:^|\n)\s*الدخول:/,
      /رادار الانفجار/,
      /توقعات الانفجار/,
      /زخم ML/,
      /مرشح داعم/,
      /قائمة المراقبة/,
    ];
    for (const rx of forbiddenWhenNoFinal) {
      if (rx.test(body)) issues.push(`no final_signals actionable=1, but payload matches ${rx}`);
    }
  }
  const isoDates = [...body.matchAll(/\b20\d{2}-\d{2}-\d{2}\b/g)].map(m => m[0]);
  const allowedIso = new Set([reportDate]);
  if (prepMode && targetSessionDate) allowedIso.add(targetSessionDate);
  const wrongDates = [...new Set(isoDates.filter(d => !allowedIso.has(d)))];
  if (wrongDates.length > 0) {
    issues.push(`payload contains non-report ISO date(s): ${wrongDates.slice(0, 5).join(', ')}`);
  }
  messages.forEach((msg, i) => {
    const qa = validateTelegramPayload(msg, {
      clientDelivery: true,
      reportDate,
      finalActionableCount,
    });
    if (!qa.ok) {
      qa.issues.forEach(issue => issues.push(`msg${i + 1}: ${issue}`));
    }
  });
  return issues;
}

// ── Same-day duplicate-send guard ────────────────────────────────────────────
// Prevents accidental double-delivery if the script is run twice in one day.
// Override with --force flag.
if (!DRY_RUN && !FORCE) {
  try {
    const _today = requestedReportDate;
    const _dup = wasAlreadySent(_today);
    const _log = JSON.parse(readFileSync(LOG_FILE, 'utf8'));
    const _lastSent = (_log.deliveries || []).slice().reverse()
      .find(d => d.messages_sent > 0 && d.date === _today);
    if (_dup.duplicate || _lastSent) {
      const act = countActionable(_today);
      logDeliveryAttempt({
        signal_date: _today,
        actionable: act.db > 0,
        deliverable: act.deliverable > 0,
        message_generated: 0,
        send_attempted: 0,
        send_success: 0,
        skip_reason: `duplicate_same_day:${_dup.reason || 'legacy_log'}:last_sent=${_lastSent?.time || '?'}`,
        pipeline_stage: 'duplicate_guard',
        dedup_key: `live:${_today}`,
        meta_json: { last_sent: _lastSent, dup: _dup, actionable: act },
      });
      sep();
      wl(`  ⛔ Already delivered today (${_today} — ${_lastSent.time || '?'})`);
      wl(`  ℹ️  Use --force to override or --dry-run to preview`);
      wl(`  📋 Skip logged to notification_delivery_audit`);
      sep();
      process.exit(0);
    }
  } catch (e) {
    wl(`  ⚠️  Duplicate guard read failed: ${e.message} — proceeding`);
  }
}

// ─── Main ─────────────────────────────────────────────────────────────────────

const t0 = Date.now();
sep();
wl(`  📲 EGX TELEGRAM DAILY BRIEFING`);
wl(`  ${PREP ? `📌 PREP — توصية الجلسة القادمة (${prepBundle.target_session_date || '?'})` : '📅 Same-day bulletin'}`);
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
  formatResult = await pythonTgFormatDaily(prepBundle.params);
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
const finalActionableCount = Number(
  formatResult.final_actionable_count ?? getFinalActionableCount(formatResult.date || requestedReportDate)
);
messages = messages.map(scrubClientText).filter(Boolean);
wl(`  ✅ Formatted: ${messages.length} messages | ${formatResult.total_chars} chars total`);
messages.forEach((msg, i) => wl(`     Msg ${i+1}: ${msg.length} chars`));
if (formatResult.formatter_diagnostics) {
  const fd = formatResult.formatter_diagnostics;
  wl(`  📊 Formatter: db_actionable=${fd.db_actionable} deliverable=${fd.deliverable_after_qg} top=${fd.formatter_top_n}`);
  if (fd.filtered?.length) {
    fd.filtered.slice(0, 5).forEach(f => wl(`     ⚠️  filtered ${f.symbol}: ${f.reason}`));
  }
  if (fd.warning) wl(`  ⚠️  ${fd.warning}`);
}

// ── Step 2a: Product freshness gate ─────────────────────────────────────────
// Live delivery must not outrun the trusted OHLCV layer. --force only skips
// the pipeline and duplicate-send guard; it never bypasses freshness.
const latestOhlcvDate = getLatestOhlcvDate();
const reportDate = formatResult.date || requestedReportDate;
normalizeDeliverableSignals(reportDate);

let preSend = null;
if (!DRY_RUN) {
  preSend = runPreSendCheck(reportDate, {
    dryRun: false, allowDuplicate: FORCE, logBlock: false, prepMode: PREP,
  });
  if (!preSend.ok) {
    wl('  ⛔ Pre-send gate blocked live delivery:');
    preSend.blockers.forEach(issue => wl(`     - ${issue}`));
    logDeliveryAttempt({
      signal_date: reportDate,
      actionable: preSend.actionable.db > 0,
      deliverable: preSend.actionable.deliverable > 0,
      message_generated: messages.length > 0 ? 1 : 0,
      send_attempted: 0,
      send_success: 0,
      skip_reason: preSend.blockers.some(b => b.includes('ML') || b.includes('ml_prediction'))
        ? `ML_STALE: ${preSend.blockers.join(' | ')}`
        : `PRE_SEND_BLOCK: ${preSend.blockers.join(' | ')}`,
      pipeline_stage: 'pre_send_block',
      ml_latest_date: preSend.ml_latest_date,
      required_ml_date: reportDate,
      dedup_key: `live:${reportDate}:pre_send`,
      meta_json: { checks: preSend.checks, blockers: preSend.blockers },
    });
    appendDeliveryLog({
      date: reportDate,
      time: formatResult.time,
      timestamp: new Date().toISOString(),
      messages_sent: 0,
      messages_failed: messages.length,
      total_chars: messages.reduce((n, m) => n + m.length, 0),
      pipeline_ran: !FORCE,
      pipeline_ok: pipelineOk,
      duration_sec: parseFloat(((Date.now() - t0) / 1000).toFixed(1)),
      errors: preSend.blockers.map((error, index) => ({ index: `pre_send_${index + 1}`, error })),
      stale_signals: null,
      validated_symbols: 0,
    });
    process.exit(5);
  }
  wl(`  ✅ Pre-send gate OK — deliverable=${preSend.actionable.deliverable} ML=${preSend.ml_latest_date}`);
}

const qaIssues = validateClientMessages(messages, {
  reportDate,
  finalActionableCount,
  prepMode: PREP,
  targetSessionDate: prepBundle.target_session_date,
});
if (qaIssues.length > 0) {
  wl(`  ⛔ Client QA blocked delivery:`);
  qaIssues.forEach(issue => wl(`     - ${issue}`));
  if (!DRY_RUN) {
    logDeliveryAttempt({
      signal_date: reportDate,
      actionable: finalActionableCount > 0,
      message_generated: messages.length > 0 ? 1 : 0,
      send_attempted: 0,
      send_success: 0,
      skip_reason: `client_qa:${qaIssues.join(' | ')}`,
      pipeline_stage: 'client_qa_block',
      dedup_key: `live:${reportDate}`,
      meta_json: { qaIssues, finalActionableCount },
    });
    appendDeliveryLog({
      date:            reportDate,
      time:            formatResult.time,
      timestamp:       new Date().toISOString(),
      messages_sent:   0,
      messages_failed: messages.length,
      total_chars:     messages.reduce((n, m) => n + m.length, 0),
      pipeline_ran:    !FORCE,
      pipeline_ok:     pipelineOk,
      duration_sec:    parseFloat(((Date.now()-t0)/1000).toFixed(1)),
      errors:          qaIssues.map((error, index) => ({ index: `client_qa_${index + 1}`, error })),
      stale_signals:   null,
      validated_symbols: 0,
    });
    process.exit(3);
  }
}
if (latestOhlcvDate && reportDate && latestOhlcvDate < reportDate) {
  const freshnessMsg = `OHLCV أحدث تاريخ موثوق ${latestOhlcvDate} بينما التقرير ${reportDate}`;
  if (!DRY_RUN) {
    wl(`  ⛔ Freshness gate blocked live delivery: ${freshnessMsg}`);
    logDeliveryAttempt({
      signal_date: reportDate,
      actionable: finalActionableCount > 0,
      message_generated: messages.length > 0 ? 1 : 0,
      send_attempted: 0,
      send_success: 0,
      skip_reason: `freshness_gate:${freshnessMsg}`,
      pipeline_stage: 'freshness_block',
      dedup_key: `live:${reportDate}`,
    });
    appendDeliveryLog({
      date:            reportDate,
      time:            formatResult.time,
      timestamp:       new Date().toISOString(),
      messages_sent:   0,
      messages_failed: messages.length,
      total_chars:     formatResult.total_chars,
      pipeline_ran:    !FORCE,
      pipeline_ok:     pipelineOk,
      duration_sec:    parseFloat(((Date.now()-t0)/1000).toFixed(1)),
      errors:          [{ index: 'freshness_gate', error: freshnessMsg }],
      stale_signals:   null,
      validated_symbols: 0,
    });
    process.exit(2);
  }
  wl(`  ⚠️  Freshness warning: ${freshnessMsg}`);
}

// ── Step 2b: Ph30 — TV MCP Live Price Validation ─────────────────────────────
wl('\n  🔍 Step 2b: Validating signal prices via TradingView...');
const topSymbols = formatResult.top_symbols || [];
let priceValidation = {};
let staleCount = 0;
if (topSymbols.length > 0 && finalActionableCount > 0) {
  try {
    priceValidation = await validateSignalPrices(topSymbols, reportDate);
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

// ── Step 2c2: Ph46 — Bayesian WR warning (opt-in, does NOT block signals) ───
// Enable with EGX_BAYESIAN_WARN=1 — prepends caution text only, never sets actionable=0
if (process.env.EGX_BAYESIAN_WARN === '1') {
wl('\n  📊 Step 2c2: Checking Bayesian WR (Ph46)...');
try {
  const db = new Database(DB_PATH, { readonly: true });
  const bayes = db.prepare(`
    SELECT mean_wr, ci_lower, run_date, n_obs
    FROM bayesian_wr
    WHERE category='overall'
    ORDER BY run_date DESC, id DESC LIMIT 1
  `).get();
  if (bayes && bayes.ci_lower != null && bayes.ci_lower < 0.45 && (bayes.n_obs ?? 0) >= 20) {
    const wrPct = ((bayes.mean_wr ?? 0) * 100).toFixed(1);
    const ciPct = (bayes.ci_lower * 100).toFixed(1);
    wl(`  ⚠️  Bayesian CI low: ${ciPct}% (mean ${wrPct}%, n=${bayes.n_obs})`);
    const bayesAlert = `⚠️ <b>تحذير: ثقة Win Rate منخفضة</b>\n<i>Bayesian CI السفلي ${ciPct}% (متوسط ${wrPct}%) — راجع حجم المراكز</i>\n\n`;
    if (messages.length > 0) messages[0] = bayesAlert + messages[0];
  } else if (bayes) {
    wl(`  ✅ Bayesian WR: mean=${((bayes.mean_wr ?? 0) * 100).toFixed(1)}% CI↓=${bayes.ci_lower != null ? (bayes.ci_lower * 100).toFixed(1) : '—'}%`);
  }
  db.close();
} catch (e) {
  wl(`  ℹ️  Bayesian check skipped: ${e.message}`);
}
} else {
  wl('\n  📊 Step 2c2: Bayesian WR warning off (set EGX_BAYESIAN_WARN=1 to enable)');
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
  if (_delta.success && _delta.n_surging > 0 && finalActionableCount > 0 && topSymbols.length > 0) {
    wl(`  ⚡ ${_delta.n_surging} أسهم قفز ML بـ ≥15pt اليوم`);
    // Append ML momentum note to last message
    const _topSet = new Set(topSymbols);
    const _topSurge = _delta.surging
      .filter(s => _topSet.has(s.symbol))
      .slice(0, 3)
      .map(s => `${s.symbol} +${s.delta}pt (${s.ml_yesterday}%→${s.ml_today}%)`)
      .join(' | ');
    if (_topSurge) {
      const _surgeNote = `\n🔥 <b>زخم ML اليوم:</b> <i>${_topSurge}</i>`;
      if (messages.length > 0) messages[messages.length - 1] += _surgeNote;
    } else {
      wl('  ℹ️  ML delta kept internal; no surging symbol is in final client signals');
    }
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
  if (_sl.success && finalActionableCount > 0 && topSymbols.length > 0) {
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
  } else if (_sl.success) {
    wl('  ℹ️  Stop-loss client note suppressed (no final actionable signals today)');
  }
} catch (e) {
  wl(`  ℹ️  Stop-loss check skipped: ${e.message.slice(0, 80)}`);
}

// ── Step 3: Dry Run — show preview ──────────────────────────────────────────
if (DRY_RUN) {
  logDeliveryAttempt({
    signal_date: reportDate,
    actionable: finalActionableCount > 0,
    message_generated: messages.length > 0 ? 1 : 0,
    send_attempted: 0,
    send_success: 0,
    skip_reason: 'dry_run_mode',
    pipeline_stage: 'dry_run',
    dedup_key: `dry_run:${reportDate}`,
    meta_json: {
      top_symbols: topSymbols,
      final_actionable_count: finalActionableCount,
      formatter_diagnostics: formatResult.formatter_diagnostics,
    },
  });
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
  const msg = sanitizeTelegramHtml(messages[i]);
  try {
    const result = await sendTelegram(msg, {
      parseMode: 'HTML',
      clientDelivery: true,
      reportDate,
      finalActionableCount,
    });
    if (!result?.ok) throw new Error(result?.error || 'Telegram send failed');
    sent.push(i + 1);
    logDeliveryAttempt({
      signal_date: reportDate,
      symbol: topSymbols[i] ?? null,
      actionable: finalActionableCount > 0,
      message_generated: 1,
      send_attempted: 1,
      send_success: 1,
      provider_response: { messageId: result.messageId },
      pipeline_stage: 'telegram_send',
      dedup_key: `live:${reportDate}:msg${i + 1}`,
      meta_json: { msg_index: i + 1, chars: msg.length },
    });
    wl(`  ✅ Message ${i+1} sent (${msg.length} chars)`);
    if (i < messages.length - 1) {
      await new Promise(r => setTimeout(r, 1500));
    }
  } catch (e) {
    wl(`  ⚠️  Message ${i+1} HTML failed, retrying plain text: ${e.message}`);
    try {
      const plain = stripTelegramHtml(msg);
      const retry = await sendTelegram(plain, {
        clientDelivery: true,
        reportDate,
        finalActionableCount,
      });
      if (!retry?.ok) throw new Error(retry?.error || 'Telegram plain-text retry failed');
      sent.push(i + 1);
      wl(`  ✅ Message ${i+1} sent as plain text (${plain.length} chars)`);
    } catch (retryError) {
      failed.push({ index: i + 1, error: retryError.message });
      logDeliveryAttempt({
        signal_date: reportDate,
        symbol: topSymbols[i] ?? null,
        actionable: finalActionableCount > 0,
        message_generated: 1,
        send_attempted: 1,
        send_success: 0,
        send_error: retryError.message,
        pipeline_stage: 'telegram_send_failed',
        dedup_key: `live:${reportDate}:msg${i + 1}:fail`,
      });
      wl(`  ❌ Message ${i+1} failed: ${retryError.message}`);
    }
  }
}

// ── Step 4b: Visual Cards Policy ─────────────────────────────────────────────
// Client delivery must use the single notify.js QA/freshness gate. The card
// sender uses a separate transport, so the daily client route leaves it off.
wl('\n  🎨 Step 4b: Visual cards blocked by unified QA delivery policy');

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
