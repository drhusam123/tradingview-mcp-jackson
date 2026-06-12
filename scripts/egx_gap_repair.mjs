#!/usr/bin/env node
/**
 * Gap repair — fixes known data/code gaps then runs verify pipeline.
 *
 * Usage: npm run egx:gap:repair
 *        npm run egx:gap:repair -- --skip-pine --skip-automate
 */
import { execSync } from 'child_process';
import { writeFileSync, mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import { loadEnv, PROJECT_ROOT } from './lib/load_env.mjs';
import { purgeTestFinalSignals } from './lib/final_signals_query.mjs';

loadEnv();

const NODE = process.execPath;
const PYTHON = process.env.PYTHON_BIN || process.env.PYTHON3 || 'python3';
const SKIP_PINE = process.argv.includes('--skip-pine');
const SKIP_AUTOMATE = process.argv.includes('--skip-automate');

const steps = [];

function run(name, fn, { optional = false } = {}) {
  const t0 = Date.now();
  console.log(`\n▶  ${name}`);
  try {
    const result = fn();
    const row = { name, ok: true, ms: Date.now() - t0, result };
    steps.push(row);
    return row;
  } catch (e) {
    const row = { name, ok: false, ms: Date.now() - t0, error: e.message?.slice(0, 200), optional };
    steps.push(row);
    if (!optional) throw e;
    console.log(`  ⚠️  ${e.message?.slice(0, 120)}`);
    return row;
  }
}

console.log('\n═══ EGX Gap Repair ═══\n');

run('purge_test_final_signals', () => purgeTestFinalSignals());

run('pillow_check', () => {
  try {
    execSync(`${PYTHON} -c "import PIL; print(PIL.__version__)"`, {
      cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe',
    });
    return { ok: true };
  } catch {
    execSync(`${PYTHON} -m pip install Pillow -q`, { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 120_000 });
    return { installed: true };
  }
}, { optional: true });

run('parquet_deps', () => {
  try {
    execSync(`${PYTHON} -c "import duckdb, pyarrow; print('ok')"`, {
      cwd: PROJECT_ROOT, encoding: 'utf8', stdio: 'pipe',
    });
    return { ok: true };
  } catch {
    execSync(`${PYTHON} -m pip install duckdb pyarrow -q`, {
      cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 180_000,
    });
    return { installed: true };
  }
}, { optional: true });

if (!SKIP_PINE) {
  run('pine_analytics', () => {
    execSync(`${NODE} scripts/fetch_pine_analytics.mjs session`, {
      cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 900_000,
    });
    return { ok: true };
  }, { optional: true });
}

run('closed_loop', () => {
  execSync(`${NODE} scripts/egx_closed_loop.mjs`, {
    cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 900_000,
  });
  return { ok: true };
});

if (!SKIP_AUTOMATE) {
  run('discovery_automate', () => {
    execSync('npm run egx:discovery:automate', {
      cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 2_400_000,
    });
    return { ok: true };
  });
} else {
  run('discovery_fabric', () => {
    execSync(`${NODE} scripts/egx_discovery_fabric.mjs`, {
      cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 1_200_000,
    });
    return { ok: true };
  });
  run('discovery_verify', () => {
    execSync(`${NODE} scripts/egx_discovery_verify.mjs`, {
      cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 300_000,
    });
    return { ok: true };
  });
}

run('data_layer_audit', () => {
  execSync(`${NODE} scripts/egx_data_layer_audit.mjs`, {
    cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 60_000,
  });
  return { ok: true };
});

run('architecture_audit', () => {
  execSync(`${NODE} scripts/egx_architecture_audit.mjs`, {
    cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 60_000,
  });
  return { ok: true };
});

run('production_acceptance', () => {
  execSync(`${NODE} scripts/egx_production_acceptance.mjs`, {
    cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 120_000,
  });
  return { ok: true };
});

run('offline_tests', () => {
  execSync('npm test', { cwd: PROJECT_ROOT, stdio: 'inherit', timeout: 180_000 });
  return { ok: true };
});

run('purge_test_final_signals_post_tests', () => purgeTestFinalSignals());

const fail = steps.filter(s => !s.ok && !s.optional).length;
const report = { at: new Date().toISOString(), pass: fail === 0, steps };
mkdirSync(join(PROJECT_ROOT, 'data'), { recursive: true });
writeFileSync(join(PROJECT_ROOT, 'data/gap_repair_last.json'), JSON.stringify(report, null, 2));

console.log('\n═══ Gap Repair Summary ═══');
for (const s of steps) {
  console.log(`  ${s.ok ? '✅' : (s.optional ? '⚠️' : '❌')} ${s.name} (${s.ms}ms)`);
}
console.log(`\n=== Gap Repair: ${steps.length - fail}/${steps.length} PASS ===\n`);
process.exit(fail ? 1 : 0);
