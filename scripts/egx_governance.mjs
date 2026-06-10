#!/usr/bin/env node
/**
 * Phase 41 — Governance Constitution Engine runner
 * "القانون الأعلى — من يتحكم في كل شيء؟"
 *
 * Sections:
 *   audit       — audit all active decisions for constitution violations (default)
 *   enforce     — enforce mutation speed limits
 *   resolve     — resolve override conflict  --phase-a ARBITRATION --phase-b SANDBOX_SUGGESTION
 *   halt        — check if system halt conditions are triggered
 *   report      — full governance report
 *   full        — report + save to DB (recommended)
 */
import { pythonGovernanceAudit, pythonGovernanceEnforce, pythonGovernanceResolve,
         pythonGovernanceHaltCheck, pythonGovernanceReport,
         pythonGovernanceBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'audit';
const phaseA  = args[args.indexOf('--phase-a') + 1] ?? 'ARBITRATION';
const phaseB  = args[args.indexOf('--phase-b') + 1] ?? 'SANDBOX_SUGGESTION';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  ⚖️  ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const HEALTH_EMOJI  = { CLEAN: '✅', DEGRADED: '⚠️', CRITICAL: '🔴' };
const SEV_EMOJI     = { VIOLATION: '🚫', WARNING: '⚠️' };

switch (section) {
  case 'audit': {
    banner('Governance: Constitution Audit');
    const r = await pythonGovernanceAudit({});
    if (r?.constitution_health) {
      const em = HEALTH_EMOJI[r.constitution_health] ?? '?';
      console.log(`\n   ${em} Constitution health: ${r.constitution_health}`);
      console.log(`   Violations: ${r.n_violations}  Warnings: ${r.n_warnings}`);
      if (r.violations?.length) {
        console.log('\n   Issues found:');
        r.violations.forEach(v => {
          const sev = SEV_EMOJI[v.severity] ?? '?';
          console.log(`   ${sev} [${v.rule}] ${v.detail}  (Phase: ${v.phase_involved})`);
        });
      } else {
        console.log('   ✅ No violations detected');
      }
      console.log(`\n   Audited at: ${r.audit_timestamp}`);
    } else pp(r);
    break;
  }
  case 'enforce': {
    banner('Governance: Enforce Mutation Limits');
    const r = await pythonGovernanceEnforce({});
    if (r?.mutations_this_week !== undefined) {
      const over = r.over_limit ? '🔴 OVER LIMIT' : '✅ Within limit';
      console.log(`\n   ${over}`);
      console.log(`   Mutations this week: ${r.mutations_this_week}  |  Limit: ${r.limit}`);
      if (r.suspended_laws?.length)
        console.log(`   Suspended: ${r.suspended_laws.join(', ')}`);
      console.log(`   Action: ${r.action_taken}`);
    } else pp(r);
    break;
  }
  case 'resolve': {
    banner(`Governance: Resolve Conflict — ${phaseA} vs ${phaseB}`);
    const r = await pythonGovernanceResolve({ phase_a: phaseA, phase_b: phaseB });
    if (r?.winner) {
      console.log(`\n   🏆 Winner: ${r.winner}  (hierarchy pos: ${r.hierarchy_positions?.[Object.keys(r.hierarchy_positions)[0]]})`);
      console.log(`   ❌ Overridden: ${r.loser}`);
      console.log(`   Reason: ${r.reason}`);
    } else pp(r);
    break;
  }
  case 'halt': {
    banner('Governance: System Halt Check');
    const r = await pythonGovernanceHaltCheck({});
    if (r?.should_halt !== undefined) {
      const em = r.should_halt ? '🚨 HALT TRIGGERED' : '✅ SYSTEM CLEAR';
      console.log(`\n   ${em}`);
      if (r.halt_reasons?.length)
        r.halt_reasons.forEach(reason => console.log(`   🔴 ${reason}`));
      console.log(`\n   STS:               ${r.sts?.toFixed(1) ?? 'N/A'}`);
      console.log(`   Total uncertainty: ${(r.total_uncertainty*100)?.toFixed(1) ?? 'N/A'}%`);
      console.log(`   MII:               ${r.mii?.toFixed(1) ?? 'N/A'}`);
      console.log(`\n   📋 ${r.recommendation}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Governance: Full Report');
    const r = await pythonGovernanceReport({});
    if (r?.audit) {
      const em = HEALTH_EMOJI[r.audit.constitution_health] ?? '?';
      console.log(`\n   ${em} Constitution: ${r.audit.constitution_health}`);
      console.log(`   Violations: ${r.audit.n_violations}  Warnings: ${r.audit.n_warnings}`);
      console.log(`   Halt check: ${r.halt_check?.should_halt ? '🚨 HALT' : '✅ Clear'}`);
      console.log(`   Active rules: ${r.active_rules}`);
      console.log(`   Constitution v${r.constitution_version}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Governance: Full Build + Save');
    const r = await pythonGovernanceBuildFull({});
    if (r?.constitution_health) {
      const em = HEALTH_EMOJI[r.constitution_health] ?? '?';
      console.log(`\n   ${em} ${r.constitution_health}  |  Violations: ${r.n_violations}  Warnings: ${r.n_warnings}`);
      console.log(`   Should halt: ${r.should_halt ? '🚨 YES' : '✅ NO'}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
