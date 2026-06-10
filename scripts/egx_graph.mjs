/**
 * EGX Phase 17 — Graph Contagion Engine (standalone runner)
 * ===========================================================
 * Builds & analyzes the stock relationship graph using NetworkX.
 * Detects contagion paths, influential hubs, and community clusters.
 *
 * Usage:
 *   node scripts/egx_graph.mjs                        # full analysis
 *   node scripts/egx_graph.mjs --section build        # build network only
 *   node scripts/egx_graph.mjs --section pagerank      # PageRank hubs
 *   node scripts/egx_graph.mjs --section communities   # community detection
 *   node scripts/egx_graph.mjs --section contagion     # contagion paths
 *   node scripts/egx_graph.mjs --section cascade       # cascade simulation
 *   node scripts/egx_graph.mjs --section centrality    # centrality analysis
 *   node scripts/egx_graph.mjs --section spillover     # momentum spillover
 *   node scripts/egx_graph.mjs --section full          # complete analysis
 */

import {
  pythonGraphBuild, pythonGraphPagerank, pythonGraphCommunity,
  pythonGraphContagion, pythonGraphCascade, pythonGraphCentrality,
  pythonGraphSpillover, pythonGraphFull,
} from '../src/egx/index.js';

const SECTION = (() => {
  const i = process.argv.indexOf('--section');
  return i !== -1 ? process.argv[i + 1] : 'full';
})();

const wl  = (s = '') => process.stdout.write(s + '\n');
const sep = (n = 65)  => wl('═'.repeat(n));

sep();
wl('  🕸️  EGX GRAPH CONTAGION ENGINE (Phase 17)');
wl(`  ${new Date().toISOString()} | section: ${SECTION}`);
sep();
wl('');

const t0 = Date.now();

async function run() {
  let result;

  switch (SECTION) {
    case 'build':
      wl('  🔨 Building contagion network...');
      result = await pythonGraphBuild({ lookback_days: 252, min_correlation: 0.3 });
      break;
    case 'pagerank':
      wl('  📊 Computing PageRank influence scores...');
      result = await pythonGraphPagerank({ top_n: 20 });
      break;
    case 'communities':
      wl('  🏘️  Detecting market communities...');
      result = await pythonGraphCommunity({});
      break;
    case 'contagion':
      wl('  🦠 Analyzing contagion paths...');
      result = await pythonGraphContagion({ top_n: 10 });
      break;
    case 'cascade':
      wl('  ⛰️  Simulating cascade scenarios...');
      result = await pythonGraphCascade({ shock_pct: 0.1 });
      break;
    case 'centrality':
      wl('  🎯 Computing centrality metrics...');
      result = await pythonGraphCentrality({ top_n: 20 });
      break;
    case 'spillover':
      wl('  🌊 Analyzing momentum spillover...');
      result = await pythonGraphSpillover({ lookback_days: 20, lag_days: 3 });
      break;
    case 'full':
    default:
      wl('  🔬 Full graph analysis — all dimensions');
      wl('  (Estimated: 30–90 seconds)\n');
      result = await pythonGraphFull({ lookback_days: 252, min_correlation: 0.3 });
      break;
  }

  if (!result || result.error) {
    wl(`  ❌ Graph engine error: ${result?.error ?? 'no result returned'}`);
    process.exit(1);
  }

  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  // ── Display results ─────────────────────────────────────────────────────────
  switch (SECTION) {
    case 'build': {
      wl(`  ✅ Network built: ${elapsed}s`);
      wl(`  📌 Nodes: ${result.n_nodes ?? '?'} stocks`);
      wl(`  🔗 Edges: ${result.n_edges ?? '?'} connections`);
      wl(`  📈 Avg degree: ${result.avg_degree?.toFixed(2) ?? '?'}`);
      wl(`  🔎 Components: ${result.n_components ?? '?'}`);
      break;
    }
    case 'pagerank': {
      wl(`  ✅ PageRank complete: ${elapsed}s`);
      const hubs = result.top_hubs ?? [];
      wl(`  🏆 Top influential stocks:`);
      for (const h of hubs.slice(0, 10))
        wl(`    ${h.rank?.toString().padStart(2)}. ${(h.ticker ?? '?').padEnd(10)} PR=${h.pagerank?.toFixed(5)}  deg=${h.degree}`);
      break;
    }
    case 'communities': {
      wl(`  ✅ Communities detected: ${elapsed}s`);
      const comms = result.communities ?? [];
      wl(`  🏘️  ${comms.length} communities found:`);
      for (const c of comms.slice(0, 8))
        wl(`    • Community ${c.community_id}: ${c.size} stocks | hub=${c.hub_ticker ?? '?'}`);
      break;
    }
    case 'contagion': {
      wl(`  ✅ Contagion analysis: ${elapsed}s`);
      const paths = result.top_paths ?? [];
      wl(`  🦠 Top contagion paths:`);
      for (const p of paths.slice(0, 8))
        wl(`    ${p.source} → ${p.target}  strength=${p.strength?.toFixed(3)}  lag=${p.lag_days}d`);
      break;
    }
    case 'cascade': {
      wl(`  ✅ Cascade simulation: ${elapsed}s`);
      wl(`  🎯 Shock origin: ${result.shock_origin ?? '?'}`);
      wl(`  💥 Stocks affected: ${result.stocks_affected ?? '?'}`);
      wl(`  📉 Avg cascade loss: ${result.avg_cascade_loss?.toFixed(3) ?? '?'}`);
      wl(`  🌊 Cascade depth: ${result.cascade_depth ?? '?'} layers`);
      break;
    }
    case 'centrality': {
      wl(`  ✅ Centrality analysis: ${elapsed}s`);
      const central = result.top_central ?? [];
      wl(`  🎯 Top central stocks:`);
      for (const c of central.slice(0, 10))
        wl(`    ${(c.ticker ?? '?').padEnd(10)} betw=${c.betweenness?.toFixed(5)}  close=${c.closeness?.toFixed(4)}`);
      break;
    }
    case 'spillover': {
      wl(`  ✅ Spillover analysis: ${elapsed}s`);
      const spill = result.top_spillover ?? [];
      wl(`  🌊 Top momentum spillover pairs:`);
      for (const s of spill.slice(0, 8))
        wl(`    ${s.leader} → ${s.follower}  corr=${s.lag_correlation?.toFixed(3)}  lag=${s.lag_days}d`);
      break;
    }
    default: {
      wl(`  ✅ Full graph analysis: ${elapsed}s`);
      wl(`  📌 Network: ${result.network?.n_nodes ?? '?'} nodes | ${result.network?.n_edges ?? '?'} edges`);
      wl(`  🏘️  Communities: ${(result.communities ?? []).length} detected`);
      const hubs = result.pagerank?.top_hubs ?? [];
      if (hubs.length) {
        wl(`  🏆 Top hubs: ${hubs.slice(0, 5).map(h => h.ticker).join(', ')}`);
      }
      const comms = result.communities ?? [];
      if (comms.length) {
        wl(`  📊 Largest community: ${comms[0]?.size ?? '?'} stocks (hub: ${comms[0]?.hub_ticker ?? '?'})`);
      }
      const spill = result.spillover?.top_spillover ?? [];
      if (spill.length) {
        wl(`  🌊 Strongest spillover: ${spill[0]?.leader} → ${spill[0]?.follower} (r=${spill[0]?.lag_correlation?.toFixed(3)})`);
      }
      break;
    }
  }
}

await run().catch(e => {
  wl(`  ❌ Fatal error: ${e.message}`);
  process.exit(1);
});

wl('');
sep();
wl('  ✅ Phase 17 Graph Contagion Engine complete');
sep();
