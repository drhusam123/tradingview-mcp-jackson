#!/usr/bin/env node
/**
 * Phase 60 — Chart Visualizer runner
 * "رسم المستويات + لقطات الشاشة"
 *
 * Sections: draws | draw | screenshots | report | summary | full
 *   --date 2026-05-15
 *   --symbol COMI
 *   --n 8
 *   --min-score 65
 */
import { pythonVizGetDrawSpecs, pythonVizGetTopPicksDraws, pythonVizLogScreenshot,
         pythonVizFinalizeReport, pythonVizListScreenshots, pythonVizReportSummary,
         pythonVizBuildFull }
  from '../src/egx/index.js';

const args     = process.argv.slice(2);
const section  = args.find(a => !a.startsWith('--')) ?? 'summary';
const dateIdx  = args.indexOf('--date');
const date     = dateIdx  !== -1 ? args[dateIdx  + 1] : new Date().toISOString().split('T')[0];
const symIdx   = args.indexOf('--symbol');
const symbol   = symIdx   !== -1 ? args[symIdx   + 1] : null;
const nIdx     = args.indexOf('--n');
const n        = nIdx     !== -1 ? parseInt(args[nIdx + 1]) : 8;
const msIdx    = args.indexOf('--min-score');
const minScore = msIdx    !== -1 ? parseFloat(args[msIdx + 1]) : 65;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🎨 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const DRAW_EMOJI = {
  rectangle:       '▭',
  horizontal_line: '─',
  trend_line:      '╱',
  text:            '🏷️',
};

switch (section) {
  case 'draws': {
    banner(`Draw Specs — ${date} (top ${n}, min_score=${minScore})`);
    const r = await pythonVizGetTopPicksDraws({ scan_date: date, n, min_score: minScore });
    const picks = r?.picks ?? [];
    if (!picks.length) {
      console.log(`\n   No picks to draw for ${date}. Run egx:scan first.`);
      break;
    }
    console.log(`\n   ${r.n_picks ?? picks.length} pick(s) ready to draw:\n`);
    picks.slice(0, 12).forEach(p => {
      const ds = p.draw_specs;
      const setup = ds?.setup_type ?? '?';
      const score = ds?.scan_score?.toFixed(0) ?? '?';
      console.log(`\n   📊 ${p.symbol}  (score=${score}, ${setup})`);
      (ds?.draws ?? []).forEach(d => {
        const em = DRAW_EMOJI[d.type] ?? '·';
        const price = d.price != null ? `@ ${d.price?.toFixed(2)}` : `${d.price_low?.toFixed(2)} – ${d.price_high?.toFixed(2)}`;
        console.log(`      ${em}  ${String(d.label).padEnd(18)} ${price}  ${d.color ?? ''}`);
      });
    });
    if (r?.n_picks > 12) console.log(`\n   ... and ${r.n_picks - 12} more picks`);
    break;
  }
  case 'draw': {
    if (!symbol) { console.log('Error: --symbol required for draw section'); process.exit(1); }
    banner(`Draw Specs — ${symbol} @ ${date}`);
    const r = await pythonVizGetDrawSpecs({ symbol, scan_date: date });
    if (r?.draws?.length) {
      console.log(`\n   ${symbol}  score=${r.scan_score?.toFixed(0)}  setup=${r.setup_type}`);
      console.log('\n   Drawings:\n');
      r.draws.forEach(d => {
        const em = DRAW_EMOJI[d.type] ?? '·';
        const price = d.price != null ? `@ ${d.price?.toFixed(2)}` : `${d.price_low?.toFixed(2)} – ${d.price_high?.toFixed(2)}`;
        console.log(`   ${em}  ${String(d.label).padEnd(20)} ${price}`);
        console.log(`      color: ${d.color ?? 'default'}  type: ${d.type}`);
      });
    } else {
      console.log(`\n   No draw specs for ${symbol} on ${date}. Check scan results.`);
    }
    break;
  }
  case 'screenshots': {
    banner('Screenshots List');
    const r = await pythonVizListScreenshots({ limit: 30 });
    const shots = r?.screenshots ?? [];
    if (!shots.length) {
      console.log('\n   No screenshots in log yet.');
      console.log('   Run: npm run egx:fetch:drawings to capture charts.');
      break;
    }
    console.log(`\n   ${shots.length} screenshot(s):\n`);
    console.log('   Symbol    Date        Score  Setup          Path');
    console.log('   ' + '─'.repeat(70));
    shots.slice(0, 25).forEach(s => {
      const shortPath = s.screenshot_path?.replace('/Users/dr.husam/tradingview-mcp-jackson/', '');
      console.log(`   📸 ${String(s.symbol).padEnd(8)} ${s.report_date ?? '?'}  ${String(s.scan_score?.toFixed(0) ?? '?').padStart(5)}  ${String(s.setup_type ?? '?').padEnd(14)} ${shortPath ?? '?'}`);
    });
    break;
  }
  case 'report': {
    banner(`Visual Report — ${date}`);
    const r = await pythonVizFinalizeReport({ report_date: date });
    if (r?.n_screenshots !== undefined) {
      console.log(`\n   Date:         ${r.report_date ?? date}`);
      console.log(`   Screenshots:  ${r.n_screenshots}`);
      console.log(`   Top picks:    ${(r.top_picks ?? []).join(', ')}`);
      if (r.avg_score) console.log(`   Avg score:    ${r.avg_score?.toFixed(1)}`);
      if (r.setups) {
        console.log('\n   Setups breakdown:');
        Object.entries(r.setups).forEach(([s, n]) => console.log(`     ${s}: ${n}`));
      }
    } else pp(r);
    break;
  }
  case 'summary': {
    banner('Visual Reports Summary');
    const r = await pythonVizReportSummary({ last_n_days: 14 });
    const reports = r?.reports ?? r?.recent_reports ?? [];
    if (r?.total_reports !== undefined || reports.length >= 0) {
      const total = r.total_reports ?? reports.length;
      const shots = r.total_screenshots ?? reports.reduce((s, rep) => s + (rep.n_screenshots ?? 0), 0);
      console.log(`\n   Total reports (${r.days ?? 14}d): ${total}`);
      console.log(`   Total screenshots:    ${shots}`);
      if (total > 0) {
        const avg = r.avg_screenshots_per_day ?? (shots / (r.days ?? 14));
        console.log(`   Avg per day:          ${avg?.toFixed(1)}`);
      }
      if (reports.length) {
        console.log('\n   Recent reports:');
        console.log('   Date        Screenshots  Top Picks');
        console.log('   ' + '─'.repeat(50));
        reports.slice(0, 10).forEach(rep => {
          console.log(`   ${rep.report_date}  ${String(rep.n_screenshots ?? 0).padStart(10)}   ${(rep.top_picks ?? []).slice(0, 5).join(', ')}`);
        });
      } else {
        console.log('\n   No visual reports yet. Run: npm run egx:fetch:drawings');
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Visualizer Full — ${date}`);
    const r = await pythonVizBuildFull({ scan_date: date, n, min_score: minScore });
    if (r?.n_picks !== undefined || r?.report !== undefined) {
      console.log(`\n   Picks to draw:  ${r.n_picks ?? 0}`);
      const rep = r.report ?? {};
      console.log(`   Screenshots:    ${rep.n_screenshots ?? 0}`);
      if (r.picks?.length) {
        console.log('\n   Draw queue:');
        r.picks.slice(0, 8).forEach(p => {
          const ds = p.draw_specs;
          console.log(`     📊 ${String(p.symbol).padEnd(8)} score=${ds?.scan_score?.toFixed(0) ?? '?'}  draws=${ds?.draws?.length ?? 0}  setup=${ds?.setup_type ?? '?'}`);
        });
      }
      console.log('\n   Run: npm run egx:fetch:drawings to draw & screenshot in TradingView');
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: draws|draw|screenshots|report|summary|full`); process.exit(1);
}
