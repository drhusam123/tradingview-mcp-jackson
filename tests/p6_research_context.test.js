import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'fs';
import {
  buildP6ResearchContext,
  writeP6ResearchContext,
  loadP6ResearchContext,
  P6_CONTEXT_PATH,
} from '../scripts/lib/p6_research_context.mjs';
import { analyzeOpportunityTrend } from '../scripts/lib/opportunity_followup.mjs';
import { DB_PATH } from '../scripts/lib/delivery_audit.mjs';

describe('P6 research context', () => {
  it('buildP6ResearchContext returns gate + hints structure', () => {
    const ctx = buildP6ResearchContext({
      signalDate: '2026-06-10',
      forensic: { n_losses: 4, by_class: { EXPLOSIVE: { n: 10, wins: 2, losses: 8 } } },
      discovery: {
        queue: [{ type: 'DOWNRANK_BEHAVIORAL', target: 'EXPLOSIVE', priority: 0.85, rationale: 'test' }],
      },
    });
    assert.ok(ctx.p6_gate);
    assert.ok(ctx.evolution_hints);
    assert.ok(Array.isArray(ctx.research_priorities));
    assert.equal(ctx.evolution_hints.downrank_behavioral.includes('EXPLOSIVE'), true);
  });

  it('write + load roundtrip', () => {
    const ctx = buildP6ResearchContext({ signalDate: '2026-06-10' });
    writeP6ResearchContext(ctx);
    assert.ok(existsSync(P6_CONTEXT_PATH));
    const loaded = loadP6ResearchContext();
    assert.ok(loaded);
    assert.equal(loaded.signal_date, '2026-06-10');
  });

  it('queryUltraLosses via build when DB present', { skip: !existsSync(DB_PATH) }, () => {
    const ctx = buildP6ResearchContext();
    assert.ok(Array.isArray(ctx.ultra_losses));
  });
});

describe('opportunity followup', () => {
  it('analyzeOpportunityTrend returns trends object', () => {
    const r = analyzeOpportunityTrend({ window: 3 });
    assert.ok(r.trends);
    assert.ok(Array.isArray(r.alerts));
    assert.ok(Array.isArray(r.directives));
  });
});
