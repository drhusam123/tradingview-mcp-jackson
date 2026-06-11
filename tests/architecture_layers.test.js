import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import Database from 'better-sqlite3';
import { join } from 'path';
import { tmpdir } from 'os';
import { unlinkSync } from 'fs';
import { LAYER_GRAPH, REFRESH_PIPELINE, CLOSED_LOOP_PIPELINE } from '../scripts/lib/architecture_layers.mjs';
import { latestActionableSignalDate } from '../scripts/lib/final_signals_query.mjs';

describe('architecture_layers', () => {
  it('defines L0-L11 with upstream/downstream wiring', () => {
    const ids = LAYER_GRAPH.map(l => l.id);
    assert.ok(ids.includes('L0'));
    assert.ok(ids.includes('L11'));
    assert.equal(ids.length, 12);
    for (const layer of LAYER_GRAPH) {
      assert.ok(layer.anchors?.length >= 1, `${layer.id} needs anchors`);
      assert.ok(Array.isArray(layer.upstream));
      assert.ok(Array.isArray(layer.downstream));
    }
    const l5 = LAYER_GRAPH.find(l => l.id === 'L5');
    assert.ok(l5.downstream.includes('L6'));
    const l11 = LAYER_GRAPH.find(l => l.id === 'L11');
    assert.ok(l11.downstream.includes('L7'));
  });

  it('refresh pipeline orders ML align before opportunity', () => {
    const mlIdx = REFRESH_PIPELINE.indexOf('L4_ml_upstream_align');
    const oppIdx = REFRESH_PIPELINE.indexOf('L7_opportunity_score_v2');
    assert.ok(mlIdx >= 0 && oppIdx > mlIdx);
    assert.ok(CLOSED_LOOP_PIPELINE.includes('L7_discovery_refresh'));
  });
});

describe('latestActionableSignalDate', () => {
  it('skips 2099 actionable rows', () => {
    const path = join(tmpdir(), `act_${Date.now()}.db`);
    const db = new Database(path);
    db.exec('CREATE TABLE final_signals (trade_date TEXT, actionable INTEGER)');
    db.prepare("INSERT INTO final_signals VALUES ('2026-06-10',1)").run();
    db.prepare("INSERT INTO final_signals VALUES ('2099-01-01',1)").run();
    assert.equal(latestActionableSignalDate(db), '2026-06-10');
    db.close();
    unlinkSync(path);
  });
});
