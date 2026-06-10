/**
 * Local TradingView tool bridge for EGX scripts.
 *
 * This replaces the old external MCP client path by dispatching tool names
 * directly to the shared core modules.
 */
import * as chart from '../core/chart.js';
import * as data from '../core/data.js';
import * as batch from '../core/batch.js';
import * as drawing from '../core/drawing.js';
import * as capture from '../core/capture.js';
import * as alerts from '../core/alerts.js';
import * as replay from '../core/replay.js';
import * as ui from '../core/ui.js';
import * as pine from '../core/pine.js';
import * as health from '../core/health.js';
import * as indicators from '../core/indicators.js';
import * as watchlist from '../core/watchlist.js';
import * as pane from '../core/pane.js';
import * as tab from '../core/tab.js';
import * as morning from '../core/morning.js';

const TOOL_MAP = {
  chart_get_state: chart.getState,
  chart_state: chart.getState,
  chart_set_symbol: chart.setSymbol,
  chart_set_timeframe: chart.setTimeframe,
  chart_set_resolution: chart.setTimeframe,
  chart_set_type: chart.setType,
  chart_manage_indicator: chart.manageIndicator,
  chart_get_visible_range: chart.getVisibleRange,
  chart_set_visible_range: chart.setVisibleRange,
  chart_scroll_to_date: chart.scrollToDate,
  chart_info: chart.symbolInfo,
  symbol_info: chart.symbolInfo,
  chart_search: chart.symbolSearch,
  symbol_search: chart.symbolSearch,

  data_get_ohlcv: data.getOhlcv,
  data_get_indicator: data.getIndicator,
  data_get_strategy_results: data.getStrategyResults,
  data_get_trades: data.getTrades,
  data_get_equity: data.getEquity,
  data_get_study_values: data.getStudyValues,
  quote_get: data.getQuote,
  data_get_quote: data.getQuote,
  depth_get: data.getDepth,
  data_get_depth: data.getDepth,
  data_get_pine_lines: data.getPineLines,
  data_get_pine_labels: data.getPineLabels,
  data_get_pine_tables: data.getPineTables,
  data_get_pine_boxes: data.getPineBoxes,

  batch_run: batch.batchRun,

  draw_shape: drawing.drawShape,
  draw_list: drawing.listDrawings,
  draw_clear: drawing.clearAll,
  draw_remove_one: drawing.removeOne,
  draw_get_properties: drawing.getProperties,

  capture_screenshot: capture.captureScreenshot,
  capture: capture.captureScreenshot,
  screenshot: capture.captureScreenshot,

  alert_create: alerts.create,
  alert_list: alerts.list,
  alert_delete: alerts.deleteAlerts,

  replay_start: replay.start,
  replay_step: replay.step,
  replay_autoplay: replay.autoplay,
  replay_stop: replay.stop,
  replay_trade: replay.trade,
  replay_status: replay.status,

  ui_click: ui.click,
  ui_open_panel: ui.openPanel,
  ui_fullscreen: ui.fullscreen,
  layout_list: ui.layoutList,
  layout_switch: ui.layoutSwitch,
  ui_keyboard: ui.keyboard,
  ui_type_text: ui.typeText,
  ui_hover: ui.hover,
  ui_scroll: ui.scroll,
  ui_mouse_click: ui.mouseClick,
  ui_find_element: ui.findElement,
  ui_evaluate: ui.uiEvaluate,

  pine_get_source: pine.getSource,
  pine_set_source: pine.setSource,
  pine_compile: pine.compile,
  pine_get_errors: pine.getErrors,
  pine_save: pine.save,
  pine_get_console: pine.getConsole,
  pine_smart_compile: pine.smartCompile,
  pine_new: pine.newScript,
  pine_open: pine.openScript,
  pine_list_scripts: pine.listScripts,
  pine_analyze: pine.analyze,
  pine_check: pine.check,

  indicator_set_inputs: indicators.setInputs,
  indicator_toggle_visibility: indicators.toggleVisibility,

  watchlist_get: watchlist.get,
  watchlist_add: watchlist.add,

  pane_list: pane.list,
  pane_set_layout: pane.setLayout,
  pane_focus: pane.focus,
  pane_set_symbol: pane.setSymbol,

  tab_list: tab.list,
  tab_new: tab.newTab,
  tab_close: tab.closeTab,
  tab_switch: tab.switchTab,

  morning_brief: morning.runBrief,
  session_save: morning.saveSession,
  session_get: morning.getSession,

  tv_health_check: health.healthCheck,
  health_check: health.healthCheck,
  tv_discover: health.discover,
  tv_ui_state: health.uiState,
  tv_launch: health.launch,
};

function normalizeToolName(tool) {
  return String(tool || '').trim();
}

async function withChartTime(point) {
  if (!point || point.time != null) return point;
  const quote = await data.getQuote({}).catch(() => null);
  const fallback = Math.floor(Date.now() / 1000);
  return { ...point, time: quote?.time ?? fallback };
}

async function normalizeDrawParams(params = {}) {
  const normalized = { ...params };
  if (normalized.color && !normalized.overrides) {
    normalized.overrides = JSON.stringify({
      linecolor: normalized.color,
      textcolor: normalized.color,
      backgroundColor: normalized.color,
      color: normalized.color,
    });
  }
  normalized.point = await withChartTime(normalized.point);
  normalized.point2 = await withChartTime(normalized.point2);
  return normalized;
}

function normalizeBatchResult(result) {
  if (!Array.isArray(result?.results)) return result;

  const iterations = result.results;
  const compatibleResults = iterations.map(row => ({ ...row }));
  for (const row of iterations) {
    const key = row.timeframe ? `${row.symbol}:${row.timeframe}` : row.symbol;
    compatibleResults[key] = row.result ?? row;
  }

  return {
    ...result,
    results: compatibleResults,
    iterations,
  };
}

async function normalizeParams(tool, params) {
  if (tool === 'draw_shape') return normalizeDrawParams(params);
  if (tool === 'ui_open_panel' && params && params.action == null) {
    return { ...params, action: 'open' };
  }
  if (tool === 'pine_new' && params && params.type == null) {
    return { ...params, type: 'indicator' };
  }
  return params || {};
}

function normalizeResult(tool, result) {
  if (tool === 'batch_run') return normalizeBatchResult(result);
  return result;
}

export async function callMCPTool(tool, params = {}) {
  const name = normalizeToolName(tool);
  const fn = TOOL_MAP[name];

  if (!fn) {
    return {
      success: false,
      error: `Unknown TradingView tool: ${name}`,
      available_tools: Object.keys(TOOL_MAP).sort(),
    };
  }

  try {
    const normalizedParams = await normalizeParams(name, params);
    const result = await fn(normalizedParams);
    return normalizeResult(name, result);
  } catch (err) {
    return {
      success: false,
      error: err?.message || String(err),
      tool: name,
    };
  }
}

export const availableTools = Object.freeze(Object.keys(TOOL_MAP).sort());

export default {
  callMCPTool,
  availableTools,
};
