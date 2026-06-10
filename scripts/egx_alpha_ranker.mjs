#!/usr/bin/env node
/**
 * Phase 70 — Alpha Ranker runner
 * "مُصنِّف الألفا — يرقّي الفائز، يقتل الفاشل، يراقب الاضمحلال"
 *
 * Sections: rank | kill | decay | leaderboard | evolve | full
 *   --apply (makes kill live)
 *   --limit 20
 */
import { pythonAlphaRankAll, pythonAlphaKill, pythonAlphaDecay,
         pythonAlphaLeader, pythonAlphaEvolve, pythonAlphaBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'leaderboard';
const apply   = args.includes('--apply');
const limIdx  = args.indexOf('--limit');
const limit   = limIdx !== -1 ? parseInt(args[limIdx + 1]) : 20;

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🏆 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const GRADE_EMOJI = { S: '🏆', A: '⭐', B: '✅', C: '⚠️', D: '🔶', F: '❌' };

function scoreBar(score) {
  const n = Math.round(Math.min(100, Math.max(0, score)) / 5);
  return '█'.repeat(n) + '░'.repeat(20 - n);
}

switch (section) {
  case 'rank': {
    banner('Rank All Strategies');
    const r = await pythonAlphaRankAll({});
    if (r?.n_ranked !== undefined) {
      console.log(`\n   Ranked: ${r.n_ranked} active strategies`);
      console.log(`   Avg composite score: ${r.avg_composite?.toFixed(1) ?? 'n/a'}`);
      const gd = r.grade_distribution ?? {};
      console.log(`\n   Grade distribution:`);
      ['S','A','B','C','D','F'].forEach(g => {
        if (gd[g]) console.log(`     ${GRADE_EMOJI[g]} Grade ${g}: ${gd[g]}`);
      });
      if (r.top_10?.length) {
        console.log(`\n   Top 10 ranked strategies:\n`);
        console.log('   Grade  Composite  Hyp ID                  Exp%    WinR%  OOS    Acts');
        console.log('   ' + '─'.repeat(78));
        r.top_10.forEach(s => {
          const em = GRADE_EMOJI[s.grade] ?? '?';
          console.log(`   ${em} ${s.grade}     ${String(s.composite_score?.toFixed(1)).padStart(8)}  ${String(s.hyp_id).padEnd(24)} ${String(s.expectancy_pct?.toFixed(3)+'%').padStart(7)} ${String(s.win_rate_pct?.toFixed(1)+'%').padStart(6)}  ${String(s.oos_score?.toFixed(3) ?? 'n/a').padStart(6)}  ${s.n_activations}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'kill': {
    banner(`Kill Weak Strategies${apply ? ' [LIVE]' : ' [DRY RUN]'}`);
    const r = await pythonAlphaKill({ dry_run: !apply });
    if (r?.n_killed !== undefined) {
      console.log(`\n   ${apply ? '✂️  LIVE' : '🔍 DRY RUN'}: ${r.n_killed} strategies to kill`);
      if (r.killed?.length) {
        console.log('\n   Candidates:');
        r.killed.slice(0, 15).forEach(k =>
          console.log(`     ❌ ${String(k.hyp_id).padEnd(26)} ${(k.reasons ?? []).join(' | ')}`));
        if (!apply)
          console.log('\n   Add --apply to execute removal');
      }
    } else pp(r);
    break;
  }
  case 'decay': {
    banner('Alpha Decay Check');
    const r = await pythonAlphaDecay({});
    if (r?.n_checked !== undefined) {
      console.log(`\n   Strategies checked: ${r.n_checked}`);
      console.log(`   Stable:    ${r.n_stable} ✅`);
      console.log(`   Decaying:  ${r.n_decaying} ⚠️`);
      if (r.decaying?.length) {
        console.log('\n   ⚠️  Strategies with alpha decay (IS → OOS drop):');
        console.log('   Hyp ID                  IS%    OOS%   Decay%');
        console.log('   ' + '─'.repeat(55));
        r.decaying.slice(0, 10).forEach(d =>
          console.log(`   📉 ${String(d.hyp_id).padEnd(24)} ${String((d.is_precision*100)?.toFixed(1)+'%').padStart(5)} → ${String((d.oos_precision*100)?.toFixed(1)+'%').padStart(5)}  -${d.decay_pct?.toFixed(1)}%`));
      }
      if (r.stable_top5?.length) {
        console.log('\n   ✅ Most stable strategies:');
        r.stable_top5.forEach(s =>
          console.log(`     ✅ ${String(s.hyp_id).padEnd(26)} IS=${(s.is_precision*100)?.toFixed(1)}% OOS=${(s.oos_precision*100)?.toFixed(1)}% decay=${s.decay_pct?.toFixed(1)}%`));
      }
    } else pp(r);
    break;
  }
  case 'leaderboard': {
    banner('Alpha Strategy Leaderboard');
    const r = await pythonAlphaLeader({ limit });
    if (r?.leaderboard !== undefined) {
      const lb = r.leaderboard ?? [];
      const gd = r.grade_dist ?? {};
      console.log(`\n   Live strategies: ${r.n_alive ?? 0}`);
      const dist = ['S','A','B','C','D','F'].filter(g => gd[g]).map(g => `${GRADE_EMOJI[g]}${g}:${gd[g]}`).join('  ');
      if (dist) console.log(`   ${dist}`);
      if (lb.length) {
        console.log('\n   Rank  Grade  Score    Strategy                     Exp%    WinR%  Hold');
        console.log('   ' + '─'.repeat(78));
        lb.slice(0, 15).forEach((s, i) => {
          const em = GRADE_EMOJI[s.grade] ?? '?';
          const bar = scoreBar(s.composite_score ?? 0);
          console.log(`   ${String(i+1).padStart(4)}. ${em} ${s.grade}  ${String(s.composite_score?.toFixed(1)).padStart(6)}  ${String(s.hyp_name ?? s.hyp_id).padEnd(28)} ${String(s.expectancy_pct?.toFixed(3)+'%').padStart(7)} ${String(s.win_rate_pct?.toFixed(1)+'%').padStart(6)}  ${s.holding_days ?? '?'}d`);
        });
        lb.slice(0, 3).forEach(s => {
          if (s.conditions_summary)
            console.log(`\n   📋 ${s.hyp_name ?? s.hyp_id}: ${s.conditions_summary}`);
        });
      } else {
        console.log('\n   No ranked strategies yet. Run: npm run egx:alpha:rank');
      }
    } else pp(r);
    break;
  }
  case 'evolve': {
    banner('Evolve Top Strategies');
    const r = await pythonAlphaEvolve({ n_top: 5, n_mutate: 3 });
    if (r?.n_children !== undefined) {
      if (r.n_children === 0) {
        console.log(`\n   ${r.message ?? 'No high-quality strategies to evolve yet'}`);
        console.log('   Run: npm run egx:alpha:rank  then  egx:grid:run  to build a portfolio');
      } else {
        console.log(`\n   Parents used:  ${r.n_parents}`);
        console.log(`   Children born: ${r.n_children}`);
        console.log('\n   Evolved variants:');
        r.children?.slice(0, 10).forEach(c =>
          console.log(`     🧬 ${c.child}  ← ${c.parent}  (${c.mutation})`));
        console.log('\n   Run: npm run egx:grid:run  to test evolved variants');
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Alpha Ranker Full');
    const r = await pythonAlphaBuildFull({});
    if (r?.ranked !== undefined) {
      const gd = r.grade_dist ?? {};
      console.log(`\n   Ranked:          ${r.ranked}`);
      const dist = ['S','A','B','C','D','F'].filter(g => gd[g]).map(g => `${GRADE_EMOJI[g]}${g}=${gd[g]}`).join('  ');
      console.log(`   Grades:          ${dist || 'none'}`);
      console.log(`   Kill candidates: ${r.kill_candidates ?? 0} (dry run)`);
      console.log(`   Decaying:        ${r.decaying ?? 0}`);
      console.log(`   Evolved:         ${r.evolved_children ?? 0} new variants`);
      if (r.top_5?.length) {
        console.log('\n   🏆 Top 5:');
        r.top_5.forEach(s => {
          const em = GRADE_EMOJI[s.grade] ?? '?';
          console.log(`     ${em} ${String(s.hyp_name ?? s.hyp_id).padEnd(30)} score=${s.composite_score?.toFixed(1)}  exp=${s.expectancy_pct?.toFixed(3)}%`);
        });
      }
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}. Use: rank|kill|decay|leaderboard|evolve|full`); process.exit(1);
}
