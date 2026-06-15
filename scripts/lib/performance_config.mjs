import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { PROJECT_ROOT } from './load_env.mjs';

const DEFAULTS = {
  profile: 'macbook_i9_16gb',
  max_workers: 4,
  memory_mode: 'balanced',
  chunk_size: 50000,
  enable_heavy_research: false,
  daily_timeout_minutes: 20,
  deep_research_timeout_minutes: 120,
};

let cached = null;

export function loadPerformanceConfig() {
  if (cached) return cached;
  const p = join(PROJECT_ROOT, 'config', 'performance.json');
  if (!existsSync(p)) {
    cached = { ...DEFAULTS, source: 'defaults' };
    return cached;
  }
  try {
    cached = { ...DEFAULTS, ...JSON.parse(readFileSync(p, 'utf8')), source: p };
  } catch {
    cached = { ...DEFAULTS, source: 'defaults_parse_error' };
  }
  return cached;
}

export function applyPerformanceEnv() {
  const cfg = loadPerformanceConfig();
  if (!process.env.MAX_WORKERS) process.env.MAX_WORKERS = String(cfg.max_workers);
  if (!process.env.MEMORY_MODE) process.env.MEMORY_MODE = cfg.memory_mode;
  if (!process.env.CHUNK_SIZE) process.env.CHUNK_SIZE = String(cfg.chunk_size);
  if (!process.env.ENABLE_HEAVY_RESEARCH) {
    process.env.ENABLE_HEAVY_RESEARCH = cfg.enable_heavy_research ? '1' : '0';
  }
  return cfg;
}
