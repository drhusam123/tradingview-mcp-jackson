#!/usr/bin/env node
/**
 * TradingView MCP capability audit for the EGX production pipeline.
 */
import { readFileSync, readdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { availableTools } from '../src/egx/tv_bridge.js';
import { getDB } from '../src/egx/index.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const TOOL_DIR = join(ROOT, 'src', 'tools');
const IGNORE_DIRS = new Set(['node_modules', '.git', 'data', 'screenshots', 'logs']);

function read(path) {
  return readFileSync(path, 'utf8');
}

function walk(dir, out = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (IGNORE_DIRS.has(entry.name)) continue;
    const p = join(dir, entry.name);
    if (entry.isDirectory()) walk(p, out);
    else if (/\.(mjs|js|py|md|json)$/.test(entry.name)) out.push(p);
  }
  return out;
}

function discoverServerTools() {
  const tools = [];
  for (const file of readdirSync(TOOL_DIR).filter(f => f.endsWith('.js'))) {
    const source = read(join(TOOL_DIR, file));
    const matches = [...source.matchAll(/server\.tool\(\s*["']([^"']+)["']/g)];
    for (const m of matches) tools.push({ tool: m[1], group: file.replace('.js', '') });
  }
  return tools.sort((a, b) => a.tool.localeCompare(b.tool));
}

function usageCounts(toolNames) {
  const files = walk(ROOT).filter(p => !p.includes('/src/tools/') && !p.includes('/src/core/'));
  const counts = new Map(toolNames.map(t => [t, 0]));
  for (const file of files) {
    const text = read(file);
    for (const tool of toolNames) {
      const re = new RegExp(`\\b${tool}\\b`, 'g');
      const c = text.match(re)?.length || 0;
      if (c) counts.set(tool, (counts.get(tool) || 0) + c);
    }
  }
  return counts;
}

function tableStats() {
  const db = getDB();
  const exists = name => db.prepare("SELECT type, name FROM sqlite_master WHERE type IN ('table','view') AND name=?").get(name);
  const cols = name => db.prepare(`PRAGMA table_info(${name})`).all().map(c => c.name);
  const stats = {};
  const tables = ['ohlcv_history', 'ohlcv_history_execution', 'cross_market_daily', 'macro_snapshot', 'pine_analytics', 'intraday_live_quotes', 'dom_snapshots', 'dom_live_snapshots', 'final_signals', 'opportunity_score_v2', 'egx_x_pro_daily', 'egx_signal_tracker'];
  for (const table of tables) {
    const meta = exists(table);
    if (!meta) {
      stats[table] = { exists: false };
      continue;
    }
    const cnt = db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n;
    stats[table] = { exists: true, type: meta.type, rows: cnt };
  }
  if (exists('ohlcv_history')) {
    stats.ohlcv_history.coverage = db.prepare(`
      SELECT COUNT(DISTINCT symbol) symbols,
             date(MIN(bar_time), 'unixepoch') oldest,
             date(MAX(bar_time), 'unixepoch') newest
      FROM ohlcv_history
    `).get();
  }
  if (exists('cross_market_daily')) {
    stats.cross_market_daily.coverage = db.prepare(`
      SELECT COUNT(DISTINCT asset) assets, MIN(bar_time) oldest, MAX(bar_time) newest
      FROM cross_market_daily
    `).get();
  }
  if (exists('pine_analytics')) {
    const dateCol = cols('pine_analytics').includes('fetch_date') ? 'fetch_date' : 'trade_date';
    stats.pine_analytics.coverage = db.prepare(`
      SELECT COUNT(DISTINCT symbol) symbols, MIN(${dateCol}) oldest, MAX(${dateCol}) newest
      FROM pine_analytics
    `).get();
  }
  if (exists('egx_x_pro_daily')) {
    stats.egx_x_pro_daily.coverage = db.prepare(`
      SELECT COUNT(DISTINCT symbol) symbols, MIN(trade_date) oldest, MAX(trade_date) newest
      FROM egx_x_pro_daily
    `).get();
  }
  if (exists('egx_signal_tracker')) {
    stats.egx_signal_tracker.coverage = db.prepare(`
      SELECT COUNT(DISTINCT symbol) symbols, status, COUNT(*) n
      FROM egx_signal_tracker
      GROUP BY status
      ORDER BY n DESC
    `).all();
  }
  return stats;
}

function main() {
  const serverTools = discoverServerTools();
  const serverNames = serverTools.map(t => t.tool);
  const bridgeSet = new Set(availableTools);
  const missingFromBridge = serverNames.filter(t => !bridgeSet.has(t));
  const aliasesOnly = availableTools.filter(t => !serverNames.includes(t));
  const counts = usageCounts(serverNames);
  const unused = serverTools.filter(t => (counts.get(t.tool) || 0) === 0);
  const used = serverTools.filter(t => (counts.get(t.tool) || 0) > 0);

  const groups = {};
  for (const row of serverTools) {
    groups[row.group] ??= { total: 0, used: 0, tools: [] };
    groups[row.group].total += 1;
    if ((counts.get(row.tool) || 0) > 0) groups[row.group].used += 1;
    groups[row.group].tools.push({ tool: row.tool, usage: counts.get(row.tool) || 0 });
  }

  const report = {
    success: missingFromBridge.length === 0,
    generated_at: new Date().toISOString(),
    server_tools: serverNames.length,
    bridge_tools_and_aliases: availableTools.length,
    groups,
    used_tools: used.length,
    unused_or_manual_tools: unused.map(t => t.tool),
    missing_from_egx_bridge: missingFromBridge,
    bridge_aliases_or_compat_names: aliasesOnly,
    data_layers: tableStats(),
  };

  console.log(JSON.stringify(report, null, 2));
  if (missingFromBridge.length) process.exit(1);
}

main();
