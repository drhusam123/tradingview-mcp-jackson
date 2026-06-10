#!/usr/bin/env node
/**
 * Verify TradingView MCP is wired, bridged, and automation-ready.
 * Usage: node scripts/egx_tv_integration_verify.mjs
 */
import { execSync } from 'child_process';
import { existsSync, readdirSync, readFileSync } from 'fs';
import { homedir } from 'os';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { availableTools, callMCPTool } from '../src/egx/tv_bridge.js';

loadEnv();

const checks = [];
function ok(name, pass, detail = '') {
  checks.push({ name, pass, detail });
  console.log(`${pass ? '✅' : '❌'} ${name}${detail ? `: ${detail}` : ''}`);
}

let cron = '';
try { cron = execSync('crontab -l 2>/dev/null', { encoding: 'utf8' }); } catch { /* */ }

ok('MCP server src/server.js', existsSync(join(PROJECT_ROOT, 'src/server.js')));

const mcpCfg = join(homedir(), '.claude', '.mcp.json');
const expectedServer = join(PROJECT_ROOT, 'src/server.js');
if (existsSync(mcpCfg)) {
  try {
    const args = JSON.parse(readFileSync(mcpCfg, 'utf8'))?.mcpServers?.tradingview?.args?.[0];
    ok('Claude MCP config path', args === expectedServer, args || 'missing');
  } catch {
    ok('Claude MCP config path', false, 'parse error');
  }
} else {
  ok('Claude MCP config path', true, 'optional — ~/.claude/.mcp.json not found');
}
ok('TV bridge tv_bridge.js', existsSync(join(PROJECT_ROOT, 'src/egx/tv_bridge.js')));
ok('Bridge tool count >= 68', availableTools.length >= 68, `${availableTools.length} tools`);

const toolDir = join(PROJECT_ROOT, 'src/tools');
const serverTools = readdirSync(toolDir)
  .filter(f => f.endsWith('.js'))
  .flatMap(f => [...readFileSync(join(toolDir, f), 'utf8').matchAll(/server\.tool\(\s*["']([^"']+)["']/g)].map(m => m[1]));
const bridgeSet = new Set(availableTools);
const missing = serverTools.filter(t => !bridgeSet.has(t));
ok('Bridge covers all MCP server tools', missing.length === 0, missing.length ? `missing: ${missing.join(', ')}` : 'all mapped');

ok('egx_tv_auto_update.mjs', existsSync(join(PROJECT_ROOT, 'scripts/egx_tv_auto_update.mjs')));
ok('Cron TV sync (egx-tv-sync)', /egx-tv-sync.*egx_tv_auto_update.*--launch/.test(cron));
ok('Cron TV live quotes', /egx-tv-live.*fetch_intraday_live/.test(cron));
ok('TV_CDP_BROWSER configured', Boolean(process.env.TV_CDP_BROWSER), process.env.TV_CDP_BROWSER || 'default chrome');

let cdpUp = false;
try {
  execSync('curl -sf --max-time 3 http://127.0.0.1:9222/json/version', { stdio: 'ignore' });
  cdpUp = true;
} catch { /* */ }
ok('CDP port 9222 reachable', cdpUp);

const health = await callMCPTool('tv_health_check', {});
ok('tv_health_check success', health?.success === true);
ok('CDP connected', health?.cdp_connected === true, health?.chart_symbol || 'no chart');
ok('Chart API available', health?.api_available === true);

const quote = await callMCPTool('quote_get', {});
ok('quote_get live', quote?.success === true, quote?.last != null ? `last=${quote.last}` : quote?.error || '');

const wiredScripts = [
  'scripts/egx_tv_auto_update.mjs',
  'scripts/egx_telegram_daily.mjs',
  'scripts/fetch_intraday_live.mjs',
  'scripts/daily_update.mjs',
  'scripts/tv_mcp_audit.mjs',
];
for (const s of wiredScripts) {
  const text = readFileSync(join(PROJECT_ROOT, s), 'utf8');
  const wired = /callMCPTool|tv_bridge|src\/core\/(chart|data|health)/.test(text);
  ok(`${s} wired to TV core`, wired);
}

const fail = checks.filter(c => !c.pass).length;
console.log(`\n=== TV MCP Integration: ${checks.length - fail}/${checks.length} PASS ===\n`);
process.exit(fail ? 1 : 0);
