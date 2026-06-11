import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import { join } from 'path';
import { computeDiscoveryQualityScore, runDiscoveryQualityLoop } from '../scripts/lib/discovery_quality_loop.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('discovery quality', () => {
  it('computeDiscoveryQualityScore returns grade and score', () => {
    const r = computeDiscoveryQualityScore({
      quant: { rules_kept: 80, avg_quality: 62, high_quality_rules: 40, sweet_spot_rules: 12 },
      opportunity: {
        symbols_scored: 200,
        avg_opportunity_score: 58,
        qualified_plus: 25,
        lower_third_count: 10,
        near_ath_risk_count: 2,
      },
    });
    assert.ok(r.discovery_quality_score >= 50);
    assert.ok(['A', 'B', 'C', 'D'].includes(r.grade));
    assert.ok(r.components.quant_avg_quality === 62);
  });

  it('low quality quant scores below high-quality baseline', () => {
    const low = computeDiscoveryQualityScore({
      quant: { rules_kept: 10, avg_quality: 38, high_quality_rules: 1, sweet_spot_rules: 0 },
    });
    const high = computeDiscoveryQualityScore({
      quant: { rules_kept: 80, avg_quality: 72, high_quality_rules: 50, sweet_spot_rules: 20 },
    });
    assert.ok(low.discovery_quality_score < high.discovery_quality_score);
    assert.ok(['C', 'D'].includes(low.grade) || low.discovery_quality_score < 58);
  });

  it('runDiscoveryQualityLoop returns report', { skip: !existsSync(DB_PATH) }, () => {
    const r = runDiscoveryQualityLoop();
    assert.ok('discovery_quality_score' in r);
    assert.ok('grade' in r);
  });
});
