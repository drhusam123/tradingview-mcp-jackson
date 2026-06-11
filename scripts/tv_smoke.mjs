#!/usr/bin/env node
/** Quick TV bridge smoke — connects, checks health, disconnects cleanly. */
import { availableTools, callMCPTool } from '../src/egx/tv_bridge.js';
import { disconnect } from '../src/connection.js';
import { existsSync } from 'fs';

try {
  const h = await callMCPTool('tv_health_check', {});
  console.log(JSON.stringify({
    cdp: h?.cdp_connected ?? false,
    symbol: h?.chart_symbol ?? null,
    tools: availableTools.length,
    server: existsSync('src/server.js'),
    bridge_ok: h?.success ?? false,
  }, null, 2));
  process.exit(h?.success ? 0 : 1);
} finally {
  await disconnect().catch(() => {});
}
