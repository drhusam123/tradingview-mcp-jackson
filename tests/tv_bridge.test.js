import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { availableTools, callMCPTool } from '../src/egx/tv_bridge.js';

describe('tv_bridge integration', () => {
  it('exposes core MCP tools', () => {
    for (const t of [
      'tv_health_check', 'tv_launch', 'chart_get_state', 'quote_get',
      'data_get_ohlcv', 'data_get_pine_lines', 'pine_smart_compile', 'capture_screenshot',
    ]) {
      assert.ok(availableTools.includes(t), `missing tool: ${t}`);
    }
    assert.ok(availableTools.length >= 68);
  });

  it('returns structured error for unknown tool', async () => {
    const r = await callMCPTool('not_a_real_tool', {});
    assert.equal(r.success, false);
    assert.ok(r.error?.includes('Unknown TradingView tool'));
    assert.ok(Array.isArray(r.available_tools));
  });
});
