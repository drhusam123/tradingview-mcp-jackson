#!/usr/bin/env node
/**
 * Phase 60 — Chart Drawing + Screenshot Fetcher
 * Draws key levels (entry zone, stop, targets) on TradingView charts
 * and captures screenshots for the daily visual report.
 *
 * Options:
 *   --date 2026-05-15    use scan results from this date
 *   --n 8                number of top picks to draw (default: 8)
 *   --min-score 65       minimum scan score
 *   --symbol COMI        draw single symbol only
 *   --clear              clear all drawings first
 *   --no-screenshot      draw but don't screenshot
 */
import { pythonVizGetTopPicksDraws, pythonVizGetDrawSpecs,
         pythonVizLogScreenshot, pythonVizFinalizeReport }
  from '../src/egx/index.js';
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const args       = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback;
};
const date       = getArg('--date', new Date().toISOString().split('T')[0]);
const n          = parseInt(getArg('--n', '8'));
const minScore   = parseFloat(getArg('--min-score', '65'));
const singleSym  = getArg('--symbol', null);
const doClear    = args.includes('--clear');
const noScreenshot = args.includes('--no-screenshot');

function log(msg) { console.log(`[drawing] ${msg}`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function loadMCPCaller() {
  const candidates = [
    '../src/egx/tv_bridge.js',
    '../src/egx/mcp_tools.js',
    '../src/client.js',
  ];

  for (const path of candidates) {
    const mod = await import(path).catch(() => null);
    if (mod?.callMCPTool) {
      const health = await mod.callMCPTool('tv_health_check', {});
      if (!health?.success) continue;
      return (tool, params = {}) => mod.callMCPTool(tool, params);
    }
    if (mod?.Client) {
      const client = new mod.Client();
      await client.connect?.();
      return (tool, params = {}) => client.callTool(tool, params);
    }
  }

  return null;
}

// Color constants for draw_shape
const COLORS = {
  entry:   '#00BB00',   // green
  stop:    '#FF3333',   // red
  target1: '#0055FF',   // blue
  target2: '#0033AA',   // dark blue
};

// ── Get picks to draw ────────────────────────────────────────────────────────
let picksResult;
if (singleSym) {
  const specs = await pythonVizGetDrawSpecs({ symbol: singleSym, scan_date: date });
  picksResult = { picks: specs?.draws ? [{ symbol: singleSym, draw_specs: specs }] : [], n_picks: specs?.draws ? 1 : 0 };
} else {
  picksResult = await pythonVizGetTopPicksDraws({ scan_date: date, n, min_score: minScore });
}

if (!picksResult?.picks?.length) {
  log(`No picks to draw for ${date} (min_score=${minScore}). Run egx:scan first.`);
  process.exit(0);
}

log(`Drawing ${picksResult.n_picks} picks for ${date}...`);

// ── Try TradingView connection ────────────────────────────────────────────────
let tvTools = null;
try {
  const callMCPTool = await loadMCPCaller();
  tvTools = callMCPTool ? { call: callMCPTool } : null;
} catch { /* offline */ }

const screenshots = [];

for (const pick of picksResult.picks) {
  const { symbol, draw_specs } = pick;
  if (!draw_specs?.draws?.length) continue;

  log(`\n  📊 ${symbol} (score=${draw_specs.scan_score?.toFixed(0)}, ${draw_specs.setup_type ?? ''})`);

  if (tvTools) {
    // Clear existing drawings for this symbol
    if (doClear) {
      await tvTools.call('draw_clear', {}).catch(() => {});
    }

    // Set symbol
    await tvTools.call('chart_set_symbol', { symbol: toTvSymbol(symbol) }).catch(e => log(`  ⚠️ ${e.message}`));
    await sleep(1500);
    await tvTools.call('chart_set_timeframe', { timeframe: 'D' }).catch(() => {});
    await sleep(1000);

    // Draw each element
    for (const draw of draw_specs.draws) {
      try {
        if (draw.type === 'rectangle') {
          await tvTools.call('draw_shape', {
            shape: 'rectangle',
            point:  { price: draw.price_high },
            point2: { price: draw.price_low },
            text:   draw.label,
            color:  draw.color ?? COLORS.entry,
          });
        } else if (draw.type === 'horizontal_line') {
          await tvTools.call('draw_shape', {
            shape: 'horizontal_line',
            point: { price: draw.price },
            text:  draw.label,
            color: draw.color ?? COLORS.stop,
          });
        }
        log(`    ✏️  Drew: ${draw.label} @ ${draw.price ?? `${draw.price_low}-${draw.price_high}`}`);
      } catch (e) {
        log(`    ⚠️  Draw failed: ${draw.label} — ${e.message}`);
      }
    }

    // Screenshot
    if (!noScreenshot) {
      await sleep(800);
      try {
        const ss = await tvTools.call('capture_screenshot', {
          region: 'chart',
          filename: `egx_${symbol}_${date}`,
        });
        const path = ss?.path ?? ss?.filename ?? `screenshots/egx_${symbol}_${date}.png`;
        log(`    📸 Screenshot: ${path}`);
        screenshots.push({ symbol, path, scan_score: draw_specs.scan_score, setup_type: draw_specs.setup_type });

        // Log to DB
        await pythonVizLogScreenshot({
          symbol,
          report_date: date,
          screenshot_path: path,
          scan_score: draw_specs.scan_score,
          setup_type: draw_specs.setup_type,
        });
      } catch (e) {
        log(`    ⚠️  Screenshot failed: ${e.message}`);
      }
    }
  } else {
    // Offline: log what would be drawn
    draw_specs.draws.forEach(d => {
      const price = d.price ?? `${d.price_low?.toFixed(2)}-${d.price_high?.toFixed(2)}`;
      log(`    [DRY-RUN] ${d.type}: ${d.label} @ ${price}`);
    });
  }

  await sleep(500);
}

// Finalize report
if (screenshots.length && !noScreenshot) {
  const report = await pythonVizFinalizeReport({ report_date: date });
  log(`\n✅ Visual report finalized: ${report?.n_screenshots ?? screenshots.length} screenshots`);
  log(`   Top picks: ${(report?.top_picks ?? screenshots.map(s => s.symbol)).join(', ')}`);
} else {
  log('\nNo screenshots captured (offline mode or no picks).');
}
