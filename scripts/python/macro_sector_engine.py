"""
Macro-Sector Sensitivity Engine — EGX Navigator  Ph82
=======================================================
يربط الماكرو الكلي بقيادة القطاعات بدلاً من عرضهم بشكل منفصل.

يجيب على: "بناءً على CBE rate + USD/EGP + Inflation الحالية،
           أي القطاعات تستفيد وأيها تتضرر؟"

CLI:
  python3 macro_sector_engine.py analyze     # تحليل كامل
  python3 macro_sector_engine.py analyze --json
  python3 macro_sector_engine.py scenario --rate-cut 1  # لو CBE خفضت 1%
"""

import sqlite3
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'egx_trading.db'

# ─────────────────────────────────────────────────────────────────────────────
# Sensitivity Matrix
# ─────────────────────────────────────────────────────────────────────────────

MACRO_SECTOR_SENSITIVITY = {
    'Banks':             {'cbe_rate': +0.65, 'usdegp': -0.20, 'inflation': +0.10},
    'Real Estate':       {'cbe_rate': -0.80, 'usdegp': +0.30, 'inflation': +0.20},
    'Construction':      {'cbe_rate': -0.50, 'usdegp': +0.40, 'inflation': -0.15},
    'Consumer Staples':  {'cbe_rate': -0.20, 'usdegp': -0.60, 'inflation': -0.40},
    'Technology':        {'cbe_rate': -0.15, 'usdegp': +0.10, 'inflation': -0.10},
    'Telecoms':          {'cbe_rate': -0.30, 'usdegp': +0.05, 'inflation': -0.20},
    'Food & Bev':        {'cbe_rate': -0.10, 'usdegp': -0.50, 'inflation': -0.45},
    'Chemicals':         {'cbe_rate': -0.20, 'usdegp': +0.60, 'inflation': -0.30},
    'Healthcare':        {'cbe_rate': -0.15, 'usdegp': +0.20, 'inflation': -0.20},
    'Utilities':         {'cbe_rate': -0.40, 'usdegp': +0.05, 'inflation': +0.05},
    'Industrials':       {'cbe_rate': -0.35, 'usdegp': +0.25, 'inflation': -0.15},
    'Consumer Durables': {'cbe_rate': -0.30, 'usdegp': +0.10, 'inflation': -0.30},
}

# Macro factor explanations (for report)
_EXPLANATIONS = {
    'Banks': 'يستفيد من الفائدة المرتفعة — هامش الفائدة الصافي يرتفع',
    'Real Estate': 'يتأثر سلباً من الفائدة المرتفعة — تكلفة التمويل ترتفع',
    'Construction': 'يتأثر من الفائدة — يستفيد من ضعف الجنيه (تصدير خدمات)',
    'Consumer Staples': 'يتأثر من ضعف الجنيه والتضخم — تكاليف الاستيراد ترتفع',
    'Technology': 'محايد نسبياً — إيرادات دولارية جزئياً',
    'Telecoms': 'يتأثر من الفائدة — إيرادات محلية بالجنيه',
    'Food & Bev': 'يتأثر من ضعف الجنيه والتضخم — مدخلات مستوردة',
    'Chemicals': 'يستفيد من ضعف الجنيه — تصدير منتجات بالدولار',
    'Healthcare': 'يستفيد جزئياً من ضعف الجنيه — استيراد معدات ولكن تصدير خدمات',
    'Utilities': 'يتأثر من الفائدة — استثمارات رأسمالية ضخمة',
    'Industrials': 'يستفيد من ضعف الجنيه (تصدير) — يتأثر من الفائدة',
    'Consumer Durables': 'يتأثر من التضخم والفائدة — انخفاض الإنفاق التقديري',
}


# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_macro_from_db(db_path: Path = DB_PATH) -> dict:
    """
    Load latest macro indicators from DB.
    Returns dict with keys: cbe_rate, inflation, usdegp, data_date.
    Falls back to Egyptian-market defaults if data unavailable.
    """
    defaults = {
        'cbe_rate': 19.0,
        'inflation': 14.9,
        'usdegp': 52.67,
        'data_date': None,
        'source': 'defaults',
    }

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = pd.read_sql_query(
                """
                SELECT indicator_code, value, date
                FROM macro_indicators
                ORDER BY date DESC
                LIMIT 60
                """,
                conn,
            )
        finally:
            conn.close()
    except Exception:
        return defaults

    if rows.empty:
        return defaults

    # Pick latest value per indicator_code
    latest = (
        rows.sort_values('date', ascending=False)
            .drop_duplicates(subset='indicator_code', keep='first')
    )
    lookup = dict(zip(latest['indicator_code'], latest['value']))

    result = dict(defaults)
    result['source'] = 'database'

    if 'EGINTR' in lookup:
        result['cbe_rate'] = float(lookup['EGINTR'])
    if 'EGIRYY' in lookup:
        result['inflation'] = float(lookup['EGIRYY'])
    if 'USDEGP' in lookup:
        result['usdegp'] = float(lookup['USDEGP'])

    # Most recent date in data
    result['data_date'] = str(rows['date'].max())
    return result


def _load_usdegp_history(db_path: Path, months_back: int = 3) -> list[float]:
    """Return list of USDEGP values from `months_back` months ago to now."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = pd.read_sql_query(
                """
                SELECT value, date FROM macro_indicators
                WHERE indicator_code = 'USDEGP'
                ORDER BY date DESC
                LIMIT 90
                """,
                conn,
            )
        finally:
            conn.close()
        if rows.empty:
            return []
        rows['date'] = pd.to_datetime(rows['date'])
        rows = rows.sort_values('date')
        cutoff = rows['date'].max() - pd.DateOffset(months=months_back)
        old = rows[rows['date'] <= cutoff]['value'].tail(1)
        return [float(old.iloc[0])] if not old.empty else []
    except Exception:
        return []


def _load_inflation_history(db_path: Path, n_readings: int = 3) -> list[float]:
    """Return last n inflation readings for trend detection."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = pd.read_sql_query(
                """
                SELECT value FROM macro_indicators
                WHERE indicator_code = 'EGIRYY'
                ORDER BY date DESC
                LIMIT ?
                """,
                conn,
                params=(n_readings,),
            )
        finally:
            conn.close()
        return rows['value'].tolist() if not rows.empty else []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Analytical functions
# ─────────────────────────────────────────────────────────────────────────────

def estimate_rate_direction(macro_data: dict, months_ahead: int = 3) -> dict:
    """
    Simple heuristic to estimate CBE rate direction.
    - inflation < cbe_rate        → likely CUT
    - inflation > cbe_rate * 0.80 → likely HOLD
    - inflation > cbe_rate        → HIKE risk
    """
    cbe = macro_data['cbe_rate']
    inf = macro_data['inflation']

    if inf < cbe * 0.60:
        direction = 'CUT'
        rate_change = -0.5
        confidence = 'MEDIUM'
    elif inf < cbe:
        direction = 'CUT'
        rate_change = -0.25
        confidence = 'LOW'
    elif inf <= cbe * 1.05:
        direction = 'HOLD'
        rate_change = 0.0
        confidence = 'MEDIUM'
    else:
        direction = 'HIKE'
        rate_change = +0.25
        confidence = 'LOW'

    direction_ar = {'CUT': 'تخفيض محتمل', 'HOLD': 'ثبات متوقع', 'HIKE': 'رفع محتمل'}

    return {
        'expected_rate_change': rate_change,
        'direction': direction,
        'direction_ar': direction_ar.get(direction, direction),
        'confidence': confidence,
        'confidence_ar': {'LOW': 'منخفضة', 'MEDIUM': 'متوسطة'}.get(confidence, confidence),
        'months_ahead': months_ahead,
    }


def compute_macro_headwinds(
    cbe_rate: float,
    cbe_rate_expected: float,
    usdegp: float,
    usdegp_3m_change: float,
    inflation: float,
    inflation_trend: str,
) -> dict:
    """
    Compute macro impact score per sector.

    Parameters
    ----------
    cbe_rate          : current CBE rate (%)
    cbe_rate_expected : expected CBE rate after estimated change
    usdegp            : current USD/EGP rate
    usdegp_3m_change  : percentage change in USD/EGP over last 3 months
    inflation         : current inflation rate (%)
    inflation_trend   : 'rising' | 'falling' | 'stable'
    """
    rate_delta = cbe_rate_expected - cbe_rate  # negative = cut
    inflation_delta = 1.0 if inflation_trend == 'rising' else (-1.0 if inflation_trend == 'falling' else 0.0)

    sector_scores: dict[str, float] = {}
    for sector, betas in MACRO_SECTOR_SENSITIVITY.items():
        score = (
            betas['cbe_rate']    * rate_delta
            + betas['usdegp']    * usdegp_3m_change
            + betas['inflation'] * inflation_delta
        )
        sector_scores[sector] = round(score, 4)

    # Sort descending
    sector_rankings = dict(sorted(sector_scores.items(), key=lambda x: x[1], reverse=True))

    top_tailwinds = [s for s, v in sector_rankings.items() if v > 0.15][:3]
    top_headwinds = [s for s, v in sector_rankings.items() if v < -0.15]

    # Reverse for headwinds (worst first)
    top_headwinds = sorted(top_headwinds, key=lambda s: sector_scores[s])[:3]

    neutral = [s for s in sector_rankings if s not in top_tailwinds and s not in top_headwinds]

    # Scenario summary
    parts = []
    if rate_delta < 0:
        parts.append(f'تخفيض الفائدة {abs(rate_delta):.2f}%')
    elif rate_delta > 0:
        parts.append(f'رفع الفائدة {abs(rate_delta):.2f}%')
    if abs(usdegp_3m_change) > 0.02:
        if usdegp_3m_change > 0:
            parts.append(f'تراجع الجنيه {usdegp_3m_change:.1%}')
        else:
            parts.append(f'تحسن الجنيه {abs(usdegp_3m_change):.1%}')
    if inflation_trend != 'stable':
        parts.append(f'التضخم في {inflation_trend}')

    scenario_summary = ' | '.join(parts) if parts else 'ظروف ماكرو مستقرة'

    return {
        'sector_rankings': sector_rankings,
        'top_tailwinds': top_tailwinds,
        'top_headwinds': top_headwinds,
        'neutral_sectors': neutral,
        'scenario_summary': scenario_summary,
        'macro_inputs': {
            'cbe_rate': cbe_rate,
            'cbe_rate_expected': cbe_rate_expected,
            'rate_delta': rate_delta,
            'usdegp': usdegp,
            'usdegp_3m_change': usdegp_3m_change,
            'inflation': inflation,
            'inflation_trend': inflation_trend,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def run_macro_analysis(
    db_path: Path = DB_PATH,
    rate_override: float = None,
    egp_depreciation_override: float = None,
) -> dict:
    """
    Run full macro-sector analysis, persist to DB, and return result dict.

    Parameters
    ----------
    rate_override               : manually override rate delta (e.g., -1.0 for -1%)
    egp_depreciation_override   : manually override USD/EGP 3m change (e.g., 0.05 = +5%)
    """
    macro_data = load_macro_from_db(db_path)

    cbe_rate = macro_data['cbe_rate']
    inflation = macro_data['inflation']
    usdegp = macro_data['usdegp']

    # Rate direction
    rate_dir = estimate_rate_direction(macro_data)

    # Override for scenario analysis
    if rate_override is not None:
        cbe_rate_expected = cbe_rate + rate_override
        rate_dir['expected_rate_change'] = rate_override
        rate_dir['direction'] = 'CUT' if rate_override < 0 else ('HIKE' if rate_override > 0 else 'HOLD')
    else:
        cbe_rate_expected = cbe_rate + rate_dir['expected_rate_change']

    # USD/EGP 3-month change
    if egp_depreciation_override is not None:
        usdegp_3m_change = egp_depreciation_override
    else:
        hist = _load_usdegp_history(db_path, months_back=3)
        if hist:
            usdegp_3m_change = float((usdegp - hist[0]) / hist[0]) if hist[0] != 0 else 0.0
        else:
            usdegp_3m_change = 0.0

    # Inflation trend from last 3 readings
    inf_hist = _load_inflation_history(db_path, n_readings=3)
    if len(inf_hist) >= 2:
        # Most recent first
        if inf_hist[0] > inf_hist[-1] * 1.02:
            inflation_trend = 'rising'
        elif inf_hist[0] < inf_hist[-1] * 0.98:
            inflation_trend = 'falling'
        else:
            inflation_trend = 'stable'
    else:
        inflation_trend = 'stable'

    # Compute headwinds
    headwinds = compute_macro_headwinds(
        cbe_rate=cbe_rate,
        cbe_rate_expected=cbe_rate_expected,
        usdegp=usdegp,
        usdegp_3m_change=usdegp_3m_change,
        inflation=inflation,
        inflation_trend=inflation_trend,
    )

    analysis_date = datetime.now().strftime('%Y-%m-%d')

    full_result = {
        'analysis_date': analysis_date,
        'macro_snapshot': {
            'cbe_rate': cbe_rate,
            'inflation': inflation,
            'usdegp': usdegp,
            'data_date': macro_data.get('data_date'),
            'source': macro_data.get('source'),
        },
        'rate_direction': rate_dir,
        'usdegp_3m_change': round(usdegp_3m_change, 4),
        'inflation_trend': inflation_trend,
        'sector_analysis': headwinds,
    }

    # ── Persist to DB ─────────────────────────────────────────────────────────
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS macro_sector_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_date TEXT UNIQUE,
                    cbe_rate REAL,
                    inflation REAL,
                    usdegp REAL,
                    rate_direction TEXT,
                    top_tailwinds TEXT,
                    top_headwinds TEXT,
                    details_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                INSERT OR REPLACE INTO macro_sector_analysis
                    (analysis_date, cbe_rate, inflation, usdegp,
                     rate_direction, top_tailwinds, top_headwinds, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_date,
                cbe_rate,
                inflation,
                usdegp,
                rate_dir['direction'],
                json.dumps(headwinds['top_tailwinds'], ensure_ascii=False),
                json.dumps(headwinds['top_headwinds'], ensure_ascii=False),
                json.dumps(full_result, ensure_ascii=False),
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        full_result['db_error'] = str(e)

    return full_result


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def print_macro_report(analysis: dict) -> None:
    """Print a human-readable macro-sector report."""
    snap = analysis['macro_snapshot']
    rate_dir = analysis['rate_direction']
    sec = analysis['sector_analysis']
    rankings = sec['sector_rankings']

    inf_trend_ar = {'rising': 'صاعد', 'falling': 'هابط', 'stable': 'مستقر'}.get(
        analysis.get('inflation_trend', 'stable'), 'غير محدد')

    print()
    print('🌍 تحليل الماكرو والقطاعات — EGX Navigator')
    print('━' * 50)
    print(
        f"  CBE Rate: {snap['cbe_rate']:.1f}%  |  "
        f"التضخم: {snap['inflation']:.1f}%  |  "
        f"USD/EGP: {snap['usdegp']:.2f}"
    )
    print(
        f"  اتجاه الفائدة: {rate_dir.get('direction_ar', rate_dir['direction'])} "
        f"(ثقة: {rate_dir.get('confidence_ar', rate_dir['confidence'])})"
    )
    print(f"  اتجاه التضخم: {inf_trend_ar}  |  تغير USD/EGP (3m): {analysis['usdegp_3m_change']:+.1%}")
    print(f"  الملخص: {sec['scenario_summary']}")
    print('━' * 50)

    # Tailwinds
    tailwinds = sec['top_tailwinds']
    if tailwinds:
        print('  🟢 قطاعات مستفيدة (Tailwinds):')
        for i, sector in enumerate(tailwinds, 1):
            score = rankings.get(sector, 0)
            explanation = _EXPLANATIONS.get(sector, '')
            print(f"    {i}. {sector} ({score:+.2f})  — {explanation}")
    else:
        print('  🟢 لا توجد قطاعات مستفيدة بشكل واضح في هذا السيناريو')

    print()

    # Headwinds
    headwinds = sec['top_headwinds']
    if headwinds:
        print('  🔴 قطاعات متضررة (Headwinds):')
        for i, sector in enumerate(headwinds, 1):
            score = rankings.get(sector, 0)
            explanation = _EXPLANATIONS.get(sector, '')
            print(f"    {i}. {sector} ({score:+.2f})  — {explanation}")
    else:
        print('  🔴 لا توجد قطاعات متضررة بشكل واضح في هذا السيناريو')

    print()

    # Neutral
    neutral = sec['neutral_sectors']
    if neutral:
        print(f"  ➡️  محايد: {', '.join(neutral)}")

    print()

    # Full ranking
    print('  📊 الترتيب الكامل للقطاعات (من الأكثر استفادةً إلى الأكثر تضرراً):')
    for sector, score in rankings.items():
        bar_len = int(abs(score) * 20)
        bar = ('█' * bar_len) if score > 0 else ('░' * bar_len)
        direction = '▲' if score > 0.15 else ('▼' if score < -0.15 else '─')
        print(f"    {direction} {sector:<20} {score:+.3f}  {bar}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='EGX Navigator — Macro-Sector Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command')

    # analyze
    analyze_p = sub.add_parser('analyze', help='تحليل الماكرو والقطاعات')
    analyze_p.add_argument('--json', action='store_true', help='JSON output')
    analyze_p.add_argument('--db', default=str(DB_PATH), help='مسار قاعدة البيانات')

    # scenario
    scenario_p = sub.add_parser('scenario', help='تحليل سيناريو افتراضي')
    scenario_p.add_argument('--json', action='store_true', help='JSON output')
    scenario_p.add_argument('--db', default=str(DB_PATH))
    scenario_p.add_argument(
        '--rate-cut', type=float, default=None,
        metavar='PCT',
        help='تخفيض الفائدة بمقدار N% (مثال: --rate-cut 1)',
    )
    scenario_p.add_argument(
        '--rate-hike', type=float, default=None,
        metavar='PCT',
        help='رفع الفائدة بمقدار N%',
    )
    scenario_p.add_argument(
        '--egp-depreciation', type=float, default=None,
        metavar='PCT',
        help='انخفاض الجنيه بنسبة N%% (مثال: --egp-depreciation 5)',
    )

    args = parser.parse_args()

    if args.command == 'analyze':
        result = run_macro_analysis(Path(args.db))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_macro_report(result)

    elif args.command == 'scenario':
        rate_override = None
        if args.rate_cut is not None:
            rate_override = -abs(args.rate_cut)
        elif args.rate_hike is not None:
            rate_override = +abs(args.rate_hike)

        egp_dep = None
        if args.egp_depreciation is not None:
            egp_dep = float(args.egp_depreciation) / 100.0  # convert % → ratio

        result = run_macro_analysis(
            db_path=Path(args.db),
            rate_override=rate_override,
            egp_depreciation_override=egp_dep,
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print()
            # Print scenario banner
            scenario_parts = []
            if rate_override is not None:
                scenario_parts.append(f"تغيير الفائدة: {rate_override:+.1f}%")
            if egp_dep is not None:
                scenario_parts.append(f"تغير USD/EGP: {egp_dep:+.1%}")
            if scenario_parts:
                print(f"🔮 سيناريو افتراضي: {' | '.join(scenario_parts)}")
            print_macro_report(result)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
