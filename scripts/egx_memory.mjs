#!/usr/bin/env node
/**
 * Phase 30 — Episodic Market Memory Engine runner
 *
 * Sections:
 *   encode    — encode all historical windows as episodes
 *   similar   — find episodes similar to current period
 *   analogy   — human-readable analogy report
 *   episode   — retrieve one episode  --id 2022-05-01_2022-05-20
 *   full      — encode + similar + analogy
 */
import { pythonEpisodicEncode, pythonEpisodicFindSimilar, pythonEpisodicAnalogy,
         pythonEpisodicGetEpisode, pythonEpisodicBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'analogy';
const episodeId = args[args.indexOf('--id') + 1] ?? null;
const topK    = parseInt(args[args.indexOf('--top') + 1] ?? '5');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'encode': {
    banner('Memory: Encoding historical episodes…');
    const r = await pythonEpisodicEncode({});
    if (r?.n_episodes_encoded !== undefined) {
      console.log(`\n   Encoded: ${r.n_episodes_encoded} episodes`);
      console.log(`   Date range: ${r.date_range?.[0]} → ${r.date_range?.[1]}`);
      console.log(`   Avg outcome 7d:  ${(r.avg_outcome_7d * 100)?.toFixed(2)}%`);
      console.log(`   Avg outcome 30d: ${(r.avg_outcome_30d * 100)?.toFixed(2)}%`);
    } else pp(r);
    break;
  }
  case 'similar': {
    banner(`Memory: Finding ${topK} similar past episodes…`);
    const r = await pythonEpisodicFindSimilar({ top_k: topK });
    if (r?.similar_episodes) {
      console.log(`\n   Current fingerprint: [${r.current_fingerprint?.map(x => x.toFixed(2)).join(', ')}]`);
      console.log(`   Consensus outlook:   ${r.consensus_outlook}`);
      console.log(`   P(Bull/Bear/Side):   ${r.probability_bull?.toFixed(2)} / ${r.probability_bear?.toFixed(2)} / ${r.probability_sideways?.toFixed(2)}`);
      console.log('\n   Similar Episodes:');
      r.similar_episodes.forEach(e =>
        console.log(`   ${e.episode_id?.padEnd(30)} sim: ${e.similarity?.toFixed(3)}  7d: ${(e.outcome_7d*100)?.toFixed(1)}%  30d: ${(e.outcome_30d*100)?.toFixed(1)}%  [${e.outcome_label}]`));
    } else pp(r);
    break;
  }
  case 'analogy': {
    banner('Memory: Market Analogy Report');
    const r = await pythonEpisodicAnalogy({});
    if (r?.analogy) {
      console.log(`\n   📅 ${r.date}`);
      console.log(`   🔄 ${r.analogy}`);
      console.log(`   📜 ${r.historical_outcome}`);
      console.log(`   🎯 Confidence: ${r.confidence}`);
      if (r.current_fingerprint_description) {
        console.log('\n   Current Market Character:');
        Object.entries(r.current_fingerprint_description).forEach(([k, v]) =>
          console.log(`     ${k.padEnd(18)} ${v}`));
      }
      console.log(`\n   Forward Probabilities:`);
      console.log(`     Bull:    ${(r.forward_probability?.bull * 100)?.toFixed(0)}%`);
      console.log(`     Bear:    ${(r.forward_probability?.bear * 100)?.toFixed(0)}%`);
      console.log(`     Sideways:${(r.forward_probability?.sideways * 100)?.toFixed(0)}%`);
    } else pp(r);
    break;
  }
  case 'episode': {
    if (!episodeId) { console.log('Usage: node egx_memory.mjs episode --id 2022-05-01_2022-05-20'); process.exit(1); }
    banner(`Memory: Episode ${episodeId}`);
    const r = await pythonEpisodicGetEpisode({ episode_id: episodeId });
    pp(r);
    break;
  }
  case 'full': {
    banner('Memory: Full Build (encode + similar + analogy)');
    const r = await pythonEpisodicBuildFull({});
    if (r?.analogy?.analogy) {
      console.log(`\n   Encoded: ${r.encoding?.n_episodes_encoded} episodes`);
      console.log(`   Analogy: ${r.analogy.analogy}`);
      console.log(`   Outlook: ${r.similar?.consensus_outlook}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
