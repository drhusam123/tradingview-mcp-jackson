/**
 * Research directive resolver — closes P6_CLOSED_LOOP directives when engines run.
 */
import Database from 'better-sqlite3';
import { existsSync } from 'fs';
import { DB_PATH } from './delivery_audit.mjs';

const ENSURE_SQL = `
CREATE TABLE IF NOT EXISTS research_directives (
  directive_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at      TEXT,
  directive_type  TEXT,
  target          TEXT,
  priority        REAL DEFAULT 0.5,
  rationale       TEXT,
  status          TEXT DEFAULT 'PENDING',
  result          TEXT,
  completed_at    TEXT
);
`;

export function completeResearchDirectives(targets = [], { engine = 'SYSTEM', note = '' } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, completed: 0, error: 'NO_DB' };
  const list = [...new Set((targets || []).filter(Boolean))];
  if (!list.length) return { ok: true, completed: 0 };

  const d = new Database(DB_PATH);
  d.exec(ENSURE_SQL);
  const placeholders = list.map(() => '?').join(',');
  const res = d.prepare(`
    UPDATE research_directives
    SET status = 'COMPLETED',
        completed_at = datetime('now'),
        result = ?
    WHERE status = 'PENDING' AND target IN (${placeholders})
  `).run(note || `completed by ${engine}`, ...list);
  d.close();
  return { ok: true, completed: res.changes, targets: list, engine };
}

export function completeAutopsyDirectives({ engine = 'evolution', note = '' } = {}) {
  if (!existsSync(DB_PATH)) return { ok: false, completed: 0 };
  const d = new Database(DB_PATH);
  d.exec(ENSURE_SQL);
  const res = d.prepare(`
    UPDATE research_directives
    SET status = 'COMPLETED',
        completed_at = datetime('now'),
        result = ?
    WHERE status = 'PENDING'
      AND directive_type = 'P6_CLOSED_LOOP'
      AND target LIKE 'autopsy_%'
  `).run(note || `autopsy rules applied by ${engine}`);
  d.close();
  return { ok: true, completed: res.changes, engine };
}

export function resolveEvolutionDirectives(result = {}) {
  const targets = [];
  const notes = [];

  const p6f = result.p6_failures || {};
  const p6a = result.p6_adjustments || {};
  if (p6f.n_ingested > 0) {
    targets.push('residual_loss_gap');
    notes.push(`${p6f.n_ingested} P6 losses → failure_reconstruction`);
  }
  if (p6a.class_rows_bumped > 0 || p6a.symbol_rows_bumped > 0) {
    targets.push('counterfactual_wr_lift');
    notes.push(`behavioral memory adjusted (${p6a.class_rows_bumped} class / ${p6a.symbol_rows_bumped} symbol)`);
  }

  const main = completeResearchDirectives(targets, {
    engine: 'evolution',
    note: notes.join('; ') || 'evolution cycle complete',
  });
  const autopsy = completeAutopsyDirectives({
    engine: 'evolution',
    note: 'P6 context consumed in evolution pipeline',
  });
  return { ...main, autopsy_completed: autopsy.completed };
}

export function resolveCognitionDirectives(result = {}) {
  const targets = [];
  const notes = [];
  const ea = result.explosion_anatomy || {};
  const ev = result.self_evolution || {};
  const p6 = result.p6_priorities || result.stages?.p6_priorities || {};

  if (ea.p6_explosive_review || p6.priorities?.some(p => p.focus === 'EXPLOSIVE')) {
    targets.push('p6_wr_below_gate');
    notes.push('EXPLOSIVE archetype review completed');
  }
  if (ev.improved_patterns?.length || ev.variants_tested > 0) {
    targets.push('counterfactual_win_collateral');
    notes.push(`self_evolve: ${ev.improved_patterns?.length ?? 0} pattern improvements`);
  }

  return completeResearchDirectives(targets, {
    engine: 'cognition',
    note: notes.join('; ') || 'cognition cycle complete',
  });
}

const OPP_ALERT_TO_TARGET = {
  QUALITY_DECLINING: 'opp_quality_decline',
  MISSED_HIGH_OPP_RISING: 'opp_missed_trend',
  SAFETY_BLOCKS_RISING: 'opp_safety_collateral',
  DELIVERY_IMPROVING: 'opp_missed_high',
};

export function resolveDiscoveryDirectives({
  quantOk = false,
  oppOk = false,
  oppFollowup = null,
  feedback = null,
  structuralOk = false,
} = {}) {
  const targets = [];
  const notes = [];

  if (quantOk) {
    targets.push('counterfactual_wr_lift', 'residual_loss_gap');
    notes.push('quant_discovery consumed P6 feedback');
  }

  if (oppOk) {
    const alertCodes = new Set((oppFollowup?.alerts || []).map(a => a.code));
    for (const [code, target] of Object.entries(OPP_ALERT_TO_TARGET)) {
      if (alertCodes.has(code)) targets.push(target);
    }
    const hasPromotionGap = (feedback?.queue || []).some(q => q.type === 'PROMOTION_GAP');
    if (hasPromotionGap) targets.push('opp_missed_high');
    if (oppFollowup?.directives?.length) {
      for (const d of oppFollowup.directives) {
        if (d.id) targets.push(d.id);
      }
    }
    if (!targets.length) targets.push('opp_blocked_safety');
    notes.push(`opp_v2 tuned (${oppFollowup?.alerts?.length ?? 0} alerts)`);
  }

  if (structuralOk) {
    targets.push('autopsy_structural_laws');
    notes.push('structural_laws merged into runtime overlay');
  }

  if (quantOk && oppOk) {
    targets.push('discovery_quality_low', 'near_ath_discovery_risk');
    notes.push('discovery quality gates applied in quant+opp pipeline');
  }

  return completeResearchDirectives([...new Set(targets)], {
    engine: 'discovery',
    note: notes.join('; ') || `quant=${quantOk} opp=${oppOk}`,
  });
}

export function resolveClosedLoopDirectives({ learning = null, runtime = null, oppFollowup = null } = {}) {
  const targets = [];
  const notes = [];

  if (learning?.counterfactual?.wr_delta > 0 && runtime?.applied_laws?.length) {
    targets.push('counterfactual_wr_lift');
    notes.push(`runtime overlay: ${runtime.applied_laws.length} laws`);
  }
  if (learning?.loss_autopsy?.proposed_rules?.length) {
    // autopsy rules merged into runtime or discovery — keep pending until evolution
  }
  if (oppFollowup?.directives?.length) {
    // ingested as new PENDING; do not complete here
  }

  return completeResearchDirectives(targets, {
    engine: 'closed_loop',
    note: notes.join('; ') || 'closed loop applied',
  });
}

export function countDirectiveStats() {
  if (!existsSync(DB_PATH)) return { pending: 0, completed: 0 };
  const d = new Database(DB_PATH, { readonly: true });
  try {
    const row = d.prepare(`
      SELECT
        SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed
      FROM research_directives
    `).get();
    return { pending: row?.pending ?? 0, completed: row?.completed ?? 0 };
  } catch {
    return { pending: 0, completed: 0 };
  } finally {
    d.close();
  }
}
