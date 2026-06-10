#!/usr/bin/env node
/**
 * TV Replay Bridge — Phase 10 TradingView MCP Replay Backtester
 * =============================================================
 * Called by egx_ml_trainer.py phase10 via subprocess:
 *   node scripts/tv_replay_bridge.mjs <symbol> <date>
 *
 * Workflow:
 *   1. chart_set_symbol(symbol)
 *   2. replay_start(date)
 *   3. replay_trade(buy)
 *   4. replay_step × 5
 *   5. replay_status() → entry price, current price, P&L
 *   6. replay_trade(close)
 *   7. Output JSON result to stdout
 *
 * Usage:
 *   node scripts/tv_replay_bridge.mjs COMI 2025-03-15
 *
 * If TradingView is not connected, exits with code 1.
 * Results are written to stdout as a single JSON line.
 */
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const [, , symbol, date] = process.argv;

if (!symbol || !date) {
  console.error(JSON.stringify({ error: 'Usage: tv_replay_bridge.mjs <symbol> <date>' }));
  process.exit(1);
}

async function loadMCPCaller() {
  const candidates = [
    '../src/egx/tv_bridge.js',
    '../src/egx/mcp_tools.js',
    '../src/client.js',
  ];

  for (const path of candidates) {
    const mod = await import(path).catch(() => null);
    if (mod?.callMCPTool) return (tool, params = {}) => mod.callMCPTool(tool, params);
    if (mod?.Client) {
      const client = new mod.Client();
      await client.connect?.();
      return (tool, params = {}) => client.callTool(tool, params);
    }
  }

  const core = await import('../src/core/index.js').catch(() => null);
  if (!core) return null;

  const coreTools = {
    chart_set_symbol: core.chart.setSymbol,
    replay_start: core.replay.start,
    replay_trade: core.replay.trade,
    replay_step: core.replay.step,
    replay_status: core.replay.status,
    replay_stop: core.replay.stop,
  };
  return (tool, params = {}) => {
    const fn = coreTools[tool];
    if (!fn) throw new Error(`Unsupported core tool: ${tool}`);
    return fn(params);
  };
}

function normalizeStatus(statusRes = {}) {
  const position = statusRes.position && typeof statusRes.position === 'object'
    ? statusRes.position
    : {};
  const pnl = statusRes.pnl ?? statusRes.realized_pnl ?? position.pnl ?? 0;
  const pnlPct = statusRes.pnl_pct ?? statusRes.pnl_percent ?? position.pnl_pct ?? 0;

  return {
    pnl,
    pnlPct,
    entry: statusRes.entry_price ?? position.entry_price ?? position.entryPrice ?? 0,
    current: statusRes.current_price ?? position.current_price ?? position.currentPrice ?? 0,
    maxProfit: statusRes.max_profit ?? position.max_profit ?? 0,
    maxLoss: statusRes.max_loss ?? position.max_loss ?? 0,
  };
}

async function main() {
  const t0 = Date.now();
  const callMCPTool = await loadMCPCaller();
  if (!callMCPTool) {
    process.stdout.write(JSON.stringify({ error: 'TV MCP bridge unavailable', symbol, date }) + '\n');
    process.exit(1);
  }

  // 1. Switch to symbol
  const setRes = await callMCPTool('chart_set_symbol', { symbol: toTvSymbol(symbol) });
  if (!setRes?.success) {
    process.stdout.write(JSON.stringify({ error: 'TV not connected or symbol failed', symbol, date }) + '\n');
    process.exit(1);
  }

  // 2. Start replay 5 bars before the signal date
  const replayRes = await callMCPTool('replay_start', { date });
  if (!replayRes?.success) {
    process.stdout.write(JSON.stringify({ error: 'replay_start failed', symbol, date }) + '\n');
    process.exit(1);
  }

  // 3. Enter trade (buy)
  await callMCPTool('replay_trade', { action: 'buy' });

  // 4. Step 5 bars forward
  for (let i = 0; i < 5; i++) {
    await callMCPTool('replay_step', {});
  }

  // 5. Get status → P&L
  const statusRes = await callMCPTool('replay_status', {});

  // 6. Close position
  await callMCPTool('replay_trade', { action: 'close' }).catch(() => {});

  // 7. Stop replay
  await callMCPTool('replay_stop', {}).catch(() => {});

  const { pnlPct, entry, current, maxProfit, maxLoss } = normalizeStatus(statusRes);

  const result = {
    symbol,
    signal_date:  date,
    entry_price:  entry,
    exit_price:   current,
    outcome:      pnlPct > 0 ? 'win' : 'loss',
    pnl_pct:      Math.round(pnlPct * 1000) / 1000,
    r_multiple:   Math.round(pnlPct / 1.5 * 100) / 100,  // assume 1.5% stop
    mfe_pct:      maxProfit,
    mae_pct:      maxLoss,
    source:       'tv_replay',
    duration_ms:  Date.now() - t0,
  };

  process.stdout.write(JSON.stringify(result) + '\n');
}

main().catch(err => {
  process.stdout.write(JSON.stringify({ error: err.message, symbol, date }) + '\n');
  process.exit(1);
});
