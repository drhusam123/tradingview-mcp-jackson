/**
 * Pull all Egyptian economic indicators from TradingView via CDP
 * Run: node scripts/pull_egypt_economics.mjs
 */
import * as health from '../src/core/health.js';
import * as data from '../src/core/data.js';
import * as chart from '../src/core/chart.js';

const SYMBOLS = [
  "ECONOMICS:EGIRYY",   // inflation YoY
  "ECONOMICS:EGINTR",   // CBE interest rate
  "FX_IDC:USDEGP",      // USD/EGP
  "ECONOMICS:EGCIR",    // core inflation
  "ECONOMICS:EGGDPYY",  // GDP YoY
  "ECONOMICS:EGUR",     // unemployment
  "ECONOMICS:EGFER",    // FX reserves
  "ECONOMICS:EGM2",     // money supply M2
  "ECONOMICS:EGBOT",    // trade balance
  "ECONOMICS:EGEXP",    // exports
  "ECONOMICS:EGIMP",    // imports
  "ECONOMICS:EGREM",    // remittances
  "ECONOMICS:EGCA",     // current account
  "ECONOMICS:EGGDG",    // govt debt/GDP
  "ECONOMICS:EGGBV",    // budget balance
  "ECONOMICS:EGFDI",    // FDI
  "ECONOMICS:EGTA",     // tourist arrivals
  "ECONOMICS:EGCOP",    // crude oil production
  "ECONOMICS:EGED",     // external debt
  "ECONOMICS:EGGR",     // government revenue
  "ECONOMICS:EGGSP",    // government spending
  "ECONOMICS:EGFE",     // fiscal expenditure
];

const OHLCV_SYMBOLS = [
  { symbol: "ECONOMICS:EGIRYY", count: 24, timeframe: null },
  { symbol: "ECONOMICS:EGINTR", count: 24, timeframe: null },
  { symbol: "FX_IDC:USDEGP",    count: 30, timeframe: "D" },
];

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function main() {
  const results = {
    health_check: null,
    quotes: {},
    ohlcv: {},
    errors: [],
    timestamp: new Date().toISOString(),
  };

  // Step 1: Health check
  console.error("Running tv_health_check...");
  try {
    results.health_check = await health.healthCheck();
    console.error("Health check:", JSON.stringify(results.health_check, null, 2));
  } catch (err) {
    results.health_check = { success: false, error: err.message };
    console.error("Health check failed:", err.message);
  }

  // Step 2: Pull quotes for all symbols
  for (const symbol of SYMBOLS) {
    console.error(`Fetching quote for ${symbol}...`);
    try {
      const result = await data.getQuote({ symbol });
      results.quotes[symbol] = result;
      console.error(`  -> ${symbol}: ${JSON.stringify(result).slice(0, 120)}`);
    } catch (err) {
      results.quotes[symbol] = { success: false, error: err.message };
      results.errors.push({ symbol, step: 'quote', error: err.message });
      console.error(`  -> ${symbol} ERROR: ${err.message}`);
    }
    await sleep(300); // small delay between calls
  }

  // Step 3: OHLCV for top 3 symbols
  for (const { symbol, count, timeframe } of OHLCV_SYMBOLS) {
    console.error(`\nSetting symbol to ${symbol} and fetching OHLCV (count=${count})...`);
    try {
      // Set symbol on chart
      const setResult = await chart.setSymbol({ symbol });
      console.error(`  chart_set_symbol: ${JSON.stringify(setResult)}`);
      await sleep(2000); // wait for chart to load

      // Set timeframe if specified
      if (timeframe) {
        try {
          const { setTimeframe } = await import('../src/core/chart.js');
          await setTimeframe({ timeframe });
          console.error(`  chart_set_timeframe: ${timeframe}`);
          await sleep(1000);
        } catch (e) {
          console.error(`  chart_set_timeframe failed: ${e.message}`);
        }
      }

      const ohlcvResult = await data.getOhlcv({ count, summary: false });
      results.ohlcv[symbol] = ohlcvResult;
      console.error(`  -> OHLCV bars received: ${ohlcvResult?.bars?.length ?? 'N/A'}`);
    } catch (err) {
      results.ohlcv[symbol] = { success: false, error: err.message };
      results.errors.push({ symbol, step: 'ohlcv', error: err.message });
      console.error(`  -> OHLCV ERROR for ${symbol}: ${err.message}`);
    }
    await sleep(500);
  }

  // Output full JSON to stdout
  console.log(JSON.stringify(results, null, 2));
}

main().catch(err => {
  console.error("Fatal error:", err);
  process.exit(1);
});
