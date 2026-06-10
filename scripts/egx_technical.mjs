#!/usr/bin/env node
/**
 * Phase 58 — Technical Confluence runner
 * "التقاطع التقني — RSI + MACD + EMA + Volume"
 *
 * Sections: score | batch | report | coverage | full
 *   --date 2026-05-15
 *   --symbol COMI
 *   --min-score 60
 */
import { pythonTechSaveIndicators, pythonTechScoreSymbol, pythonTechScoreBatch,
         pythonTechReport, pythonTechCoverage, pythonTechBuildFull }
  from '../src/egx/index.js';

const args     = process.argv.slice(2);
const section  = args.find(a => !a.startsWith('--')) ?? 'report';
const dateIdx  = args.indexOf('--date');
const date     = dateIdx  !== -1 ? args[dateIdx  + 1] : new Date().toISOString().split('T')[0];
const symIdx   = args.indexOf('--symbol');
const symbol   = symIdx   !== -1 ? args[symIdx   + 1] : null;
const msIdx    = args.indexOf('--min-score');
const minScore = msIdx    !== -1 ? parseFloat(args[msIdx + 1]) : 60;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  📐 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const SIG_EMOJI = {
  STRONG_BUY: '🟢🟢', BUY: '🟢', NEUTRAL: '⚖️', SELL: '🔴', STRONG_SELL: '🔴🔴',
};
const EMA_EMOJI = { BULLISH: '📈', BEARISH: '📉', NEUTRAL: '➡️', MIXED: '〰️' };

switch (section) {
  case 'score': {
    const sym = symbol ?? 'COMI';
    banner(`Technical Score — ${sym} @ ${date}`);
    const r = await pythonTechScoreSymbol({ symbol: sym, fetch_date: date });
    if (r?.tech_score !== undefined) {
      const em = SIG_EMOJI[r.tech_signal] ?? '⚖️';
      console.log(`\n   ${em} Signal: ${r.tech_signal}`);
      console.log(`   Tech Score: ${r.tech_score?.toFixed(1)}/100`);
      console.log(`   EMA Align:  ${EMA_EMOJI[r.ema_alignment] ?? '?'} ${r.ema_alignment}`);
      console.log(`\n   RSI(14):    ${r.rsi_14?.toFixed(1) ?? 'n/a'}`);
      console.log(`   MACD:       ${r.macd_value?.toFixed(3) ?? 'n/a'}  signal=${r.macd_signal_line?.toFixed(3) ?? 'n/a'}`);
      console.log(`   BB pos:     ${r.bb_position ?? 'n/a'}`);
      if (r.ema_20 && r.ema_50) {
        console.log(`   EMA 20/50/200: ${r.ema_20?.toFixed(2)} / ${r.ema_50?.toFixed(2)} / ${r.ema_200?.toFixed(2) ?? 'n/a'}`);
      }
      if (r.volume && r.volume_ma20) {
        const volRatio = r.volume / r.volume_ma20;
        console.log(`   Volume:     ${(volRatio * 100)?.toFixed(0)}% of 20-day avg`);
      }
    } else pp(r);
    break;
  }
  case 'batch': {
    banner(`Technical Score Batch — ${date}`);
    const r = await pythonTechScoreBatch({ fetch_date: date, min_scan_score: minScore });
    const scored = r?.scored ?? [];
    if (!scored.length) {
      console.log(`\n   No symbols scored for ${date}. Run egx:fetch:tech first.`);
      break;
    }
    console.log(`\n   ${scored.length} symbol(s) scored:\n`);
    console.log('   Symbol    Tech  Signal         EMA        RSI   MACD');
    console.log('   ' + '─'.repeat(65));
    scored.slice(0, 25).forEach(s => {
      const em = SIG_EMOJI[s.tech_signal] ?? '⚖️';
      const emaEm = EMA_EMOJI[s.ema_alignment] ?? '?';
      console.log(`   ${em} ${String(s.symbol).padEnd(8)} ${String(s.tech_score?.toFixed(1)).padStart(5)}  ${String(s.tech_signal ?? 'NEUTRAL').padEnd(12)} ${emaEm} ${String(s.ema_alignment ?? '?').padEnd(7)} ${String(s.rsi_14?.toFixed(1) ?? 'n/a').padStart(5)}  ${s.macd_value?.toFixed(3) ?? 'n/a'}`);
    });
    if (r?.avg_tech_score !== undefined) {
      console.log(`\n   Avg tech score: ${r.avg_tech_score?.toFixed(1)}`);
    }
    break;
  }
  case 'report': {
    banner(`Technical Confluence Report — ${date}`);
    const r = await pythonTechReport({ scan_date: date, min_score: minScore });
    if (r?.strongly_confirmed?.length || r?.contradicted?.length || r?.unscored?.length) {
      if (r.strongly_confirmed?.length) {
        console.log(`\n   🔥 Strongly Confirmed (${r.strongly_confirmed.length}):`);
        console.log('   Symbol    Scan  Tech  Combined  Signal');
        console.log('   ' + '─'.repeat(50));
        r.strongly_confirmed.slice(0, 10).forEach(p => {
          const em = SIG_EMOJI[p.tech_signal] ?? '⚖️';
          console.log(`   ${em} ${String(p.symbol).padEnd(8)} ${String(p.scan_score?.toFixed(0)).padStart(4)}  ${String(p.tech_score?.toFixed(0)).padStart(4)}  ${String(p.combined_score?.toFixed(1)).padStart(8)}  ${p.tech_signal ?? ''}`);
        });
      }
      if (r.contradicted?.length) {
        console.log(`\n   ⚠️  Contradicted (scan bullish, tech bearish) — ${r.contradicted.length}:`);
        r.contradicted.slice(0, 8).forEach(p =>
          console.log(`     ⚡ ${String(p.symbol).padEnd(8)} scan=${p.scan_score?.toFixed(0)} tech=${p.tech_score?.toFixed(0)}  ${p.tech_signal ?? ''}`));
      }
      if (r.confirmed?.length) {
        console.log(`\n   ✅ Confirmed (${r.confirmed.length}): ${r.confirmed.slice(0,12).map(p => p.symbol).join(', ')}`);
      }
      if (r.unscored?.length) {
        console.log(`\n   📭 Not yet scored: ${r.unscored.slice(0,10).join(', ')}  (run egx:fetch:tech)`);
      }
    } else pp(r);
    break;
  }
  case 'coverage': {
    banner(`Technical Coverage — ${date}`);
    const r = await pythonTechCoverage({ date });
    if (r?.success !== undefined) {
      const nCached  = r.n_cached ?? r.n_scored ?? 0;
      const nPicks   = r.n_scan_picks ?? 0;
      const pct      = nPicks > 0 ? (nCached / nPicks * 100) : (nCached > 0 ? 100 : 0);
      const oldest   = r.oldest_cache ?? r.earliest_fetch ?? 'n/a';
      const newest   = r.newest_cache ?? r.latest_fetch ?? 'n/a';
      const bar      = '█'.repeat(Math.round(pct / 5)) + '░'.repeat(20 - Math.round(pct / 5));
      console.log(`\n   Cached symbols:  ${nCached}`);
      if (nPicks > 0) {
        console.log(`   Scan picks:      ${nPicks}`);
        console.log(`   Coverage:        ${pct?.toFixed(1)}%`);
        console.log(`   [${bar}] ${pct?.toFixed(0)}%`);
      } else {
        console.log(`   (No scan picks for ${date} — run egx:scan first)`);
      }
      console.log(`   Cache range:     ${oldest} → ${newest}`);
      const missing = r.missing ?? [];
      if (missing.length) {
        console.log(`\n   Missing: ${missing.slice(0, 12).join(', ')}`);
        console.log('   Run: npm run egx:fetch:tech to fill gaps');
      } else if (nCached === 0) {
        console.log('\n   No cached indicators yet. Run: npm run egx:fetch:tech');
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner(`Technical Full Report — ${date}`);
    const r = await pythonTechBuildFull({ scan_date: date, min_score: minScore });
    if (r?.coverage !== undefined || r?.report !== undefined) {
      const cov = r.coverage ?? {};
      const rep = r.report ?? {};
      console.log(`\n   Coverage: ${cov.n_scored ?? 0}/${cov.n_scan_picks ?? 0} (${(cov.coverage_pct ?? 0)?.toFixed(1)}%)`);
      if (rep.strongly_confirmed?.length) {
        console.log(`\n   🔥 Top picks (scan+tech confirmed):`);
        rep.strongly_confirmed.slice(0, 8).forEach(p => {
          const em = SIG_EMOJI[p.tech_signal] ?? '⚖️';
          console.log(`     ${em} ${String(p.symbol).padEnd(8)} combined=${p.combined_score?.toFixed(1)}  scan=${p.scan_score?.toFixed(0)}  tech=${p.tech_score?.toFixed(0)}`);
        });
      }
      if (rep.contradicted?.length)
        console.log(`\n   ⚠️  Contradicted: ${rep.contradicted.map(p => p.symbol).join(', ')}`);
      if (cov.missing?.length)
        console.log(`\n   📭 Need tech data: ${cov.missing.slice(0,8).join(', ')}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: score|batch|report|coverage|full`); process.exit(1);
}
