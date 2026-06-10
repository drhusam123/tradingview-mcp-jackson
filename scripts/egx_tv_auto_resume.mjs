#!/usr/bin/env node
/**
 * Resume egx_tv_auto_update from the ML/scoring tail (after scan + quant).
 * Usage: node scripts/egx_tv_auto_resume.mjs [--date 2026-06-10] [--notify]
 */
import { execSync } from 'child_process';
import { existsSync, readFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { getDB } from '../src/egx/index.js';
import { tradingDayStaleness } from './lib/egx_calendar.mjs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
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
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const NOTIFY = args.includes('--notify');
const PINE = !args.includes('--no-pine');
const TECH = !args.includes('--no-tech');
const SKIP_TECH = args.includes('--skip-tech');

function latestDailyDate() {
  const db = getDB();
  const row = db.prepare(`
    SELECT MAX(date(bar_time, 'unixepoch')) AS d FROM ohlcv_history_execution
  `).get();
  return row?.d ?? null;
}

const signalDate = getArg('--date', null)
  || tradingDayStaleness(latestDailyDate()).last_trading_day
  || latestDailyDate()
  || new Date().toISOString().slice(0, 10);

function log(msg) {
  console.log(`[tv-resume] ${msg}`);
}

function run(cmd, label, { critical = false, timeoutMs = 1000 * 60 * 90 } = {}) {
  const t0 = Date.now();
  log(label);
  try {
    execSync(cmd, { cwd: ROOT, env: process.env, stdio: 'inherit', timeout: timeoutMs });
    log(`OK ${label} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
    return true;
  } catch (err) {
    const msg = err?.stdout?.toString?.() || err?.stderr?.toString?.() || err?.message;
    log(`FAIL ${label}: ${String(msg).slice(0, 600)}`);
    if (critical) process.exit(1);
    return false;
  }
}

log(`Resuming pipeline tail for signalDate=${signalDate}`);

if (TECH && !SKIP_TECH) {
  run('node scripts/fetch_technical_indicators.mjs --max-symbols 30 --local-only', 'Fetch technical indicators (local)', {
    timeoutMs: 1000 * 60 * 15,
    critical: false,
  });
}

run('node scripts/egx_market_breadth.mjs signal', 'Compute market breadth', { critical: true });
run('node scripts/egx_hidden_regime.mjs detect', 'Detect market regime');
run('node scripts/egx_regime_transition.mjs warning', 'Regime transition warning');
run(`${PYTHON3} scripts/python/egx_ml_trainer.py phase21`, 'Spectral features before scoring');
run(`node scripts/egx_explosion_ml.mjs predict --date ${signalDate} --top-n 20`, 'Explosion ML prediction refresh', { critical: true });
run(`${PYTHON3} scripts/python/egx_ml_trainer.py predict_ensemble`, 'Ensemble ML prediction refresh', { critical: true });
run(`${PYTHON3} scripts/python/ml_purged_audit.py`, 'ML purged walk-forward governance');
run(`${PYTHON3} scripts/python/macro_edge_validator.py`, 'Macro edge purged validation');
run('node scripts/tv_macro_reconcile.mjs', 'TradingView macro reconcile gate', { critical: false, timeoutMs: 1000 * 60 * 20 });
run(`${PYTHON3} scripts/python/ml_advanced.py daily ${signalDate}`, 'ML-Advanced daily', { critical: true });

const scoreParams = JSON.stringify({ date: signalDate });
run(`${PYTHON3} scripts/python/signal_integration.py score_all '${scoreParams}'`, 'Final signal scoring', { critical: true });
run(`${PYTHON3} scripts/python/cognitive_arbitration.py arbitrate_all '{}'`, 'Cognitive arbitration');
run(`${PYTHON3} scripts/python/signal_integration.py apply_arbitration_veto '${scoreParams}'`, 'Apply arbitration veto');
run(`${PYTHON3} scripts/python/signal_integration.py track_outcomes`, 'Track outcomes');
run(`${PYTHON3} scripts/python/ml_advanced.py shadow_update ${signalDate}`, 'Gate shadow book');
run(`${PYTHON3} scripts/python/egx_outcome_tracker.py`, 'Fill forward_test outcomes');
run(`${PYTHON3} scripts/python/egx_ml_trainer.py phase46`, 'Bayesian WR update');
run(`${PYTHON3} scripts/python/alpha_ranker.py decay_check '{}'`, 'Alpha decay monitor');
run(`${PYTHON3} scripts/python/signal_integration.py signal_freshness '{}'`, 'Signal freshness gate');
run(`${PYTHON3} scripts/python/opportunity_score_v2.py run`, 'Opportunity Score v2');
run(`${PYTHON3} scripts/python/client_signal_promotion.py '${scoreParams}'`, 'Client signal promotion');
run(`${PYTHON3} scripts/python/replay_gate.py '${scoreParams}'`, 'Replay gate', { critical: false });
run('node scripts/fetch_actionable_dom.mjs', 'DOM snapshots', { critical: false, timeoutMs: 1000 * 60 * 15 });
run('node scripts/tv_fundamentals_sync.mjs --max 25', 'TV fundamentals sync', { critical: false, timeoutMs: 1000 * 60 * 20 });
run(`${PYTHON3} scripts/python/egx_x_pro_engine.py run`, 'EGX-X Pro run');
run(`${PYTHON3} scripts/python/egx_x_pro_engine.py track`, 'EGX-X Pro track');

if (PINE) {
  run('node scripts/load_mcp_exporter_indicator.mjs', 'Upload MCP Exporter Pine', { critical: false, timeoutMs: 1000 * 60 * 15 });
  run('node scripts/load_spectral_indicator.mjs', 'Upload spectral Pine', { critical: false, timeoutMs: 1000 * 60 * 15 });
  run('node scripts/fetch_pine_analytics.mjs all --max-symbols 80 --local-fallback', 'Fetch Pine analytics', { critical: false, timeoutMs: 1000 * 60 * 30 });
}
run(`${PYTHON3} scripts/python/egx_ml_trainer.py phase11`, 'Fuse Pine into ML');

if (TECH) {
  run('node scripts/fetch_technical_indicators.mjs --max-symbols 30', 'TV technical confirmation', {
    timeoutMs: 1000 * 60 * 20,
    critical: false,
  });
  run('node scripts/fetch_intraday_live.mjs --quotes --dom --once', 'Live quote/DOM snapshot', {
    timeoutMs: 1000 * 60 * 15,
    critical: false,
  });
}
run(`${PYTHON3} scripts/python/ml_feature_bridge.py run`, 'ML feature bridge');
run(`node scripts/tv_proof_pack.mjs --date ${signalDate} --limit 8`, 'Client proof-pack gate', { critical: false });
run(`node scripts/fetch_alerts.mjs --date ${signalDate} --max-picks 5`, 'Preview alert targets', { critical: false });
run('node scripts/egx_validate.mjs --quick', 'Validation gate', { critical: true });

if (NOTIFY) {
  run('node scripts/egx_telegram_daily.mjs', 'Send Telegram daily report');
} else {
  run('node scripts/egx_telegram_daily.mjs --dry-run', 'Telegram dry-run');
}

log('Done.');
