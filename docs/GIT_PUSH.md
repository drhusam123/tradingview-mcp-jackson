# Git Push — drhusam123 (حسابك)

المشروع **يعمل بالكامل محلياً** على جهازك. GitHub **اختياري** — نسخة احتياطية وسجل تغييرات.

## الريموتات

| Remote | الغرض |
|--------|--------|
| `drhusam` | **ريبوك أنت** — `drhusam123/tradingview-mcp-jackson` ← ادفع هنا |
| `origin` | ريبو قديم/أصلي (`LewisWJackson/...`) — **لست مالكه، تجاهله** |

## أوامر التحقق قبل الـ push

```bash
npm test
npm run egx:data:audit          # 31 فحص L0→L1
npm run egx:architecture:audit  # 12/12 طبقة
npm run egx:discovery:verify
npm run egx:loop:audit
npm run egx:e2e:complete -- --skip-automate --skip-go-live
```

## تشغيل يومي (بعد إغلاق الجلسة)

```bash
npm run egx:tv:auto             # orchestrator الرسمي
npm run egx:data:audit
npm run egx:exclusions:report
npm run egx:parquet:export
npm run egx:ops                 # Freshness KPIs
npm run egx:signals:diagnose    # لماذا 0 actionable؟ (gate funnel)
```

## قبل الجلسة القادمة

```bash
npm run egx:pre:session        # audit + session + funnel + verify (موصى به)
npm run egx:session:next
npm run egx:runbook:next
```

## Cron

```bash
npm run egx:cron:install        # تثبيت/تحديث 75+ مهمة
npm run egx:cron:dedupe         # إزالة تكرارات EGX-*
npm run egx:cron:show
```

## Push (جاهز للصق)

```bash
cd /Users/dr.husam/tradingview-mcp-jackson
git push drhusam main
```

أو:

```bash
npm run egx:git:push
```

## إذا فشل الـ auth

```bash
gh auth login
# GitHub.com → HTTPS → Browser → drhusam123

gh auth status
git push drhusam main
```

## هل أحتاج GitHub؟

**لا** لتشغيل EGX يومياً:

- البيانات في `data/`
- Cron على جهازك
- TradingView محلي
- Telegram من `.env`

**نعم** (اختياري) إذا أردت:

- نسخة احتياطية لو تعطل الجهاز
- سجل commits (من غيّر ماذا ومتى)
- CI على GitHub Actions (اختبارات عند push)
- فتح المشروع من جهاز ثانٍ

## تغيير الريموت الافتراضي

```bash
export EGX_GIT_REMOTE=drhusam   # في .env أو ~/.zshrc
```
