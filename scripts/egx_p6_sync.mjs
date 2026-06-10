#!/usr/bin/env node
/**
 * P6 sync — consume p6_research_context via evolution + cognition (no Telegram).
 * Run after egx:closed:loop to close the research feedback loop same day.
 *
 * Usage: node scripts/egx_p6_sync.mjs [--evolution-only] [--cognition-only] [--quick]
 */
import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { P6_CONTEXT_PATH, loadP6ResearchContext } from './lib/p6_research_context.mjs';
import { countDirectiveStats, resolveEvolutionDirectives, resolveCognitionDirectives } from './lib/directive_resolver.mjs';
import { pythonEvoP6Sync, pythonCogStockDNA, pythonCogLaws, pythonCogEvolve } from '../src/egx/index.js';

loadEnv();

const NODE = process.execPath;
const EVO_ONLY = process.argv.includes('--evolution-only');
const COG_ONLY = process.argv.includes('--cognition-only');
const QUICK = process.argv.includes('--quick');
const LIGHT = process.argv.includes('--light');

if (!existsSync(P6_CONTEXT_PATH)) {
  console.error('❌ Missing p6_research_context.json — run: npm run egx:closed:loop');
  process.exit(1);
}

const before = countDirectiveStats();
console.log('\n═══ EGX P6 Sync (evolution + cognition) ═══');
console.log(`  Context: ${P6_CONTEXT_PATH}`);
console.log(`  Directives before: ${before.pending}P / ${before.completed}C\n`);

function run(script, args = '') {
  execSync(`"${NODE}" ${script} ${args}`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 600_000 });
}

try {
  if (LIGHT) {
    const p6 = loadP6ResearchContext();
    const params = p6 ? { p6_context: p6 } : {};
    if (!COG_ONLY) {
      console.log('▶  P6 evolution sync (light)...');
      const evo = await pythonEvoP6Sync(params);
      if (evo.error) throw new Error(evo.error);
      const res = resolveEvolutionDirectives(evo);
      console.log(`   failures ingested: ${evo.p6_failures?.n_ingested ?? 0}`);
      console.log(`   stock adjustments: ${evo.p6_adjustments?.symbol_rows_bumped ?? 0}`);
      console.log(`   directives done:   ${res.completed} (+${res.autopsy_completed ?? 0} autopsy)`);
    }
    if (!EVO_ONLY) {
      console.log('\n▶  Cognition quick (light)...');
      const [sd, ul, ev] = await Promise.all([pythonCogStockDNA(), pythonCogLaws(), pythonCogEvolve()]);
      const cog = { stock_dna: sd, universal_laws: ul, self_evolution: ev };
      const res = resolveCognitionDirectives(cog);
      console.log(`   directives done:   ${res.completed}`);
    }
  } else if (!COG_ONLY) {
    console.log('▶  Evolution...');
    run('scripts/egx_evolution.mjs', QUICK ? '--quick' : '');
  }
  if (!LIGHT && !EVO_ONLY) {
    console.log('\n▶  Cognition...');
    try {
      run('scripts/egx_cognition.mjs', QUICK ? '--quick' : '');
    } catch (cogErr) {
      console.log(`\n⚠️  Cognition skipped: ${cogErr.message?.slice(0, 120)}`);
      console.log('   Fix: pip install numpy  |  Or use --evolution-only');
    }
  }
} catch (e) {
  console.error(`\n❌ P6 sync failed: ${e.message?.slice(0, 120)}`);
  process.exit(e.status || 1);
}

const after = countDirectiveStats();
console.log(`\n  Directives after:  ${after.pending}P / ${after.completed}C (Δ${after.completed - before.completed} completed)`);
console.log('\n═══ P6 Sync OK ═══\n');
