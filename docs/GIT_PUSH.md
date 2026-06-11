# Git Push — drhusam123 (حسابك)

المشروع **يعمل بالكامل محلياً** على جهازك. GitHub **اختياري** — نسخة احتياطية وسجل تغييرات.

## الريموتات

| Remote | الغرض |
|--------|--------|
| `drhusam` | **ريبوك أنت** — `drhusam123/tradingview-mcp-jackson` ← ادفع هنا |
| `origin` | ريبو قديم/أصلي (`LewisWJackson/...`) — **لست مالكه، تجاهله** |

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
