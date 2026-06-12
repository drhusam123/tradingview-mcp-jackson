#!/usr/bin/env python3
"""
pine_analytics_bridge.py — Phase 53: Pine Analytics Bridge
═══════════════════════════════════════════════════════════
Stores and analyzes data harvested from TradingView Pine Script indicators
via the MCP (data_get_pine_tables, data_get_pine_labels, data_get_pine_lines).

Pine scripts run on TradingView; this Python engine processes their outputs:
  • Volume Profile  → POC, VAH, VAL
  • Session Analytics → VWAP, opening range, session bias
  • Relative Strength vs EGX30 → RS score, rank percentile
  • Corporate Events → unusual volume/price events detected by Pine

Commands:
  store_pine_data          — ingest raw Pine output into pine_analytics
  volume_profile_analysis  — POC stability, value area width, S/R proximity
  rs_ranking               — rank all stocks by RS on a date
  vwap_position            — VWAP trend + distance analysis
  corporate_event_scan     — recent Pine-detected corporate events
  pine_data_coverage       — coverage stats across symbols & scripts
  build_full               — RS rankings + corporate event summary for latest date
"""

import os
import sys
import json
import math
import sqlite3
import statistics
from datetime import datetime, timedelta
from collections import defaultdict

# ── DB path ────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'egx_trading.db')

# ── Source script identifiers ──────────────────────────────────────────────
SRC_VOLUME_PROFILE   = 'volume_profile'
SRC_SESSION_ANALYTICS = 'session_analytics'
SRC_RELATIVE_STRENGTH = 'relative_strength'
SRC_CORPORATE_EVENTS  = 'corporate_events'

KNOWN_SCRIPTS = {SRC_VOLUME_PROFILE, SRC_SESSION_ANALYTICS,
                 SRC_RELATIVE_STRENGTH, SRC_CORPORATE_EVENTS}

# ── RS classification bands ────────────────────────────────────────────────
RS_LEADER   = 'RS_LEADER'    # top 20 %
RS_STRONG   = 'RS_STRONG'    # 20-40 %
RS_NEUTRAL  = 'RS_NEUTRAL'   # 40-60 %
RS_WEAK     = 'RS_WEAK'      # 60-80 %
RS_LAGGARD  = 'RS_LAGGARD'   # bottom 20 %

# ── Session bias ───────────────────────────────────────────────────────────
BIAS_ABOVE   = 'ABOVE_VWAP'
BIAS_BELOW   = 'BELOW_VWAP'
BIAS_AT      = 'AT_VWAP'
BIAS_UNKNOWN = 'UNKNOWN'

# bps tolerance for "AT_VWAP"
AT_VWAP_BPS = 10.0


# ══════════════════════════════════════════════════════════════════════════
# DB helpers
# ══════════════════════════════════════════════════════════════════════════

def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS pine_analytics (
            symbol               TEXT    NOT NULL,
            trade_date           TEXT    NOT NULL,
            volume_poc           REAL,
            volume_vah           REAL,
            volume_val           REAL,
            vwap                 REAL,
            opening_range_high   REAL,
            opening_range_low    REAL,
            session_bias         TEXT,
            rs_score             REAL,
            rs_percentile        REAL,
            corporate_event_flag INTEGER DEFAULT 0,
            corporate_event_type TEXT,
            raw_pine_data        TEXT,
            source_script        TEXT,
            PRIMARY KEY (symbol, trade_date)
        )
    """)
    con.commit()


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ══════════════════════════════════════════════════════════════════════════
# Pine data parsers — translate raw pine_data → structured fields
# ══════════════════════════════════════════════════════════════════════════

def _parse_volume_profile(pine_data: dict) -> dict:
    """
    Expected keys (case-insensitive):
      poc / point_of_control / POC
      vah / value_area_high  / VAH
      val / value_area_low   / VAL
    Also handles list-of-row format from data_get_pine_tables:
      rows: [["POC", "12.50"], ["VAH", "12.80"], ...]
    """
    fields = {}
    aliases = {
        'volume_poc': {'poc', 'point_of_control'},
        'volume_vah': {'vah', 'value_area_high'},
        'volume_val': {'val', 'value_area_low'},
    }
    # flat dict format
    lower = {k.lower(): v for k, v in pine_data.items()}
    for field, keys in aliases.items():
        for key in keys:
            if key in lower:
                try:
                    fields[field] = float(lower[key])
                except (TypeError, ValueError):
                    pass
                break
    # table rows format: [["label", "value"], ...]
    rows = pine_data.get('rows') or pine_data.get('table_rows') or []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        label = str(row[0]).strip().lower()
        raw_val = row[1]
        for field, keys in aliases.items():
            if label in keys or any(label.startswith(k) for k in keys):
                if field not in fields:
                    try:
                        fields[field] = float(str(raw_val).replace(',', ''))
                    except (TypeError, ValueError):
                        pass
    return fields


def _parse_session_analytics(pine_data: dict) -> dict:
    """
    Expected keys:
      vwap, opening_range_high / or_high, opening_range_low / or_low, session_bias
    """
    fields = {}
    lower = {k.lower(): v for k, v in pine_data.items()}

    def _get_float(*keys):
        for k in keys:
            if k in lower:
                try:
                    return float(str(lower[k]).replace(',', ''))
                except (TypeError, ValueError):
                    pass
        return None

    vwap = _get_float('vwap')
    if vwap is not None:
        fields['vwap'] = vwap

    orh = _get_float('opening_range_high', 'or_high', 'open_range_high')
    if orh is not None:
        fields['opening_range_high'] = orh

    orl = _get_float('opening_range_low', 'or_low', 'open_range_low')
    if orl is not None:
        fields['opening_range_low'] = orl

    bias_raw = lower.get('session_bias') or lower.get('bias')
    if bias_raw:
        bias_raw = str(bias_raw).upper().strip()
        if 'ABOVE' in bias_raw:
            fields['session_bias'] = BIAS_ABOVE
        elif 'BELOW' in bias_raw:
            fields['session_bias'] = BIAS_BELOW
        elif 'AT' in bias_raw:
            fields['session_bias'] = BIAS_AT
        else:
            fields['session_bias'] = bias_raw

    # table rows format
    rows = pine_data.get('rows') or pine_data.get('table_rows') or []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        label = str(row[0]).strip().lower()
        raw_val = str(row[1]).strip()
        if 'vwap' in label and 'vwap' not in fields:
            try:
                fields['vwap'] = float(raw_val.replace(',', ''))
            except (TypeError, ValueError):
                pass
        elif ('or_high' in label or 'opening range high' in label) and 'opening_range_high' not in fields:
            try:
                fields['opening_range_high'] = float(raw_val.replace(',', ''))
            except (TypeError, ValueError):
                pass
        elif ('or_low' in label or 'opening range low' in label) and 'opening_range_low' not in fields:
            try:
                fields['opening_range_low'] = float(raw_val.replace(',', ''))
            except (TypeError, ValueError):
                pass

    return fields


def _parse_relative_strength(pine_data: dict) -> dict:
    """
    Expected keys: rs_score, rs_percentile / percentile
    """
    fields = {}
    lower = {k.lower(): v for k, v in pine_data.items()}

    def _get_float(*keys):
        for k in keys:
            if k in lower:
                try:
                    return float(str(lower[k]).replace(',', ''))
                except (TypeError, ValueError):
                    pass
        return None

    rs = _get_float('rs_score', 'rs', 'relative_strength')
    if rs is not None:
        fields['rs_score'] = max(-100.0, min(100.0, rs))

    pct = _get_float('rs_percentile', 'percentile', 'rank_percentile')
    if pct is not None:
        fields['rs_percentile'] = max(0.0, min(100.0, pct))

    # table rows
    rows = pine_data.get('rows') or pine_data.get('table_rows') or []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        label = str(row[0]).strip().lower()
        raw_val = str(row[1]).strip()
        if ('rs' in label or 'relative' in label) and 'rs_score' not in fields:
            try:
                fields['rs_score'] = max(-100.0, min(100.0, float(raw_val.replace(',', ''))))
            except (TypeError, ValueError):
                pass
        elif 'percentile' in label and 'rs_percentile' not in fields:
            try:
                fields['rs_percentile'] = max(0.0, min(100.0, float(raw_val.replace(',', ''))))
            except (TypeError, ValueError):
                pass

    return fields


def _parse_corporate_events(pine_data: dict) -> dict:
    """
    Expected keys: event_type, event_flag / flag
    """
    fields = {'corporate_event_flag': 0, 'corporate_event_type': None}
    lower = {k.lower(): v for k, v in pine_data.items()}

    flag = lower.get('event_flag') or lower.get('flag') or lower.get('corporate_event_flag')
    if flag is not None:
        try:
            fields['corporate_event_flag'] = 1 if int(float(str(flag))) else 0
        except (TypeError, ValueError):
            fields['corporate_event_flag'] = 1 if str(flag).strip().lower() in ('true', '1', 'yes') else 0

    etype = lower.get('event_type') or lower.get('corporate_event_type') or lower.get('type')
    if etype:
        fields['corporate_event_type'] = str(etype).strip()

    # label / rows format
    rows = pine_data.get('rows') or pine_data.get('table_rows') or []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        label = str(row[0]).strip().lower()
        raw_val = str(row[1]).strip()
        if 'flag' in label:
            try:
                fields['corporate_event_flag'] = 1 if int(float(raw_val)) else 0
            except (TypeError, ValueError):
                pass
        elif 'type' in label and fields.get('corporate_event_type') is None:
            fields['corporate_event_type'] = raw_val

    return fields


PARSER_MAP = {
    SRC_VOLUME_PROFILE:    _parse_volume_profile,
    SRC_SESSION_ANALYTICS: _parse_session_analytics,
    SRC_RELATIVE_STRENGTH: _parse_relative_strength,
    SRC_CORPORATE_EVENTS:  _parse_corporate_events,
}


# ══════════════════════════════════════════════════════════════════════════
# Command implementations
# ══════════════════════════════════════════════════════════════════════════

def store_pine_data(params: dict) -> dict:
    """
    Ingest raw Pine output into pine_analytics.
    Supports partial updates — only overwrites fields that are provided.
    """
    symbol = str(params.get('symbol', '')).upper().strip()
    date   = str(params.get('date', '')).strip()
    pine_data   = params.get('pine_data', {})
    source_script = str(params.get('source_script', '')).lower().strip()

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}
    if not date:
        return {'success': False, 'error': 'date is required'}
    if not isinstance(pine_data, dict):
        return {'success': False, 'error': 'pine_data must be a dict'}
    if source_script not in KNOWN_SCRIPTS:
        return {
            'success': False,
            'error': f"Unknown source_script '{source_script}'. Valid: {sorted(KNOWN_SCRIPTS)}"
        }

    parser = PARSER_MAP[source_script]
    parsed = parser(pine_data)

    con = _connect()
    _ensure_table(con)

    # fetch existing row
    existing = _row_to_dict(
        con.execute(
            "SELECT * FROM pine_analytics WHERE symbol=? AND trade_date=?",
            (symbol, date)
        ).fetchone()
    )

    raw_history = []
    if existing.get('raw_pine_data'):
        try:
            raw_history = json.loads(existing['raw_pine_data'])
            if not isinstance(raw_history, list):
                raw_history = [raw_history]
        except (json.JSONDecodeError, TypeError):
            raw_history = []

    entry = {'source': source_script, 'ts': datetime.utcnow().isoformat(), 'data': pine_data}
    raw_history.append(entry)

    # merge: parsed fields override only if new value is non-None
    merged = dict(existing) if existing else {}
    for field, value in parsed.items():
        if value is not None:
            merged[field] = value

    # always track latest source_script
    merged['source_script'] = source_script
    merged['raw_pine_data'] = json.dumps(raw_history[-50:])  # keep last 50 entries
    merged['symbol'] = symbol
    merged['trade_date'] = date

    con.execute("""
        INSERT INTO pine_analytics
            (symbol, trade_date, volume_poc, volume_vah, volume_val,
             vwap, opening_range_high, opening_range_low, session_bias,
             rs_score, rs_percentile, corporate_event_flag, corporate_event_type,
             raw_pine_data, source_script)
        VALUES
            (:symbol, :trade_date, :volume_poc, :volume_vah, :volume_val,
             :vwap, :opening_range_high, :opening_range_low, :session_bias,
             :rs_score, :rs_percentile, :corporate_event_flag, :corporate_event_type,
             :raw_pine_data, :source_script)
        ON CONFLICT(symbol, trade_date) DO UPDATE SET
            volume_poc           = COALESCE(:volume_poc,           volume_poc),
            volume_vah           = COALESCE(:volume_vah,           volume_vah),
            volume_val           = COALESCE(:volume_val,           volume_val),
            vwap                 = COALESCE(:vwap,                 vwap),
            opening_range_high   = COALESCE(:opening_range_high,   opening_range_high),
            opening_range_low    = COALESCE(:opening_range_low,    opening_range_low),
            session_bias         = COALESCE(:session_bias,         session_bias),
            rs_score             = COALESCE(:rs_score,             rs_score),
            rs_percentile        = COALESCE(:rs_percentile,        rs_percentile),
            corporate_event_flag = COALESCE(:corporate_event_flag, corporate_event_flag),
            corporate_event_type = COALESCE(:corporate_event_type, corporate_event_type),
            raw_pine_data        = :raw_pine_data,
            source_script        = :source_script
    """, {
        'symbol':               merged.get('symbol'),
        'trade_date':           merged.get('trade_date'),
        'volume_poc':           merged.get('volume_poc'),
        'volume_vah':           merged.get('volume_vah'),
        'volume_val':           merged.get('volume_val'),
        'vwap':                 merged.get('vwap'),
        'opening_range_high':   merged.get('opening_range_high'),
        'opening_range_low':    merged.get('opening_range_low'),
        'session_bias':         merged.get('session_bias'),
        'rs_score':             merged.get('rs_score'),
        'rs_percentile':        merged.get('rs_percentile'),
        'corporate_event_flag': merged.get('corporate_event_flag', 0),
        'corporate_event_type': merged.get('corporate_event_type'),
        'raw_pine_data':        merged.get('raw_pine_data'),
        'source_script':        merged.get('source_script'),
    })
    con.commit()
    con.close()

    return {
        'success': True,
        'symbol': symbol,
        'date': date,
        'source_script': source_script,
        'fields_parsed': list(parsed.keys()),
        'fields_stored': [k for k, v in merged.items() if v is not None and k not in ('raw_pine_data', 'symbol', 'trade_date')],
    }


def volume_profile_analysis(params: dict) -> dict:
    """
    Analyze volume profile data:
    - POC vs current price
    - POC stability over lookback
    - Value area width (consolidation indicator)
    - POC as support/resistance
    """
    symbol       = str(params.get('symbol', '')).upper().strip()
    lookback_days = int(params.get('lookback_days', 20))

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}

    con = _connect()
    _ensure_table(con)

    rows = con.execute("""
        SELECT trade_date, volume_poc, volume_vah, volume_val
        FROM pine_analytics
        WHERE symbol = ?
          AND volume_poc IS NOT NULL
        ORDER BY trade_date DESC
        LIMIT ?
    """, (symbol, lookback_days)).fetchall()
    con.close()

    if not rows:
        return {'success': True, 'symbol': symbol, 'error': 'No volume profile data found', 'sessions': 0}

    sessions = [dict(r) for r in rows]
    latest = sessions[0]

    poc   = latest.get('volume_poc')
    vah   = latest.get('volume_vah')
    val   = latest.get('volume_val')

    # --- POC stability ---
    poc_values = [s['volume_poc'] for s in sessions if s.get('volume_poc') is not None]
    poc_range_pct = None
    poc_std_pct   = None
    if len(poc_values) >= 2:
        poc_hi = max(poc_values)
        poc_lo = min(poc_values)
        mid    = (poc_hi + poc_lo) / 2 if (poc_hi + poc_lo) else 1
        poc_range_pct = round((poc_hi - poc_lo) / mid * 100, 3) if mid else None
        poc_std_pct   = round(statistics.stdev(poc_values) / mid * 100, 3) if mid else None

    if poc_range_pct is not None:
        if poc_range_pct < 0.5:
            poc_stability = 'VERY_STABLE'
        elif poc_range_pct < 1.5:
            poc_stability = 'STABLE'
        elif poc_range_pct < 3.0:
            poc_stability = 'DRIFTING'
        else:
            poc_stability = 'MIGRATING'
    else:
        poc_stability = 'UNKNOWN'

    # --- Value area width ---
    va_width_pct = None
    consolidation = None
    if vah is not None and val is not None and poc is not None and poc != 0:
        va_width_pct = round((vah - val) / poc * 100, 3)
        if va_width_pct < 2.0:
            consolidation = 'TIGHT'
        elif va_width_pct < 4.0:
            consolidation = 'NORMAL'
        elif va_width_pct < 7.0:
            consolidation = 'WIDE'
        else:
            consolidation = 'VERY_WIDE'

    # compute VA width trend (narrowing / widening / stable)
    va_widths = []
    for s in sessions:
        if s.get('volume_vah') and s.get('volume_val') and s.get('volume_poc'):
            w = (s['volume_vah'] - s['volume_val']) / s['volume_poc'] * 100
            va_widths.append(w)

    va_width_trend = 'UNKNOWN'
    if len(va_widths) >= 4:
        recent_avg = statistics.mean(va_widths[:3])
        older_avg  = statistics.mean(va_widths[3:min(len(va_widths), 7)])
        delta = recent_avg - older_avg
        if delta < -0.3:
            va_width_trend = 'NARROWING'
        elif delta > 0.3:
            va_width_trend = 'WIDENING'
        else:
            va_width_trend = 'STABLE'

    # --- fetch current price from ohlcv_history_execution / financial_data ---
    current_price = None
    price_vs_poc  = None
    poc_proximity = None

    try:
        con2 = _connect()
        row_p = con2.execute("""
            SELECT close FROM ohlcv_history_execution
            WHERE symbol = ?
            ORDER BY bar_date DESC
            LIMIT 1
        """, (symbol,)).fetchone()
        if row_p:
            current_price = float(row_p[0])
        else:
            row_p2 = con2.execute("""
                SELECT close FROM financial_data
                WHERE symbol = ?
                ORDER BY date DESC
                LIMIT 1
            """, (symbol,)).fetchone()
            if row_p2:
                current_price = float(row_p2[0])
        con2.close()
    except Exception:
        pass

    if current_price is not None and poc is not None and poc != 0:
        price_vs_poc = round((current_price - poc) / poc * 100, 3)
        poc_dist_pct = abs(price_vs_poc)
        if poc_dist_pct < 0.25:
            poc_proximity = 'AT_POC'
        elif poc_dist_pct < 1.0:
            poc_proximity = 'NEAR_POC'
        elif poc_dist_pct < 2.5:
            poc_proximity = 'CLOSE_TO_POC'
        else:
            poc_proximity = 'AWAY_FROM_POC'

        # support / resistance role
        if price_vs_poc > 0:
            sr_role = 'POC_IS_SUPPORT'
        elif price_vs_poc < 0:
            sr_role = 'POC_IS_RESISTANCE'
        else:
            sr_role = 'PRICE_AT_POC'
    else:
        sr_role = 'UNKNOWN'

    return {
        'success': True,
        'symbol': symbol,
        'sessions_analyzed': len(sessions),
        'latest_date': latest['trade_date'],
        'current_price': current_price,
        'poc': poc,
        'vah': vah,
        'val': val,
        'price_vs_poc_pct': price_vs_poc,
        'poc_proximity': poc_proximity,
        'sr_role': sr_role,
        'poc_stability': poc_stability,
        'poc_range_pct': poc_range_pct,
        'poc_std_pct': poc_std_pct,
        'va_width_pct': va_width_pct,
        'consolidation_state': consolidation,
        'va_width_trend': va_width_trend,
        'poc_history': [
            {'date': s['trade_date'], 'poc': s['volume_poc']}
            for s in sessions[:10]
        ],
    }


def rs_ranking(params: dict) -> dict:
    """
    Rank all stocks by relative strength on a given date.
    Returns top_n with RS classification + trend vs yesterday.
    """
    date   = str(params.get('date', '')).strip()
    top_n  = int(params.get('top_n', 20))

    if not date:
        return {'success': False, 'error': 'date is required'}

    con = _connect()
    _ensure_table(con)

    rows_today = con.execute("""
        SELECT symbol, rs_score, rs_percentile
        FROM pine_analytics
        WHERE trade_date = ?
          AND rs_score IS NOT NULL
        ORDER BY rs_score DESC
    """, (date,)).fetchall()

    if not rows_today:
        # try to find closest available date
        latest = con.execute("""
            SELECT trade_date FROM pine_analytics
            WHERE rs_score IS NOT NULL
            ORDER BY trade_date DESC LIMIT 1
        """).fetchone()
        con.close()
        if latest:
            return {
                'success': False,
                'error': f"No RS data for {date}. Latest available: {latest[0]}",
                'latest_date': latest[0]
            }
        return {'success': True, 'date': date, 'error': 'No RS data available at all', 'count': 0}

    today_map = {r['symbol']: {'rs_score': r['rs_score'], 'rs_percentile': r['rs_percentile']}
                 for r in rows_today}
    total = len(today_map)

    # get yesterday data for trend
    prev_date_row = con.execute("""
        SELECT DISTINCT trade_date FROM pine_analytics
        WHERE trade_date < ?
          AND rs_score IS NOT NULL
        ORDER BY trade_date DESC LIMIT 1
    """, (date,)).fetchone()

    yesterday_map = {}
    if prev_date_row:
        prev_rows = con.execute("""
            SELECT symbol, rs_score FROM pine_analytics
            WHERE trade_date = ? AND rs_score IS NOT NULL
        """, (prev_date_row[0],)).fetchall()
        yesterday_map = {r['symbol']: r['rs_score'] for r in prev_rows}
    con.close()

    # assign percentile ranks (recompute from data if rs_percentile missing)
    sorted_symbols = sorted(today_map.items(), key=lambda x: x[1]['rs_score'], reverse=True)

    def _classify_rs(rank_position: int, total_count: int) -> str:
        pct = (rank_position / total_count) * 100 if total_count else 50
        if pct <= 20:
            return RS_LEADER
        elif pct <= 40:
            return RS_STRONG
        elif pct <= 60:
            return RS_NEUTRAL
        elif pct <= 80:
            return RS_WEAK
        else:
            return RS_LAGGARD

    results = []
    for rank_idx, (symbol, data) in enumerate(sorted_symbols, start=1):
        rs = data['rs_score']
        rs_pct = data['rs_percentile']
        if rs_pct is None:
            rs_pct = round((rank_idx / total) * 100, 1)

        classification = _classify_rs(rank_idx, total)

        # trend
        if symbol in yesterday_map:
            delta = rs - yesterday_map[symbol]
            if delta > 2.0:
                trend = 'IMPROVING_FAST'
            elif delta > 0.5:
                trend = 'IMPROVING'
            elif delta < -2.0:
                trend = 'DETERIORATING_FAST'
            elif delta < -0.5:
                trend = 'DETERIORATING'
            else:
                trend = 'STABLE'
        else:
            trend = 'NO_PRIOR_DATA'

        results.append({
            'rank': rank_idx,
            'symbol': symbol,
            'rs_score': round(rs, 2),
            'rs_percentile': round(rs_pct, 1),
            'classification': classification,
            'trend': trend,
        })
        if rank_idx >= top_n:
            break

    # distribution summary
    all_rs = [v['rs_score'] for v in today_map.values()]
    rs_mean = round(statistics.mean(all_rs), 2) if all_rs else None
    rs_median = round(statistics.median(all_rs), 2) if all_rs else None
    rs_stdev = round(statistics.stdev(all_rs), 2) if len(all_rs) >= 2 else None

    leaders = sum(1 for v in today_map.values()
                  if v['rs_score'] >= (sorted(all_rs, reverse=True)[int(len(all_rs)*0.2)] if len(all_rs) >= 5 else 0))

    return {
        'success': True,
        'date': date,
        'total_stocks_ranked': total,
        'top_n': top_n,
        'distribution': {
            'mean': rs_mean,
            'median': rs_median,
            'stdev': rs_stdev,
        },
        'rankings': results,
    }


def vwap_position(params: dict) -> dict:
    """
    VWAP analysis over a lookback window:
    - % of days closed above VWAP
    - Current VWAP level and distance
    - VWAP trend (rising / falling)
    """
    symbol       = str(params.get('symbol', '')).upper().strip()
    lookback_days = int(params.get('lookback_days', 5))

    if not symbol:
        return {'success': False, 'error': 'symbol is required'}

    con = _connect()
    _ensure_table(con)

    rows = con.execute("""
        SELECT trade_date, vwap, opening_range_high, opening_range_low,
               session_bias, volume_poc
        FROM pine_analytics
        WHERE symbol = ?
          AND vwap IS NOT NULL
        ORDER BY trade_date DESC
        LIMIT ?
    """, (symbol, lookback_days)).fetchall()
    con.close()

    if not rows:
        return {'success': True, 'symbol': symbol, 'error': 'No VWAP data found', 'sessions': 0}

    sessions = [dict(r) for r in rows]
    latest   = sessions[0]
    current_vwap = latest['vwap']

    # fetch current price
    current_price = None
    try:
        con2 = _connect()
        row_p = con2.execute("""
            SELECT close FROM ohlcv_history_execution
            WHERE symbol = ? ORDER BY bar_date DESC LIMIT 1
        """, (symbol,)).fetchone()
        if row_p:
            current_price = float(row_p[0])
        con2.close()
    except Exception:
        pass

    # % of days above VWAP (using session_bias field)
    above_count = sum(1 for s in sessions if s.get('session_bias') == BIAS_ABOVE)
    at_count    = sum(1 for s in sessions if s.get('session_bias') == BIAS_AT)
    pct_above_vwap = round(above_count / len(sessions) * 100, 1) if sessions else None

    # distance from VWAP
    distance_pct = None
    distance_bps = None
    position_label = BIAS_UNKNOWN
    if current_price is not None and current_vwap and current_vwap != 0:
        distance_pct = round((current_price - current_vwap) / current_vwap * 100, 3)
        distance_bps = round(distance_pct * 100, 1)
        if abs(distance_bps) <= AT_VWAP_BPS:
            position_label = BIAS_AT
        elif distance_pct > 0:
            position_label = BIAS_ABOVE
        else:
            position_label = BIAS_BELOW

    # VWAP trend
    vwap_values = [s['vwap'] for s in sessions if s.get('vwap')]
    vwap_trend = 'UNKNOWN'
    if len(vwap_values) >= 3:
        # compare newest vs oldest
        recent = statistics.mean(vwap_values[:2])
        older  = statistics.mean(vwap_values[-2:])
        delta  = recent - older
        if delta > 0:
            vwap_trend = 'RISING'
        elif delta < 0:
            vwap_trend = 'FALLING'
        else:
            vwap_trend = 'FLAT'

    history = [
        {
            'date': s['trade_date'],
            'vwap': s['vwap'],
            'session_bias': s.get('session_bias', BIAS_UNKNOWN),
        }
        for s in sessions
    ]

    return {
        'success': True,
        'symbol': symbol,
        'sessions_analyzed': len(sessions),
        'latest_date': latest['trade_date'],
        'current_vwap': current_vwap,
        'current_price': current_price,
        'distance_from_vwap_pct': distance_pct,
        'distance_from_vwap_bps': distance_bps,
        'current_position': position_label,
        'pct_days_above_vwap': pct_above_vwap,
        'pct_days_at_vwap': round(at_count / len(sessions) * 100, 1) if sessions else None,
        'vwap_trend': vwap_trend,
        'opening_range_high': latest.get('opening_range_high'),
        'opening_range_low': latest.get('opening_range_low'),
        'vwap_history': history,
    }


def corporate_event_scan(params: dict) -> dict:
    """
    Scan for corporate events detected by Pine scripts.
    Groups by event type, shows surrounding price context.
    """
    lookback_days = int(params.get('lookback_days', 90))
    cutoff_date = (datetime.utcnow() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

    con = _connect()
    _ensure_table(con)

    rows = con.execute("""
        SELECT pa.symbol, pa.trade_date, pa.corporate_event_type,
               pa.volume_poc, pa.vwap, pa.rs_score
        FROM pine_analytics pa
        WHERE pa.corporate_event_flag = 1
          AND pa.trade_date >= ?
        ORDER BY pa.trade_date DESC
    """, (cutoff_date,)).fetchall()
    con.close()

    if not rows:
        return {
            'success': True,
            'lookback_days': lookback_days,
            'total_events': 0,
            'events': [],
            'by_type': {},
        }

    events = []
    by_type = defaultdict(list)

    for r in rows:
        event_type = r['corporate_event_type'] or 'UNSPECIFIED'
        entry = {
            'symbol': r['symbol'],
            'date': r['trade_date'],
            'event_type': event_type,
            'vwap': r['vwap'],
            'volume_poc': r['volume_poc'],
            'rs_score': r['rs_score'],
        }
        events.append(entry)
        by_type[event_type].append({'symbol': r['symbol'], 'date': r['trade_date']})

    # symbol-level counts
    symbol_counts = defaultdict(int)
    for e in events:
        symbol_counts[e['symbol']] += 1

    return {
        'success': True,
        'lookback_days': lookback_days,
        'cutoff_date': cutoff_date,
        'total_events': len(events),
        'unique_symbols': len(symbol_counts),
        'events': events[:200],
        'by_type': dict(by_type),
        'most_active_symbols': sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:10],
    }


def pine_data_coverage(params: dict) -> dict:
    """
    Coverage statistics across symbols and Pine scripts.
    """
    con = _connect()
    _ensure_table(con)

    total_rows = con.execute("SELECT COUNT(*) FROM pine_analytics").fetchone()[0]
    if total_rows == 0:
        con.close()
        return {
            'success': True,
            'total_rows': 0,
            'message': 'No Pine analytics data yet.',
        }

    symbols_count = con.execute("SELECT COUNT(DISTINCT symbol) FROM pine_analytics").fetchone()[0]
    date_range = con.execute("""
        SELECT MIN(trade_date), MAX(trade_date) FROM pine_analytics
    """).fetchone()

    # per-source-script coverage
    script_counts = con.execute("""
        SELECT source_script, COUNT(*) as cnt, COUNT(DISTINCT symbol) as syms
        FROM pine_analytics
        GROUP BY source_script
    """).fetchall()

    # field coverage
    field_coverage = {}
    for field in ('volume_poc', 'vwap', 'rs_score', 'corporate_event_flag'):
        cnt = con.execute(f"SELECT COUNT(*) FROM pine_analytics WHERE {field} IS NOT NULL").fetchone()[0]
        field_coverage[field] = {
            'rows_with_data': cnt,
            'pct': round(cnt / total_rows * 100, 1) if total_rows else 0,
        }

    # recent activity (last 7 days)
    week_ago = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
    recent_rows = con.execute("""
        SELECT COUNT(*) FROM pine_analytics WHERE trade_date >= ?
    """, (week_ago,)).fetchone()[0]

    # symbols without recent data (potential gaps)
    active_symbols = con.execute("""
        SELECT symbol FROM pine_analytics
        GROUP BY symbol
        HAVING MAX(trade_date) < ?
    """, (week_ago,)).fetchall()
    stale_symbols = [r['symbol'] for r in active_symbols]

    con.close()

    return {
        'success': True,
        'total_rows': total_rows,
        'unique_symbols': symbols_count,
        'date_range': {
            'earliest': date_range[0],
            'latest': date_range[1],
        },
        'recent_activity': {
            'rows_last_7d': recent_rows,
            'stale_symbols': stale_symbols[:50],
            'stale_count': len(stale_symbols),
        },
        'by_script': [
            {'source_script': r['source_script'], 'rows': r['cnt'], 'symbols': r['syms']}
            for r in script_counts
        ],
        'field_coverage': field_coverage,
    }


def build_full(params: dict) -> dict:
    """
    Build RS rankings for the latest date + corporate event summary.
    Returns an integrated daily Pine analytics briefing.
    """
    con = _connect()
    _ensure_table(con)

    latest_date_row = con.execute("""
        SELECT trade_date FROM pine_analytics
        WHERE rs_score IS NOT NULL
        ORDER BY trade_date DESC LIMIT 1
    """).fetchone()
    con.close()

    if not latest_date_row:
        return {
            'success': True,
            'warning': 'No RS data available yet. Run store_pine_data first.',
        }

    latest_date = latest_date_row[0]

    # RS rankings
    rs_result = rs_ranking({'date': latest_date, 'top_n': 30})

    # Corporate events (last 90 days)
    corp_result = corporate_event_scan({'lookback_days': 90})

    # Coverage stats
    coverage = pine_data_coverage({})

    # Recent unusual: stocks with RS_LEADER classification + corporate event
    leaders = {r['symbol'] for r in rs_result.get('rankings', [])
               if r.get('classification') == RS_LEADER}
    flagged = {e['symbol'] for e in corp_result.get('events', [])}
    overlap = sorted(leaders & flagged)

    return {
        'success': True,
        'date': latest_date,
        'rs_summary': {
            'total_ranked': rs_result.get('total_stocks_ranked', 0),
            'top_10': rs_result.get('rankings', [])[:10],
            'distribution': rs_result.get('distribution', {}),
        },
        'corporate_events': {
            'total_90d': corp_result.get('total_events', 0),
            'unique_symbols': corp_result.get('unique_symbols', 0),
            'recent_5': corp_result.get('events', [])[:5],
            'by_type': corp_result.get('by_type', {}),
        },
        'high_conviction_watchlist': overlap,
        'coverage': {
            'total_rows': coverage.get('total_rows', 0),
            'unique_symbols': coverage.get('unique_symbols', 0),
            'date_range': coverage.get('date_range', {}),
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# Command dispatch
# ══════════════════════════════════════════════════════════════════════════

COMMANDS = {
    'store_pine_data':          store_pine_data,
    'volume_profile_analysis':  volume_profile_analysis,
    'rs_ranking':               rs_ranking,
    'vwap_position':            vwap_position,
    'corporate_event_scan':     corporate_event_scan,
    'pine_data_coverage':       pine_data_coverage,
    'build_full':               build_full,
}


def main():
    if len(sys.argv) < 3:
        print(json.dumps({
            'success': False,
            'error': 'Usage: pine_analytics_bridge.py <command> <json_params>',
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    cmd    = sys.argv[1].strip()
    try:
        params = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        print(json.dumps({'success': False, 'error': f'Invalid JSON params: {e}'}))
        sys.exit(1)

    if cmd not in COMMANDS:
        print(json.dumps({
            'success': False,
            'error': f"Unknown command: '{cmd}'",
            'available_commands': list(COMMANDS.keys()),
        }))
        sys.exit(1)

    try:
        result = COMMANDS[cmd](params)
    except Exception as e:
        import traceback
        result = {
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc(),
        }

    print(json.dumps(result))


if __name__ == '__main__':
    main()
