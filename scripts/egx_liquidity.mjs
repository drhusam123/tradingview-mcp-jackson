#!/usr/bin/env node
/**
 * Phase 52 — Liquidity Microstructure runner
 * "هل يمكن تنفيذ الصفقة؟ — Liquidity Gate"
 *
 * Sections: tiers | filter | report | build | full
 *   --symbol COMI --capital 100000
 */
import { pythonLiquiditySymbol, pythonLiquidityTiers, pythonLiquidityFilter,
         pythonLiquidityMaxSize, pythonLiquidityBuildProfiles, pythonLiquidityReport,
         pythonLiquidityBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'report';
const symbol  = args[args.indexOf('--symbol') + 1]  ?? 'COMI';
const capital = parseFloat(args[args.indexOf('--capital') + 1] ?? '100000');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💧 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const TIER_EMOJI  = { TIER1: '🟢', TIER2: '🟡', TIER3: '🟠', TIER4: '🔴', ILLIQUID: '⛔' };

switch (section) {
  case 'symbol': {
    banner(`Liquidity: Symbol Profile — ${symbol}`);
    const r = await pythonLiquiditySymbol({ symbol });
    if (r?.liquidity_tier) {
      const em = TIER_EMOJI[r.liquidity_tier] ?? '?';
      console.log(`\n   ${em} Tier: ${r.liquidity_tier}  |  Score: ${r.liquidity_score?.toFixed(1)}/100`);
      console.log(`   ADVT 10d: EGP ${(r.advt_10d_egp ?? r.advt_10d)?.toLocaleString()}`);
      console.log(`   ADVT 30d: EGP ${(r.advt_30d_egp ?? r.advt_30d)?.toLocaleString()}`);
      console.log(`   Amihud ratio: ${r.amihud_ratio?.toFixed(4)}`);
      console.log(`   Spread est: ${r.bid_ask_spread_est?.toFixed(1)} bps`);
      console.log(`   Max safe order: EGP ${r.max_safe_order_egp?.toLocaleString()}`);
    } else pp(r);
    break;
  }
  case 'tiers': {
    banner('Liquidity: Universe Tier Classification');
    const r = await pythonLiquidityTiers({});
    if (r?.tier_counts) {
      console.log('\n   Tier Distribution:');
      Object.entries(r.tier_counts ?? {}).forEach(([t, n]) => {
        const em = TIER_EMOJI[t] ?? '?';
        console.log(`   ${em} ${String(t).padEnd(10)} ${String(n).padStart(4)} symbols`);
      });
      console.log(`\n   ✅ Tradeable universe: ${r.liquid_universe?.length ?? 0} symbols (TIER1+TIER2)`);
      console.log(`   ⛔ Avoid list: ${r.avoid_list?.length ?? 0} symbols`);
      console.log(`   Coverage: ${r.coverage_pct?.toFixed(1)}%`);
    } else pp(r);
    break;
  }
  case 'filter': {
    banner('Liquidity: Tradeable Filter');
    const r = await pythonLiquidityFilter({ min_tier: 'TIER2' });
    if (r?.filtered_symbols?.length !== undefined) {
      console.log(`\n   ✅ Tradeable symbols (TIER1+TIER2): ${r.filtered_symbols?.length}`);
      if (r.filtered_symbols?.length) {
        console.log('\n   Symbol     Tier    ADVT 10d');
        r.filtered_symbols.slice(0, 20).forEach(s => {
          const advt = s.advt_10d_egp ?? s.advt_10d ?? 0;
          console.log(`   ${String(s.symbol).padEnd(10)} ${TIER_EMOJI[s.tier] ?? '?'} ${String(s.tier).padEnd(7)} EGP ${(advt / 1e6).toFixed(1)}M`);
        });
        if (r.filtered_symbols.length > 20) console.log(`   ... and ${r.filtered_symbols.length - 20} more`);
      }
    } else pp(r);
    break;
  }
  case 'sizing': {
    banner(`Liquidity: Max Position Size — ${symbol}`);
    const r = await pythonLiquidityMaxSize({ symbol, capital_egp: capital });
    if (r?.recommended_egp !== undefined) {
      const em = r.constraint_reason === 'LIQUIDITY' ? '💧' : r.constraint_reason === 'RISK' ? '⚠️' : '✅';
      console.log(`\n   ${em} Recommended: EGP ${r.recommended_egp?.toLocaleString()}`);
      console.log(`   Shares (est): ${r.recommended_shares_est?.toLocaleString()}`);
      console.log(`   Constraint: ${r.constraint_reason}`);
      console.log(`   Liquidity cap: EGP ${r.max_safe_order_egp?.toLocaleString()}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Liquidity: Full Universe Report');
    const r = await pythonLiquidityReport({});
    if (r?.market_liquidity_score !== undefined) {
      const trend = r.liquidity_trend;
      const trendDir  = trend?.direction ?? trend ?? '?';
      const trendPct  = trend?.trend_pct ?? r.advt_trend_pct;
      console.log(`\n   Market liquidity score: ${r.market_liquidity_score?.toFixed(1)}/100`);
      console.log(`   Trend: ${trendDir}  (ADVT change: ${trendPct != null ? trendPct.toFixed(1)+'%' : '?'})`);
      console.log(`   Tradeable: ${r.tradeable_count ?? '?'} symbols (${r.tradeable_pct?.toFixed(1) ?? '?'}%)`);
      const top20 = r.top_20 ?? r.top_20_liquid ?? [];
      if (top20?.length) {
        console.log('\n   🟢 Top 10 most liquid:');
        top20.slice(0, 10).forEach(s => {
          const a = s.advt_10d_egp ?? s.advt_10d ?? 0;
          console.log(`   ${String(s.symbol).padEnd(8)} ADVT: EGP ${(a/1e6).toFixed(1)}M`);
        });
      }
      const bot10 = r.bottom_10 ?? r.bottom_10_illiquid ?? [];
      if (bot10?.length) {
        console.log('\n   🔴 Bottom 10 (most illiquid):');
        bot10.forEach(s => {
          const a = s.advt_10d_egp ?? s.advt_10d ?? 0;
          const tier = s.tier ?? s.liquidity_tier ?? '?';
          console.log(`   ${String(s.symbol).padEnd(8)} ADVT: EGP ${(a/1e3).toFixed(0)}K  [${tier}]`);
        });
      }
    } else pp(r);
    break;
  }
  case 'build': {
    banner('Liquidity: Build All Profiles');
    const r = await pythonLiquidityBuildProfiles({});
    if (r?.n_computed !== undefined) {
      console.log(`\n   ✅ Computed: ${r.n_computed}  ⏭️ Skipped: ${r.n_skipped}`);
      if (r.tier_summary) {
        Object.entries(r.tier_summary).forEach(([t, n]) =>
          console.log(`   ${TIER_EMOJI[t] ?? '?'} ${t}: ${n}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Liquidity: Full Build + Report');
    const r = await pythonLiquidityBuildFull({});
    if (r?.market_liquidity_score !== undefined || r?.n_computed !== undefined) {
      console.log(`\n   Market score: ${r.market_liquidity_score?.toFixed(1)}/100`);
      console.log(`   Symbols computed: ${r.n_computed}`);
      console.log(`   Tradeable: ${r.n_tradeable}  |  Avoid: ${r.n_avoid}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
