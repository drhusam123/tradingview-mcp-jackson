#!/usr/bin/env node
/**
 * Phase 24 — Explosion Physics Engine runner
 *
 * Sections:
 *   readiness      — compute today's readiness scores for all symbols
 *   signatures     — analyze archetype signatures
 *   anatomy        — false explosion anatomy
 *   sector         — sector physics comparison  --sector Finance
 *   watchlist      — show today's explosion watchlist (top candidates)
 *   full           — run all
 */
import { pythonExplosionReadiness, pythonExplosionSignatures,
         pythonExplosionFalseAnatomy, pythonExplosionSectorPhysics,
         pythonExplosionWatchlist, pythonExplosionBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'watchlist';
const sector  = args[args.indexOf('--sector') + 1] ?? 'Finance';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  💥 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

switch (section) {
  case 'readiness': {
    banner('Explosion: Computing readiness scores…');
    const r = await pythonExplosionReadiness({});
    if (r?.top_candidates) {
      console.log(`\n🔥 Top Explosion Candidates (${r.date}):  Regime: ${r.regime}`);
      r.top_candidates.slice(0, 10).forEach(c =>
        console.log(`   ${String(c.symbol).padEnd(10)} Score: ${String(c.score ?? 0).padStart(5)}  Arch: ${String(c.archetype ?? '').padEnd(20)}  ${c.sector ?? ''}`));
      if (r.score_stats)
        console.log(`\n   Stats: max=${r.score_stats.max}  avg=${r.score_stats.avg}  above70=${r.score_stats.above_70}  above50=${r.score_stats.above_50}`);
    } else pp(r);
    break;
  }
  case 'signatures': {
    banner('Explosion: Archetype signatures');
    const r = await pythonExplosionSignatures({});
    if (r?.archetype_signatures) {
      r.archetype_signatures.forEach(s =>
        console.log(`   ${String(s.archetype_name).padEnd(25)} prec: ${s.precision}  fbr: ${s.false_positive_rate}  n: ${s.n_support}`));
    }
    pp(r); break;
  }
  case 'anatomy': {
    banner('Explosion: False explosion anatomy');
    const r = await pythonExplosionFalseAnatomy({});
    console.log(`   True: ${r.n_true}  False: ${r.n_false}  False rate: ${r.false_rate_pct}%`);
    pp(r); break;
  }
  case 'sector': {
    banner(`Explosion: Sector physics — ${sector}`);
    const r = await pythonExplosionSectorPhysics({ sector });
    pp(r); break;
  }
  case 'watchlist': {
    banner('Explosion: Daily Watchlist');
    const r = await pythonExplosionWatchlist({});
    if (r?.watchlist) {
      console.log(`\n🔥 Top Candidates (${r.date}):  Regime: ${r.regime}`);
      r.watchlist.slice(0, 15).forEach(c =>
        console.log(`   ${String(c.symbol).padEnd(10)} ${String(c.score ?? 0).padStart(5)}  ${String(c.archetype ?? '').padEnd(22)}  ${String(c.sector ?? '').padEnd(15)}  ${c.failure_mode}`));
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Explosion: Full build');
    const r = await pythonExplosionBuildFull({});
    pp(r); break;
  }
  default: console.log(`Unknown: ${section}`); process.exit(1);
}
