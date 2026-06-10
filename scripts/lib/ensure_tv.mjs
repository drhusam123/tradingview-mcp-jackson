/**
 * ensure_tv.mjs — Auto-launch TradingView with CDP
 * =========================================================
 * macOS: newer TradingView Desktop rejects --remote-debugging-port on the binary.
 * Use open -a first; if CDP still fails, fall back to Chrome (TV_CDP_BROWSER=chrome).
 *
 * Usage:
 *   import { ensureTradingView } from './lib/ensure_tv.mjs';
 *   await ensureTradingView({ log: myLogFn });
 */

import { execSync, spawnSync } from 'child_process';
import { existsSync } from 'fs';

const TV_BINARY  = '/Applications/TradingView.app/Contents/MacOS/TradingView';
const TV_APP     = '/Applications/TradingView.app';
const DEFAULT_CDP_PORT = Number(process.env.TV_CDP_PORT || 9222);
const WAIT_MS    = 45_000;
const POLL_MS    = 3_000;
const SETTLE_MS  = 7_000;
const TV_URL     = process.env.TV_CDP_URL || 'https://www.tradingview.com/chart/?symbol=EGX_DLY:COMI';

const cdpUrl = (port = DEFAULT_CDP_PORT) => `http://localhost:${port}/json`;

const isCdpAlive = (port = DEFAULT_CDP_PORT) => {
  try {
    execSync(`curl -sf --max-time 2 "${cdpUrl(port)}"`, { stdio: 'ignore' });
    return true;
  } catch { return false; }
};

const tvProcessRunning = () => {
  try {
    const r = execSync('pgrep -f "TradingView.app/Contents/MacOS/TradingView$"', { encoding: 'utf8' });
    return r.trim().length > 0;
  } catch { return false; }
};

async function launchChromeCdp({ log, cdpPort, dryRun }) {
  log(`🌐  Chrome CDP fallback (port ${cdpPort})...`);
  if (dryRun) {
    log(`   (dry-run) TV_CDP_BROWSER=chrome → ${TV_URL}`);
    return false;
  }
  const { launch } = await import('../../src/core/health.js');
  const result = await launch({
    port: cdpPort,
    kill_existing: false,
    browser: 'chrome',
    url: TV_URL,
  });
  if (result.cdp_ready === false && !isCdpAlive(cdpPort)) {
    log(`❌  Chrome CDP لم يستجب — تحقق من Google Chrome`);
    return false;
  }
  log(`✅  Chrome CDP جاهز — ${result.target_url || TV_URL}`);
  await new Promise(r => setTimeout(r, SETTLE_MS));
  return true;
}

async function launchDesktopCdp({ log, cdpPort, dryRun, restart }) {
  if (restart || (tvProcessRunning() && !isCdpAlive(cdpPort))) {
    log(`⚠️  إعادة تشغيل TradingView Desktop مع CDP (open -a --args)...`);
    if (!dryRun) {
      try { execSync('pkill -f "TradingView.app/Contents/MacOS/TradingView$"', { stdio: 'ignore' }); } catch {}
      await new Promise(r => setTimeout(r, 2000));
    }
  } else if (!tvProcessRunning()) {
    log('⚠️  TradingView مغلق — يفتح مع CDP (open -a --args)...');
  }

  if (!existsSync(TV_APP)) {
    log(`❌  TradingView غير موجود: ${TV_APP}`);
    return false;
  }

  if (!dryRun) {
    spawnSync('open', ['-a', TV_APP, '--args', `--remote-debugging-port=${cdpPort}`], { stdio: 'ignore' });
  } else {
    log(`   (dry-run) open -a "${TV_APP}" --args --remote-debugging-port=${cdpPort}`);
    return false;
  }

  const deadline = Date.now() + WAIT_MS;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, POLL_MS));
    if (isCdpAlive(cdpPort)) {
      log(`✅  TradingView Desktop + CDP جاهز على port ${cdpPort}`);
      await new Promise(r => setTimeout(r, SETTLE_MS));
      return true;
    }
    const remaining = Math.round((deadline - Date.now()) / 1000);
    log(`   ⏳ ننتظر TradingView CDP... (${remaining}s متبقية)`);
  }

  log(`⚠️  Desktop CDP لم يستجب خلال ${WAIT_MS / 1000}s`);
  log(`   ملاحظة: إصدارات TV الحديثة ترفض --remote-debugging-port على الـ binary مباشرة`);
  return false;
}

/**
 * @param {object} opts
 * @param {Function} opts.log
 * @param {boolean}  opts.dryRun
 * @param {boolean}  opts.restart
 * @param {number}   opts.port
 * @param {string}   opts.browser  desktop | chrome | auto
 * @returns {boolean}
 */
export async function ensureTradingView({
  log = console.log,
  dryRun = false,
  restart = false,
  port = DEFAULT_CDP_PORT,
  browser,
} = {}) {
  const cdpPort = Number(port) || DEFAULT_CDP_PORT;
  const browserPref = String(browser || process.env.TV_CDP_BROWSER || 'auto').toLowerCase();

  if (!restart && isCdpAlive(cdpPort)) {
    log(`✅  CDP متصل (port ${cdpPort})`);
    return true;
  }

  if (browserPref === 'chrome' || browserPref === 'web') {
    return launchChromeCdp({ log, cdpPort, dryRun });
  }

  const desktopOk = await launchDesktopCdp({ log, cdpPort, dryRun, restart });
  if (desktopOk) return true;

  if (browserPref === 'desktop') {
    log(`❌  Desktop CDP فشل — جرّب TV_CDP_BROWSER=chrome في .env`);
    return false;
  }

  // auto: fall back to Chrome
  return launchChromeCdp({ log, cdpPort, dryRun });
}
