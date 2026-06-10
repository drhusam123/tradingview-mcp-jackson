#!/usr/bin/env node
/**
 * Client-output proof gate: only final actionable signals may receive TV proof.
 */
import { getDB } from '../src/egx/index.js';
import { callMCPTool } from '../src/egx/tv_bridge.js';
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const args = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const limit = Number(getArg('--limit', '8'));
const dateArg = getArg('--date', null);

function latestSignalDate(db) {
  return db.prepare('SELECT MAX(trade_date) AS d FROM final_signals').get()?.d;
}

function actionable(db, date) {
  return db.prepare(`
    SELECT trade_date, symbol, score, entry_price, entry_high, stop_loss, t1_target, t2_target, confidence
    FROM final_signals
    WHERE trade_date = ?
      AND actionable = 1
      AND veto_reason IS NULL
    ORDER BY score DESC
    LIMIT ?
  `).all(date, limit);
}

async function main() {
  const db = getDB();
  const date = dateArg || latestSignalDate(db);
  const picks = actionable(db, date);
  if (!picks.length) {
    console.log(JSON.stringify({
      success: true,
      status: 'NO_ACTIONABLE_SIGNALS',
      date,
      message: 'No client proof pack generated because final_signals has no actionable rows.',
    }, null, 2));
    return;
  }

  const health = await callMCPTool('tv_health_check', {});
  if (!health?.success) {
    console.log(JSON.stringify({
      success: false,
      status: 'SKIPPED_NO_TV',
      date,
      picks: picks.length,
      error: health?.error || 'TradingView Desktop is not connected',
    }, null, 2));
    process.exitCode = 2;
    return;
  }

  const proof = [];
  for (const p of picks) {
    const tvSymbol = toTvSymbol(p.symbol);
    await callMCPTool('chart_set_symbol', { symbol: tvSymbol });
    await new Promise(r => setTimeout(r, 800));
    await callMCPTool('chart_set_timeframe', { timeframe: 'D' });
    const [quote, ohlcv] = await Promise.all([
      callMCPTool('quote_get', {}),
      callMCPTool('data_get_ohlcv', { count: 20, summary: true }),
    ]);
    const shot = await callMCPTool('capture_screenshot', { region: 'chart' });
    proof.push({
      symbol: p.symbol,
      tv_symbol: tvSymbol,
      quote_ok: !!quote?.success,
      ohlcv_ok: !!ohlcv?.success,
      screenshot: shot?.path || shot?.file || null,
      score: p.score,
      confidence: p.confidence,
    });
  }

  console.log(JSON.stringify({
    success: true,
    status: 'PASS',
    date,
    proof_count: proof.length,
    proof,
  }, null, 2));
}

main().catch(err => {
  console.error(JSON.stringify({ success: false, error: err.message }, null, 2));
  process.exit(1);
});
