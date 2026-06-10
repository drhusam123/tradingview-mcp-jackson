---
name: post-mortem
description: >
  تحليل ما بعد الجلسة — يجلب الأسعار الحالية، يحسب P&L لكل توصية،
  يحدّث TRADING_LESSONS.md وقاعدة البيانات، ويستخرج دروساً جديدة.
  استخدمه بعد إغلاق الصفقات أو في نهاية يوم التداول.
---

# Post-Mortem Skill — EGX

أنت تُجري تحليل ما بعد الجلسة وتُحدّث قاعدة التعلم.

---

## الخطوة 1: جلب الأسعار الحالية

```javascript
import { setSymbol } from './src/core/chart.js';
import { getQuote, getOhlcv } from './src/core/data.js';
import { getDB } from './src/egx/index.js';

// جلب آخر scan من قاعدة البيانات
const db = getDB();
const lastScan = db.prepare(`
  SELECT DISTINCT symbol, scan_date, entry_low, entry_high, stop_loss, t1, t2, setup_type, score
  FROM scans
  WHERE scan_date = (SELECT MAX(scan_date) FROM scans)
  ORDER BY score DESC
`).all();

// جلب السعر الحالي لكل سهم
const currentPrices = [];
for (const scan of lastScan) {
  await setSymbol({ symbol: `EGX:${scan.symbol}` });
  await new Promise(r => setTimeout(r, 1500));
  const q = await getQuote();
  currentPrices.push({ ...scan, currentPrice: q.last ?? q.close, high: q.high, low: q.low });
}

console.log(JSON.stringify(currentPrices, null, 2));
```

---

## الخطوة 2: حساب النتائج لكل صفقة

لكل سهم في آخر scan:

| الحقل | الحساب |
|------|--------|
| `entered` | هل السعر دخل منطقة الدخول؟ |
| `hit_sl` | هل السعر نزل إلى أو دون الـ SL؟ |
| `hit_t1` | هل السعر وصل لـ T1؟ |
| `hit_t2` | هل السعر وصل لـ T2؟ |
| `pnl_pct` | (exitPrice - entryPrice) / entryPrice × 100 |
| `result`  | 'win' / 'loss' / 'breakeven' |

```javascript
import { saveTrade, savePostMortem, updateSetupPerformance } from './src/egx/index.js';

for (const pos of closedPositions) {
  // حفظ الصفقة
  saveTrade({
    scan_id:    pos.scan_id,
    scan_date:  pos.scan_date,
    symbol:     pos.symbol,
    setup_type: pos.setup_type,
    entry_price: pos.entry_price,
    entry_date: pos.scan_date,
    exit_price: pos.exit_price,
    exit_date:  today,
    pnl_pct:    pos.pnl_pct,
    result:     pos.result,
    hit_t1:     pos.hit_t1 ? 1 : 0,
    hit_sl:     pos.hit_sl ? 1 : 0,
    hold_days:  pos.hold_days,
  });

  // تحديث أداء النمط
  const dow = new Date(pos.entry_date).getDay();
  const dayNames = ['الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس'];
  updateSetupPerformance(pos.setup_type, dow, dayNames[dow], pos.result === 'win', pos.pnl_pct);
}
```

---

## الخطوة 3: جدول الأداء (الإخراج الإلزامي)

```
╔══════════════════════════════════════════════════════════════╗
║  POST-MORTEM — [التاريخ]                                    ║
╠══════════════════════════════════════════════════════════════╣
║  Win Rate: [X]%  |  صفقات: [N]  |  ربح: [N]  |  خسارة: [N] ║
╚══════════════════════════════════════════════════════════════╝

| السهم | Setup | سعر الدخول | الإغلاق | PnL% | Hit | النتيجة |
|-------|-------|-----------|---------|------|-----|---------|
| PHDC  | Inst. Retest | 11.36 | 12.40 | +9.2% | T1 ✅ | WIN |
| ...   | ...   | ...  | ...  | ...  | ... | ... |

🟢 الفائزون: [قائمة]
🔴 الخاسرون: [قائمة مع سبب الخسارة]
```

---

## الخطوة 4: استخراج الدروس الجديدة

بعد حساب النتائج، ابحث عن أنماط:

1. **هل الفلاتر المكتسبة منعت الأخطاء؟**
   - هل أي سهم مرفوض كان سيخسر؟ → تأكيد الفلتر
   - هل سهم اجتاز الفلاتر وخسر؟ → فلتر جديد مطلوب

2. **هل يوجد نمط جديد في الخسائر؟**
   - سبب مشترك؟ (حجم، قطاع، وقت، setup type)

3. **تحديث TRADING_LESSONS.md إذا لزم**

---

## الخطوة 5: حفظ الـ Post-Mortem في قاعدة البيانات

```javascript
savePostMortem({
  session_date: today,
  total_trades: trades.length,
  wins:         wins.length,
  losses:       losses.length,
  breakevens:   breakevens.length,
  win_rate:     wins.length / trades.length * 100,
  avg_win_pct:  avgWin,
  avg_loss_pct: avgLoss,
  best_trade:   bestTrade.symbol,
  worst_trade:  worstTrade.symbol,
  best_pnl_pct: bestTrade.pnl_pct,
  worst_pnl_pct: worstTrade.pnl_pct,
  key_lessons:  JSON.stringify(newLessons),
});
```

---

## الخطوة 6: تحديث Win Rate في CLAUDE.md

```javascript
import { readFileSync, writeFileSync } from 'fs';

const claude = readFileSync('CLAUDE.md', 'utf8');
const updated = claude.replace(
  /Win Rate الحالية.*?\n/,
  `Win Rate الحالية: ${newWinRate}% (جلسة ${sessionNum} — ${today}) — الهدف: ≥ 70%\n`
);
writeFileSync('CLAUDE.md', updated);
```

---

## الناتج النهائي

بعد اكتمال الـ post-mortem، اعرض:
1. جدول النتائج الكامل
2. Win Rate المحدّث
3. الدروس الجديدة المكتسبة (إن وُجدت)
4. تأكيد أن البيانات حُفظت في `data/egx_trading.db`
5. رسم الـ Win Rate trajectory (جلسة 1: 56% → الهدف: 70%)
