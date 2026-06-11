#!/usr/bin/env node
/** Discovery quality report — quant + opportunity precision score. */
import { runDiscoveryQualityLoop } from './lib/discovery_quality_loop.mjs';

const report = runDiscoveryQualityLoop();
console.log(JSON.stringify(report, null, 2));
process.exit(report.error ? 1 : 0);
