/**
 * EGX trading calendar bridge (Node → event_calendar.py)
 * Uses Africa/Cairo for ref dates and trading-day staleness (not calendar days).
 */
import { execFileSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
export const EVENT_CALENDAR_SCRIPT = join(__dirname, '../python/event_calendar.py');

export function pythonBin() {
  for (const p of [process.env.PYTHON_BIN, '/usr/bin/python3', '/usr/local/bin/python3', 'python3'].filter(Boolean)) {
    try {
      execFileSync(p, ['--version'], { stdio: 'ignore' });
      return p;
    } catch {}
  }
  return 'python3';
}

export function cairoDateParts(d = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Africa/Cairo',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(d).reduce((acc, p) => {
    if (p.type !== 'literal') acc[p.type] = p.value;
    return acc;
  }, {});
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
  };
}

export function addDaysIso(dateIso, days) {
  const d = new Date(`${dateIso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Ref date for freshness: before 15:30 Cairo use previous calendar day. */
export function freshnessReferenceDate(d = new Date()) {
  const cairo = cairoDateParts(d);
  const minutes = cairo.hour * 60 + cairo.minute;
  return minutes < (15 * 60 + 30) ? addDaysIso(cairo.date, -1) : cairo.date;
}

function runCalendarCommand(cmd, params = {}) {
  const raw = execFileSync(
    pythonBin(),
    [EVENT_CALENDAR_SCRIPT, cmd, JSON.stringify(params)],
    { encoding: 'utf8', timeout: 15000 },
  );
  const parsed = JSON.parse(raw);
  if (parsed.error) throw new Error(parsed.error);
  return parsed;
}

export function seedHolidayCalendar() {
  try {
    runCalendarCommand('seed', {});
  } catch {
    try {
      runCalendarCommand('repair_2026', {});
    } catch {}
  }
}

export function tradingDayStaleness(dataDate, refDate = freshnessReferenceDate()) {
  return runCalendarCommand('staleness', { data_date: dataDate, ref_date: refDate });
}

export function isTradingDay(dateIso = cairoDateParts().date) {
  return runCalendarCommand('is_trading_day', { date: dateIso });
}

export function nextTradingDay(refDate = cairoDateParts().date) {
  for (let i = 1; i <= 14; i++) {
    const d = addDaysIso(refDate, i);
    const cal = isTradingDay(d);
    if (cal.is_trading_day) {
      return { ref_date: refDate, next_trading_day: d, holiday_name: null };
    }
  }
  return { ref_date: refDate, next_trading_day: null };
}

export function formatFreshnessLine(latestBarDate, cal = null) {
  const info = cal ?? tradingDayStaleness(latestBarDate);
  const stale = Number(info.staleness_trading_days ?? 999);
  const closed = info.market_status === 'MARKET_CLOSED';
  const holiday = info.holiday_name ? ` (${info.holiday_name})` : '';

  if (stale === 0 && closed) {
    return {
      level: 'ok',
      text: `آخر شمعة: ${latestBarDate} | السوق مغلق اليوم${holiday} | محدّث لآخر جلسة ✅`,
      cal: info,
    };
  }
  if (stale === 0) {
    return {
      level: 'ok',
      text: `آخر شمعة: ${latestBarDate} (محدّث — 0 جلسات متأخرة) ✅`,
      cal: info,
    };
  }
  if (stale <= 3) {
    return {
      level: 'warn',
      text: `آخر شمعة: ${latestBarDate} | متأخر ${stale} جلسة تداول | آخر جلسة: ${info.last_trading_day}`,
      cal: info,
    };
  }
  return {
    level: 'err',
    text: `آخر شمعة: ${latestBarDate} | متأخر ${stale} جلسات تداول — يحتاج تحديث`,
    cal: info,
  };
}
