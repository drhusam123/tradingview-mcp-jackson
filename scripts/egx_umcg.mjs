#!/usr/bin/env node
/**
 * Phase 21 — Unified Market Cognition Graph (UMCG) runner
 *
 * Usage:
 *   node scripts/egx_umcg.mjs [section] [options]
 *
 * Sections:
 *   build          — build full UMCG from all data sources
 *   metrics        — compute PageRank + betweenness + eigenvector
 *   communities    — detect Louvain communities
 *   fragility      — find fragility hubs and structural bridges
 *   snapshot       — weekly snapshot (metrics + communities + fragility)
 *   paths          — query paths between nodes  --from Banking --to Finance
 *   status         — show latest snapshot
 *   full           — build + metrics + communities + fragility + snapshot
 */
import { pythonUMCGBuild, pythonUMCGMetrics, pythonUMCGCommunities,
         pythonUMCGFragility, pythonUMCGSnapshot, pythonUMCGPaths,
         pythonUMCGGetSnapshot } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'status';
const fromNode = args[args.indexOf('--from') + 1];
const toNode   = args[args.indexOf('--to')   + 1];

function banner(text) {
  console.log('\n' + '═'.repeat(60));
  console.log(`  🕸️  ${text}`);
  console.log('═'.repeat(60));
}
function pp(obj) { console.log(JSON.stringify(obj, null, 2)); }

async function runBuild() {
  banner('UMCG: Building full graph…');
  const r = await pythonUMCGBuild({});
  if (r?.n_nodes !== undefined) {
    console.log(`   Nodes: ${r.n_nodes}  Edges: ${r.n_edges}`);
    console.log(`   Node types: ${JSON.stringify(r.node_type_dist)}`);
    console.log(`   Edge types: ${JSON.stringify(r.edge_type_dist)}`);
  } else pp(r);
}

async function runMetrics() {
  banner('UMCG: Computing centrality metrics…');
  const r = await pythonUMCGMetrics({});
  if (r?.top_pagerank) {
    console.log('\n🔵 Top 10 by PageRank:');
    r.top_pagerank.forEach(n =>
      console.log(`   ${String(n.name ?? n.node_id).padEnd(25)} [${n.node_type?.padEnd(10)}] PR: ${(n.pagerank ?? 0).toFixed(5)}`));
  }
  pp(r);
}

async function runCommunities() {
  banner('UMCG: Detecting communities…');
  const r = await pythonUMCGCommunities({});
  if (r?.n_communities) console.log(`   Communities detected: ${r.n_communities}  Modularity: ${r.modularity}`);
  pp(r);
}

async function runFragility() {
  banner('UMCG: Finding fragility hubs…');
  const r = await pythonUMCGFragility({});
  if (r?.fragility_hubs) {
    console.log('\n⚠️  Fragility Hubs:');
    r.fragility_hubs.forEach(n =>
      console.log(`   ${String(n.name ?? n.node_id).padEnd(25)} [${n.node_type}] PR: ${(n.pagerank ?? 0).toFixed(5)}`));
  }
  pp(r);
}

async function runSnapshot() {
  banner('UMCG: Weekly snapshot…');
  const r = await pythonUMCGSnapshot({});
  pp(r);
}

async function runPaths(from, to) {
  banner(`UMCG: Paths from "${from}" to "${to}"…`);
  const r = await pythonUMCGPaths({ source_name: from, target_name: to });
  if (r?.paths) {
    console.log(`   Found ${r.paths.length} path(s)`);
    r.paths.slice(0, 3).forEach((p, i) =>
      console.log(`   Path ${i+1}: ${p.path?.join(' → ')}`));
  }
  pp(r);
}

async function runStatus() {
  banner('UMCG: Latest snapshot');
  const r = await pythonUMCGGetSnapshot({});
  if (r?.n_nodes !== undefined) {
    console.log(`   Nodes: ${r.n_nodes}  Edges: ${r.n_edges}`);
  }
  pp(r);
}

async function runFull() {
  await runBuild();
  await runMetrics();
  await runCommunities();
  await runFragility();
  await runSnapshot();
}

switch (section) {
  case 'build':       await runBuild(); break;
  case 'metrics':     await runMetrics(); break;
  case 'communities': await runCommunities(); break;
  case 'fragility':   await runFragility(); break;
  case 'snapshot':    await runSnapshot(); break;
  case 'paths':       await runPaths(fromNode ?? 'Banking', toNode ?? 'Finance'); break;
  case 'status':      await runStatus(); break;
  case 'full':        await runFull(); break;
  default:
    console.log(`Unknown section: ${section}`);
    process.exit(1);
}
