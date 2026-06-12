/**
 * Closed-loop health audit — verify artifacts, freshness, and wiring.
 */
import { existsSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { countDirectiveStats } from './directive_resolver.mjs';

function readJson(rel) {
  const p = join(PROJECT_ROOT, rel);
  if (!existsSync(p)) return null;
  try {
    return JSON.parse(readFileSync(p, 'utf8'));
  } catch {
    return null;
  }
}

function ageHours(rel) {
  const p = join(PROJECT_ROOT, rel);
  if (!existsSync(p)) return null;
  const mtime = statSync(p).mtimeMs;
  return Math.round((Date.now() - mtime) / 36e5 * 10) / 10;
}

export function auditClosedLoops({ maxAgeHours = 168 } = {}) {
  const checks = [];

  const artifacts = [
    { file: 'data/closed_loop_last.json', label: 'closed_loop' },
    { file: 'data/p6_research_context.json', label: 'p6_context' },
    { file: 'data/discovery_feedback_last.json', label: 'discovery_feedback' },
    { file: 'data/opportunity_followup_last.json', label: 'opportunity_followup' },
    { file: 'data/discovery_quality_last.json', label: 'discovery_quality' },
    { file: 'data/discovery_audit_last.json', label: 'discovery_audit' },
    { file: 'data/discovery_refresh_last.json', label: 'discovery_refresh' },
    { file: 'data/tv_microstructure_last.json', label: 'tv_microstructure' },
    { file: 'data/counterfactual_atoms_last.json', label: 'counterfactual_atoms' },
    { file: 'data/discovery_engine_manifest.json', label: 'discovery_engine_manifest' },
    { file: 'data/regime_conditional_sweep_last.json', label: 'regime_sweep' },
    { file: 'data/hypothesis_sandbox_bridge_last.json', label: 'hypothesis_bridge' },
    { file: 'data/p6_delivered_orchestrator_last.json', label: 'p6_delivered_orchestrator' },
    { file: 'data/discovery_fabric_last.json', label: 'discovery_fabric' },
    { file: 'data/discovery_ml_manifest.json', label: 'discovery_ml_manifest' },
    { file: 'data/discovery_data_catalog.json', label: 'discovery_data_catalog' },
    { file: 'data/egx_rules_runtime.json', label: 'runtime_rules' },
    { file: 'data/learning_loop_last.json', label: 'learning_loop' },
    { file: 'data/proof_loop_last.json', label: 'proof_loop' },
    { file: 'data/pre_session_last.json', label: 'pre_session' },
    { file: 'data/post_session_last.json', label: 'post_session' },
    { file: 'data/ml_boost_last.json', label: 'ml_boost' },
    { file: 'data/signal_funnel_last.json', label: 'signal_funnel' },
  ];

  for (const { file, label } of artifacts) {
    const age = ageHours(file);
    const ok = age != null && age <= maxAgeHours;
    checks.push({
      id: label,
      ok,
      age_hours: age,
      detail: age == null ? 'missing' : `age ${age}h`,
    });
  }

  const closed = readJson('data/closed_loop_last.json');
  if (closed?.stages) {
    const failed = closed.stages.filter(s => !s.ok);
    checks.push({
      id: 'closed_loop_stages',
      ok: failed.length === 0,
      detail: failed.length ? `${failed.length} failed: ${failed.map(f => f.name).join(', ')}` : `${closed.stages.length} stages OK`,
    });
  }

  const loops = closed?.loops_closed || [];
  const requiredLoops = [
    { id: 'loop_p6_context', needle: 'p6_research_context' },
    { id: 'loop_discovery_fb', needle: 'discovery_feedback' },
    { id: 'loop_discovery_quality', needle: 'discovery_quality' },
    { id: 'loop_discovery_refresh', needle: 'discovery_refresh' },
    { id: 'loop_tv_microstructure', needle: 'tv_microstructure →' },
    { id: 'loop_counterfactual_atoms', needle: 'counterfactual' },
    { id: 'loop_perpetual_orchestrator', needle: 'perpetual orchestrator' },
    { id: 'loop_regime_sweep', needle: 'regime_conditional_sweep' },
    { id: 'loop_hypothesis_bridge', needle: 'hypothesis_sandbox_bridge' },
    { id: 'loop_discovery_fabric', needle: 'discovery_fabric' },
    { id: 'loop_runtime_rules', needle: 'egx_rules_runtime' },
    { id: 'loop_directive_resolver', needle: 'directive_resolver' },
  ];
  for (const { id, needle } of requiredLoops) {
    const ok = loops.some(l => l.includes(needle));
    checks.push({ id, ok, detail: ok ? 'wired' : 'not in loops_closed (re-run closed loop)' });
  }

  const p6 = readJson('data/p6_research_context.json');
  if (p6) {
    checks.push({
      id: 'p6_ultra_losses_wired',
      ok: Array.isArray(p6.ultra_losses),
      detail: `${p6.ultra_losses?.length ?? 0} ULTRA losses in context`,
    });
    checks.push({
      id: 'p6_evolution_hints',
      ok: !!p6.evolution_hints,
      detail: `downrank: ${(p6.evolution_hints?.downrank_behavioral || []).join(',') || '—'}`,
    });
  }

  const directives = countDirectiveStats();
  checks.push({
    id: 'directives_pending',
    ok: directives.pending < 50,
    detail: `pending ${directives.pending} | completed ${directives.completed}`,
  });

  const pass = checks.every(c => c.ok);
  return {
    at: new Date().toISOString(),
    pass,
    max_age_hours: maxAgeHours,
    checks,
    directives,
    closed_loop_at: closed?.at ?? null,
  };
}
