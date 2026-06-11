/**
 * Merge DMIDS structural_laws knowledge base into egx_rules_runtime.json overlay.
 */
import { existsSync, readFileSync, writeFileSync, readdirSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';
const KB_DIR = join(PROJECT_ROOT, 'data/knowledge_base');
const RUNTIME_PATH = join(PROJECT_ROOT, 'data/egx_rules_runtime.json');

function readRuntimeOverlay() {
  if (!existsSync(RUNTIME_PATH)) return null;
  try {
    return JSON.parse(readFileSync(RUNTIME_PATH, 'utf8'));
  } catch {
    return null;
  }
}

export function latestStructuralLawsFile() {
  if (!existsSync(KB_DIR)) return null;
  const files = readdirSync(KB_DIR)
    .filter(f => f.startsWith('structural_laws_') && f.endsWith('.json'))
    .sort()
    .reverse();
  return files[0] ? join(KB_DIR, files[0]) : null;
}

export function mergeStructuralLawsIntoRuntime({ minSupportPct = 28, maxLaws = 16 } = {}) {
  const lawFile = latestStructuralLawsFile();
  if (!lawFile) return { ok: false, n_merged: 0, reason: 'NO_STRUCTURAL_LAWS_FILE' };

  const pack = JSON.parse(readFileSync(lawFile, 'utf8'));
  const upLaws = (pack.laws || [])
    .filter(l => String(l.directions || '').toUpperCase() === 'UP')
    .filter(l => Number(l.support_pct || 0) >= minSupportPct)
    .sort((a, b) => Number(b.support_pct) - Number(a.support_pct))
    .slice(0, maxLaws)
    .map(l => ({
      id: l.id,
      title: l.title,
      support_pct: l.support_pct,
      effect_size: l.effect_size,
      confidence_level: l.confidence_level,
    }));

  const overlay = readRuntimeOverlay() || {
    at: new Date().toISOString(),
    source: 'structural_laws_bridge',
    behavioral_filters: {},
    lessons_filters: {},
    applied_laws: [],
    warnings: [],
  };

  overlay.at = new Date().toISOString();
  overlay.structural_laws = upLaws;
  overlay.structural_laws_file = lawFile.replace(`${PROJECT_ROOT}/`, '');

  if (upLaws.some(l => String(l.title || '').toLowerCase().includes('volatile'))) {
    overlay.behavioral_filters = {
      ...overlay.behavioral_filters,
      prefer_lower_third_close: true,
    };
  }

  const existing = new Set((overlay.applied_laws || []).map(l => l.id));
  for (const law of upLaws.slice(0, 6)) {
    if (existing.has(`structural_${law.id}`)) continue;
    overlay.applied_laws.push({
      id: `structural_${law.id}`,
      evidence: law.support_pct,
      confidence: law.confidence_level || 'MEDIUM',
      source: 'structural_laws',
    });
  }

  writeFileSync(RUNTIME_PATH, JSON.stringify(overlay, null, 2));
  return { ok: true, n_merged: upLaws.length, file: overlay.structural_laws_file };
}
