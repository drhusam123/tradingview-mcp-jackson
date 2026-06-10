#!/usr/bin/env node
/**
 * Phase 65 — Unified Evidence Score (UES) runner
 * "نقطة الدليل الموحدة — دمج كل طبقات التحليل في نقطة واحدة"
 *
 * Sections: score | all | daily | conviction | history | full
 *   --symbol COMI
 *   --date 2026-05-15
 *   --min-ues 55
 */
import { pythonSigScoreSymbol, pythonSigScoreAll, pythonSigDailySignals,
         pythonSigConviction, pythonSigHistory, pythonSigBuildFull,
         getDB }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'daily';
const symIdx  = args.indexOf('--symbol');
const symbol  = symIdx !== -1 ? args[symIdx + 1] : null;
const dateIdx = args.indexOf('--date');
const mIdx    = args.indexOf('--min-ues');
const minUES  = mIdx !== -1 ? parseFloat(args[mIdx + 1]) : 55;

function latestMarketDate() {
  try {
    const db = getDB();
    const row = db.prepare(`
      SELECT MAX(date(bar_time,'unixepoch')) AS d
      FROM ohlcv_history_execution
    `).get();
    if (row?.d) return row.d;
  } catch {}
  try {
    const db = getDB();
    const row = db.prepare(`
      SELECT MAX(date(bar_time,'unixepoch')) AS d
      FROM ohlcv_history
    `).get();
    if (row?.d) return row.d;
  } catch {}
  return new Date().toISOString().split('T')[0];
}

const date = dateIdx !== -1 ? args[dateIdx + 1] : latestMarketDate();

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  ⚡ ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const CONV_EMOJI = {
  HIGH_CONVICTION: '🔥🔥', MEDIUM_CONVICTION: '🟢', LOW_CONVICTION: '⚠️', REJECT: '❌',
};

function uesBar(ues) {
  const pct = Math.min(100, Math.max(0, ues));
  return '█'.repeat(Math.round(pct / 5)) + '░'.repeat(20 - Math.round(pct / 5));
}

switch (section) {
  case 'score': {
    if (!symbol) { console.log('Error: --symbol required for score section'); process.exit(1); }
    banner(`UES Score — ${symbol} @ ${date}`);
    const r = await pythonSigScoreSymbol({ symbol, date });
    // Python returns unified_score / conviction_tier (not ues / conviction)
    const ues       = r?.unified_score ?? r?.ues;
    const conviction= r?.conviction_tier ?? r?.conviction;
    if (ues !== undefined) {
      const em = CONV_EMOJI[conviction] ?? '⚖️';
      console.log(`\n   ${em} ${symbol}`);
      console.log(`\n   UES Score:   ${ues?.toFixed(1)}/100`);
      console.log(`   [${uesBar(ues)}] ${ues?.toFixed(0)}`);
      console.log(`   Conviction:  ${conviction}`);
      console.log(`   Regime:      ${r.active_regime ?? 'UNKNOWN'}  |  Breadth: ${r.breadth_signal ?? 'n/a'}`);
      if (r.n_confirming_laws !== undefined)
        console.log(`   Laws:        ${r.n_confirming_laws} confirming (${r.top_law ?? 'none'})`);

      console.log(`\n   Component breakdown (weight → score → contribution):`);
      const comps = r.components ?? {};
      // Core 6 weighted components (scores 0-100)
      const coreWeights = {
        explosion_ml: 0.25, breadth: 0.20, technical: 0.20,
        cross_market: 0.15, liquidity: 0.10, anti_law:  0.10,
      };
      Object.entries(coreWeights).forEach(([k, w]) => {
        const val = comps[k] ?? null;
        const pts = val != null ? (val * w).toFixed(1) : 'n/a';
        const raw = val != null ? val.toFixed(1) : 'n/a';
        console.log(`   ${String(k).padEnd(14)} ${String(Math.round(w*100)+'%').padStart(4)} × ${String(raw).padStart(5)} = ${String(pts).padStart(5)} pts`);
      });
      // Boost components (±5/±3 pts)
      const boosts = { law_confirm: '±5pt', alpha_grid: '±3pt' };
      Object.entries(boosts).forEach(([k, label]) => {
        const val = comps[k];
        if (val !== undefined) {
          const adj = ((val - 50) / 50 * (k === 'law_confirm' ? 5 : 3)).toFixed(2);
          console.log(`   ${String(k).padEnd(14)} ${label.padStart(4)}   ${String(val.toFixed(1)).padStart(5)} → ${adj > 0 ? '+' : ''}${adj} pts`);
        }
      });
      if (r.entry_price) console.log(`\n   Entry price: ${r.entry_price?.toFixed(2)} EGP  |  Max pos: ${r.max_position_egp?.toLocaleString() ?? 'n/a'} EGP`);
    } else pp(r);
    break;
  }
  case 'all': {
    banner(`Score All Symbols — ${date}`);
    console.log('\n   Scoring all symbols (may take 30s)...');
    const r = await pythonSigScoreAll({ date });
    if (r?.n_scored !== undefined || r?.scores?.length) {
      const scores = r.scores ?? [];
      console.log(`\n   Scored: ${r.n_scored ?? scores.length} symbols`);
      if (scores.length) {
        console.log('\n   Symbol    UES    Conviction         Expl%  Tech  Breadth');
        console.log('   ' + '─'.repeat(62));
        scores.slice(0, 20).forEach(s => {
          const em = CONV_EMOJI[s.conviction] ?? '⚖️';
          const expl = ((s.components?.explosion ?? s.explosion_prob ?? 0) * 100)?.toFixed(0);
          const tech = ((s.components?.technical ?? s.tech_score ?? 0) * 100)?.toFixed(0);
          const brd  = ((s.components?.breadth   ?? s.breadth_score ?? 0) * 100)?.toFixed(0);
          console.log(`   ${em} ${String(s.symbol).padEnd(8)} ${String(s.ues?.toFixed(1)).padStart(5)}  ${String(s.conviction ?? 'UNKNOWN').padEnd(18)} ${String(expl).padStart(4)}%  ${String(tech).padStart(3)}  ${String(brd).padStart(4)}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'daily': {
    banner(`Daily UES Signals — ${date}`);
    const r = await pythonSigDailySignals({ date, min_ues: minUES });
    const sigs = r?.signals ?? r?.daily_signals ?? [];
    if (sigs.length) {
      console.log(`\n   ${sigs.length} signal(s) with UES ≥ ${minUES}:\n`);
      const getConv = s => s.conviction ?? s.conviction_tier ?? 'UNKNOWN';
      const getUES  = s => s.ues ?? s.unified_score ?? 0;
      const high = sigs.filter(s => getConv(s) === 'HIGH_CONVICTION');
      const med  = sigs.filter(s => getConv(s) === 'MEDIUM_CONVICTION');
      const low  = sigs.filter(s => getConv(s) === 'LOW_CONVICTION');

      if (high.length) {
        console.log(`   🔥🔥 HIGH CONVICTION (${high.length}):`);
        console.log('   Symbol    UES    Expl  Tech  Liq');
        console.log('   ' + '─'.repeat(45));
        high.forEach(s => {
          const c   = s.components ?? {};
          const ues = getUES(s);
          const expl = c.explosion?.toFixed(0) ?? '50';
          const tech = c.technical?.toFixed(0) ?? '50';
          const liq  = c.liquidity?.toFixed(0)  ?? '40';
          console.log(`   🔥 ${String(s.symbol).padEnd(8)} ${String(ues?.toFixed(1)).padStart(5)}  ${String(expl).padStart(4)}  ${String(tech).padStart(4)}  ${String(liq).padStart(4)}`);
        });
      }
      if (med.length) {
        console.log(`\n   🟢 MEDIUM CONVICTION (${med.length}):`);
        med.slice(0, 8).forEach(s =>
          console.log(`     🟢 ${String(s.symbol).padEnd(8)} UES=${getUES(s)?.toFixed(1)}`));
      }
      if (low.length)
        console.log(`\n   ⚠️  LOW CONVICTION: ${low.slice(0,8).map(s=>s.symbol).join(', ')}`);

      const avg = sigs.reduce((a,s) => a + getUES(s), 0) / sigs.length;
      console.log(`\n   Avg UES: ${avg?.toFixed(1)}  |  High: ${high.length}  Med: ${med.length}  Low: ${low.length}`);
    } else {
      console.log(`\n   No signals ≥ UES ${minUES} for ${date}.`);
      console.log('   Run: npm run egx:signal:all  to score all symbols first');
    }
    break;
  }
  case 'conviction': {
    banner(`High-Conviction Filter — ${date}`);
    const r = await pythonSigConviction({ date, min_conviction: 'HIGH_CONVICTION' });
    const sigs = r?.signals ?? r?.high_conviction ?? [];
    if (sigs.length) {
      console.log(`\n   🔥 ${sigs.length} HIGH_CONVICTION signal(s):\n`);
      sigs.forEach((s, i) => {
        const c = s.components ?? {};
        console.log(`   ${i+1}. 🔥🔥 ${s.symbol}  UES=${s.ues?.toFixed(1)}`);
        console.log(`      explosion=${((c.explosion??0)*100)?.toFixed(0)}%  tech=${((c.technical??0)*100)?.toFixed(0)}%  breadth=${((c.breadth??0)*100)?.toFixed(0)}%  cross=${((c.cross_market??0)*100)?.toFixed(0)}%`);
      });
    } else {
      console.log('\n   No HIGH_CONVICTION signals today.');
      console.log(`   Lower threshold: npm run egx:signal:daily -- --min-ues 45`);
    }
    break;
  }
  case 'history': {
    banner('UES Signal History');
    const r = await pythonSigHistory({ last_n_days: 14 });
    const hist = r?.history ?? r?.days ?? [];
    if (hist.length) {
      console.log(`\n   ${hist.length} day(s) of signal history:\n`);
      console.log('   Date        Signals  High  Med  Avg UES');
      console.log('   ' + '─'.repeat(45));
      hist.slice(0, 14).forEach(d => {
        const em = (d.n_high ?? 0) >= 3 ? '🔥' : (d.n_high ?? 0) >= 1 ? '🟢' : '⚪';
        console.log(`   ${em} ${d.date ?? d.signal_date}  ${String(d.n_total ?? d.n_signals ?? 0).padStart(6)}   ${String(d.n_high ?? 0).padStart(4)}  ${String(d.n_med ?? d.n_medium ?? 0).padStart(3)}  ${d.avg_ues?.toFixed(1) ?? 'n/a'}`);
      });
    } else {
      console.log('\n   No signal history. Run egx:signal:all to generate signals.');
    }
    break;
  }
  case 'full': {
    banner(`UES Full Report — ${date}`);
    const r = await pythonSigBuildFull({ date, min_ues: minUES });
    if (r?.scored !== undefined || r?.top_signals !== undefined || r?.actionable !== undefined) {
      console.log(`\n   Symbols scored:  ${r.scored ?? r.n_scored ?? '?'}`);
      console.log(`   High conviction: ${r.high_conviction ?? r.n_high_conviction ?? 0}`);
      console.log(`   Medium:          ${r.medium_conviction ?? r.n_medium_conviction ?? 0}`);
      console.log(`   Avg UES:         ${r.avg_ues?.toFixed(1) ?? 'n/a'}`);
      const top = r.top_signals ?? r.high_conviction ?? [];
      if (top.length) {
        console.log(`\n   🔥 Top picks today:`);
        top.slice(0, 8).forEach(s => {
          const ues  = s.ues ?? s.unified_score;
          const conv = s.conviction ?? s.conviction_tier ?? 'UNKNOWN';
          const em   = CONV_EMOJI[conv] ?? '⚖️';
          console.log(`     ${em} ${String(s.symbol).padEnd(8)} UES=${ues?.toFixed(1) ?? 'n/a'}  ${conv}`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: score|all|daily|conviction|history|full`); process.exit(1);
}
