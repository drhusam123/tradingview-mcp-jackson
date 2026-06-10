#!/usr/bin/env node
/**
 * Phase 22 — Causal Discovery Engine runner
 *
 * Sections:
 *   transfer       — transfer entropy between sectors
 *   lagged         — lagged causal inference
 *   stability      — causal chain stability over time
 *   regime         — regime-conditional causality  --regime BULL
 *   macro          — macro → sector transmission
 *   full           — run all (transfer + lagged + stability)
 */
import { pythonCausalTransferEntropy, pythonCausalLaggedInference,
         pythonCausalStability, pythonCausalRegime,
         pythonCausalMacroTransmission, pythonCausalBuildFull } from '../src/egx/index.js';

const args    = process.argv.slice(2);
const section = args.find(a => !a.startsWith('--')) ?? 'full';
const regime  = args[args.indexOf('--regime') + 1] ?? 'BULL';

function banner(t) { console.log('\n' + '═'.repeat(60) + `\n  🔗 ${t}\n` + '═'.repeat(60)); }
function pp(o)     { console.log(JSON.stringify(o, null, 2)); }

async function runTransfer() {
  banner('Causal: Transfer Entropy (sector→sector)');
  const r = await pythonCausalTransferEntropy({ tau_max: 5, n_sectors: 10 });
  if (r?.top_links) {
    console.log(`   Links found: ${r.n_links_found}  Sectors: ${r.n_sectors}`);
    r.top_links.forEach(l =>
      console.log(`   ${String(l.source).padEnd(20)} → ${String(l.target).padEnd(20)} lag:${l.lag}d r:${l.strength}`));
  } else pp(r);
}

async function runLagged() {
  banner('Causal: Lagged Inference');
  const r = await pythonCausalLaggedInference({ min_lag: 1, max_lag: 10 });
  if (r?.n_validated !== undefined)
    console.log(`   Validated chains: ${r.n_validated}  Sector pairs: ${Object.keys(r.sector_lead_lags ?? {}).length}`);
  pp(r);
}

async function runStability() {
  banner('Causal: Chain Stability Analysis');
  const r = await pythonCausalStability({});
  if (r?.n_chains_tested !== undefined)
    console.log(`   Tested: ${r.n_chains_tested}  Avg stability: ${r.avg_stability}  Unstable: ${r.unstable_chains?.length}`);
  pp(r);
}

async function runRegime() {
  banner(`Causal: Regime Causality (${regime})`);
  const r = await pythonCausalRegime({ regime });
  pp(r);
}

async function runMacro() {
  banner('Causal: Macro Transmission');
  const r = await pythonCausalMacroTransmission({});
  pp(r);
}

async function runFull() {
  banner('Causal: Full Discovery Pipeline');
  const r = await pythonCausalBuildFull({});
  pp(r);
}

switch (section) {
  case 'transfer':  await runTransfer(); break;
  case 'lagged':    await runLagged(); break;
  case 'stability': await runStability(); break;
  case 'regime':    await runRegime(); break;
  case 'macro':     await runMacro(); break;
  case 'full':      await runFull(); break;
  default: console.log(`Unknown: ${section}`); process.exit(1);
}
