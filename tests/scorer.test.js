import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { scoreSetup } from '../src/egx/scorer.js';

function bar(open, high, low, close, volume) {
  return { open, high, low, close, volume };
}

function history({ length = 300, high = 200 } = {}) {
  return Array.from({ length }, (_, i) => bar(100 + i * 0.01, high, 90, 100 + i * 0.01, 1000));
}

function baseBars({ close = 102, volume = 2600 } = {}) {
  return [
    bar(100, 104, 99, 102, 1000),
    bar(102, 105, 101, 103, 1000),
    bar(103, 106, 102, 104, 1000),
    bar(104, 107, 103, 105, 1000),
    bar(105, 110, 100, close, volume),
  ];
}

describe('EGX scorer TRADING_LESSONS consistency', () => {
  it('does not expose win-rate percentages in setup labels or bonus strings', () => {
    const result = scoreSetup({
      symbol: 'COMBO',
      last_5_bars: baseBars(),
      indicators: { rsi: 29, obvDivergence: 'bullish' },
    });

    const clientText = [result.setupType, ...result.bonuses].join(' ');
    assert.equal(result.setupId, 'rsi_obv_combo');
    assert.doesNotMatch(clientText, /\bWR\b|Win Rate|win-rate/i);
  });

  it('does not penalize lower-third closes when structural R:R remains valid', () => {
    const result = scoreSetup({
      symbol: 'LOWCLOSE',
      last_5_bars: baseBars({ close: 102, volume: 2600 }),
      all_bars: history(),
    });

    assert.equal(result.closePosition, 0.2);
    assert.equal(result.levels.rr1, 2);
    assert.equal(result.levels.rr2, 3.5);
    assert.ok(result.bonuses.some(b => b.includes('SL الهيكلي') && b.includes('R:R')));
    assert.ok(!result.bonuses.some(b => b.startsWith('-8:') || b.includes('ضعيف')));
  });

  it('requires 300 bars before applying Near ATH classification/rejection', () => {
    const result = scoreSetup({
      symbol: 'SHORTATH',
      last_5_bars: [
        bar(95, 98, 94, 97, 1000),
        bar(97, 99, 96, 98, 1000),
        bar(98, 100, 97, 99, 1000),
        bar(99, 101, 98, 100, 1000),
        bar(100, 102, 99, 101, 1200),
      ],
    });

    assert.equal(result.isNearATH, false);
    assert.equal(result.athProximityPct, null);
    assert.ok(result.warnings.some(w => w.includes('300')));
    assert.ok(!result.rejections.some(r => r.includes('Near ATH')));
  });

  it('scores the 2.5-3x volume sweet spot above larger volume spikes', () => {
    const sweetSpot = scoreSetup({
      symbol: 'SWEET',
      last_5_bars: baseBars({ volume: 2600 }),
      all_bars: history(),
    });
    const hotterSpike = scoreSetup({
      symbol: 'HOT',
      last_5_bars: baseBars({ volume: 3200 }),
      all_bars: history(),
    });

    assert.ok(sweetSpot.bonuses.some(b => b.includes('النطاق الأمثل')));
    assert.ok(hotterSpike.bonuses.some(b => b.includes('يحتاج تأكيد متابعة')));
    assert.ok(sweetSpot.score > hotterSpike.score);
  });
});
