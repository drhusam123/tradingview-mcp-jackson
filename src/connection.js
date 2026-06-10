import CDP from 'chrome-remote-interface';

let client = null;
let browserClient = null;
let sessionId = null;
let targetInfo = null;
const CDP_HOST = 'localhost';
const CDP_PORT = 9222;
const MAX_RETRIES = Number(process.env.TV_MCP_MAX_RETRIES || 2);
const BASE_DELAY = 500;
const STEP_TIMEOUT_MS = Number(process.env.TV_MCP_STEP_TIMEOUT_MS || 4000);
const CONNECT_BUDGET_MS = Number(process.env.TV_MCP_CONNECT_BUDGET_MS || 15000);
const DEBUG = process.env.TV_MCP_DEBUG === '1';
const BROWSER_PREFERENCE = (process.env.TV_CDP_BROWSER || 'auto').toLowerCase();

// Known direct API paths discovered via live probing (see PROBE_RESULTS.md)
const KNOWN_PATHS = {
  chartApi: 'window.TradingViewApi._activeChartWidgetWV.value()',
  chartWidgetCollection: 'window.TradingViewApi._chartWidgetCollection',
  bottomWidgetBar: 'window.TradingView.bottomWidgetBar',
  replayApi: 'window.TradingViewApi._replayApi',
  alertService: 'window.TradingViewApi._alertService',
  chartApiInstance: 'window.ChartApiInstance',
  mainSeriesBars: 'window.TradingViewApi._activeChartWidgetWV.value()._chartWidget.model().mainSeries().bars()',
  // Phase 1: Strategy data — model().dataSources() → find strategy → .performance().value(), .ordersData(), .reportData()
  strategyStudy: 'chart._chartWidget.model().model().dataSources()',
  // Phase 2: Layouts — getSavedCharts(cb), loadChartFromServer(id)
  layoutManager: 'window.TradingViewApi.getSavedCharts',
  // Phase 5: Symbol search — searchSymbols(query) returns Promise
  symbolSearchApi: 'window.TradingViewApi.searchSymbols',
  // Phase 6: Pine scripts — REST API at pine-facade.tradingview.com/pine-facade/list/?filter=saved
  pineFacadeApi: 'https://pine-facade.tradingview.com/pine-facade',
};

export { KNOWN_PATHS };

function logDebug(...args) {
  if (DEBUG) console.error('[TV_MCP_DEBUG]', ...args);
}

function withTimeout(promise, label, timeoutMs = STEP_TIMEOUT_MS) {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`${label} timeout after ${timeoutMs}ms`)), timeoutMs),
    ),
  ]);
}

function wrapSessionClient(rootClient, sid) {
  const send = (method, params = {}, timeoutMs = STEP_TIMEOUT_MS) =>
    withTimeout(rootClient.send(method, params, sid), method, timeoutMs);

  return {
    send,
    Runtime: {
      enable: () => send('Runtime.enable'),
      evaluate: (params) => send('Runtime.evaluate', params, Number(process.env.TV_MCP_EVAL_TIMEOUT_MS || 8000)),
    },
    Page: {
      enable: () => send('Page.enable'),
      captureScreenshot: (params) => send('Page.captureScreenshot', params),
    },
    DOM: {
      enable: () => send('DOM.enable'),
    },
    Input: {
      dispatchKeyEvent: (params) => send('Input.dispatchKeyEvent', params),
      insertText: (params) => send('Input.insertText', params),
      dispatchMouseEvent: (params) => send('Input.dispatchMouseEvent', params),
    },
  };
}

export async function getClient() {
  if (client) {
    try {
      // Quick liveness check
      await client.Runtime.evaluate({ expression: '1', returnByValue: true });
      return client;
    } catch {
      client = null;
      sessionId = null;
      targetInfo = null;
    }
  }
  return connect();
}

export async function connect() {
  const connectStart = Date.now();
  let lastError;
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    if (Date.now() - connectStart > CONNECT_BUDGET_MS) {
      throw new Error(`CDP connect budget exceeded (${CONNECT_BUDGET_MS}ms): ${lastError?.message || 'timeout'}`);
    }
    try {
      const candidates = await findChartTargets();
      if (!candidates.length) {
        throw new Error('No TradingView page target found. Is TradingView open?');
      }

      // Browser-level attach first; then per-target session attach (flatten:true).
      browserClient = await withTimeout(
        CDP({ host: CDP_HOST, port: CDP_PORT }),
        'Browser.attach',
      );

      logDebug(`Trying ${candidates.length} targets`);
      for (const target of candidates) {
        let sid = null;
        try {
          logDebug('Attach target', target.id, target.url);
          const attach = await withTimeout(
            browserClient.Target.attachToTarget({ targetId: target.id, flatten: true }),
            'Target.attachToTarget',
          );
          sid = attach.sessionId;
          const scopedClient = wrapSessionClient(browserClient, sid);

          // Enable required domains with per-step timeouts.
          await scopedClient.Runtime.enable();
          await scopedClient.Page.enable();
          await scopedClient.DOM.enable();
          await scopedClient.Runtime.evaluate({ expression: '1', returnByValue: true });

          sessionId = sid;
          targetInfo = target;
          client = scopedClient;
          logDebug('Connected target', target.id);
          return client;
        } catch (targetErr) {
          logDebug('Target failed', target.id, targetErr.message);
          try {
            if (browserClient && browserClient.Target && sid) {
              await browserClient.Target.detachFromTarget({ sessionId: sid });
            }
          } catch {
            // ignore detach errors
          }
        }
      }

      throw new Error('All candidate targets failed Runtime/Page/DOM attach');
    } catch (err) {
      lastError = err;
      logDebug('connect attempt failed', attempt + 1, err.message);
      const delay = Math.min(BASE_DELAY * Math.pow(2, attempt), 30000);
      await new Promise(r => setTimeout(r, delay));
    }
  }
  throw new Error(`CDP connection failed after ${MAX_RETRIES} attempts: ${lastError?.message}`);
}

async function findChartTargets() {
  const resp = await fetch(`http://${CDP_HOST}:${CDP_PORT}/json/list`);
  const targets = await resp.json();
  const pages = targets.filter(t => t.type === 'page');
  const webChartPages = pages.filter(t => /https?:\/\/(www\.)?tradingview\.com\/chart/i.test(t.url || ''));
  const webTvPages = pages.filter(t => /https?:\/\/(www\.)?tradingview\.com/i.test(t.url || ''));
  const desktopChartPages = pages.filter(t => /tradingview\.com\/chart/i.test(t.url || ''));
  const tvPages = pages.filter(t => /tradingview/i.test(t.url || t.title || ''));
  const others = pages.filter(
    t =>
      !webChartPages.some(c => c.id === t.id) &&
      !webTvPages.some(v => v.id === t.id) &&
      !desktopChartPages.some(c => c.id === t.id) &&
      !tvPages.some(v => v.id === t.id),
  );

  let ordered = [];
  if (BROWSER_PREFERENCE === 'chrome' || BROWSER_PREFERENCE === 'web') {
    // Strongly prefer TradingView web pages first.
    ordered = [...webChartPages, ...webTvPages, ...desktopChartPages, ...tvPages, ...others];
  } else if (BROWSER_PREFERENCE === 'desktop') {
    // Prefer desktop-style targets first.
    ordered = [...desktopChartPages, ...tvPages, ...webChartPages, ...webTvPages, ...others];
  } else {
    // Auto mode: prefer web chart > desktop/chart > tv pages > fallback.
    ordered = [...webChartPages, ...desktopChartPages, ...webTvPages, ...tvPages, ...others];
  }

  return ordered.map((t) => ({
    id: t.id || t.targetId,
    url: t.url,
    title: t.title,
    type: t.type,
  }));
}

export async function getTargetInfo() {
  if (!targetInfo) {
    await getClient();
  }
  return targetInfo;
}

export async function evaluate(expression, opts = {}) {
  const c = await getClient();
  const result = await c.Runtime.evaluate({
    expression,
    returnByValue: true,
    awaitPromise: opts.awaitPromise ?? false,
    ...opts,
  });
  if (result.exceptionDetails) {
    const msg = result.exceptionDetails.exception?.description
      || result.exceptionDetails.text
      || 'Unknown evaluation error';
    throw new Error(`JS evaluation error: ${msg}`);
  }
  return result.result?.value;
}

export async function evaluateAsync(expression) {
  return evaluate(expression, { awaitPromise: true });
}

export async function disconnect() {
  try {
    if (browserClient && sessionId) {
      await browserClient.Target.detachFromTarget({ sessionId });
    }
  } catch {}
  try {
    if (browserClient) await browserClient.close();
  } catch {}
  client = null;
  browserClient = null;
  sessionId = null;
  targetInfo = null;
}

// --- Direct API path helpers ---
// Each returns the STRING expression path after verifying it exists.
// Callers use the returned string in their own evaluate() calls.

async function verifyAndReturn(path, name) {
  const exists = await evaluate(`typeof (${path}) !== 'undefined' && (${path}) !== null`);
  if (!exists) {
    throw new Error(`${name} not available at ${path}`);
  }
  return path;
}

export async function getChartApi() {
  return verifyAndReturn(KNOWN_PATHS.chartApi, 'Chart API');
}

export async function getChartCollection() {
  return verifyAndReturn(KNOWN_PATHS.chartWidgetCollection, 'Chart Widget Collection');
}

export async function getBottomBar() {
  return verifyAndReturn(KNOWN_PATHS.bottomWidgetBar, 'Bottom Widget Bar');
}

export async function getReplayApi() {
  return verifyAndReturn(KNOWN_PATHS.replayApi, 'Replay API');
}

export async function getMainSeriesBars() {
  return verifyAndReturn(KNOWN_PATHS.mainSeriesBars, 'Main Series Bars');
}
