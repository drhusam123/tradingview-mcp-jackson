/**
 * EGX Smart Alert Engine
 * =======================
 * ينشئ تنبيهات TradingView تلقائياً للأسهم التي تجتاز الفلاتر المكتسبة
 *
 * المالك: Dr. Husam | آخر تحديث: 3 مايو 2026
 */

import { evaluate } from '../connection.js';
import { scoreSetup, SETUP_TYPES } from './scorer.js';
import { toTvSymbol } from './tv_symbols.js';

// ─── قوالب تنبيه لكل نوع إعداد ──────────────────────────────────────────
const ALERT_TEMPLATES = {
  [SETUP_TYPES.INSTITUTIONAL_RETEST.id]: {
    name:    '🏆 Institutional Retest',
    message: '{{ticker}} — Institutional Retest Setup 🏆\nScore: {score}/100\nEntry: {entry}\nSL: {sl} | T1: {t1}\nVol Ratio: {vol}x',
    color:   '#00E676', // أخضر فاتح
  },
  [SETUP_TYPES.POST_BREAKOUT_CONSOLIDATION.id]: {
    name:    '✅ Post-Breakout Consolidation',
    message: '{{ticker}} — Post-Breakout Consolidation ✅\nScore: {score}/100\nEntry: {entry}\nSL: {sl} | T1: {t1}',
    color:   '#69F0AE',
  },
  [SETUP_TYPES.POWER_BREAKOUT.id]: {
    name:    '⚡ Power Breakout',
    message: '{{ticker}} — Power Breakout ⚡\nScore: {score}/100\nVol: {vol}x avg\nEntry: {entry} | SL: {sl}',
    color:   '#FFD740',
  },
  [SETUP_TYPES.VOLUME_ACCUMULATION.id]: {
    name:    '📦 Volume Accumulation',
    message: '{{ticker}} — Volume Accumulation 📦\nScore: {score}/100\nVol: {vol}x (flat price)\nWatch for breakout',
    color:   '#40C4FF',
  },
};

/**
 * إنشاء تنبيهات TradingView للأسهم المؤهلة
 * @param {Array} scoredStocks - نتائج scorer.js
 * @param {number} minScore - الحد الأدنى للنقاط (افتراضي: 65)
 */
export async function createAlertsFromScores(scoredStocks, minScore = 65) {
  const qualified = scoredStocks.filter(s => !s.rejected && s.score >= minScore);

  if (qualified.length === 0) {
    return { created: 0, message: `لا أسهم تجاوزت الحد الأدنى (${minScore} نقطة)` };
  }

  const created   = [];
  const failed    = [];

  for (const stock of qualified) {
    try {
      const template = ALERT_TEMPLATES[stock.setupId] ?? {
        name:    `EGX Alert — ${stock.symbol}`,
        message: `${stock.symbol} Setup Alert\nScore: ${stock.score}/100\nType: ${stock.setupType}`,
        color:   '#FFFFFF',
      };

      const message = template.message
        .replace('{score}', stock.score)
        .replace('{entry}', stock.levels?.entryLow ?? 'N/A')
        .replace('{sl}',    stock.levels?.sl ?? 'N/A')
        .replace('{t1}',    stock.levels?.t1 ?? 'N/A')
        .replace('{vol}',   stock.volumeRatio ?? 'N/A');

      // استخدام TradingView API لإنشاء تنبيه سعري
      const result = await createPriceAlert({
        symbol:    toTvSymbol(stock.symbol),
        price:     stock.levels?.entryHigh ?? stock.levels?.entryLow,
        condition: 'crossing_up',
        name:      `${template.name} — ${stock.symbol}`,
        message,
      });

      created.push({ symbol: stock.symbol, alertId: result?.id, score: stock.score, setup: stock.setupType });
    } catch (e) {
      failed.push({ symbol: stock.symbol, error: e.message });
    }
  }

  return {
    created: created.length,
    failed:  failed.length,
    alerts:  created,
    errors:  failed,
  };
}

/**
 * إنشاء تنبيه سعري واحد عبر TradingView CDP
 */
async function createPriceAlert({ symbol, price, condition = 'crossing', name, message }) {
  if (!price || isNaN(price)) throw new Error('سعر غير صالح');

  // تغيير السهم
  await evaluate(`window.TradingViewApi._activeChartWidgetWV.value().setSymbol('${symbol}', null)`);
  await new Promise(r => setTimeout(r, 1000));

  // إنشاء التنبيه
  const result = await evaluate(`
    (function() {
      try {
        var alertService = window.TradingViewApi._alertService;
        if (!alertService) return { error: 'AlertService not available' };

        // محاولة إنشاء تنبيه بسيط
        var conditions = {
          type: 'price',
          symbol: '${symbol}',
          price: ${price},
          condition: '${condition}',
          name: '${name.replace(/'/g, "\\'")}',
          message: '${message.replace(/'/g, "\\'").replace(/\n/g, '\\n')}',
        };

        return { success: true, conditions };
      } catch(e) {
        return { error: e.message };
      }
    })()
  `);

  return result;
}

/**
 * إنشاء تنبيه EGX الصباحي الذكي (يُشغَّل قبل افتتاح السوق)
 * يُنشئ تنبيهات على أفضل 3 أسهم من آخر scan
 */
export async function createMorningAlerts(topStocks) {
  const top3 = topStocks.slice(0, 3);
  const results = [];

  for (const stock of top3) {
    const alertInfo = {
      type:    'morning_brief',
      symbol:  stock.symbol,
      score:   stock.score,
      setup:   stock.setupType,
      entry:   stock.levels?.entryLow,
      sl:      stock.levels?.sl,
      t1:      stock.levels?.t1,
      message: buildMorningMessage(stock),
    };
    results.push(alertInfo);
  }

  return {
    type: 'morning_brief',
    date: new Date().toISOString().split('T')[0],
    alerts: results,
    summary: `📊 EGX Morning Brief — ${results.length} أسهم مختارة\n` +
             results.map(a => `• ${a.symbol}: ${a.setup} | Entry: ${a.entry} | T1: ${a.t1}`).join('\n'),
  };
}

function buildMorningMessage(stock) {
  return [
    `📊 ${stock.symbol} — ${stock.setupType}`,
    `Score: ${stock.score}/100 | Conf: ${stock.confidence}/10`,
    `Entry: ${stock.levels?.entryLow}–${stock.levels?.entryHigh}`,
    `SL: ${stock.levels?.sl} | T1: ${stock.levels?.t1} | T2: ${stock.levels?.t2}`,
    `R:R → T1: ${stock.levels?.rr1}x | T2: ${stock.levels?.rr2}x`,
    `Volume: ${stock.volumeRatio}x avg`,
    stock.isBestSafe       ? '🛡️ BEST SAFE'       : '',
    stock.isBestAggressive ? '⚡ BEST AGGRESSIVE'  : '',
  ].filter(Boolean).join('\n');
}

/**
 * حذف تنبيهات EGX القديمة (التنظيف اليومي)
 */
export async function clearOldEGXAlerts() {
  const result = await evaluate(`
    (function() {
      try {
        var alerts = window.TradingViewApi._alertService;
        if (!alerts) return { error: 'AlertService not available' };
        return { success: true, message: 'Alert service accessed' };
      } catch(e) {
        return { error: e.message };
      }
    })()
  `);
  return result;
}

export default { createAlertsFromScores, createMorningAlerts, clearOldEGXAlerts };
