/**
 * Pull all Egyptian economic indicators from TradingView via CDP
 * Correct approach: set each symbol on chart, wait, then read OHLCV/quote
 * Run: node scripts/pull_egypt_economics_v2.mjs
 */
import * as health from '../src/core/health.js';
import * as data from '../src/core/data.js';
import * as chart from '../src/core/chart.js';
import { evaluate } from '../src/connection.js';

const SYMBOLS = [
  { sym: "ECONOMICS:EGIRYY",  label: "inflation_yoy" },
  { sym: "ECONOMICS:EGINTR",  label: "cbe_interest_rate" },
  { sym: "FX_IDC:USDEGP",     label: "usdegp" },
  { sym: "ECONOMICS:EGCIR",   label: "core_inflation" },
  { sym: "ECONOMICS:EGGDPYY", label: "gdp_yoy" },
  { sym: "ECONOMICS:EGUR",    label: "unemployment" },
  { sym: "ECONOMICS:EGFER",   label: "fx_reserves" },
  { sym: "ECONOMICS:EGM2",    label: "money_supply_m2" },
  { sym: "ECONOMICS:EGBOT",   label: "trade_balance" },
  { sym: "ECONOMICS:EGEXP",   label: "exports" },
  { sym: "ECONOMICS:EGIMP",   label: "imports" },
  { sym: "ECONOMICS:EGREM",   label: "remittances" },
  { sym: "ECONOMICS:EGCA",    label: "current_account" },
  { sym: "ECONOMICS:EGGDG",   label: "govt_debt_gdp" },
  { sym: "ECONOMICS:EGGBV",   label: "budget_balance" },
  { sym: "ECONOMICS:EGFDI",   label: "fdi" },
  { sym: "ECONOMICS:EGTA",    label: "tourist_arrivals" },
  { sym: "ECONOMICS:EGCOP",   label: "crude_oil_production" },
  { sym: "ECONOMICS:EGED",    label: "external_debt" },
  { sym: "ECONOMICS:EGGR",    label: "govt_revenue" },
  { sym: "ECONOMICS:EGGSP",   label: "govt_spending" },
  { sym: "ECONOMICS:EGFE",    label: "fiscal_expenditure" },
];

// Read the actual last bar value from the active chart via CDP
async function getChartLastBar() {
  return evaluate(`
    (function() {
      try {
        var api = window.TradingViewApi._activeChartWidgetWV.value();
        var bars = api._chartWidget.model().mainSeries().bars();
        if (!bars || typeof bars.lastIndex !== 'function') return { error: 'bars not available' };
        var lastIdx = bars.lastIndex();
        var bar = bars.valueAt(lastIdx);
        if (!bar) return { error: 'no bar at lastIndex' };
        var sym = api.symbol();
        var ext = {};
        try { ext = api.symbolExt() || {}; } catch(e) {}
        return {
          symbol: sym,
          description: ext.description || null,
          exchange: ext.exchange || null,
          type: ext.type || null,
          time: bar[0],
          open: bar[1],
          high: bar[2],
          low: bar[3],
          close: bar[4],
          volume: bar[5] || 0,
          barIndex: lastIdx,
        };
      } catch(e) {
        return { error: e.message };
      }
    })()
  `);
}

// Get bar count / recent history
async function getRecentBars(count) {
  return evaluate(`
    (function() {
      try {
        var api = window.TradingViewApi._activeChartWidgetWV.value();
        var bars = api._chartWidget.model().mainSeries().bars();
        if (!bars || typeof bars.lastIndex !== 'function') return { error: 'bars not available' };
        var lastIdx = bars.lastIndex();
        var result = [];
        for (var i = Math.max(0, lastIdx - ${count} + 1); i <= lastIdx; i++) {
          var bar = bars.valueAt(i);
          if (bar) result.push({ time: bar[0], open: bar[1], high: bar[2], low: bar[3], close: bar[4], volume: bar[5] || 0 });
        }
        return { bars: result, total_available: lastIdx + 1 };
      } catch(e) {
        return { error: e.message };
      }
    })()
  `);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function setSymbolAndWait(symbol, maxWaitMs = 5000) {
  await chart.setSymbol({ symbol });
  const start = Date.now();
  let prev = null;
  while (Date.now() - start < maxWaitMs) {
    await sleep(500);
    try {
      const bar = await getChartLastBar();
      if (bar && !bar.error && bar.symbol === symbol) {
        // Symbol loaded and has a valid bar
        if (prev && prev.close === bar.close && prev.time === bar.time) {
          // Stable: two consecutive reads match
          return bar;
        }
        prev = bar;
      }
    } catch(e) { /* continue waiting */ }
  }
  // Return whatever we have
  return await getChartLastBar();
}

async function main() {
  const results = {
    health_check: null,
    quotes: {},
    ohlcv: {},
    errors: [],
    timestamp: new Date().toISOString(),
    note: "quotes pulled by setting each symbol on chart and reading last bar; values are latest available data point from TradingView"
  };

  // Step 1: Health check
  console.error("Running tv_health_check...");
  try {
    results.health_check = await health.healthCheck();
    console.error("Connected:", results.health_check.chart_symbol, results.health_check.chart_resolution);
  } catch (err) {
    results.health_check = { success: false, error: err.message };
    console.error("Health check failed:", err.message);
    process.exit(1);
  }

  // Step 2: For each symbol, set on chart and read last bar
  for (const { sym, label } of SYMBOLS) {
    console.error(`\n[${label}] Setting chart to ${sym}...`);
    try {
      const setResult = await chart.setSymbol({ symbol: sym });
      await sleep(2500); // wait for chart to load new symbol data
      const bar = await getChartLastBar();
      if (bar && bar.error) {
        throw new Error(bar.error);
      }
      // Verify the symbol actually changed
      if (bar.symbol !== sym) {
        console.error(`  Warning: chart shows ${bar.symbol} but expected ${sym}`);
      }
      results.quotes[sym] = {
        success: true,
        symbol: sym,
        label,
        chart_symbol: bar.symbol,
        description: bar.description,
        exchange: bar.exchange,
        type: bar.type,
        time: bar.time,
        time_human: new Date(bar.time * 1000).toISOString().slice(0, 10),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
        last: bar.close,
        volume: bar.volume,
      };
      console.error(`  -> ${sym}: close=${bar.close}, time=${new Date(bar.time * 1000).toISOString().slice(0,10)}`);
    } catch (err) {
      results.quotes[sym] = { success: false, symbol: sym, label, error: err.message };
      results.errors.push({ symbol: sym, label, step: 'quote', error: err.message });
      console.error(`  -> ERROR: ${err.message}`);
    }
  }

  // Step 3: OHLCV history for top 3 symbols
  const ohlcvTargets = [
    { sym: "ECONOMICS:EGIRYY", count: 24 },
    { sym: "ECONOMICS:EGINTR", count: 24 },
    { sym: "FX_IDC:USDEGP",    count: 30, timeframe: "D" },
  ];

  for (const { sym, count, timeframe } of ohlcvTargets) {
    console.error(`\n[OHLCV] Setting chart to ${sym} (count=${count})...`);
    try {
      await chart.setSymbol({ symbol: sym });
      await sleep(2500);
      if (timeframe) {
        try {
          await chart.setTimeframe({ timeframe });
          await sleep(1500);
          console.error(`  timeframe set to ${timeframe}`);
        } catch(e) {
          console.error(`  timeframe set failed: ${e.message}`);
        }
      }
      const barsResult = await getRecentBars(count);
      if (barsResult.error) throw new Error(barsResult.error);

      // Add human-readable dates
      const barsWithDates = (barsResult.bars || []).map(b => ({
        ...b,
        date: new Date(b.time * 1000).toISOString().slice(0, 10)
      }));

      results.ohlcv[sym] = {
        success: true,
        symbol: sym,
        count: barsWithDates.length,
        total_available: barsResult.total_available,
        timeframe: timeframe || "chart_default",
        bars: barsWithDates
      };
      console.error(`  -> ${barsWithDates.length} bars retrieved, latest: ${barsWithDates[barsWithDates.length-1]?.date} close=${barsWithDates[barsWithDates.length-1]?.close}`);
    } catch (err) {
      results.ohlcv[sym] = { success: false, symbol: sym, error: err.message };
      results.errors.push({ symbol: sym, step: 'ohlcv', error: err.message });
      console.error(`  -> OHLCV ERROR: ${err.message}`);
    }
  }

  // Output full JSON to stdout
  console.log(JSON.stringify(results, null, 2));
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
