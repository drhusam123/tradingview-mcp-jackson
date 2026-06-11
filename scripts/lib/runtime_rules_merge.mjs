/**
 * Merge delivery_laws + autopsy evidence into runtime rules overlay.
 * Does NOT edit egx_rules.json — writes data/egx_rules_runtime.json for safety check.
 */
import { existsSync, readFileSync, writeFileSync, mkdirSync, readdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
import { loadEgxRules, RULES_PATH } from './egx_safety_check.mjs';
import { latestStructuralLawsFile } from './structural_laws_bridge.mjs';

const RUNTIME_PATH = join(PROJECT_ROOT, 'data/egx_rules_runtime.json');
const KB_DIR = join(PROJECT_ROOT, 'data/knowledge_base');

const LAW_TO_FILTER = {
  delivery_law_repeat_loser: { max_ultra_losses_per_symbol: 1 },
  delivery_law_indicator_cache: { require_indicator_cache: true },
  delivery_law_explosive_rsi: { explosive_max_rsi: 70 },
  delivery_law_volatile: { block_volatile_client: true },
};

export function latestDeliveryLawsFile() {
  if (!existsSync(KB_DIR)) return null;
  const files = readdirSync(KB_DIR)
    .filter(f => f.startsWith('delivery_laws_') && f.endsWith('.json'))
    .sort()
    .reverse();
  return files[0] ? join(KB_DIR, files[0]) : null;
}

export function mergeRuntimeRules({ learningReport = null, minEvidence = 2 } = {}) {
  const base = loadEgxRules();
  const overlay = {
    at: new Date().toISOString(),
    source: 'runtime_rules_merge',
    base_rules: RULES_PATH,
    behavioral_filters: { ...base.behavioral_filters },
    lessons_filters: { ...base.lessons_filters },
    applied_laws: [],
    warnings: [],
  };

  const lawFile = latestDeliveryLawsFile();
  if (lawFile) {
    try {
      const pack = JSON.parse(readFileSync(lawFile, 'utf8'));
      for (const law of pack.laws || []) {
        const evidence = Number(law.evidence) || 0;
        const conf = (law.confidence || '').toUpperCase();
        const mapping = LAW_TO_FILTER[law.id];
        if (!mapping) continue;
        if (conf === 'MANDATORY' || (conf === 'HIGH' && evidence >= minEvidence)) {
          Object.assign(overlay.behavioral_filters, mapping);
          overlay.applied_laws.push({ id: law.id, evidence, confidence: conf });
        }
      }
    } catch (e) {
      overlay.warnings.push(`delivery_laws parse: ${e.message}`);
    }
  }

  const autopsy = learningReport?.loss_autopsy;
  if (autopsy?.proposed_rules?.length) {
    for (const rule of autopsy.proposed_rules) {
      if ((rule.evidence || 0) >= minEvidence) {
        overlay.applied_laws.push({ id: `autopsy_${rule.id}`, evidence: rule.evidence, source: 'autopsy' });
      }
    }
  }

  const structFile = latestStructuralLawsFile();
  if (structFile) {
    try {
      const pack = JSON.parse(readFileSync(structFile, 'utf8'));
      const upLaws = (pack.laws || [])
        .filter(l => String(l.directions || '').toUpperCase() === 'UP')
        .filter(l => Number(l.support_pct || 0) >= 30)
        .slice(0, 8);
      overlay.structural_laws = upLaws.map(l => ({
        id: l.id,
        title: l.title,
        support_pct: l.support_pct,
      }));
      overlay.structural_laws_file = structFile.replace(`${PROJECT_ROOT}/`, '');
    } catch (e) {
      overlay.warnings.push(`structural_laws parse: ${e.message}`);
    }
  }

  mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
  writeFileSync(RUNTIME_PATH, JSON.stringify(overlay, null, 2));
  return overlay;
}

export function loadRuntimeRulesOverlay() {
  if (!existsSync(RUNTIME_PATH)) return null;
  try {
    return JSON.parse(readFileSync(RUNTIME_PATH, 'utf8'));
  } catch {
    return null;
  }
}
