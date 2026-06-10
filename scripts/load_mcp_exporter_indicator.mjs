#!/usr/bin/env node
/**
 * Upload the fixed-key EGX MCP Exporter Pine indicator to TradingView.
 */
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { callMCPTool } from '../src/egx/tv_bridge.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const PINE_PATH = join(ROOT, 'scripts', 'pine', 'egx_mcp_exporter.pine');
const SAVE_ONLY = process.argv.includes('--save-only');

function log(msg) {
  console.log(`[mcp-exporter] ${msg}`);
}

async function main() {
  const source = readFileSync(PINE_PATH, 'utf8');
  if (SAVE_ONLY) {
    log(`Pine source ready: ${PINE_PATH}`);
    return;
  }

  const health = await callMCPTool('tv_health_check', {});
  if (!health?.success) {
    log(`TradingView not connected: ${health?.error || 'unknown'}`);
    log(`Pine saved locally: ${PINE_PATH}`);
    process.exitCode = 2;
    return;
  }

  await callMCPTool('ui_open_panel', { panel: 'pine-editor', action: 'open' });
  await new Promise(r => setTimeout(r, 1200));
  await callMCPTool('pine_new', { type: 'indicator' });
  await new Promise(r => setTimeout(r, 800));

  const set = await callMCPTool('pine_set_source', { source });
  if (!set?.success) throw new Error(`pine_set_source failed: ${set?.error || JSON.stringify(set)}`);

  const comp = await callMCPTool('pine_smart_compile', {});
  if (comp?.success === false || comp?.errors?.length) {
    console.log(JSON.stringify({ success: false, compile: comp }, null, 2));
    process.exit(1);
  }

  const save = await callMCPTool('pine_save', {});
  log(`Uploaded EGX MCP Exporter (${source.length} chars)`);
  console.log(JSON.stringify({ success: true, pine_path: PINE_PATH, save }, null, 2));
  process.exit(0);
}

main().catch(err => {
  console.error(JSON.stringify({ success: false, error: err.message }, null, 2));
  process.exit(1);
});
