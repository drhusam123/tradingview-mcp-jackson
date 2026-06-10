#!/usr/bin/env node
/**
 * Phase 38 — Cognitive Compression Engine runner
 * "الذكاء في جملة واحدة — Market Intelligence Index"
 *
 * Sections:
 *   forces      — 5 dominant market forces (default)
 *   risks       — 3 critical risks
 *   opps        — 2 best opportunities
 *   briefing    — full market briefing (Arabic + English)
 *   mii         — Market Intelligence Index 0-100
 *   full        — all + save to DB (recommended)
 */
import { pythonCompressionForces, pythonCompressionRisks, pythonCompressionOpps,
         pythonCompressionBriefing, pythonCompressionMII,
         pythonCompressionBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'mii';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🧠 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const FORCE_EMOJI   = { MOMENTUM: '⚡', LIQUIDITY: '💧', REGIME_PULL: '🧲', SENTIMENT_WAVE: '🌊',
                        CATALYST_FLOW: '⚗️', LAW_DENSITY: '📐', RISK_PRESSURE: '⚠️',
                        ANOMALY_FIELD: '🔮', CONTAGION_WAVE: '🔗', STRUCTURAL_DRIFT: '🏗️' };
const RISK_EMOJI    = { CRITICAL: '🔴', HIGH: '🟠', MEDIUM: '🟡' };
const MII_EMOJI     = { PRIME: '🌟', GOOD: '✅', NEUTRAL: '⚪', POOR: '⚠️', CRISIS: '🚨' };

switch (section) {
  case 'forces': {
    banner('Compression: Dominant Market Forces');
    const r = await pythonCompressionForces({});
    if (r?.forces) {
      console.log(`\n   Market vector: ${r.market_vector?.toFixed(3)} (${r.market_vector > 0 ? 'Bullish ↑' : r.market_vector < 0 ? 'Bearish ↓' : 'Neutral →'})`);
      console.log(`   Dominant force: ${r.dominant_force}  |  Bull:${r.n_bullish}  Bear:${r.n_bearish}\n`);
      console.log('   Top 5 Forces:');
      r.forces.forEach(f => {
        const em  = FORCE_EMOJI[f.force_type] ?? '•';
        const dir = f.direction > 0 ? '↑' : f.direction < 0 ? '↓' : '→';
        console.log(`   ${em} ${String(f.force_type).padEnd(20)} ${dir} magnitude:${(f.magnitude*100).toFixed(0)}%  ${f.evidence}`);
      });
    } else pp(r);
    break;
  }
  case 'risks': {
    banner('Compression: Critical Risks');
    const r = await pythonCompressionRisks({});
    if (r?.risks) {
      const riskEm = r.aggregate_risk_level === 'EXTREME' ? '🚨' : r.aggregate_risk_level === 'HIGH' ? '🔴' : r.aggregate_risk_level === 'MODERATE' ? '🟡' : '✅';
      console.log(`\n   ${riskEm} Aggregate risk: ${r.aggregate_risk_level}  (score: ${(r.risk_score*100).toFixed(0)}%)\n`);
      console.log('   Top 3 Risks:');
      r.risks.forEach(risk => {
        const em = RISK_EMOJI[risk.severity] ?? '?';
        console.log(`   ${em} [${risk.risk_type}] ${risk.description}`);
        console.log(`      Source: ${risk.engine_source}  |  Mitigation: ${risk.mitigation}`);
      });
    } else pp(r);
    break;
  }
  case 'opps': {
    banner('Compression: Best Opportunities');
    const r = await pythonCompressionOpps({});
    if (r?.opportunities) {
      console.log(`\n   Opportunity score: ${(r.opportunity_score*100).toFixed(0)}%  |  Best: ${r.best_opportunity}\n`);
      r.opportunities.forEach(o => {
        console.log(`   🎯 [${o.opportunity_type}] ${o.symbol_or_pattern}`);
        console.log(`      Confidence: ${(o.confidence*100).toFixed(0)}%  Edge: ${(o.expected_edge*100).toFixed(1)}%  Window: ${o.time_window}`);
      });
    } else pp(r);
    break;
  }
  case 'briefing': {
    banner('Compression: Market Briefing');
    const r = await pythonCompressionBriefing({});
    if (r?.arabic_briefing) {
      console.log(`\n   🇸🇦 ${r.arabic_briefing}`);
      console.log(`\n   🇬🇧 ${r.english_briefing}`);
      console.log(`\n   Market vector: ${r.market_vector?.toFixed(3)}`);
    } else pp(r);
    break;
  }
  case 'mii': {
    banner('Compression: Market Intelligence Index');
    const r = await pythonCompressionMII({});
    if (r?.mii !== undefined) {
      const em = MII_EMOJI[r.interpretation] ?? '?';
      console.log(`\n   ${em} MII: ${r.mii?.toFixed(1)}/100  |  ${r.interpretation}`);
      if (r.components) {
        console.log('\n   Components:');
        Object.entries(r.components).forEach(([k, v]) =>
          console.log(`   ${String(k).padEnd(28)} ${(v*100).toFixed(1)}%`));
      }
      console.log(`\n   📋 ${r.recommendation}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Compression: Full Build');
    const r = await pythonCompressionBuildFull({});
    if (r?.mii !== undefined) {
      const em = MII_EMOJI[r.interpretation] ?? '?';
      console.log(`\n   ${em} MII: ${r.mii?.toFixed(1)}/100  |  ${r.interpretation}`);
      console.log(`   Market vector: ${r.market_vector?.toFixed(3)}`);
      console.log(`   Dominant force: ${r.dominant_force}`);
      console.log(`   Top risk:       ${r.top_risk}`);
      console.log(`   Top opportunity: ${r.top_opportunity}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
