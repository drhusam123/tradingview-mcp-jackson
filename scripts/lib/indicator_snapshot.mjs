/**
 * Shared indicator snapshot builder for rebuild + historical backfill.
 */
import { calculateIndicators } from '../../src/egx/index.js';

export function formatBarDate(unixSec) {
  return new Date(unixSec * 1000).toISOString().split('T')[0];
}

/** @returns {{ ind: object, barDate: string, lastBar: object } | null} */
export function buildIndicatorPayload(bars) {
  if (!bars || bars.length < 30) return null;

  const ind = calculateIndicators(bars);
  if (!ind) return null;

  const lastBar = bars[bars.length - 1];
  const barDate = formatBarDate(lastBar.time);

  ind.lastClose = lastBar.close;
  ind.rsi = ind.rsi;
  ind.closePosition = (lastBar.close - lastBar.low) / (lastBar.high - lastBar.low || 1);

  ind.ema10 = ind.ema10val ?? null;
  ind.ema20 = ind.ema20val ?? null;
  ind.ema50 = ind.ema50val ?? null;
  ind.ema200 = ind.ema200val ?? null;

  const rsiArr = ind.arrays?.rsi;
  if (rsiArr && rsiArr.length >= 4) {
    const rsiNow = rsiArr[rsiArr.length - 1];
    const rsi3ago = rsiArr[rsiArr.length - 4];
    ind.rsiSlope3d = (rsiNow != null && rsi3ago != null)
      ? +((rsiNow - rsi3ago) / 3).toFixed(3)
      : null;
  } else {
    ind.rsiSlope3d = null;
  }

  const adjCloses = [bars[0].close];
  for (let i = 1; i < bars.length; i++) {
    const prev = adjCloses[adjCloses.length - 1];
    if (prev > 0) {
      const rawRet = (bars[i].close - prev) / prev;
      const clipped = Math.max(-0.25, Math.min(0.25, rawRet));
      adjCloses.push(+(prev * (1 + clipped)).toFixed(4));
    } else {
      adjCloses.push(bars[i].close);
    }
  }
  const lastAdj = adjCloses[adjCloses.length - 1];
  if (bars.length >= 6) ind.momentum5d = +((lastAdj / adjCloses[adjCloses.length - 6] - 1) * 100).toFixed(2);
  if (bars.length >= 11) ind.momentum10d = +((lastAdj / adjCloses[adjCloses.length - 11] - 1) * 100).toFixed(2);
  if (bars.length >= 21) ind.momentum20d = +((lastAdj / adjCloses[adjCloses.length - 21] - 1) * 100).toFixed(2);

  const volArr = bars.slice(-21, -1).map(b => b.volume).filter(v => v > 0);
  if (volArr.length > 0) {
    const sorted = [...volArr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    const medVol20 = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    ind.volumeRatio20 = medVol20 > 0 ? +(lastBar.volume / medVol20).toFixed(2) : null;
  }

  const athPrice = Math.max(...bars.map(b => b.high));
  ind.athProximity = athPrice > 0 ? +((athPrice - lastBar.close) / athPrice).toFixed(4) : null;

  if (ind.bollingerBands) {
    const range = ind.bollingerBands.upper - ind.bollingerBands.lower;
    ind.bollingerBands.position = range > 0
      ? +((lastBar.close - ind.bollingerBands.lower) / range).toFixed(3)
      : 0.5;
    ind.bollingerBands.width = range > 0
      ? +(range / ind.bollingerBands.middle).toFixed(4)
      : null;
  }

  return { ind, barDate, lastBar };
}
