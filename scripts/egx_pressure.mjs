#!/usr/bin/env node
/**
 * Phase 43 — Guided Research Pressure Zones runner
 * "ابحث حيث يؤلم — البحث الموجَّه لا العشوائي"
 *
 * Sections:
 *   zones       — identify active pressure zones (default)
 *   mandates    — generate research mandates from zones
 *   cycle       — full guided research cycle (zones → hypotheses → test → promote)
 *   report      — pressure zone activity report
 *   full        — zones + mandates + cycle + save (recommended)
 */
import { pythonPressureIdentify, pythonPressureMandates, pythonPressureCycle,
         pythonPressureReport, pythonPressureBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'zones';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🎯 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

const ZONE_EMOJI = {
  ANTI_LAW_GAP:      '🚫',
  OOD_PATTERN:       '🌐',
  ENGINE_CONFLICT:   '⚡',
  REGIME_INSTABILITY:'🌊',
  GRAPH_FRACTURE:    '🔗',
  STAT_FAILURE:      '📊',
  PREDICTION_FLIP:   '🔄',
  CATALYST_MISS:     '💥',
};

const PRI_EMOJI = { HIGH: '🔴', MEDIUM: '🟡', LOW: '🟢' };

switch (section) {
  case 'zones': {
    banner('Pressure: Active Zones');
    const r = await pythonPressureIdentify({});
    if (r?.n_zones !== undefined) {
      console.log(`\n   Active pressure zones: ${r.n_zones}`);
      console.log(`   Top zone: ${r.top_zone}`);
      console.log(`   Total urgency: ${(r.total_urgency*100)?.toFixed(0)}%\n`);
      console.log('   Zone Distribution:');
      Object.entries(r.zone_distribution ?? {}).forEach(([t, n]) =>
        console.log(`   ${ZONE_EMOJI[t] ?? '•'} ${String(t).padEnd(25)} ${n}`));
      if (r.zones?.length) {
        console.log('\n   Top Zones:');
        r.zones.slice(0, 8).forEach(z => {
          const em = ZONE_EMOJI[z.zone_type] ?? '•';
          console.log(`   ${em} ${String(z.zone_type).padEnd(22)} urgency:${(z.urgency_score*100).toFixed(0)}%  ${z.description}`);
        });
      }
    } else pp(r);
    break;
  }
  case 'mandates': {
    banner('Pressure: Research Mandates');
    const r = await pythonPressureMandates({});
    if (r?.n_mandates !== undefined) {
      console.log(`\n   Mandates generated: ${r.n_mandates}`);
      console.log(`   🔴 HIGH: ${r.by_priority?.HIGH ?? 0}  🟡 MEDIUM: ${r.by_priority?.MEDIUM ?? 0}  🟢 LOW: ${r.by_priority?.LOW ?? 0}`);
      console.log(`   Top mandate: ${r.top_mandate}\n`);
      if (r.mandates?.length) {
        console.log('   Top Research Mandates:');
        r.mandates.slice(0, 8).forEach(m => {
          const pri = PRI_EMOJI[m.priority] ?? '?';
          console.log(`   ${pri} [${m.zone_type}] ${m.hypothesis_text?.slice(0, 80)}...`);
        });
      }
    } else pp(r);
    break;
  }
  case 'cycle': {
    banner('Pressure: Guided Research Cycle');
    const r = await pythonPressureCycle({});
    if (r?.cycle_id !== undefined) {
      const rate = (r.promotion_rate*100)?.toFixed(1);
      console.log(`\n   Cycle: ${r.cycle_id}`);
      console.log(`   Mandates:  ${r.n_mandates}`);
      console.log(`   Tested:    ${r.n_hypotheses_tested}`);
      console.log(`   ✅ Promoted: ${r.n_promoted}  (${rate}% rate)`);
      if (r.promoted_laws?.length) {
        console.log('\n   Pressure-Guided Discoveries:');
        r.promoted_laws.forEach(l =>
          console.log(`   🎯 [${l.zone_type}] ${String(l.law_name).slice(0,40).padEnd(42)} prec:${(l.precision*100).toFixed(0)}%  EAE:${l.eae?.toFixed(4)}`));
      }
      console.log(`\n   ${r.cycle_summary}`);
    } else pp(r);
    break;
  }
  case 'report': {
    banner('Pressure: Activity Report');
    const r = await pythonPressureReport({});
    if (r?.total_mandates !== undefined) {
      console.log(`\n   Total mandates: ${r.total_mandates}`);
      console.log(`   Promoted: ${r.n_promoted_from_pressure}  Rate: ${(r.conversion_rate*100)?.toFixed(1)}%`);
      console.log(`   Best zone type: ${r.best_zone_type}`);
      console.log(`   Hotspots: ${r.hotspots?.join(', ')}`);
      console.log(`   Health: ${r.pressure_health}`);
    } else pp(r);
    break;
  }
  case 'full': {
    banner('Pressure: Full Build');
    const r = await pythonPressureBuildFull({});
    if (r?.n_zones !== undefined) {
      console.log(`\n   Zones: ${r.n_zones}  |  Mandates: ${r.n_mandates}  |  Promoted: ${r.n_promoted}`);
      console.log(`   Top pressure: ${r.top_pressure}`);
      console.log(`   Cycle: ${r.cycle_id}`);
    } else pp(r);
    break;
  }
  default: console.log(`Unknown section: ${section}`); process.exit(1);
}
