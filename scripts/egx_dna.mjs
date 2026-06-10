#!/usr/bin/env node
/**
 * Phase 25 — Market DNA Engine runner
 *
 * Sections:
 *   build          — build stock DNA with relative percentile ranking
 *   mutations      — detect DNA archetype mutations
 *   clusters       — cluster DNA communities (k-means)
 *   profile        — full DNA profile for one stock  --ticker COMI
 *   sector         — refresh sector DNA archetypes
 *   full           — run all
 */
import { pythonDNABuild, pythonDNAMutations, pythonDNAClusters,
         pythonDNAProfile, pythonDNASectorRefresh, pythonDNABuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'full';
const ticker  = args[args.indexOf('--ticker') + 1] ?? 'COMI';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'build': {
    banner('DNA: Building stock DNA (percentile-ranked)…');
    const r = await pythonDNABuild({});
    if (r?.archetype_distribution) {
      console.log('\n📊 Archetype Distribution:');
      Object.entries(r.archetype_distribution).forEach(([k, v]) =>
        console.log(`   ${k.padEnd(30)} ${v}`));
    }
    pp(r); break;
  }
  case 'mutations': {
    banner('DNA: Detecting mutations…');
    const r = await pythonDNAMutations({});
    if (r?.mutations) {
      console.log(`   Mutations: ${r.mutations.length}`);
      r.mutations.slice(0, 10).forEach(m =>
        console.log(`   ${m.symbol?.padEnd(10)} ${m.from_archetype} → ${m.to_archetype}`));
    }
    pp(r); break;
  }
  case 'clusters': {
    banner('DNA: Clustering communities…');
    const r = await pythonDNAClusters({});
    pp(r); break;
  }
  case 'profile': {
    banner(`DNA: Profile for ${ticker}`);
    const r = await pythonDNAProfile({ symbol: ticker });
    pp(r); break;
  }
  case 'sector': {
    banner('DNA: Sector DNA refresh…');
    const r = await pythonDNASectorRefresh({});
    if (r?.sector_distribution) {
      console.log('\n📊 Sector Archetype Distribution:');
      Object.entries(r.sector_distribution).forEach(([k, v]) =>
        console.log(`   ${k.padEnd(20)} ${v}`));
    }
    pp(r); break;
  }
  case 'full': {
    banner('DNA: Full build pipeline');
    const r = await pythonDNABuildFull({});
    pp(r); break;
  }
  default: console.log(`Unknown: ${section}`); process.exit(1);
}
