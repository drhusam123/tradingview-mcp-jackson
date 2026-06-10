"""
Event Calendar — EGX Navigator
================================
يتتبع الأحداث الشركاتية للبورصة المصرية: إعلانات الأرباح، التوزيعات النقدية،
الجمعيات العمومية، زيادات رأس المال، وإيقافات التداول.

يخزن الأحداث في قاعدة بيانات SQLite ويوفر تغذية يومية بالأحداث القادمة
لـ night_lab.py.

CLI:
  python3 event_calendar.py status '{}'                              # ملخص 7 أيام
  python3 event_calendar.py upcoming '{"days":14}'                   # قائمة أحداث
  python3 event_calendar.py seed '{}'                                # تحميل العطلات
  python3 event_calendar.py add '{"date":"2025-08-15","type":"earnings","title":"نتائج COMI Q2","symbol":"COMI","impact":"high"}'
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_DB = _SCRIPT_DIR.parent.parent / "data" / "egx_trading.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
_DDL_TABLE = """
CREATE TABLE IF NOT EXISTS event_calendar (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date      TEXT NOT NULL,
    symbol          TEXT,
    event_type      TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    source          TEXT,
    confirmed       INTEGER DEFAULT 0,
    impact          TEXT DEFAULT 'medium',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(event_date, symbol, event_type, title)
)
"""

_DDL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_event_calendar_date ON event_calendar(event_date)
"""

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_conn(db_path=None) -> sqlite3.Connection:
    """Return a sqlite3 connection with Row row_factory."""
    path = Path(db_path) if db_path else _DEFAULT_DB
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create event_calendar table and index if they do not exist."""
    conn.execute(_DDL_TABLE)
    conn.execute(_DDL_INDEX)
    conn.commit()


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def add_event(
    event_date: str,
    event_type: str,
    title: str,
    symbol: Optional[str] = None,
    description: Optional[str] = None,
    source: str = "manual",
    confirmed: bool = True,
    impact: str = "medium",
    db_path=None,
) -> int:
    """Insert a single event into event_calendar.

    Parameters
    ----------
    event_date : str
        ISO date string YYYY-MM-DD.
    event_type : str
        One of: earnings, dividend_ex, dividend_pay, agm, egm,
        capital_increase, suspension, resumption, holiday, other.
    title : str
        Short human-readable title.
    symbol : str or None
        Ticker symbol; None for market-wide events.
    description : str or None
        Optional longer description.
    source : str
        Data source: 'manual', 'egx_website', 'parsed'.
    confirmed : bool
        True if the date is confirmed; False if estimated.
    impact : str
        'high', 'medium', or 'low'.
    db_path : path-like or None
        Override default DB path.

    Returns
    -------
    int
        Auto-increment id of the inserted row, or -1 if duplicate.
    """
    with _get_conn(db_path) as conn:
        ensure_tables(conn)
        try:
            cur = conn.execute(
                """
                INSERT INTO event_calendar
                    (event_date, symbol, event_type, title, description,
                     source, confirmed, impact)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_date,
                    symbol,
                    event_type,
                    title,
                    description,
                    source,
                    1 if confirmed else 0,
                    impact,
                ),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return -1


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row, today: date | None = None) -> Dict:
    """Convert a sqlite3.Row to a plain dict, adding days_until."""
    d = dict(row)
    if today is None:
        today = date.today()
    try:
        ev_date = date.fromisoformat(d["event_date"])
        d["days_until"] = (ev_date - today).days
    except Exception:
        d["days_until"] = None
    return d


def get_upcoming(days_ahead: int = 7, db_path=None) -> List[Dict]:
    """Return events in the next *days_ahead* days (inclusive of today).

    Results are sorted by event_date ASC.

    Parameters
    ----------
    days_ahead : int
        Number of days from today to look ahead.
    db_path : path-like or None
        Override default DB path.

    Returns
    -------
    list of dict
        Each dict has keys: id, event_date, symbol, event_type, title,
        description, impact, confirmed, days_until.
    """
    today = date.today()
    end = today + timedelta(days=days_ahead)
    with _get_conn(db_path) as conn:
        ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT id, event_date, symbol, event_type, title,
                   description, impact, confirmed
            FROM event_calendar
            WHERE event_date >= ? AND event_date <= ?
            ORDER BY event_date ASC
            """,
            (today.isoformat(), end.isoformat()),
        ).fetchall()
    return [_row_to_dict(r, today) for r in rows]


def get_today_events(db_path=None) -> List[Dict]:
    """Return all events scheduled for today.

    Returns
    -------
    list of dict
        Same structure as get_upcoming().
    """
    today = date.today()
    with _get_conn(db_path) as conn:
        ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT id, event_date, symbol, event_type, title,
                   description, impact, confirmed
            FROM event_calendar
            WHERE event_date = ?
            ORDER BY event_type ASC
            """,
            (today.isoformat(),),
        ).fetchall()
    return [_row_to_dict(r, today) for r in rows]


def get_symbol_events(
    symbol: str,
    days_back: int = 30,
    days_ahead: int = 30,
    db_path=None,
) -> List[Dict]:
    """Return events for *symbol* within [today - days_back, today + days_ahead].

    Parameters
    ----------
    symbol : str
        Ticker symbol (case-sensitive match).
    days_back : int
        How many days in the past to include.
    days_ahead : int
        How many days in the future to include.
    db_path : path-like or None
        Override default DB path.

    Returns
    -------
    list of dict
        Sorted by event_date ASC.
    """
    today = date.today()
    start = today - timedelta(days=days_back)
    end = today + timedelta(days=days_ahead)
    with _get_conn(db_path) as conn:
        ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT id, event_date, symbol, event_type, title,
                   description, impact, confirmed
            FROM event_calendar
            WHERE symbol = ?
              AND event_date >= ?
              AND event_date <= ?
            ORDER BY event_date ASC
            """,
            (symbol, start.isoformat(), end.isoformat()),
        ).fetchall()
    return [_row_to_dict(r, today) for r in rows]


# ---------------------------------------------------------------------------
# Seed holidays
# ---------------------------------------------------------------------------

def seed_egx_holidays_2025_2026(db_path=None) -> int:
    """Insert known and estimated EGX market holidays for 2025 and 2026.

    Fixed-date public holidays are inserted with confirmed=True.
    Islamic holidays (dates shift yearly with the lunar calendar) are
    inserted with confirmed=False as reasonable estimates.

    Returns
    -------
    int
        Number of rows actually inserted (duplicates are skipped).
    """
    _SRC = "egx_website"

    holidays = [
        # ── 2025 fixed holidays ─────────────────────────────────────────
        ("2025-01-01", None, "holiday", "New Year's Day 2025", "رأس السنة الميلادية", True, "high"),
        ("2025-01-07", None, "holiday", "Coptic Christmas 2025", "عيد الميلاد المجيد القبطي", True, "high"),
        ("2025-01-25", None, "holiday", "Revolution Day 2025 (Jan 25)", "عيد ثورة 25 يناير", True, "medium"),
        ("2025-05-01", None, "holiday", "Labour Day 2025", "عيد العمال", True, "medium"),
        ("2025-06-30", None, "holiday", "Revolution Day 2025 (Jun 30)", "عيد ثورة 30 يونيو", True, "medium"),
        ("2025-07-23", None, "holiday", "Revolution Day 2025 (Jul 23)", "عيد ثورة 23 يوليو", True, "medium"),
        ("2025-10-06", None, "holiday", "Armed Forces Day 2025", "عيد القوات المسلحة", True, "medium"),

        # ── 2025 Islamic holidays (estimated) ────────────────────────────
        ("2025-03-30", None, "holiday", "Eid Al Fitr 2025 (est.)", "عيد الفطر المبارك — تقريبي", False, "high"),
        ("2025-03-31", None, "holiday", "Eid Al Fitr 2025 day 2 (est.)", "عيد الفطر — اليوم الثاني (تقريبي)", False, "high"),
        ("2025-04-01", None, "holiday", "Eid Al Fitr 2025 day 3 (est.)", "عيد الفطر — اليوم الثالث (تقريبي)", False, "high"),
        ("2025-04-18", None, "holiday", "Sinai Liberation Day 2025", "عيد تحرير سيناء", True, "medium"),
        ("2025-06-06", None, "holiday", "Eid Al Adha 2025 day 1 (est.)", "عيد الأضحى المبارك — تقريبي", False, "high"),
        ("2025-06-07", None, "holiday", "Eid Al Adha 2025 day 2 (est.)", "عيد الأضحى — اليوم الثاني (تقريبي)", False, "high"),
        ("2025-06-08", None, "holiday", "Eid Al Adha 2025 day 3 (est.)", "عيد الأضحى — اليوم الثالث (تقريبي)", False, "high"),
        ("2025-06-26", None, "holiday", "Islamic New Year 2025 (est.)", "رأس السنة الهجرية (تقريبي)", False, "medium"),
        ("2025-09-04", None, "holiday", "Prophet's Birthday 2025 (est.)", "المولد النبوي الشريف (تقريبي)", False, "medium"),

        # ── 2026 fixed holidays ─────────────────────────────────────────
        ("2026-01-01", None, "holiday", "New Year's Day 2026", "رأس السنة الميلادية", True, "high"),
        ("2026-01-07", None, "holiday", "Coptic Christmas 2026", "عيد الميلاد المجيد القبطي", True, "high"),
        ("2026-01-25", None, "holiday", "Revolution Day 2026 (Jan 25)", "عيد ثورة 25 يناير", True, "medium"),
        ("2026-05-01", None, "holiday", "Labour Day 2026", "عيد العمال", True, "medium"),
        ("2026-04-18", None, "holiday", "Sinai Liberation Day 2026", "عيد تحرير سيناء", True, "medium"),
        ("2026-06-30", None, "holiday", "Revolution Day 2026 (Jun 30)", "عيد ثورة 30 يونيو", True, "medium"),
        ("2026-07-23", None, "holiday", "Revolution Day 2026 (Jul 23)", "عيد ثورة 23 يوليو", True, "medium"),
        ("2026-10-06", None, "holiday", "Armed Forces Day 2026", "عيد القوات المسلحة", True, "medium"),

        # ── 2026 Islamic holidays (estimated) ────────────────────────────
        ("2026-03-20", None, "holiday", "Eid Al Fitr 2026 day 1 (est.)", "عيد الفطر — اليوم الأول (تقريبي)", False, "high"),
        ("2026-03-21", None, "holiday", "Eid Al Fitr 2026 day 2 (est.)", "عيد الفطر — اليوم الثاني (تقريبي)", False, "high"),
        ("2026-03-22", None, "holiday", "Eid Al Fitr 2026 day 3 (est.)", "عيد الفطر — اليوم الثالث (تقريبي)", False, "high"),
        ("2026-05-26", None, "holiday", "Eid Al Adha 2026 holiday", "عيد الأضحى — إجازة رسمية مؤكدة", True, "high"),
        ("2026-05-27", None, "holiday", "Eid Al Adha 2026 holiday", "عيد الأضحى — إجازة رسمية مؤكدة", True, "high"),
        ("2026-05-28", None, "holiday", "Eid Al Adha 2026 holiday", "عيد الأضحى — إجازة رسمية مؤكدة", True, "high"),
        ("2026-05-29", None, "holiday", "Eid Al Adha 2026 holiday", "عيد الأضحى — إجازة رسمية مؤكدة", True, "high"),
        ("2026-05-30", None, "holiday", "Eid Al Adha 2026 holiday", "عيد الأضحى — إجازة رسمية مؤكدة", True, "high"),
        ("2026-05-31", None, "holiday", "Eid Al Adha 2026 holiday", "عيد الأضحى — إجازة رسمية مؤكدة", True, "high"),
        ("2026-06-16", None, "holiday", "Islamic New Year 2026 (est.)", "رأس السنة الهجرية (تقريبي)", False, "medium"),
        ("2026-08-25", None, "holiday", "Prophet's Birthday 2026 (est.)", "المولد النبوي الشريف (تقريبي)", False, "medium"),
    ]

    n_inserted = 0
    with _get_conn(db_path) as conn:
        ensure_tables(conn)
        for ev_date, sym, ev_type, title, desc, confirmed, impact in holidays:
            try:
                conn.execute(
                    """
                    INSERT INTO event_calendar
                        (event_date, symbol, event_type, title, description,
                         source, confirmed, impact)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (ev_date, sym, ev_type, title, desc, _SRC, 1 if confirmed else 0, impact),
                )
                n_inserted += 1
            except sqlite3.IntegrityError:
                pass  # already seeded
        conn.commit()
    return n_inserted


def repair_egx_holidays_2026(db_path=None) -> Dict:
    """Repair confirmed EGX Eid Al-Adha 2026 closure dates.

    Officially observed EGX closure: 2026-05-26 through 2026-05-31,
    with trading resuming on 2026-06-01. 2026-05-25 is a trading day
    and must not be classified as a holiday.
    """
    confirmed_dates = [f"2026-05-{d:02d}" for d in range(26, 32)]
    with _get_conn(db_path) as conn:
        ensure_tables(conn)
        cur = conn.execute(
            """
            DELETE FROM event_calendar
            WHERE event_type='holiday'
              AND event_date BETWEEN '2026-05-25' AND '2026-05-31'
              AND (
                event_date='2026-05-25'
                OR title LIKE 'Eid Al Adha 2026%'
              )
            """
        )
        deleted = cur.rowcount
        inserted = 0
        for ev_date in confirmed_dates:
            try:
                conn.execute(
                    """
                    INSERT INTO event_calendar
                        (event_date, symbol, event_type, title, description,
                         source, confirmed, impact)
                    VALUES (?, NULL, 'holiday', ?, ?, 'egx_disclosure', 1, 'high')
                    """,
                    (
                        ev_date,
                        "Eid Al Adha 2026 holiday",
                        "EGX closed for Eid Al-Adha; trading resumes 2026-06-01",
                    ),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    return {"deleted": deleted, "inserted": inserted, "dates": confirmed_dates}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def summarize_upcoming(days_ahead: int = 7, db_path=None) -> Dict:
    """Build a compact summary of events in the next *days_ahead* days.

    Returns
    -------
    dict with keys:
        n_events        : int
        n_high_impact   : int
        next_event      : dict or None
        has_earnings    : bool
        has_holiday     : bool
        events_by_type  : dict[str, int]
        alert           : str or None
    """
    events = get_upcoming(days_ahead=days_ahead, db_path=db_path)

    n_events = len(events)
    n_high = sum(1 for e in events if e.get("impact") == "high")

    next_event = None
    if events:
        e0 = events[0]
        next_event = {
            "event_date": e0["event_date"],
            "title": e0["title"],
            "symbol": e0["symbol"],
            "event_type": e0["event_type"],
        }

    has_earnings = any(e["event_type"] == "earnings" for e in events)
    has_holiday = any(e["event_type"] == "holiday" for e in events)

    events_by_type: Dict[str, int] = {}
    for e in events:
        t = e.get("event_type", "other")
        events_by_type[t] = events_by_type.get(t, 0) + 1

    # Build alert string
    alert: Optional[str] = None
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    tomorrow_earnings = [
        e["symbol"] or e["title"]
        for e in events
        if e["event_type"] == "earnings" and e["event_date"] == tomorrow
    ]
    today_events = [e for e in events if e.get("days_until") == 0]
    tomorrow_holidays = [e for e in events if e["event_type"] == "holiday" and e["event_date"] == tomorrow]

    parts = []
    if today_events:
        parts.append(f"اليوم: {len(today_events)} حدث")
    if tomorrow_earnings:
        symbols_str = ", ".join(s for s in tomorrow_earnings if s)
        parts.append(f"غداً أرباح: {symbols_str}" if symbols_str else "غداً إعلان أرباح")
    if tomorrow_holidays:
        parts.append(f"غداً إجازة: {tomorrow_holidays[0]['title']}")
    if n_high > 0 and not parts:
        parts.append(f"{n_high} أحداث عالية التأثير خلال {days_ahead} أيام")

    if parts:
        alert = "⚠️ " + " | ".join(parts)

    return {
        "n_events": n_events,
        "n_high_impact": n_high,
        "next_event": next_event,
        "has_earnings": has_earnings,
        "has_holiday": has_holiday,
        "events_by_type": events_by_type,
        "alert": alert,
    }


# ---------------------------------------------------------------------------
# EGX Trading Calendar helpers
# ---------------------------------------------------------------------------
# EGX trading week: Sunday–Thursday (weekday 6, 0, 1, 2, 3)
# Egypt weekend:    Friday + Saturday (weekday 4, 5)
# Holidays are read from event_calendar table (type='holiday').
# ---------------------------------------------------------------------------

_EGX_TRADING_WEEKDAYS = {6, 0, 1, 2, 3}   # Sun=6, Mon=0, Tue=1, Wed=2, Thu=3

# In-memory holiday cache  {date_str: title}  — populated on first call
_holiday_cache: dict = {}
_holiday_cache_loaded: bool = False


def _load_holiday_cache(db_path=None) -> None:
    """Load all holidays from event_calendar into the in-memory cache."""
    global _holiday_cache, _holiday_cache_loaded
    if _holiday_cache_loaded:
        return
    try:
        with _get_conn(db_path) as conn:
            ensure_tables(conn)
            rows = conn.execute(
                "SELECT event_date, title FROM event_calendar WHERE event_type='holiday'"
            ).fetchall()
            _holiday_cache = {r["event_date"]: r["title"] for r in rows}
    except Exception:
        _holiday_cache = {}
    _holiday_cache_loaded = True


def is_trading_day(d: "date | str", db_path=None) -> bool:
    """
    Return True if *d* is an EGX trading day:
      — weekday is Sun–Thu (not Fri/Sat)
      — date is NOT in event_calendar holidays

    Accepts a date object or ISO string ('YYYY-MM-DD').
    """
    if isinstance(d, str):
        from datetime import date as _date
        d = _date.fromisoformat(d)

    # Egypt weekend: Friday(4) and Saturday(5)
    if d.weekday() not in _EGX_TRADING_WEEKDAYS:
        return False

    # Check holiday table
    _load_holiday_cache(db_path)
    return d.isoformat() not in _holiday_cache


def last_trading_day(ref: "date | str | None" = None, db_path=None) -> "date":
    """
    Return the most recent EGX trading day on or before *ref* (default: today).
    Walks backwards up to 30 calendar days.
    """
    from datetime import date as _date, timedelta as _td
    if ref is None:
        ref = _date.today()
    elif isinstance(ref, str):
        ref = _date.fromisoformat(ref)

    _load_holiday_cache(db_path)
    d = ref
    for _ in range(30):
        if is_trading_day(d, db_path):
            return d
        d -= _td(days=1)
    return ref  # fallback — should never reach here


def next_trading_day(ref: "date | str | None" = None, db_path=None) -> "date":
    """Return the next EGX trading day strictly AFTER *ref* (default: today)."""
    from datetime import date as _date, timedelta as _td
    if ref is None:
        ref = _date.today()
    elif isinstance(ref, str):
        ref = _date.fromisoformat(ref)

    _load_holiday_cache(db_path)
    d = ref + _td(days=1)
    for _ in range(30):
        if is_trading_day(d, db_path):
            return d
        d += _td(days=1)
    return d  # fallback


def trading_days_between(start: "date | str", end: "date | str",
                         inclusive: bool = True, db_path=None) -> int:
    """
    Count EGX trading days between *start* and *end*.
    inclusive=True  → counts both endpoints if they are trading days.
    inclusive=False → counts days strictly between start and end.
    """
    from datetime import date as _date, timedelta as _td
    if isinstance(start, str):
        start = _date.fromisoformat(start)
    if isinstance(end, str):
        end = _date.fromisoformat(end)

    _load_holiday_cache(db_path)
    count = 0
    d = start if inclusive else start + _td(days=1)
    limit = end if inclusive else end - _td(days=1)
    while d <= limit:
        if is_trading_day(d, db_path):
            count += 1
        d += _td(days=1)
    return count


def trading_day_offset(ref: "date | str", n: int, db_path=None) -> "date | None":
    """
    Return the date of the n-th EGX trading session strictly after *ref*.
    n=1 → next trading day after ref.
    """
    from datetime import date as _date, timedelta as _td
    if n <= 0:
        return _date.fromisoformat(ref) if isinstance(ref, str) else ref
    if isinstance(ref, str):
        ref = _date.fromisoformat(ref)
    _load_holiday_cache(db_path)
    cur = ref
    found = 0
    for _ in range(120):
        cur += _td(days=1)
        if is_trading_day(cur, db_path):
            found += 1
            if found >= n:
                return cur
    return None


def staleness_trading_days(data_date: "date | str", ref: "date | str | None" = None,
                           db_path=None) -> int:
    """
    Return how many EGX trading days have elapsed since *data_date*.

    Examples (EGX week = Sun–Thu):
      data_date = last_trading_day  → 0  (fresh, up to date)
      data_date = one session ago   → 1  (missed 1 trading session)

    Algorithm: count trading days in the half-open interval
    (data_date, last_trading_day(ref)], i.e. exclusive of data_date,
    inclusive of last_td.  This correctly gives:
      May 21 → May 24 (last_td): count [May 22 excluded (Fri), May 23 (Sat),
                                         May 24 ✅] = 1  ✓
      May 24 → May 24 (last_td): count [] = 0  ✓
    """
    from datetime import date as _date, timedelta as _td
    if isinstance(data_date, str):
        data_date = _date.fromisoformat(data_date)
    if ref is None:
        ref = _date.today()
    elif isinstance(ref, str):
        ref = _date.fromisoformat(ref)

    last_td = last_trading_day(ref, db_path)
    if last_td <= data_date:
        return 0  # data is at or ahead of last trading day

    # Count trading days in (data_date, last_td] — exclude data_date, include last_td
    _load_holiday_cache(db_path)
    count = 0
    d = data_date + _td(days=1)
    while d <= last_td:
        if is_trading_day(d, db_path):
            count += 1
        d += _td(days=1)
    return count


def holiday_name(d: "date | str", db_path=None) -> "str | None":
    """Return the holiday name for *d*, or None if it is a trading day."""
    if isinstance(d, str):
        from datetime import date as _date
        d = _date.fromisoformat(d)
    _load_holiday_cache(db_path)
    return _holiday_cache.get(d.isoformat())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    if cmd == "status":
        result = summarize_upcoming(days_ahead=params.get("days", 7))
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "upcoming":
        events = get_upcoming(days_ahead=params.get("days", 7))
        print(json.dumps({"events": events, "n": len(events)}, ensure_ascii=False))

    elif cmd == "seed":
        n = seed_egx_holidays_2025_2026()
        print(json.dumps({"seeded": n}))

    elif cmd == "repair_2026":
        print(json.dumps(repair_egx_holidays_2026(), ensure_ascii=False))

    elif cmd == "add":
        eid = add_event(
            event_date=params["date"],
            event_type=params["type"],
            title=params["title"],
            symbol=params.get("symbol"),
            description=params.get("description"),
            impact=params.get("impact", "medium"),
        )
        print(json.dumps({"id": eid, "status": "ok" if eid > 0 else "duplicate"}))

    elif cmd == "run":  # night_lab.py compatibility
        result = summarize_upcoming(days_ahead=params.get("days", 7))
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "is_trading_day":
        from datetime import date as _date
        d_str = params.get("date", _date.today().isoformat())
        trading = is_trading_day(d_str)
        hname   = holiday_name(d_str)
        last_td = last_trading_day(d_str)
        print(json.dumps({
            "date":            d_str,
            "is_trading_day":  trading,
            "holiday_name":    hname,
            "last_trading_day": last_td.isoformat(),
        }, ensure_ascii=False))

    elif cmd == "staleness":
        from datetime import date as _date
        data_date = params.get("data_date")
        if not data_date:
            print(json.dumps({"error": "missing data_date"}))
            sys.exit(1)
        ref_date = params.get("ref_date", _date.today().isoformat())
        trading = is_trading_day(ref_date)
        hname   = holiday_name(ref_date)
        last_td = last_trading_day(ref_date)
        stale_td = staleness_trading_days(data_date, ref_date)
        print(json.dumps({
            "data_date": data_date,
            "ref_date": ref_date,
            "is_ref_trading_day": trading,
            "market_status": "OPEN" if trading else "MARKET_CLOSED",
            "holiday_name": hname,
            "last_trading_day": last_td.isoformat(),
            "staleness_trading_days": stale_td,
        }, ensure_ascii=False))

    elif cmd == "calendar_check":
        # Quick audit: show next 10 calendar days with trading status
        from datetime import date as _date, timedelta as _td
        d = _date.today()
        rows = []
        for i in range(14):
            cd = d + _td(days=i)
            rows.append({
                "date":     cd.isoformat(),
                "weekday":  cd.strftime("%A"),
                "trading":  is_trading_day(cd),
                "holiday":  holiday_name(cd),
            })
        print(json.dumps({"days": rows}, ensure_ascii=False))

    else:
        print(json.dumps({"error": f"unknown command: {cmd}"}))
        sys.exit(1)
