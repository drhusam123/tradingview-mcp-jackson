#!/usr/bin/env node
/**
 * EGX dependency & environment check — Node, Python, TV CDP, DB, cron.
 * Usage: npm run egx:deps:check
 */
import { execSync, spawnSync } from 'child_process';
import { existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PY = process.env.PYTHON3 || process.env.PYTHON_BIN || 'python3';
const CDP_PORT = Number(process.env.TV_CDP_PORT || 9222);

const REQUIRED_PY = [
  'numpy', 'pandas', 'lightgbm', 'sklearn', 'scipy', 'optuna',
  'shap', 'mlflow', 'yfinance', 'statsmodels', 'tsfresh', 'joblib', 'xgboost',
  'lifelines',
];
const OPTIONAL_PY = ['tensorflow', 'catboost', 'pyod', 'networkx', 'tigramite'];

let fails = 0;
let warns = 0;

function ok(msg) { console.log(`  ✅  ${msg}`); }
function warn(msg) { console.log(`  ⚠️  ${msg}`); warns += 1; }
function fail(msg) { console.log(`  ❌  ${msg}`); fails += 1; }

function cdpAlive(port = CDP_PORT) {
  try {
    execSync(`curl -sf --max-time 2 "http://localhost:${port}/json/version"`, { stdio: 'ignore' });
    return true;
  } catch { return false; }
}

function cronEgxCount() {
  try {
    const out = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' });
    return (out.match(/# EGX/g) || []).length;
  } catch { return 0; }
}

console.log('═══ EGX Dependencies Check ═══\n');

// Node
console.log('▶  Runtime');
try {
  const nv = execSync('node -v', { encoding: 'utf8' }).trim();
  const major = Number(nv.replace('v', '').split('.')[0]);
  if (major >= 18) ok(`Node ${nv}`);
  else fail(`Node ${nv} — يحتاج 18+`);
} catch { fail('Node غير مثبت'); }

try {
  const pv = execSync(`${PY} --version`, { encoding: 'utf8' }).trim();
  ok(pv);
} catch { fail('Python3 غير مثبت'); }

// npm packages
console.log('\n▶  Node packages');
if (existsSync(join(ROOT, 'node_modules'))) {
  ok('node_modules موجود');
  for (const pkg of ['@modelcontextprotocol/sdk', 'chrome-remote-interface', 'better-sqlite3']) {
    if (existsSync(join(ROOT, 'node_modules', pkg))) ok(pkg);
    else fail(`${pkg} غير مثبت — شغّل npm install`);
  }
} else {
  fail('node_modules مفقود — شغّل npm install');
}

// Python packages
console.log('\n▶  Python packages');
const pyCheck = spawnSync(PY, ['-c', `
import json, sys
req = ${JSON.stringify(REQUIRED_PY)}
opt = ${JSON.stringify(OPTIONAL_PY)}
missing, optional_missing = [], []
for p in req:
    try: __import__(p)
    except ImportError: missing.append(p)
for p in opt:
    try: __import__(p)
    except ImportError: optional_missing.append(p)
print(json.dumps({"missing": missing, "optional_missing": optional_missing}))
`], { encoding: 'utf8', cwd: ROOT });

if (pyCheck.status === 0) {
  const { missing, optional_missing } = JSON.parse(pyCheck.stdout.trim());
  for (const p of REQUIRED_PY.filter(x => !missing.includes(x))) ok(p);
  for (const p of missing) fail(`${p} — pip3 install ${p}`);
  for (const p of optional_missing) warn(`${p} اختياري — غير مثبت`);
} else {
  fail(`فحص Python فشل: ${pyCheck.stderr?.slice(0, 200)}`);
}

// ML models
console.log('\n▶  ML models');
const lgbm = join(ROOT, 'scripts/python/models/ml_trainer/explosion_lgbm_v3.txt');
if (existsSync(lgbm)) ok('explosion_lgbm_v3.txt');
else warn('explosion_lgbm_v3.txt مفقود — شغّل egx:train:full');

const mladvDir = join(ROOT, 'scripts/python/models/ml_advanced');
for (const f of ['meta_labeler_v1.txt', 'survival_cox.pkl', 'pattern_analogs.npz']) {
  if (existsSync(join(mladvDir, f))) ok(`ml_advanced/${f}`);
  else warn(`ml_advanced/${f} مفقود — شغّل npm run egx:mladv:weekly`);
}

try {
  const mladv = spawnSync(PY, ['scripts/python/ml_advanced.py', 'status'], {
    cwd: ROOT, encoding: 'utf8', timeout: 30_000,
  });
  if (mladv.status === 0) {
    const st = JSON.parse(mladv.stdout.trim().split('\n').pop());
    const ev = st.events?.n ?? 0;
    const purged = st.purged_auc_triple_barrier;
    if (ev >= 500) ok(`ml_adv_events: ${ev} (latest ${st.events?.latest})`);
    else warn(`ml_adv_events: ${ev} — شغّل egx:mladv:weekly`);
    if (purged != null) ok(`purged AUC (honest): ${purged}`);
    if (st.mladv_drift_throttle >= 1) warn('drift throttle ON — ML floor +4');
  }
} catch { warn('ml_advanced status غير متاح'); }

// Database
console.log('\n▶  Database');
const db = join(ROOT, 'data/egx_trading.db');
if (existsSync(db)) {
  ok('data/egx_trading.db');
  try {
    const ohlcv = execSync(`sqlite3 "${db}" "SELECT COUNT(DISTINCT symbol), MAX(date) FROM ohlcv"`, { encoding: 'utf8' }).trim();
    const [sym, latest] = ohlcv.split('|');
    ok(`OHLCV: ${sym} سهم، آخر ${latest}`);
  } catch { warn('تعذر قراءة ohlcv'); }
} else {
  fail('data/egx_trading.db مفقود');
}

// TradingView / CDP
console.log('\n▶  TradingView MCP (CDP)');
const tvApp = '/Applications/TradingView.app/Contents/MacOS/TradingView';
if (existsSync(tvApp)) ok('TradingView Desktop مثبت');
else warn('TradingView Desktop غير موجود في /Applications');

if (cdpAlive()) {
  ok(`CDP port ${CDP_PORT} متصل`);
  try {
    const health = spawnSync('node', ['-e', `
      import { healthCheck } from './src/core/health.js';
      const h = await healthCheck();
      console.log(JSON.stringify(h));
    `], { cwd: ROOT, encoding: 'utf8', timeout: 30_000 });
    if (health.status === 0) {
      const h = JSON.parse(health.stdout.trim());
      ok(`Chart: ${h.chart_symbol} @ ${h.chart_resolution} (api=${h.api_available})`);
    }
  } catch { /* optional */ }
} else {
  warn(`CDP port ${CDP_PORT} غير متصل`);
  warn('على macOS الحديث: TV Desktop يرفض --remote-debugging-port مباشرة');
  warn('الحل: أضف TV_CDP_BROWSER=chrome في .env ثم npm run tv:launch');
}

// Cron
console.log('\n▶  Automation');
const cronN = cronEgxCount();
if (cronN >= 40) ok(`Cron: ${cronN} مهمة EGX`);
else if (cronN > 0) warn(`Cron: ${cronN} مهمة فقط — شغّل npm run egx:cron:install`);
else warn('Cron غير مثبت — شغّل npm run egx:cron:install');

// Training process
console.log('\n▶  Background jobs');
try {
  const procs = execSync('pgrep -fl "egx_full_train|egx_ml_trainer" 2>/dev/null || true', { encoding: 'utf8' }).trim();
  if (procs) ok(`تدريب ML جاري:\n       ${procs.split('\n').join('\n       ')}`);
  else ok('لا يوجد تدريب ML جاري');
} catch { ok('لا يوجد تدريب ML جاري'); }

console.log(`\n═══ النتيجة: ${fails === 0 ? 'PASS' : `${fails} FAIL`}${warns ? ` / ${warns} WARN` : ''} ═══`);
process.exit(fails > 0 ? 1 : 0);
