---
name: egx-scanner
description: >
  ماسح EGX الذكي — يمسح 25+ سهم مصري، يطبّق فلاتر TRADING_LESSONS.md،
  يصنّف كل إعداد، ويبني برايف التوصية الكامل تلقائياً.
  استخدمه عند طلب "scan EGX" أو "أفضل أسهم اليوم" أو "EGX brief".
---

# EGX Smart Scanner Skill

أنت تنفّذ مسحاً شاملاً للسوق المصري وتبني توصية swing trading.

---

## الخطوة 1: تحميل الذاكرة المكتسبة

**اقرأ هذين الملفين أولاً قبل أي تحليل:**
1. `TRADING_LESSONS.md` — الفلاتر والقواعد المكتسبة
2. `agents/egx-analyst.md` — معايير الإعداد الجيد

---

## الخطوة 2: تشغيل الـ Scan

```javascript
// شغّل هذا الكود مباشرة عبر node --input-type=module:
import { setSymbol } from './src/core/chart.js';
import { getOhlcv, getQuote } from './src/core/data.js';
import { rankStocks, EGX_UNIVERSE, saveScan } from './src/egx/index.js';

const today = new Date().toISOString().split('T')[0];
const results = [];
const failed  = [];

for (const sym of EGX_UNIVERSE) {
  try {
    await setSymbol({ symbol: `EGX:${sym}` });
    await new Promise(r => setTimeout(r, 1800));

    const quote = await getQuote();
    const ohlcv = await getOhlcv({ count: 8, summary: true });

    if (!ohlcv.success) { failed.push(sym); continue; }

    results.push({
      symbol:      sym,
      quote:       quote,
      ohlcv:       ohlcv,
      last_5_bars: ohlcv.last_5_bars,
    });
    process.stdout.write(`✓ ${sym}: ${quote.last}\n`);
  } catch(e) {
    failed.push(sym);
    process.stdout.write(`✗ ${sym}: ${e.message}\n`);
  }
}

// تطبيق الفلاتر والتقييم
const ranked = rankStocks(results);
saveScan(today, ranked);

// طباعة الأفضل
const brief = ranked.filter(s => !s.rejected && s.score >= 65).slice(0, 10);
console.log(JSON.stringify({ brief, failed, total: results.length }, null, 2));
```

---

## الخطوة 3: قراءة النتائج

بعد اكتمال الـ scan، خذ نتائج `brief` وابنِ التقرير:

### تنسيق التقرير الإلزامي:

```
🇪🇬 EGX Swing Brief — [التاريخ]
Universe: [N] سهم | Direction: Long Only | Horizon: 1-3 أيام

✅ [N] سهم اجتاز الفلاتر | ❌ [N] مرفوض بالفلاتر | ⚠️ [N] فشل في البيانات

═══ TOP [N] SETUPS ═══

للسهم #1 الأعلى نقاطاً:
  📊 [SYMBOL] — [Setup Type] | Score: [X]/100 | Grade: [A/B/C]
  ✅ Bonuses: [قائمة المكافآت]
  Entry: [X]–[Y] | SL: [Z] | T1: [T] | T2: [T2]
  R:R → T1: [X]x | T2: [Y]x | Confidence: [X]/10

[... وهكذا لكل سهم]

🏆 SPECIAL DESIGNATIONS:
  🛡️ Best Safe: [SYMBOL] — [السبب بالأرقام]
  ⚡ Best Aggressive: [SYMBOL] — [السبب بالأرقام]

❌ REJECTED SETUPS (مع سبب الرفض):
  • [SYMBOL]: [سبب الرفض من الفلتر]

⚠️ MANDATORY RULES (من TRADING_LESSONS.md):
  • لا دخول فوق منطقة الدخول بأكثر من 0.5%
  • [قائمة القواعد النشطة]
```

---

## الخطوة 4: الفلاتر الإلزامية (تُطبَّق بـ scorer.js تلقائياً)

| الفلتر | الشرط | المصدر |
|--------|-------|--------|
| Near ATH Volume | ≥ 2.5x | قاعدة #1 |
| Volume Collapse | > 40% يبقى | قاعدة #2 |
| Entry Zone | لا فوق +0.5% | قاعدة #3 |
| Institutional Retest | أولوية قصوى | قاعدة #4 |
| Defensive Sector | مصرفي فقط | قاعدة #5 |

---

## الخطوة 5: تحديث قاعدة البيانات

```javascript
import { saveScan } from './src/egx/index.js';
saveScan(today, ranked); // يحفظ تلقائياً في data/egx_trading.db
```

---

## ملاحظات

- **EGX_DLY**: البيانات متأخرة يوم واحد — خذ هذا بالحسبان
- **الأسبوع**: EGX يعمل الأحد–الخميس
- **أفضل وقت للـ scan**: الخميس مساءً أو الأحد صباحاً قبل الافتتاح
- **الحد الأدنى للبرايف**: 65 نقطة من 100
- **Win Rate الحالي**: 56% — الهدف 70%+
