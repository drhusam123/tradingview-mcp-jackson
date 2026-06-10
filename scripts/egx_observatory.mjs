#!/usr/bin/env node
/**
 * Phase 37 — Intelligence Reliability Observatory runner
 * "هل النظام يعمل بشكل صحيح؟ — System Trustability Score"
 *
 * Sections:
 *   health      — engine health scan for all 18 engines (default)
 *   trustability — System Trustability Score + safe_to_trade flag
 *   failures    — detect failure modes across the system
 *   agreement   — inter-engine directional agreement
 *   report      — full health report (health + failures + agreement)
 *   full        — report + save to DB (recommended)
 *   enhanced    — enhanced health: latency + freshness + regime disagreement + causal + entropy + fragmentation
 *   entropy     — model decision entropy
 *   latency     — engine latency drift
 *   freshness   — data freshness degradation trend
 *   fragmentation — knowledge graph fragmentation
 */
import { pythonObservatoryEngineHealth, pythonObservatoryTrustability,
         pythonObservatoryDetectFailures, pythonObservatoryAgreement,
         pythonObservatoryReport, pythonObservatoryBuildFull,
         pythonObservatoryEnhanced, pythonObservatoryEntropy,
         pythonObservatoryLatencyDrift, pythonObservatoryFreshness,
         pythonObservatoryRegimeDisagreement, pythonObservatoryFragmentation } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'health';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔭 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const STATUS_EMOJI  = { HEALTHY: '✅', DEGRADED: '⚠️', STALE: '🕐', MISSING: '❌' };
const SYSTEM_EMOJI  = { OPERATIONAL: '🟢', DEGRADED: '🟡', CRITICAL: '🔴' };

switch (section) {
  case 'health': {
    banner('Observatory: Engine Health Scan');
    const r = await pythonObservatoryEngineHealth({});
    if (r?.engines) {
      console.log(`\n   Engines monitored: ${r.engines.length}`);
      console.log(`   ✅ Healthy:  ${r.n_healthy}  ⚠️  Degraded: ${r.n_degraded}  ❌ Missing: ${r.n_missing}`);
      console.log(`   Avg health score: ${r.avg_health?.toFixed(1)}/100\n`);
      r.engines.forEach(e => {
        const em = STATUS_EMOJI[e.status] ?? '?';
        const age = e.days_since_update != null ? `${e.days_since_update?.toFixed(1)}d ago` : 'unknown';
        console.log(`   ${em} ${String(e.name).padEnd(28)} score:${String(e.health_score?.toFixed(0) ?? '?').padStart(3)}  rows:${String(e.row_count ?? 0).padStart(5)}  updated:${age}`);
      });
    } else pp(r);
    break;
  }
  case 'trustability': {
    banner('Observatory: System Trustability Score');
    const r = await pythonObservatoryTrustability({});
    if (r?.sts !== undefined) {
      const em = SYSTEM_EMOJI[r.status] ?? '?';
      console.log(`\n   ${em} STS: ${r.sts?.toFixed(1)}/100  |  Status: ${r.status}`);
      console.log(`   Safe to trade: ${r.safe_to_trade ? '✅ YES' : '❌ NO'}`);
      if (r.critical_failures?.length)
        console.log(`\n   🔴 Critical Failures: ${r.critical_failures.join(', ')}`);
      console.log(`\n   📋 ${r.recommendation}`);
    } else pp(r);
    break;
  }
  case 'failures': {
    banner('Observatory: Failure Detection');
    const r = await pythonObservatoryDetectFailures({});
    if (r?.failures) {
      console.log(`\n   System alert: ${r.system_alert ? '🚨 YES' : '✅ NO'}`);
      console.log(`   Critical: ${r.n_critical}  Moderate: ${r.n_moderate}`);
      if (r.failures?.length) {
        console.log('\n   Detected Failures:');
        r.failures.forEach(f => {
          const sev = f.severity === 'HIGH' ? '🔴' : f.severity === 'MEDIUM' ? '🟡' : '🔵';
          console.log(`   ${sev} [${f.type}] ${f.engine}: ${f.detail}`);
        });
      } else {
        console.log('   ✅ No failures detected');
      }
    } else pp(r);
    break;
  }
  case 'agreement': {
    banner('Observatory: Inter-Engine Agreement');
    const r = await pythonObservatoryAgreement({});
    if (r?.agreement_score !== undefined) {
      const em = r.consensus === 'CONSENSUS' ? '✅' : r.consensus === 'MIXED' ? '⚠️' : '🔴';
      console.log(`\n   ${em} Agreement: ${(r.agreement_score * 100)?.toFixed(0)}%  (${r.consensus})`);
      console.log(`   Regime says:      ${r.regime_says}`);
      console.log(`   Predictions say:  ${r.predictions_say}`);
      console.log(`   Arbitration says: ${r.arbitration_says}`);
      console.log(`   Sentiment says:   ${r.sentiment_says}`);
      if (r.conflict_areas?.length)
        console.log(`\n   ⚠️  Conflicts: ${r.conflict_areas.join(', ')}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Observatory: Full Health Report');
    const r = await pythonObservatoryReport({});
    if (r?.health) {
      const h = r.health;
      console.log(`\n   Engine health: ${h.n_healthy} healthy / ${h.n_degraded} degraded / ${h.n_missing} missing`);
      console.log(`   Avg health: ${h.avg_health?.toFixed(1)}/100`);
      if (r.failures) {
        console.log(`   Failures: ${r.failures.n_critical} critical, ${r.failures.n_moderate} moderate`);
        console.log(`   System alert: ${r.failures.system_alert ? '🚨 YES' : '✅ NO'}`);
      }
      if (r.agreement)
        console.log(`   Engine agreement: ${(r.agreement.agreement_score * 100)?.toFixed(0)}% (${r.agreement.consensus})`);
      console.log(`\n   Generated: ${r.generated_at}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Observatory: Full Build + Save');
    const r = await pythonObservatoryBuildFull({});
    if (r?.sts !== undefined) {
      const em = SYSTEM_EMOJI[r.status] ?? '?';
      console.log(`\n   ${em} STS: ${r.sts?.toFixed(1)}/100  |  ${r.status}`);
      console.log(`   Safe to trade: ${r.safe_to_trade ? '✅ YES' : '❌ NO'}`);
      console.log(`   Healthy engines: ${r.n_healthy}`);
      console.log(`   ${r.report_summary ?? r.status}`);
    } else pp(r);
    break;
  }
  case 'enhanced': {
    banner('Observatory: Enhanced Health Metrics');
    const r = await pythonObservatoryEnhanced({});
    if (r?.overall_enhancement_score !== undefined) {
      const em = r.overall_status === 'HEALTHY' ? '✅' : r.overall_status === 'DEGRADED' ? '⚠️' : '🔴';
      console.log(`\n   ${em} Enhancement score: ${r.overall_enhancement_score?.toFixed(1)}/100  (${r.overall_status})`);
      if (r.alerts?.length) { console.log('\n   🚨 Alerts:'); r.alerts.forEach(a => console.log(`   • ${a}`)); }
      if (r.metrics) {
        console.log('\n   Metric breakdown:');
        Object.entries(r.metrics).forEach(([k, v]) =>
          console.log(`   ${String(k).padEnd(35)} ${JSON.stringify(v)?.slice(0,60)}`));
      }
    } else pp(r);
    break;
  }
  case 'entropy': {
    banner('Observatory: Model Decision Entropy');
    const r = await pythonObservatoryEntropy({});
    if (r?.system_entropy !== undefined) {
      const em = r.entropy_level === 'HEALTHY' ? '✅' : r.entropy_level === 'LOW' ? '🟢' : '🔴';
      console.log(`\n   ${em} Entropy: ${(r.system_entropy*100)?.toFixed(1)}%  (${r.entropy_level})`);
      console.log(`   Arbitration: ${(r.arbitration_entropy*100)?.toFixed(1)}%  |  Prediction: ${(r.prediction_entropy*100)?.toFixed(1)}%`);
      if (r.decision_distribution) {
        console.log('\n   Decision distribution:');
        Object.entries(r.decision_distribution).forEach(([k, v]) => console.log(`   ${k.padEnd(8)} ${v}`));
      }
      console.log(`\n   ${r.interpretation}`);
      if (r.alert) console.log(`   🚨 ENTROPY ALERT`);
    } else pp(r);
    break;
  }
  case 'latency': {
    banner('Observatory: Engine Latency Drift');
    const r = await pythonObservatoryLatencyDrift({});
    if (r?.avg_system_drift !== undefined) {
      console.log(`\n   Avg system drift: ${(r.avg_system_drift*100)?.toFixed(1)}%`);
      console.log(`   Degrading: ${r.n_degrading}  Improving: ${r.n_improving}`);
      console.log(`   Worst: ${r.worst_drift_engine}`);
      console.log(`\n   ${r.interpretation}`);
    } else pp(r);
    break;
  }
  case 'freshness': {
    banner('Observatory: Data Freshness Degradation');
    const r = await pythonObservatoryFreshness({});
    if (r?.system_freshness_health) {
      console.log(`\n   Freshness health: ${r.system_freshness_health}`);
      console.log(`   Critical engines: ${r.n_critical}  |  Avg degrade: ${r.avg_degrade_score?.toFixed(2)}`);
      if (r.degrading_engines?.length)
        r.degrading_engines.filter(e => e.trend !== 'STABLE').slice(0, 5).forEach(e =>
          console.log(`   ⚠️  ${String(e.engine).padEnd(30)} score:${e.degrade_score?.toFixed(2)}  (${e.trend})`));
      console.log(`\n   ${r.recommendation}`);
    } else pp(r);
    break;
  }
  case 'fragmentation': {
    banner('Observatory: Graph Fragmentation');
    const r = await pythonObservatoryFragmentation({});
    if (r?.fragmentation_level) {
      const em = r.fragmentation_level === 'STABLE' ? '✅' : r.fragmentation_level === 'MODERATE' ? '⚠️' : '🔴';
      console.log(`\n   ${em} Fragmentation: ${r.fragmentation_level}  (rate: ${(r.fragmentation_rate*100)?.toFixed(1)}%)`);
      console.log(`   Links lost: ${r.n_link_pairs_lost}`);
      if (r.disconnected_sectors?.length) console.log(`   Disconnected: ${r.disconnected_sectors.join(', ')}`);
      console.log(`\n   ${r.recommendation}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
