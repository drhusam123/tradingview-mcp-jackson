import { evaluate } from './connection.js';

const DEFAULT_TIMEOUT = 10000;
const POLL_INTERVAL = 200;

export async function waitForChartReady(expectedSymbol = null, expectedTf = null, timeout = DEFAULT_TIMEOUT) {
  const start = Date.now();
  let stableCount = 0;
  let lastStateKey = null;

  // Strip exchange prefix so "EGX:COMI" → "COMI" for legend comparison
  const expectedTicker = expectedSymbol
    ? (expectedSymbol.includes(':') ? expectedSymbol.split(':')[1] : expectedSymbol).toUpperCase()
    : null;

  while (Date.now() - start < timeout) {
    const state = await evaluate(`
      (function() {
        var api = window.TradingViewApi && window.TradingViewApi._activeChartWidgetWV
          ? window.TradingViewApi._activeChartWidgetWV.value()
          : null;
        if (!api) return null;

        var currentSymbol = '';
        var resolution = '';
        var lastIndex = null;
        var lastTime = null;
        var barCount = 0;
        try { currentSymbol = api.symbol ? api.symbol() : ''; } catch(e) {}
        try { resolution = api.resolution ? api.resolution() : ''; } catch(e) {}
        try {
          var bars = api._chartWidget.model().mainSeries().bars();
          lastIndex = bars.lastIndex();
          barCount = bars.size ? bars.size() : 0;
          var v = bars.valueAt(lastIndex);
          lastTime = v ? v[0] : null;
        } catch(e) {}

        var spinner = document.querySelector('[class*="loader"]')
          || document.querySelector('[class*="loading"]')
          || document.querySelector('[data-name="loading"]');
        var isLoading = spinner && spinner.offsetParent !== null;

        return {
          isLoading: !!isLoading,
          currentSymbol: currentSymbol,
          resolution: resolution,
          lastIndex: lastIndex,
          lastTime: lastTime,
          barCount: barCount
        };
      })()
    `);

    if (!state || !state.currentSymbol || !state.barCount || state.lastTime == null) {
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
      continue;
    }

    if (state.isLoading) {
      stableCount = 0;
      lastStateKey = null;
      await new Promise(r => setTimeout(r, POLL_INTERVAL));
      continue;
    }

    if (expectedTicker) {
      // Legend shows just the ticker (e.g. "COMI"), possibly with exchange ("EGX:COMI")
      const legendTicker = state.currentSymbol.split(':').pop().toUpperCase().trim();
      if (legendTicker !== expectedTicker) {
        stableCount = 0;
        lastStateKey = null;
        await new Promise(r => setTimeout(r, POLL_INTERVAL));
        continue;
      }
    }

    if (expectedTf) {
      const currentTf = String(state.resolution || '').replace(/^1D$/, 'D');
      const expected = String(expectedTf || '').replace(/^1D$/, 'D');
      if (currentTf !== expected) {
        stableCount = 0;
        lastStateKey = null;
        await new Promise(r => setTimeout(r, POLL_INTERVAL));
        continue;
      }
    }

    const stateKey = `${state.currentSymbol}|${state.resolution}|${state.lastIndex}|${state.lastTime}|${state.barCount}`;
    // Symbol/timeframe/bars match — require 2 stable consecutive reads before declaring ready.
    if (stateKey === lastStateKey) {
      stableCount++;
    } else {
      stableCount = 1;
      lastStateKey = stateKey;
    }

    if (stableCount >= 2) {
      return true;
    }

    await new Promise(r => setTimeout(r, POLL_INTERVAL));
  }

  return false;
}
