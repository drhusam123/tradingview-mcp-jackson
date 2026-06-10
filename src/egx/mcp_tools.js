import { callMCPTool } from './tv_bridge.js';

function studiesArrayToObject(result) {
  if (!Array.isArray(result?.studies)) return result;

  const studies = {};
  for (const study of result.studies) {
    if (!study?.name) continue;
    studies[study.name] = study.values ?? study;
  }

  return {
    ...result,
    studies,
    studies_array: result.studies,
  };
}

export function callTool(name, params = {}) {
  return callMCPTool(name, params);
}

export function chartSetSymbol(params) {
  return callMCPTool('chart_set_symbol', params);
}

export function chartSetTimeframe(params) {
  return callMCPTool('chart_set_timeframe', params);
}

export function chartManageIndicator(params) {
  return callMCPTool('chart_manage_indicator', params);
}

export async function dataGetStudyValues(params = {}) {
  const result = await callMCPTool('data_get_study_values', params);
  return studiesArrayToObject(result);
}

export function dataGetOhlcv(params = {}) {
  return callMCPTool('data_get_ohlcv', params);
}

export function quoteGet(params = {}) {
  return callMCPTool('quote_get', params);
}

export function depthGet(params = {}) {
  return callMCPTool('depth_get', params);
}

export function batchRun(params = {}) {
  return callMCPTool('batch_run', params);
}

export function captureScreenshot(params = {}) {
  return callMCPTool('capture_screenshot', params);
}

export function drawShape(params = {}) {
  return callMCPTool('draw_shape', params);
}

export function drawClear(params = {}) {
  return callMCPTool('draw_clear', params);
}

export default {
  callTool,
  chartSetSymbol,
  chartSetTimeframe,
  chartManageIndicator,
  dataGetStudyValues,
  dataGetOhlcv,
  quoteGet,
  depthGet,
  batchRun,
  captureScreenshot,
  drawShape,
  drawClear,
};
