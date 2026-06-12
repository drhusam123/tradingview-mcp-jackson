#!/usr/bin/env node
/**
 * EGX TradingView Auto Update
 * ===========================
 * One official EOD runner for the TradingView Desktop MCP layer.
 *
 * Safe defaults:
 * - never sends Telegram unless --notify is passed
 * - never creates live TradingView alerts unless --live-alerts is passed
 * - launches TradingView only with --launch
 */
import { execSync } from 'child_process';
import { closeSync, existsSync, mkdirSync, openSync, readFileSync, unlinkSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { getDB } from '../src/egx/index.js';
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { isTradingDay, cairoDateParts } from './lib/egx_calendar.mjs';
import { alertNotification } from './lib/notification_alert.mjs';
import { enforceDailyQualityGate } from './lib/data_quality_gate.mjs';
import { writeProofLoopSnapshot } from './lib/proof_loop.mjs';
import { checkIndicatorCacheCoverage } from './lib/indicator_cache_gate.mjs';
import { buildDiscoveryParams } from './lib/discovery_context.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// Load .env (TV_CDP_BROWSER, Telegram, PY_TIMEOUT)
const envPath = join(ROOT, '.env');
if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, 'utf8').split('\n')) {
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const [k, ...rest] = line.split('=');
    const key = k.trim();
    const val = rest.join('=').trim().replace(/^["']|["']$/g, '');
    if (key && process.env[key] === undefined) process.env[key] = val;
  }
}
const PYTHON3 = process.env.PYTHON_BIN || process.env.PYTHON3 || '/usr/bin/python3';
process.env.PYTHON_BIN = PYTHON3;

const args = process.argv.slice(2);
const has = flag => args.includes(flag);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};

const DRY_RUN = has('--dry-run');
const FORCE = has('--force');
const LAUNCH = has('--launch');
const NOTIFY = has('--notify');
const LIVE_ALERTS = has('--live-alerts');
const DEEP = has('--deep');
const INTRADAY = has('--intraday') || DEEP;
const PINE = has('--pine') || (LAUNCH && !has('--no-pine'));
const DRAWINGS = has('--drawings');
const TECH = has('--tech') || (LAUNCH && !has('--no-tech'));
const maxSymbols = getArg('--max-symbols', null);

mkdirSync(join(ROOT, 'logs'), { recursive: true });
const LOCK_FILE = join(ROOT, 'logs', 'egx_tv_auto_update.lock');
const LOCK_TTL_MS = 4 * 60 * 60 * 1000;
let lockFd = null;

function pidAlive(pid) {
  if (!pid || !Number.isFinite(Number(pid))) return false;
  try {
    process.kill(Number(pid), 0);
    return true;
  } catch {
    return false;
  }
}

function acquireLock() {
  if (existsSync(LOCK_FILE)) {
    try {
      const raw = JSON.parse(readFileSync(LOCK_FILE, 'utf8'));
      const age = Date.now() - Date.parse(raw.started_at || 0);
      const alive = pidAlive(raw.pid);
      if (age >= LOCK_TTL_MS || !alive) {
        log(`removing stale lock (age=${(age / 3600000).toFixed(1)}h pid=${raw.pid} alive=${alive})`);
        unlinkSync(LOCK_FILE);
      } else {
        console.error(`[tv-auto] another egx_tv_auto_update run is already active (pid=${raw.pid}); exiting safely`);
        process.exit(0);
      }
    } catch {
      unlinkSync(LOCK_FILE);
    }
  }
  try {
    lockFd = openSync(LOCK_FILE, 'wx');
    writeFileSync(lockFd, JSON.stringify({
      pid: process.pid,
      started_at: new Date().toISOString(),
      args,
    }));
  } catch {
    console.error(`[tv-auto] another egx_tv_auto_update run is already active (${LOCK_FILE}); exiting safely`);
    process.exit(0);
  }
}

function releaseLock() {
  try { if (lockFd != null) closeSync(lockFd); } catch {}
  try { unlinkSync(LOCK_FILE); } catch {}
}

acquireLock();
process.on('exit', releaseLock);
process.on('SIGINT', () => { releaseLock(); process.exit(130); });
process.on('SIGTERM', () => { releaseLock(); process.exit(143); });

function log(message) {
  console.log(`[tv-auto] ${message}`);
}

function ensureStepAuditTable() {
  if (DRY_RUN) return;
  try {
    const db = getDB();
    db.exec(`
      CREATE TABLE IF NOT EXISTS pipeline_step_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL,
        step_order INTEGER,
        step_name TEXT NOT NULL,
        command TEXT,
        status TEXT NOT NULL,
        duration_sec REAL,
        error TEXT,
        started_at TEXT,
        finished_at TEXT DEFAULT (datetime('now'))
      );
      CREATE INDEX IF NOT EXISTS idx_pipeline_step_runs_run
        ON pipeline_step_runs(run_id, step_order);
    `);
  } catch (e) {
    log(`step audit table unavailable: ${e.message}`);
  }
}

const RUN_ID = new Date().toISOString();
let STEP_NO = 0;

function recordStep({ label, cmd, status, durationSec = 0, error = null, startedAt = null }) {
  if (DRY_RUN) return;
  try {
    const db = getDB();
    db.prepare(`
      INSERT INTO pipeline_step_runs
      (run_id, step_order, step_name, command, status, duration_sec, error, started_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(RUN_ID, STEP_NO, label, cmd, status, durationSec, error ? String(error).slice(0, 1200) : null, startedAt);
  } catch (e) {
    log(`step audit write failed: ${e.message}`);
  }
}

function run(cmd, label, { critical = false, timeoutMs = 1000 * 60 * 90 } = {}) {
  STEP_NO += 1;
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  log(`${DRY_RUN ? '[DRY] ' : ''}${label}`);
  if (DRY_RUN) return { skipped: true };
  try {
    execSync(cmd, {
      cwd: ROOT,
      env: process.env,
      stdio: 'inherit',
      timeout: timeoutMs,
    });
    recordStep({ label, cmd, status: 'OK', durationSec: (Date.now() - t0) / 1000, startedAt });
    return { success: true };
  } catch (err) {
    const msg = err?.stdout?.toString?.() || err?.stderr?.toString?.() || err?.message;
    log(`FAILED: ${label}: ${String(msg).slice(0, 800)}`);
    recordStep({ label, cmd, status: 'FAIL', durationSec: (Date.now() - t0) / 1000, error: msg, startedAt });
    if (critical) process.exit(1);
    return { success: false, error: msg };
  }
}

function latestDailyDate() {
  const db = getDB();
  const row = db.prepare('SELECT MAX(bar_time) AS latest FROM ohlcv_history').get();
  if (!row?.latest) return null;
  return new Date(Number(row.latest) * 1000).toISOString().split('T')[0];
}

function staleness(dataDate, refDate = new Date().toISOString().split('T')[0]) {
  if (!dataDate) return { staleness_trading_days: 999, data_date: null, ref_date: refDate };
  const raw = execSync(
    `${PYTHON3} scripts/python/event_calendar.py staleness '${JSON.stringify({ data_date: dataDate, ref_date: refDate })}'`,
    { cwd: ROOT, timeout: 10_000 }
  ).toString();
  return JSON.parse(raw);
}

async function ensureTradingView() {
  let health = await callMCPTool('tv_health_check', {});
  if (health?.success) {
    log(`TradingView connected: ${health.chart_symbol ?? 'unknown'} ${health.chart_resolution ?? ''}`);
    return true;
  }

  if (!LAUNCH) {
    log(`TradingView not connected: ${health?.error ?? 'unknown error'}`);
    log('Use --launch to start TradingView Desktop automatically.');
    return false;
  }

  log('Launching TradingView Desktop with CDP...');
  const launch = await callMCPTool('tv_launch', {
    port: 9222,
    kill_existing: false,
    url: 'https://www.tradingview.com/chart/?symbol=EGX_DLY:COMI',
  });
  if (!launch?.success) {
    log(`Launch failed: ${launch?.error ?? 'unknown error'}`);
    return false;
  }

  for (let i = 0; i < 20; i += 1) {
    await new Promise(r => setTimeout(r, 1500));
    health = await callMCPTool('tv_health_check', {});
    if (health?.success) {
      log(`TradingView connected after launch: ${health.chart_symbol ?? 'unknown'}`);
      return true;
    }
  }

  log('TradingView launched but CDP chart target is still unavailable.');
  return false;
}

async function main() {
  if (!FORCE && !DRY_RUN) {
    try {
      const cal = isTradingDay(cairoDateParts().date);
      if (!cal.is_trading_day) {
        log(`Skip TV sync: not EGX trading day (${cal.holiday_name || 'weekend'})`);
        process.exit(0);
      }
    } catch (e) {
      log(`Trading day check failed: ${e.message} — continuing`);
    }
  }

  ensureStepAuditTable();
  run('node scripts/migrations/migrate.mjs', 'Apply schema migrations');
  run(`${PYTHON3} scripts/python/event_calendar.py repair_2026 '{}'`, 'Repair EGX 2026 holiday calendar');

  const latest = latestDailyDate();
  const stale = staleness(latest);
  const signalDate = stale.last_trading_day || latest || stale.ref_date;
  log(`Latest OHLCV: ${latest ?? 'none'} | last trading day: ${stale.last_trading_day} | stale sessions: ${stale.staleness_trading_days}`);

  const needsDaily = FORCE || stale.staleness_trading_days > 0;
  const tvReady = needsDaily || PINE || DRAWINGS || LIVE_ALERTS || TECH
    ? await ensureTradingView()
    : false;

  run('node scripts/tv_mcp_audit.mjs', 'TradingView MCP capability audit', { critical: true });

  if (tvReady) {
    run('node scripts/tv_universe_sync.mjs', 'TV watchlist → stock_universe sync');
  }

  if (needsDaily) {
    if (tvReady) {
      const extra = maxSymbols ? ` --max-symbols ${maxSymbols}` : '';
      run(`node scripts/daily_update.mjs --force${extra}`, 'TradingView daily OHLCV sync', {
        critical: true,
        timeoutMs: 1000 * 60 * 240,
      });
    } else {
      log('Daily OHLCV sync skipped because TradingView is not connected.');
    }
  } else {
    log('Daily OHLCV is fresh by EGX trading calendar; no daily sync needed.');
  }

  if (DEEP && tvReady) {
    run('node scripts/fetch_egx_deep_history.mjs --weekly', 'Weekly OHLCV sync');
    run('node scripts/fetch_egx_deep_history.mjs --monthly', 'Monthly OHLCV sync');
  }

  if (INTRADAY && tvReady) {
    run('node scripts/fetch_egx_intraday.mjs --core-only --resume', 'Intraday 60/15min (core universe)', {
      critical: false,
      timeoutMs: 1000 * 60 * 90,
    });
  }

  if (tvReady) {
    const reconcileSymbols = maxSymbols ? '--symbols COMI,EFIH' : '--symbols COMI,EFIH,ORHD,SWDY,TMGH';
    run(`node scripts/tv_data_reconcile.mjs ${reconcileSymbols} --count 20 --repair`, 'TradingView OHLCV reconcile and repair gate');
    run('node scripts/fetch_cross_market.mjs --daily', 'TradingView cross-market daily sync');
  }
  run('node scripts/repair_cross_market_quality.mjs', 'Cross-market deterministic quality repair');

  if (!DRY_RUN && tvReady) {
    try {
      const dbLag = getDB();
      const ohlcvD = dbLag.prepare(
        "SELECT MAX(date(bar_time,'unixepoch')) d FROM ohlcv_history",
      ).get()?.d;
      const crossD = dbLag.prepare(
        "SELECT MAX(date(bar_time,'unixepoch')) d FROM cross_market_daily",
      ).get()?.d;
      if (ohlcvD && crossD && Date.parse(ohlcvD) > Date.parse(crossD)) {
        run('node scripts/fetch_cross_market.mjs --daily', 'Cross-market catch-up (lag behind OHLCV)');
      }
    } catch { /* non-blocking */ }
  }

  run('node scripts/exclusions_daily_report.mjs', 'Data quality exclusions daily report', { critical: false });

  STEP_NO += 1;
  const gateLabel = 'Layer-2 data quality gate (gate_daily — mandatory before ML)';
  const gateT0 = Date.now();
  log(`${DRY_RUN ? '[DRY] ' : ''}${gateLabel}`);
  if (!DRY_RUN) {
    try {
      const gate = enforceDailyQualityGate({}, { exitOnBlock: true });
      recordStep({
        label: gateLabel,
        cmd: 'gate_daily',
        status: 'OK',
        durationSec: (Date.now() - gateT0) / 1000,
        startedAt: new Date().toISOString(),
        detail: `${gate.latest_date} trust=${gate.trust_score} (${gate.trust_status})`,
      });
    } catch (err) {
      recordStep({
        label: gateLabel,
        cmd: 'gate_daily',
        status: 'FAIL',
        durationSec: (Date.now() - gateT0) / 1000,
        error: err.message,
        startedAt: new Date().toISOString(),
      });
      alertNotification('DATA_QUALITY_GATE_BLOCKED', {
        reason: err.gate?.reason || err.message,
        latest: err.gate?.latest_date,
        trust: err.gate?.trust_score,
      });
      process.exit(1);
    }
  }

  run('node scripts/rebuild_indicators.mjs', 'Rebuild local indicators', { critical: true });
  run(`${PYTHON3} scripts/python/duckdb_layer.py --force --quiet`, 'Parquet OHLCV/analytics export', { critical: false });
  if (!DRY_RUN) {
    const cacheGate = checkIndicatorCacheCoverage(signalDate);
    log(`indicator cache: ${cacheGate.symbols_on_date}/${cacheGate.min_required} on ${signalDate}`);
    if (!cacheGate.ok) {
      alertNotification('INDICATOR_CACHE_LOW', {
        date: signalDate,
        symbols: cacheGate.symbols_on_date,
        required: cacheGate.min_required,
      });
    }
  }
  if (tvReady) {
    run('node scripts/tv_pine_rotation.mjs', 'Pine analytics rotation (80 sym)');
  } else {
    run('node scripts/fetch_pine_analytics.mjs all --local-only --all-symbols', 'Pine analytics local backfill (pre-scan)');
  }
  run(`node scripts/scan_today.mjs --db-only --cache-only --top 60 --date ${signalDate}`, 'Fresh daily setup scan', { critical: true });
  const directorParams = JSON.stringify({ date: signalDate, skip_ues_score: true });
  run(
    `${PYTHON3} scripts/python/research_director.py morning_run '${directorParams}'`,
    'Research director (quant kill/evolve — feedback-aware, no duplicate quant)',
    { critical: false },
  );

  if (TECH) {
    run('node scripts/fetch_technical_indicators.mjs --max-symbols 30 --local-only', 'Fetch technical indicators', {
      timeoutMs: 1000 * 60 * 15,
      critical: false,
    });
    run('node scripts/merge_technical_indicators.mjs', 'Merge TV technical → indicators_cache (source=tv)');
  }

  run('node scripts/egx_market_breadth.mjs signal', 'Compute market breadth');
  run('node scripts/egx_hidden_regime.mjs detect', 'Detect market regime');
  run('node scripts/egx_regime_transition.mjs warning', 'Regime transition warning');
  run(`${PYTHON3} scripts/python/egx_ml_trainer.py phase21`, 'Spectral features before scoring');
  run(`node scripts/egx_explosion_ml.mjs predict --date ${signalDate} --top-n 20`, 'Explosion ML prediction refresh');
  run(`${PYTHON3} scripts/python/egx_ml_trainer.py predict_ensemble`, 'Ensemble ML prediction refresh (LGBM+XGB+RF+Meta)');
  run(`${PYTHON3} scripts/python/ml_purged_audit.py`, 'ML purged walk-forward governance');
  run(`${PYTHON3} scripts/python/macro_edge_validator.py`, 'Macro edge purged validation');
  run('node scripts/tv_macro_reconcile.mjs', 'TradingView macro and cross-market reconcile gate');
  run(`${PYTHON3} scripts/python/ml_advanced.py daily ${signalDate}`, 'ML-Advanced daily (meta/MoE/analogs/conformal/survival/leadlag/drift)');
  const discoveryCtx = buildDiscoveryParams({ signalDate });
  const scoreParams = JSON.stringify({ date: signalDate });
  const discoveryParamsJson = JSON.stringify(discoveryCtx.params);
  const promotionParams = JSON.stringify({ date: signalDate, ...discoveryCtx.params });
  run(`${PYTHON3} scripts/python/signal_integration.py score_all '${scoreParams}'`, 'Final signal scoring', { critical: true });
  run(`${PYTHON3} scripts/python/cognitive_arbitration.py arbitrate_all '{}'`, 'Cognitive arbitration (Phase 34)');
  run(`${PYTHON3} scripts/python/signal_integration.py apply_arbitration_veto '${scoreParams}'`, 'Apply arbitration veto to final_signals');
  const tvMicroMode = tvReady ? '--local-fallback' : '--local-only';
  run(
    `node scripts/tv_microstructure_engine.mjs ${tvMicroMode} --max-symbols 40`,
    'TV microstructure → tv_discovery_features',
    { critical: false, timeoutMs: 900_000 },
  );
  run(
    `${PYTHON3} scripts/python/counterfactual_atom_miner.py`,
    'Counterfactual atom seeds (learning loop → opp boosts)',
    { critical: false },
  );
  run(
    `${PYTHON3} scripts/python/opportunity_score_v2.py run '${discoveryParamsJson}'`,
    'Opportunity Score v2 (P6-tuned, post-score)',
    { critical: true },
  );
  run(`${PYTHON3} scripts/python/signal_integration.py track_outcomes`, 'Track recommendation outcomes (Ph 32)');
  run(`${PYTHON3} scripts/python/ml_advanced.py shadow_update ${signalDate}`, 'Gate shadow book — record vetoed signals');
  run(`${PYTHON3} scripts/python/egx_outcome_tracker.py`, 'Fill forward_test_predictions outcomes');
  run(`${PYTHON3} scripts/python/egx_ml_trainer.py phase46`, 'Bayesian WR update (Ph 46)');
  run(`${PYTHON3} scripts/python/alpha_ranker.py decay_check '{}'`, 'Alpha decay monitor (Ph 70)');
  run(`${PYTHON3} scripts/python/signal_integration.py signal_freshness '{}'`, 'Signal freshness gate');
  run(
    `${PYTHON3} scripts/python/client_signal_promotion.py '${promotionParams}'`,
    'Client signal promotion (P6 feedback + opp followup tuned)',
    { critical: true },
  );
  if (tvReady) {
    run(`${PYTHON3} scripts/python/replay_gate.py '${scoreParams}'`, 'Replay gate (ULTRA validation)');
    run('node scripts/fetch_actionable_dom.mjs', 'DOM snapshots for actionable signals');
    run('node scripts/tv_fundamentals_sync.mjs --max 25', 'TV symbol_info fundamentals sync');
  }
  run(`${PYTHON3} scripts/python/egx_x_pro_engine.py run`, 'EGX-X Pro liquidity/RS discovery engine');
  run(`${PYTHON3} scripts/python/egx_x_pro_engine.py track`, 'Update signal outcome tracker');

  if (PINE && tvReady) {
    run('node scripts/load_mcp_exporter_indicator.mjs', 'Upload EGX MCP Exporter Pine');
    run('node scripts/load_spectral_indicator.mjs', 'Upload spectral Pine overlay');
    run('node scripts/fetch_pine_analytics.mjs all --max-symbols 80 --local-fallback', 'Fetch Pine analytics');
  }
  run(`${PYTHON3} scripts/python/egx_ml_trainer.py phase11`, 'Fuse Pine analytics into ML feature store');

  if (tvReady) {
    run('node scripts/fetch_technical_indicators.mjs --max-symbols 30', 'Daily TradingView technical confirmation', {
      timeoutMs: 1000 * 60 * 20,
      critical: false,
    });
    run('node scripts/fetch_intraday_live.mjs --quotes --dom --once', 'Daily TradingView live quote/DOM snapshot');
  }
  run(`${PYTHON3} scripts/python/ml_feature_bridge.py run`, 'Bridge opportunity/X-Pro/DOM features into ML');

  if (DRAWINGS) {
    run(`node scripts/fetch_chart_drawings.mjs --date ${signalDate} --n 8`, 'Draw signal levels and screenshots');
  }

  run(`node scripts/tv_proof_pack.mjs --date ${signalDate} --limit 8`, 'Client proof-pack gate');

  if (LIVE_ALERTS) {
    run(`node scripts/fetch_alerts.mjs --date ${signalDate} --live`, 'Create live TradingView alerts');
  } else {
    run(`node scripts/fetch_alerts.mjs --date ${signalDate} --max-picks 5`, 'Preview alert targets');
  }

  run('node scripts/egx_validate.mjs --quick', 'Validation gate');
  try {
    const proof = writeProofLoopSnapshot();
    log(`Proof loop: ${proof.n_completed}/${proof.samples_needed + proof.n_completed} ULTRA | WR5=${proof.win_rate ?? '—'}%`);
  } catch (e) {
    log(`Proof loop snapshot skipped: ${e.message}`);
  }

  if (NOTIFY) {
    run('node scripts/egx_telegram_daily.mjs', 'Send Telegram daily report');
  } else {
    run('node scripts/egx_telegram_daily.mjs --dry-run', 'Render Telegram report dry-run');
  }

  log('Done.');
}

main().catch(err => {
  console.error(`[tv-auto] fatal: ${err.message}\n${err.stack}`);
  alertNotification('TV_SYNC_FAILED', {
    error: err.message?.slice(0, 500),
    date: cairoDateParts().date,
  });
  process.exit(1);
});
