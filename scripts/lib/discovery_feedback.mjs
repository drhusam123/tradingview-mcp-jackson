/**
 * Discovery feedback — loss/win patterns → research priority queue.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

export function buildDiscoveryFeedback({ forensic = null, autopsy = null, opportunity = null } = {}) {
  const queue = [];

  if (forensic?.by_class) {
    for (const [cls, stats] of Object.entries(forensic.by_class)) {
      const wr = stats.n > 0 ? Math.round((stats.wins / stats.n) * 1000) / 10 : 0;
      if (stats.losses >= 2 && wr < 40) {
        queue.push({
          type: 'DOWNRANK_BEHAVIORAL',
          target: cls,
          priority: 0.85,
          rationale: `P6 forensic: ${cls} WR ${wr}% (${stats.wins}W/${stats.losses}L)`,
        });
      }
      if (stats.wins >= 3 && wr >= 55) {
        queue.push({
          type: 'UPRANK_BEHAVIORAL',
          target: cls,
          priority: 0.7,
          rationale: `P6 forensic: ${cls} strong WR ${wr}%`,
        });
      }
    }
  }

  if (autopsy?.flag_counts) {
    for (const [flag, n] of Object.entries(autopsy.flag_counts)) {
      if (n >= 3) {
        queue.push({
          type: 'INVESTIGATE_PATTERN',
          target: flag,
          priority: 0.75,
          rationale: `Loss autopsy flag ${flag} in ${n} cases`,
        });
      }
    }
  }

  if (opportunity?.missed_high_opportunity?.length >= 2) {
    queue.push({
      type: 'PROMOTION_GAP',
      target: 'client_signal_promotion',
      priority: 0.8,
      rationale: `${opportunity.missed_high_opportunity.length} opp≥75 missed actionable`,
      symbols: opportunity.missed_high_opportunity.map(m => m.symbol).slice(0, 5),
    });
  }

  const report = {
    at: new Date().toISOString(),
    n_items: queue.length,
    queue: queue.sort((a, b) => b.priority - a.priority),
  };

  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(join(PROJECT_ROOT, 'data/discovery_feedback_last.json'), JSON.stringify(report, null, 2));
  return report;
}

export function readDiscoveryFeedback() {
  const p = join(PROJECT_ROOT, 'data/discovery_feedback_last.json');
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}
