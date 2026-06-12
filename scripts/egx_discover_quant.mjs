#!/usr/bin/env node
/** Run quant_discovery with full P6 discovery context (feedback + directives). */
import { runQuantDiscovery } from './lib/run_quant_discovery.mjs';

const dateArg = process.argv.find((a, i) => process.argv[i - 1] === '--date');
const { result } = runQuantDiscovery({ signalDate: dateArg || null });
console.log(JSON.stringify(result, null, 2));
