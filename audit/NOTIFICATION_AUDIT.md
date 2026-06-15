# Notification Audit

**Generated:** 2026-06-15T10:06:43.781Z
**Report date:** 2026-06-14

## Status

- **Root cause:** delivered
- **Actionable:** 1 | **Deliverable:** 1
- **Telegram configured:** ✅

## Diagnosis

- Delivery audit: 3 successful send(s) (data_correction, telegram_send, telegram_send).

## Commands

```bash
npm run egx:prod:prepare-send -- --dry-run
npm run egx:notify:reconcile
npm run egx:cron:telegram:dry
```
