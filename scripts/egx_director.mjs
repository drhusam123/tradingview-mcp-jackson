#!/usr/bin/env node
/**
 * Phase 71 — Autonomous Research Director runner
 * "مدير البحث المستقل — دورة البحث اليومية الذاتية"
 *
 * Sections: morning | status | top | report | history | full
 *   --date 2026-05-15
 *   --min-grade A
 */
import { pythonDirectorMorning, pythonDirectorStatus, pythonDirectorTopAlpha,
         pythonDirectorHistory, pythonDirectorReport, pythonDirectorBuildFull }
  from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'status';
const dateIdx = args.indexOf('--date');
const date    = dateIdx !== -1 ? args[dateIdx + 1] : new Date().toISOString().split('T')[0];
const gradeIdx = args.indexOf('--min-grade');
const minGrade = gradeIdx !== -1 ? args[gradeIdx + 1] : 'B';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🤖 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const GRADE_EMOJI = { S: '💎', A: '🔥', B: '🟢', C: '🟡', D: '🟠', F: '❌' };
const STEP_EMOJI  = ['1️⃣ ', '2️⃣ ', '3️⃣ ', '4️⃣ ', '5️⃣ ', '6️⃣ ', '7️⃣ '];

function gradeBar(score) {
  const pct = Math.min(100, Math.max(0, score ?? 0));
  return '█'.repeat(Math.round(pct / 5)) + '░'.repeat(20 - Math.round(pct / 5));
}

switch (section) {

  /* ──────────────────────────────────────────────────────────
     morning — autonomous daily research cycle (7 steps)
  ────────────────────────────────────────────────────────── */
  case 'morning': {
    banner(`Morning Research Cycle — ${date}`);
    console.log('\n   🚀 Starting autonomous research director...');
    console.log('   This runs all 7 pipeline steps. Estimated time: 2-5 min\n');

    const steps = [
      'Scoring UES signals',
      'Evaluating hypothesis library',
      'Running research grid',
      'Ranking alpha candidates',
      'Pruning weak strategies',
      'Monitoring alpha decay',
      'Generating daily report',
    ];
    steps.forEach((s, i) => console.log(`   ${STEP_EMOJI[i] ?? '  '} ${s}...`));

    console.log('\n   ⏳ Running pipeline...\n');
    const t0 = Date.now();
    const r = await pythonDirectorMorning({ date });
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

    if (r?.cycle_id !== undefined || r?.steps_completed !== undefined || r?.summary !== undefined) {
      const summary = r.summary ?? r;
      console.log(`   ✅ Cycle complete in ${elapsed}s`);
      console.log(`   Cycle ID:      ${r.cycle_id ?? 'n/a'}`);
      console.log(`   Steps done:    ${r.steps_completed ?? r.n_steps ?? '?'}/7`);
      console.log(`   Date:          ${r.date ?? date}`);

      if (summary?.total_hypotheses !== undefined || r.total_hypotheses !== undefined) {
        const total   = summary.total_hypotheses   ?? r.total_hypotheses   ?? 0;
        const ranked  = summary.ranked             ?? r.ranked             ?? 0;
        const killed  = summary.killed             ?? r.killed             ?? 0;
        const evolved = summary.evolved            ?? r.evolved            ?? 0;
        const signals = summary.ues_signals        ?? r.ues_signals        ?? 0;
        const high    = summary.high_conviction    ?? r.high_conviction    ?? 0;

        console.log('\n   ─── Research Summary ─────────────────────────────');
        console.log(`   Hypotheses in library: ${total}`);
        console.log(`   Alpha candidates ranked: ${ranked}`);
        console.log(`   Weak strategies killed:  ${killed}`);
        console.log(`   Evolved new variants:    ${evolved}`);
        console.log(`   UES signals today:       ${signals}`);
        console.log(`   High-conviction signals: ${high}`);
      }

      // top alpha from this cycle
      const top = r.top_alpha ?? r.top_signals ?? summary?.top_alpha ?? [];
      if (top.length) {
        console.log('\n   ─── 🏆 Top Alpha Today ───────────────────────────');
        console.log('   Rank  ID                    Grade  Score   WinR%  OOS   Expect');
        console.log('   ' + '─'.repeat(65));
        top.slice(0, 8).forEach((a, i) => {
          const grade = a.grade ?? 'C';
          const em    = GRADE_EMOJI[grade] ?? '⚖️';
          const score = (a.composite_score ?? a.score ?? 0).toFixed(1);
          const oos   = (a.oos_score ?? 0).toFixed(3);
          const wr    = (a.win_rate_pct ?? 0).toFixed(1);
          const exp   = (a.expectancy_pct ?? 0).toFixed(2);
          const id    = String(a.hyp_id ?? a.symbol ?? 'UNKNOWN').padEnd(20);
          console.log(`   ${String(i+1).padStart(3)}.  ${em} ${id}  ${grade}    ${String(score).padStart(5)}  ${String(wr).padStart(5)}%  ${String(oos).padStart(5)}  ${String(exp).padStart(6)}%`);
        });
      }

      // errors / warnings from steps
      const errors = r.step_errors ?? r.errors ?? [];
      if (errors.length) {
        console.log(`\n   ⚠️  Step warnings (${errors.length}):`);
        errors.forEach(e => console.log(`   • ${e}`));
      }

    } else {
      pp(r);
    }
    break;
  }

  /* ──────────────────────────────────────────────────────────
     status — current director state
  ────────────────────────────────────────────────────────── */
  case 'status': {
    banner('Research Director Status');
    const r = await pythonDirectorStatus({});

    const hyp   = r?.hypotheses ?? {};
    const alpha = r?.alpha ?? {};
    const last  = r?.last_run ?? null;

    if (r?.success && (hyp.total !== undefined || alpha.n_alive !== undefined)) {
      console.log(`\n   ─── Hypothesis Library ───────────────────────────`);
      console.log(`   Total:    ${hyp.total ?? 0}  |  Tested: ${hyp.tested ?? 0}  |  Active: ${hyp.active ?? 0}  |  Killed: ${hyp.killed ?? 0}`);

      console.log(`\n   ─── Alpha Portfolio ──────────────────────────────`);
      console.log(`   Alive strategies:  ${alpha.n_alive ?? 0}`);
      console.log(`   Grade S:           ${alpha.n_grade_s ?? 0}`);
      console.log(`   Grade A:           ${alpha.n_grade_a ?? 0}`);
      if (alpha.avg_score != null)   console.log(`   Avg composite:     ${alpha.avg_score}`);
      if (alpha.best_exp_pct != null) console.log(`   Best expectancy:   ${alpha.best_exp_pct}%`);

      if (last) {
        console.log(`\n   ─── Last Director Run ────────────────────────────`);
        console.log(`   Date:      ${last.run_date ?? '?'}`);
        console.log(`   Elapsed:   ${last.elapsed_sec?.toFixed(0) ?? '?'}s`);
        console.log(`   Top grade: ${GRADE_EMOJI[last.top_grade] ?? ''}${last.top_grade ?? '?'}  (${last.top_hyp_id ?? 'n/a'}  exp=${last.top_expectancy?.toFixed(2) ?? '?'}%)`);
        if (last.summary) {
          console.log(`\n   Steps log:`);
          last.summary.split('\n').forEach(l => l.trim() && console.log(`     ${l}`));
        }
      }
    } else {
      console.log('\n   No director data yet. Run morning cycle first:');
      console.log('   npm run egx:director:morning');
    }
    break;
  }

  /* ──────────────────────────────────────────────────────────
     top — top alpha candidates right now
  ────────────────────────────────────────────────────────── */
  case 'top': {
    banner(`Top Alpha Candidates — Grade ≥ ${minGrade}`);
    const r = await pythonDirectorTopAlpha({ min_grade: minGrade });
    const alphas = r?.alpha ?? r?.top_alpha ?? r?.candidates ?? [];

    if (alphas.length) {
      console.log(`\n   ${alphas.length} alpha candidate(s) with grade ≥ ${minGrade}:\n`);
      console.log('   Rank  ID                   Grade  Score  WinR%  OOS    Expect   Type');
      console.log('   ' + '─'.repeat(78));

      alphas.forEach((a, i) => {
        const grade = a.grade ?? 'C';
        const em    = GRADE_EMOJI[grade] ?? '⚖️';
        const hyp   = String(a.hyp_id ?? a.id ?? 'UNKNOWN').padEnd(20);
        const score = (a.composite_score ?? a.score ?? 0).toFixed(1);
        const oos   = (a.oos_score ?? 0).toFixed(3);
        const wr    = (a.win_rate_pct ?? a.win_rate ?? 0).toFixed(1);
        const exp   = (a.expectancy_pct ?? 0).toFixed(2);
        const typ   = (a.hyp_id ?? '').startsWith('EVO_') ? 'evolved' : 'original';
        console.log(`   ${String(i+1).padStart(3)}.  ${em} ${hyp} ${grade}    ${String(score).padStart(5)}  ${String(wr).padStart(5)}%  ${String(oos).padStart(5)}  ${String(exp).padStart(7)}%  ${typ}`);
      });

      // grade summary
      const gradeCount = {};
      alphas.forEach(a => { const g = a.grade ?? 'C'; gradeCount[g] = (gradeCount[g] ?? 0) + 1; });
      const parts = ['S','A','B','C','D'].map(g => gradeCount[g] ? `${GRADE_EMOJI[g]}${g}:${gradeCount[g]}` : null).filter(Boolean);
      console.log(`\n   ${parts.join('  ')}`);

    } else {
      console.log(`\n   No alpha candidates at grade ≥ ${minGrade}.`);
      console.log('   Try:  node scripts/egx_director.mjs top --min-grade C');
      console.log('   Or run:  npm run egx:director:morning  to generate alpha');
    }
    break;
  }

  /* ──────────────────────────────────────────────────────────
     report — generate + display daily alpha report
  ────────────────────────────────────────────────────────── */
  case 'report': {
    banner(`Daily Alpha Report — ${date}`);
    const r = await pythonDirectorReport({ date });

    if (r?.report_date !== undefined || r?.sections !== undefined || r?.executive_summary !== undefined) {
      console.log(`\n   📋 Report for: ${r.report_date ?? date}`);
      console.log(`   Generated:    ${r.generated_at ?? 'now'}`);

      if (r.executive_summary) {
        console.log('\n   ─── Executive Summary ────────────────────────────');
        if (typeof r.executive_summary === 'string') {
          console.log(`   ${r.executive_summary}`);
        } else {
          const es = r.executive_summary;
          console.log(`   Market Bias:    ${es.market_bias ?? 'NEUTRAL'}`);
          console.log(`   Alpha Quality:  ${es.alpha_quality ?? 'n/a'}`);
          console.log(`   Actionable:     ${es.n_actionable ?? 0} strategies`);
          console.log(`   Risk Level:     ${es.risk_level ?? 'MEDIUM'}`);
          if (es.key_insight) console.log(`\n   💡 ${es.key_insight}`);
        }
      }

      if (r.top_picks?.length) {
        console.log('\n   ─── 🏆 Today\'s Top Picks ─────────────────────────');
        r.top_picks.slice(0, 5).forEach((p, i) => {
          const em = GRADE_EMOJI[p.grade ?? 'C'] ?? '⚖️';
          console.log(`   ${i+1}. ${em} ${p.hyp_id ?? p.symbol ?? 'n/a'}  score=${((p.composite_score ?? p.score ?? 0)).toFixed(1)}  ${p.grade ?? '?'}`);
        });
      }

      if (r.risk_warnings?.length) {
        console.log('\n   ─── ⚠️  Risk Warnings ────────────────────────────');
        r.risk_warnings.forEach(w => console.log(`   • ${w}`));
      }

      if (r.recommendations?.length) {
        console.log('\n   ─── 📌 Recommendations ──────────────────────────');
        r.recommendations.forEach((rec, i) => console.log(`   ${i+1}. ${rec}`));
      }

      if (r.sections && typeof r.sections === 'object') {
        const keys = Object.keys(r.sections);
        if (keys.length) {
          console.log('\n   ─── Report Sections ──────────────────────────────');
          keys.forEach(k => {
            const val = r.sections[k];
            const preview = typeof val === 'object' ? JSON.stringify(val).slice(0, 80) : String(val).slice(0, 80);
            console.log(`   [${k}] ${preview}${preview.length >= 80 ? '...' : ''}`);
          });
        }
      }

    } else {
      pp(r);
    }
    break;
  }

  /* ──────────────────────────────────────────────────────────
     history — past research cycles log
  ────────────────────────────────────────────────────────── */
  case 'history': {
    banner('Research Cycle History');
    const r = await pythonDirectorHistory({ last_n_days: 30 });
    const hist = r?.history ?? r?.cycles ?? r?.days ?? [];

    if (hist.length) {
      console.log(`\n   ${hist.length} cycle(s) on record:\n`);
      console.log('   Date        Hyp   Ranked  Killed  Evolved  Avg Score  Top Grade');
      console.log('   ' + '─'.repeat(65));

      hist.slice(0, 30).forEach(d => {
        const date    = d.date ?? d.cycle_date ?? '?';
        const hyp     = String(d.n_hypotheses ?? d.library_size ?? 0).padStart(4);
        const ranked  = String(d.n_ranked     ?? d.ranked      ?? 0).padStart(6);
        const killed  = String(d.n_killed     ?? d.killed      ?? 0).padStart(6);
        const evolved = String(d.n_evolved    ?? d.evolved     ?? 0).padStart(7);
        const avg     = (d.avg_score ?? d.avg_composite ?? 0).toFixed(1);
        const top     = d.top_grade ?? d.best_grade ?? '?';
        const em      = GRADE_EMOJI[top] ?? '⚖️';
        console.log(`   ${date}  ${hyp}  ${ranked}  ${killed}  ${evolved}  ${String(avg).padStart(9)}  ${em} ${top}`);
      });

      // trend summary
      if (hist.length >= 2) {
        const last  = hist[hist.length - 1];
        const first = hist[0];
        const scoreChange = ((last.avg_score ?? 0) - (first.avg_score ?? 0)).toFixed(1);
        const trend = scoreChange > 0 ? '📈' : scoreChange < 0 ? '📉' : '➡️';
        console.log(`\n   ${trend} Alpha quality trend: ${scoreChange > 0 ? '+' : ''}${scoreChange} pts over ${hist.length} cycles`);
      }

    } else {
      console.log('\n   No cycle history yet.');
      console.log('   Run:  npm run egx:director:morning  to start first cycle');
    }
    break;
  }

  /* ──────────────────────────────────────────────────────────
     full — complete director build (all commands in sequence)
  ────────────────────────────────────────────────────────── */
  case 'full': {
    banner('Research Director Full Build');
    console.log('\n   Running full director build...\n');
    const t0 = Date.now();
    const r = await pythonDirectorBuildFull({ date });
    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

    if (r?.cycles !== undefined || r?.alpha_count !== undefined || r?.morning !== undefined) {
      console.log(`   ✅ Full build done in ${elapsed}s`);
      console.log(`   Cycles run:     ${r.cycles ?? r.n_cycles ?? '?'}`);
      console.log(`   Alpha tracked:  ${r.alpha_count ?? r.n_alpha ?? '?'}`);
      console.log(`   Grade dist:     ${JSON.stringify(r.grade_distribution ?? {})}`);

      const top = r.top_alpha ?? r.top_picks ?? [];
      if (top.length) {
        console.log('\n   🏆 Top 5 alpha candidates:');
        top.slice(0, 5).forEach((a, i) => {
          const em = GRADE_EMOJI[a.grade ?? 'C'] ?? '⚖️';
          console.log(`   ${i+1}. ${em} ${a.hyp_id ?? a.id ?? '?'}  score=${((a.composite_score ?? a.score ?? 0)).toFixed(1)}  grade=${a.grade ?? '?'}`);
        });
      }
    } else {
      pp(r);
    }
    break;
  }

  default:
    console.log(`Unknown section: ${section}. Use: morning|status|top|report|history|full`);
    process.exit(1);
}
