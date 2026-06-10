#!/usr/bin/env node
/**
 * Phase 24 — Spectral Cycle Pine Overlay (TradingView MCP)
 * =========================================================
 * يقرأ spectral features من feature_store لأفضل 30 سهم،
 * يولّد Pine Script v5 يعرض:
 *   - Oscillator 0-1 لـ cycle_bottom_prox (الخط الأزرق)
 *   - ألوان الـ bar حسب الـ regime (أخضر=دوري، رمادي=ضوضاء، برتقالي=توسع، أزرق=ضغط)
 *   - جدول صغير يعرض أفضل 5 أسهم عند قاع الدورة
 *
 * التشغيل:
 *   node scripts/load_spectral_indicator.mjs           (توليد + تحميل على TV)
 *   node scripts/load_spectral_indicator.mjs --dry-run (توليد بدون رفع على TV)
 *   node scripts/load_spectral_indicator.mjs --save-only (توليد + حفظ Pine فقط)
 *
 * المالك: Dr. Husam | مايو 2026
 */

import Database from 'better-sqlite3';
import { join }  from 'path';
import { mkdirSync, writeFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { toTvSymbol } from '../src/egx/tv_symbols.js';

const __dir   = fileURLToPath(new URL('.', import.meta.url));
const ROOT    = join(__dir, '..');
const DB_PATH = join(ROOT, 'data', 'egx_trading.db');
const PINE_DIR = join(ROOT, 'scripts', 'pine');

const DRY_RUN   = process.argv.includes('--dry-run');
const SAVE_ONLY = process.argv.includes('--save-only');

const log = msg => process.stdout.write(`[spectral-pine] ${msg}\n`);

async function loadTVClient() {
  const candidates = [
    '../src/egx/tv_bridge.js',
    '../src/egx/mcp_tools.js',
    '../src/client.js',
  ];

  for (const path of candidates) {
    const mod = await import(path).catch(() => null);
    if (mod?.callMCPTool) {
      return {
        callTool: (tool, params = {}) => mod.callMCPTool(tool, params),
        disconnect: async () => {},
      };
    }
    if (mod?.Client) {
      const client = new mod.Client();
      await client.connect?.();
      return client;
    }
  }

  const core = await import('../src/core/index.js').catch(() => null);
  if (core) {
    const coreTools = {
      ui_open_panel: core.ui.openPanel,
      pine_new: core.pine.newScript,
      pine_set_source: core.pine.setSource,
      pine_smart_compile: core.pine.smartCompile,
      pine_save: core.pine.save,
      capture_screenshot: core.capture.captureScreenshot,
    };
    return {
      callTool: (tool, params = {}) => {
        const fn = coreTools[tool];
        if (!fn) throw new Error(`Unsupported core tool: ${tool}`);
        return fn(params);
      },
      disconnect: async () => {},
    };
  }

  throw new Error('No TradingView MCP bridge or core API available');
}

mkdirSync(PINE_DIR, { recursive: true });

// ── 1. Read spectral features from feature_store ─────────────────────────────
const db    = new Database(DB_PATH, { readonly: true });
const today = new Date().toISOString().split('T')[0];

// Try today first, then most recent available date
let featureDate = today;
const latestDate = db.prepare(`
  SELECT MAX(feature_date) as d FROM feature_store
  WHERE feature_name = 'fft_cycle_bottom_prox'
`).get()?.d;
if (!latestDate) {
  log('⚠️  No spectral features in DB — run phase21 first');
  process.exit(0);
}
featureDate = latestDate;

log(`Reading spectral features for ${featureDate}...`);

// Fetch all spectral features for this date
const rawFeats = db.prepare(`
  SELECT symbol, feature_name, feature_value
  FROM feature_store
  WHERE feature_date = ?
    AND feature_name IN (
      'fft_cycle_bottom_prox', 'fft_noise_ratio',
      'fft_stability_score', 'spectral_regime',
      'fft_dominant_period', 'fft_dominant_amplitude'
    )
`).all(featureDate);

// Group by symbol
const symFeats = {};
for (const r of rawFeats) {
  if (!symFeats[r.symbol]) symFeats[r.symbol] = {};
  symFeats[r.symbol][r.feature_name] = parseFloat(r.feature_value);
}

// Sort: cyclical + high bottom_prox first
const entries = Object.entries(symFeats)
  .map(([sym, f]) => ({
    sym,
    regime:    f['spectral_regime'] ?? 1,
    bottomProx: f['fft_cycle_bottom_prox'] ?? 0,
    noisRatio:  f['fft_noise_ratio'] ?? 1,
    stability:  f['fft_stability_score'] ?? 0,
    domPeriod:  f['fft_dominant_period'] ?? 0,
    domAmp:     f['fft_dominant_amplitude'] ?? 0,
  }))
  .sort((a, b) => {
    // Cyclical first, then by bottom_prox descending
    const aScore = (a.regime === 0 ? 10 : 0) + a.bottomProx;
    const bScore = (b.regime === 0 ? 10 : 0) + b.bottomProx;
    return bScore - aScore;
  });

const top30 = entries.slice(0, 30);
const topCycleBottom5 = entries
  .filter(e => e.regime === 0 && e.bottomProx > 0.6)
  .slice(0, 5);

const totalCyclical   = entries.filter(e => e.regime === 0).length;
const totalNoisy      = entries.filter(e => e.regime === 1).length;
const totalCompression = entries.filter(e => e.regime === 2).length;
const totalExpansion  = entries.filter(e => e.regime === 3).length;
const totalSyms       = entries.length;

log(`  Regime distribution: cyclical=${totalCyclical}  noisy=${totalNoisy}  expansion=${totalExpansion}  compression=${totalCompression}`);
log(`  Cycle-bottom leaders (≥30): ${topCycleBottom5.map(e => e.sym).join(', ') || 'none today'}`);

db.close();

// ── 2. Generate Pine Script v5 ────────────────────────────────────────────────
// Use placeholder replacement to avoid {brace} conflicts with Pine Script

const regimeNames = { 0: 'cyclical', 1: 'noisy', 2: 'compression', 3: 'expansion' };

// Build top-5 cycle-bottom table rows (safe: padded to exactly 5 entries)
const padded5 = [...topCycleBottom5];
while (padded5.length < 5) padded5.push({ sym: '', bottomProx: 0, regime: 1 });

const tableRow = (entry, idx) => {
  if (!entry.sym) return `    table.cell(tbl, 0, __IDX__, "", text_color=color.gray, text_size=size.small)
    table.cell(tbl, 1, __IDX__, "", text_color=color.gray, text_size=size.small)`.replace(/__IDX__/g, String(idx + 1));
  const regColor = entry.regime === 0 ? 'color.green' : 'color.orange';
  return `    table.cell(tbl, 0, __IDX__, "__SYM__", text_color=__COL__, text_size=size.small)
    table.cell(tbl, 1, __IDX__, "__BP__", text_color=__COL__, text_size=size.small)`
    .replace(/__IDX__/g, String(idx + 1))
    .replace('__SYM__', entry.sym)
    .replace('__BP__', (entry.bottomProx * 100).toFixed(0) + '%')
    .replace(/__COL__/g, regColor);
};

const tableRows = padded5.map((e, i) => tableRow(e, i)).join('\n');

// Build barcolor lookup — top 30 symbols embedded as Pine arrays
const symsArr   = top30.map(e => JSON.stringify(toTvSymbol(e.sym))).join(', ');
const regimeArr = top30.map(e => String(e.regime)).join(', ');
const bpArr     = top30.map(e => e.bottomProx.toFixed(4)).join(', ');

const pineCode = `// @version=5
// EGX Spectral Cycle Intelligence — Auto-Generated by Phase 24
// Generated: __DATE__  |  Feature date: __FEAT_DATE__
// Symbols: __N_SYMS__ total | cyclical=__N_CYC__ noisy=__N_NSY__ expansion=__N_EXP__ compression=__N_CMP__
//
// PURPOSE: Visualise cycle timing for EGX stocks.
//   - Blue line:   cycle_bottom_prox (0 = peak, 1 = trough)
//   - Bar color:   regime of CURRENT SYMBOL on feature_date
//   - Table:       top-5 cycle-bottom leaders (cyclical + prox > 60%)
//
// HOW TO READ:
//   Blue line near 1.0  →  stock is near cycle trough  →  potential reversal zone
//   Green bars          →  clear cyclical structure detected
//   Gray bars           →  noisy / no reliable cycle
//   Orange bars         →  expansion (structural shift)
//   Blue bars           →  compression (pre-breakout quiet)

indicator("EGX Spectral Cycle [Ph24]", overlay=false, max_bars_back=300)

// ── Embedded spectral data for top-30 symbols ────────────────────────────────
var string[] SYM_ARR    = array.from(array.new_string(0))
var int[]    REGIME_ARR = array.from(array.new_int(0))
var float[]  BP_ARR     = array.from(array.new_float(0))

// Initialise once on first bar
if barstate.isfirst
    // Symbols
    array.push(SYM_ARR, __SYMS_PLACEHOLDER__)
    // Regimes: 0=cyclical 1=noisy 2=compression 3=expansion
    array.push(REGIME_ARR, __REGIMES_PLACEHOLDER__)
    // Cycle bottom proximity (0-1)
    array.push(BP_ARR, __BP_PLACEHOLDER__)

// ── Look up current symbol ────────────────────────────────────────────────────
var float cycle_bottom_prox = na
var int   spectral_regime   = na
var string regime_name      = "unknown"

if barstate.islast
    cur_sym = syminfo.tickerid
    for i = 0 to array.size(SYM_ARR) - 1
        if array.get(SYM_ARR, i) == cur_sym
            cycle_bottom_prox := array.get(BP_ARR, i)
            spectral_regime   := array.get(REGIME_ARR, i)
            break

    if not na(spectral_regime)
        if   spectral_regime == 0
            regime_name := "cyclical"
        else if spectral_regime == 1
            regime_name := "noisy"
        else if spectral_regime == 2
            regime_name := "compression"
        else if spectral_regime == 3
            regime_name := "expansion"

// ── Oscillator plot ───────────────────────────────────────────────────────────
plot_val = barstate.islast and not na(cycle_bottom_prox) ? cycle_bottom_prox : na

plot(plot_val,  "Cycle Bottom Prox",
     color=color.new(color.blue, 20), linewidth=2, style=plot.style_line)
hline(0.70, "High Bottom", color=color.new(color.green, 60), linestyle=hline.style_dashed)
hline(0.50, "Mid",         color=color.new(color.gray,  70), linestyle=hline.style_dotted)
hline(0.30, "Low Bottom",  color=color.new(color.red,   60), linestyle=hline.style_dashed)

// ── Bar coloring by regime ────────────────────────────────────────────────────
bar_col = if not na(spectral_regime) and barstate.islast
    if   spectral_regime == 0
        color.new(color.green,  55)   // cyclical
    else if spectral_regime == 2
        color.new(color.blue,   55)   // compression
    else if spectral_regime == 3
        color.new(color.orange, 55)   // expansion
    else
        color.new(color.gray,   70)   // noisy
else
    na

bgcolor(bar_col, title="Spectral Regime BG")

// ── Info label at last bar ────────────────────────────────────────────────────
if barstate.islast and not na(cycle_bottom_prox)
    lbl_txt = "Regime: " + regime_name + "\\n" +
              "Bottom Prox: " + str.tostring(math.round(cycle_bottom_prox * 100)) + "%"
    label.new(bar_index, cycle_bottom_prox,
              text=lbl_txt, style=label.style_label_left,
              color=color.new(color.navy, 15), textcolor=color.white, size=size.small)

// ── Dashboard table: top-5 cycle-bottom leaders ───────────────────────────────
var table tbl = table.new(position.top_right, 2, 6,
    bgcolor=color.new(color.navy, 85), border_color=color.new(color.blue, 50), border_width=1)

if barstate.islast
    table.cell(tbl, 0, 0, "Symbol",    text_color=color.silver, text_size=size.normal, text_halign=text.align_left)
    table.cell(tbl, 1, 0, "Cyc Bot%",  text_color=color.silver, text_size=size.normal, text_halign=text.align_center)
__TABLE_ROWS__
`;

// Apply substitutions
const finalPine = pineCode
  .replace('__DATE__',      today)
  .replace('__FEAT_DATE__', featureDate)
  .replace('__N_SYMS__',    String(totalSyms))
  .replace('__N_CYC__',     String(totalCyclical))
  .replace('__N_NSY__',     String(totalNoisy))
  .replace('__N_EXP__',     String(totalExpansion))
  .replace('__N_CMP__',     String(totalCompression))
  .replace('__SYMS_PLACEHOLDER__',    symsArr)
  .replace('__REGIMES_PLACEHOLDER__', regimeArr)
  .replace('__BP_PLACEHOLDER__',      bpArr)
  .replace('__TABLE_ROWS__',          tableRows);

// ── 3. Save Pine file ─────────────────────────────────────────────────────────
const pinePath = join(PINE_DIR, 'egx_spectral_cycle.pine');
writeFileSync(pinePath, finalPine, 'utf8');
log(`✅ Pine Script saved: ${pinePath} (${finalPine.length} chars)`);

if (DRY_RUN || SAVE_ONLY) {
  log('  (dry-run / save-only) — skipping TradingView upload');
  process.exit(0);
}

// ── 4. Upload to TradingView via MCP ─────────────────────────────────────────
log('Connecting to TradingView (CDP port 9222)...');

let tvClient;
try {
  tvClient = await loadTVClient();
  log('  ✅ Connected to TradingView');
} catch (e) {
  log(`⚠️  TradingView not connected: ${e.message}`);
  log('    Pine saved locally — upload manually via TradingView Pine Editor');
  log('    File: ' + pinePath);
  process.exit(0);
}

try {
  // Open Pine editor panel
  log('  Opening Pine editor...');
  await tvClient.callTool('ui_open_panel', { panel: 'pine-editor', action: 'open' });
  await new Promise(r => setTimeout(r, 1500));

  // Create new indicator slot
  await tvClient.callTool('pine_new', { type: 'indicator' });
  await new Promise(r => setTimeout(r, 1000));

  // Inject the source
  log('  Injecting Pine source...');
  const setResult = await tvClient.callTool('pine_set_source', { source: finalPine });
  if (!setResult?.success) throw new Error('pine_set_source failed: ' + JSON.stringify(setResult));
  await new Promise(r => setTimeout(r, 800));

  // Compile
  log('  Compiling...');
  const compResult = await tvClient.callTool('pine_smart_compile', {});
  if (compResult?.errors?.length) {
    log(`⚠️  Compile errors: ${JSON.stringify(compResult.errors)}`);
  } else {
    log('  ✅ Compiled successfully');
  }

  // Save to TV cloud
  await tvClient.callTool('pine_save', {});
  log('  ✅ Saved to TradingView cloud');

  // Screenshot for confirmation
  const ss = await tvClient.callTool('capture_screenshot', { region: 'chart' }).catch(() => null);
  if (ss?.path) log(`  📸 Screenshot: ${ss.path}`);

  log('');
  log('═══ Phase 24 complete ═══');
  log(`  Spectral overlay loaded: ${topCycleBottom5.length} cycle-bottom leaders`);
  log(`  Feature date: ${featureDate}`);
  log(`  Add to chart: chart tab → Indicators → "EGX Spectral Cycle [Ph24]"`);

} catch (err) {
  log(`❌ TradingView upload failed: ${err.message}`);
  log(`   Pine saved locally: ${pinePath}`);
}
