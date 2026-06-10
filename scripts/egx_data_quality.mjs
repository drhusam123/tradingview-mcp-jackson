#!/usr/bin/env node
/**
 * Phase 55 — Data Quality Gate runner
 * "ثق لكن تحقق — Trust the Data Gate"
 *
 * Sections: ohlcv | gaps | continuity | stale | audit | trust | issues | full
 */
import { pythonQualityOHLCV, pythonQualityGaps, pythonQualityContinuity,
         pythonQualityStale, pythonQualityFullAudit, pythonQualityTrustScores,
         pythonQualityOpenIssues, pythonQualityQuarantined,
         pythonQualityBuildFull } from '../src/egx/index.js';
import { spawnSync } from 'child_process';
import { join } from 'path';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'audit';
const ROOT    = process.cwd();
const PYTHON  = process.env.PYTHON ?? 'python3';
const DQ_GATE = join(ROOT, 'scripts/python/data_quality_gate.py');

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔬 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }
function runDQ(cmd, p={}) {
  const r = spawnSync(PYTHON, [DQ_GATE, cmd, JSON.stringify(p)], { encoding: 'utf8', cwd: ROOT });
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(r.stderr || r.stdout || `Data quality command failed: ${cmd}`);
  return JSON.parse(r.stdout);
}

const SEV_EMOJI  = { CRITICAL: '🔴', HIGH: '🟠', MEDIUM: '🟡', WARNING: '🔵', INFO: 'ℹ️' };
const STAT_EMOJI = { TRUSTED: '✅', HEALTHY: '✅', DEGRADED: '⚠️', UNRELIABLE: '❌', CRITICAL: '🆘' };

switch (section) {
  case 'ohlcv': {
    banner('Quality: OHLCV Integrity Check');
    const r = await pythonQualityOHLCV({ table: 'ohlcv_history' });
    if (r?.n_checked !== undefined) {
      // violations is a dict {TYPE: count} or {TYPE: {count, severity}}
      const violations = r.violations ?? r.violations_by_type ?? {};
      const totalViol = Object.values(violations).reduce((s, v) =>
        s + (typeof v === 'number' ? v : v?.count ?? 0), 0);
      const em = totalViol === 0 ? '✅' : r.n_critical > 0 ? '🔴' : '🟡';
      console.log(`\n   ${em} Checked: ${r.n_checked}  |  Total violations: ${totalViol}`);
      if (r.symbols_affected?.length)
        console.log(`   Symbols affected: ${r.symbols_affected.join(', ')}`);
      if (Object.keys(violations).length) {
        console.log('\n   Violation types:');
        Object.entries(violations).forEach(([k, v]) => {
          const cnt = typeof v === 'number' ? v : v?.count ?? 0;
          const sev = v?.severity ?? '';
          if (cnt > 0) console.log(`   ${SEV_EMOJI[sev] ?? '🟡'} ${String(k).padEnd(25)} ${cnt}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'zero-volume':
  case 'zerovol':
  case 'zero_volume': {
    banner('Quality: ZERO_VOLUME Production Gate');
    const r = runDQ('build_zero_volume_gate', { table: 'ohlcv_history' });
    console.log(`\n   ✅ Raw table preserved; production views rebuilt`);
    console.log(`   Zero-volume bars: ${r.n_zero_volume}`);
    console.log(`   Flat/no-trade retained: ${r.n_flat_no_trade}`);
    console.log(`   Non-flat corrupt isolated: ${r.n_nonflat_corrupt}`);
    console.log(`   Active exclusions: ${r.active_exclusions}`);
    console.log(`   Clean feature rows: ${r.clean_view_rows}`);
    if (r.worst_symbols?.length) {
      console.log('\n   Worst symbols:');
      r.worst_symbols.slice(0, 10).forEach(s =>
        console.log(`   ⚠️  ${String(s.symbol).padEnd(8)} zero=${String(s.zero_volume).padStart(4)} flat=${String(s.flat_no_trade).padStart(4)} nonflat=${String(s.nonflat_corrupt).padStart(3)}  ${s.first_zero}→${s.last_zero}`));
    }
    break;
  }
  case 'gaps': {
    banner('Quality: Timestamp Gaps Check');
    const r = await pythonQualityGaps({ table: 'ohlcv_history' });
    const nGaps = r?.n_gaps ?? r?.total_gaps_found;
    if (nGaps !== undefined) {
      const em = nGaps === 0 ? '✅' : nGaps < 500 ? '🟡' : '🟠';
      console.log(`\n   ${em} Missing trading days total: ${nGaps}`);
      console.log(`   Symbols checked: ${r.n_symbols_checked ?? r.symbols_affected ?? '?'}`);
      if (r.worst_symbols?.length) {
        console.log('\n   Worst offenders:');
        r.worst_symbols.slice(0, 10).forEach(s =>
          console.log(`   ⚠️  ${String(s.symbol).padEnd(10)} ${s.n_gaps} gaps`));
      } else if (r.gaps?.length) {
        // Summarize by symbol
        const sym_count = {};
        r.gaps.forEach(g => { sym_count[g.symbol] = (sym_count[g.symbol]??0) + (g.missing_days?.length??1); });
        const top = Object.entries(sym_count).sort((a,b)=>b[1]-a[1]).slice(0, 8);
        if (top.length) {
          console.log('\n   Top gaps by symbol:');
          top.forEach(([s, n]) => console.log(`   ⚠️  ${String(s).padEnd(10)} ${n} missing days`));
        }
      }
    } else pp(r);
    break;
  }
  case 'continuity': {
    banner('Quality: Price Continuity Check');
    const r = await pythonQualityContinuity({ threshold_pct: 20 });
    if (r?.n_suspicious !== undefined) {
      console.log(`\n   Suspicious moves (>20%): ${r.n_suspicious}`);
      console.log(`   Known corporate actions: ${r.n_known_corp_actions}`);
      console.log(`   Unexplained: ${r.n_unexplained}`);
      if (r.unexplained?.length) {
        console.log('\n   Unexplained large moves:');
        r.unexplained.slice(0, 10).forEach(e =>
          console.log(`   ⚡ ${String(e.symbol).padEnd(8)} ${e.date}  ${e.gap_pct?.toFixed(1)}%`));
      }
    } else pp(r);
    break;
  }
  case 'stale': {
    banner('Quality: Stale Data Check');
    const r = await pythonQualityStale({});
    if (r?.stale_sources !== undefined) {
      if (!r.stale_sources?.length) { console.log('\n   ✅ All data sources are fresh'); break; }
      r.stale_sources.forEach(s => {
        const em = s.days_stale > 10 ? '🔴' : s.days_stale > 5 ? '🟡' : '🟠';
        console.log(`\n   ${em} ${String(s.source).padEnd(20)} last: ${s.last_update}  (${s.days_stale}d stale)`);
      });
    } else pp(r);
    break;
  }
  case 'audit': {
    banner('Quality: Full Audit');
    const r = await pythonQualityFullAudit({ tables: ['ohlcv_history', 'ohlcv_weekly', 'cross_market_daily'] });
    if (r?.results) {
      console.log('\n   Table              Trust  CRIT  HIGH   MED  Status');
      console.log('   ' + '─'.repeat(60));
      Object.entries(r.results).forEach(([t, s]) => {
        if (s.skipped) {
          console.log(`   ⏭️  ${String(t).padEnd(20)} (skipped: ${s.reason})`);
          return;
        }
        const em = STAT_EMOJI[s.status] ?? '?';
        const ts  = s.trust_score != null ? String(s.trust_score.toFixed(0)) : 'N/A';
        console.log(`   ${em} ${String(t).padEnd(20)} ${ts.padStart(5)}   ${String(s.n_critical??0).padStart(4)} ${String(s.n_high??0).padStart(5)} ${String(s.n_medium??0).padStart(5)}  ${s.status ?? ''}`);
      });
      if (r.stale_data_check?.stale_sources?.length) {
        console.log('\n   ⏱️  Stale sources:');
        r.stale_data_check.stale_sources.forEach(s =>
          console.log(`   ${s.days_stale > 10 ? '🔴' : '🟡'} ${String(s.source).padEnd(20)} ${s.days_stale}d stale`));
      }
    } else pp(r);
    break;
  }
  case 'trust': {
    banner('Quality: Trust Scores');
    const r = await pythonQualityTrustScores({});
    if (r?.scores?.length || r?.trust_scores) {
      const scores = r.scores ?? r.trust_scores ?? [];
      scores.forEach(s => {
        const em = STAT_EMOJI[s.status] ?? '?';
        console.log(`   ${em} ${String(s.source).padEnd(22)} ${String(s.trust_score?.toFixed(1)).padStart(5)}/100  open:${s.n_issues_open}  crit:${s.n_issues_critical}`);
      });
    } else pp(r);
    break;
  }
  case 'issues': {
    banner('Quality: Open Issues');
    const r = await pythonQualityOpenIssues({});
    if (r?.total_open !== undefined) {
      console.log(`\n   Total open: ${r.total_open}`);
      // by_severity can be {SEV: count} or {SEV: [list of issues]}
      if (r.counts) {
        Object.entries(r.counts).forEach(([s, n]) =>
          console.log(`   ${SEV_EMOJI[s] ?? '?'} ${String(s).padEnd(10)} ${n}`));
      } else if (r.by_severity) {
        Object.entries(r.by_severity).forEach(([s, v]) => {
          const n = Array.isArray(v) ? v.length : v;
          if (n > 0) console.log(`   ${SEV_EMOJI[s] ?? '?'} ${String(s).padEnd(10)} ${n}`);
        });
      }
      // Show sample issues if any HIGH
      if (Array.isArray(r.by_severity?.HIGH) && r.by_severity.HIGH.length) {
        console.log('\n   Sample HIGH issues:');
        r.by_severity.HIGH.slice(0, 5).forEach(i =>
          console.log(`   ⚠️  ${String(i.symbol).padEnd(8)} ${i.bar_date ?? i.check_date ?? ''}  ${i.issue_description?.slice(0, 60)}`));
      }
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Quality: Full System Data Health');
    const r = await pythonQualityBuildFull({});
    const status = r?.system_status ?? r?.overall_status;
    if (status) {
      const em = STAT_EMOJI[status] ?? '❌';
      console.log(`\n   ${em} System data health: ${status}`);
      console.log(`   Avg trust score:   ${r.avg_trust_score?.toFixed(1) ?? 'N/A'}/100`);
      console.log(`   Worst trust score: ${r.worst_trust_score?.toFixed(1) ?? 'N/A'}/100`);
      console.log(`   Total open issues: ${r.n_open_issues ?? r.total_open_issues ?? '?'}`);
      console.log(`   Critical open:     ${r.n_critical_open ?? r.n_critical ?? '?'}`);
      if (r.tables_audited?.length)
        console.log(`   Tables audited:    ${r.tables_audited.join(', ')}`);
      if (r.stale_check?.stale_sources?.length) {
        console.log('\n   ⏱️  Stale data:');
        r.stale_check.stale_sources.forEach(s =>
          console.log(`   ${s.days_stale > 10 ? '🔴' : '🟡'} ${String(s.source).padEnd(20)} last: ${s.last_update}  (${s.days_stale}d stale)`));
      } else {
        console.log('   ✅ No stale sources');
      }
      if (r.open_issues_summary?.by_severity)
        Object.entries(r.open_issues_summary.by_severity).forEach(([s, n]) =>
          n > 0 && console.log(`   ${SEV_EMOJI[s] ?? '?'} ${String(s).padEnd(10)} ${n}`));
      console.log(`\n   ${r.recommendation ?? ''}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
