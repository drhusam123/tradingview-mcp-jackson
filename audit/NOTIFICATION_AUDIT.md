# Notification Audit

**Generated:** 2026-06-15T20:06:37.810Z
**Report date:** 2026-06-15

## Status

- **Root cause:** notification_not_run_or_failed
- **Actionable:** 2 | **Deliverable:** 2
- **Telegram configured:** ✅

## Diagnosis

- Actionable signals exist but no successful delivery audit for this date.
- Check cron: npm run egx:cron:show | verify egx:tg:daily ran

## Commands

```bash
npm run egx:prod:prepare-send -- --dry-run
npm run egx:notify:reconcile
npm run egx:cron:telegram:dry
```
