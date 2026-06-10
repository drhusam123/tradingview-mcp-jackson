"""
EGX ML Signal Report Generator
================================
Produces a professional, client-ready daily signal report from final_signals.
Raw ML predictions are research-only and must not drive client opportunities.

Usage:
    python3 egx_client_report.py               # today's report
    python3 egx_client_report.py 2026-05-29    # specific date
    python3 egx_client_report.py --json        # JSON output only
"""

import sqlite3
import json
import sys
import datetime
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path('/Users/dr.husam/tradingview-mcp-jackson/data/egx_trading.db')


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def tier_star(tier: str) -> str:
    return {'HIGH': '★★★', 'MEDIUM': '★★☆', 'LOW': '★☆☆'}.get(tier, '★☆☆')


def tier_ar(tier: str) -> str:
    return {'HIGH': 'عالي الثقة', 'MEDIUM': 'متوسط الثقة', 'LOW': 'منخفض الثقة'}.get(tier, '-')


def regime_ar(regime: str) -> str:
    return {'BULL': 'صاعد', 'BEAR': 'هابط', 'NEUTRAL': 'محايد',
            'CHOPPY': 'متذبذب', 'UNKNOWN': 'غير محدد'}.get(regime, regime)


def risk_warning(regime: str, n_signals: int) -> str:
    if regime == 'BEAR':
        return ("⚠️  تحذير: السوق في وضع هبوطي (BEAR). الإشارات مفلترة بمعيار "
                f"ثقة ≥82%. المركز الواحد لا يتجاوز الحجم المحدد. "
                f"عدد الإشارات المُصفاة: {n_signals}")
    elif regime == 'BULL':
        return f"✅ السوق في وضع صاعد (BULL). {n_signals} إشارة بمعيار ≥60%."
    else:
        return f"➡️  السوق محايد. {n_signals} إشارة بمعيار ≥68%."


def driver_text(drivers: list) -> str:
    """Convert top_drivers JSON to readable Arabic explanation."""
    labels = {
        'lgbm_prob':              'LightGBM',
        'xgb_prob':               'XGBoost',
        'rf_prob':                'Random Forest',
        'vol_ratio':              'نسبة الحجم',
        'rsi14':                  'RSI',
        'bb_width':               'ضغط BB',
        'regime_bull_prob':       'نموذج BULL',
        'regime_bear_prob':       'نموذج BEAR',
        'stock_model_prob':       'نموذج السهم',
        'ensemble_prob_pre_blend':'المجموعة الخام',
        'days_since_explosion':   'أيام منذ آخر انفجار',
        'dse_penalty':            'خصم ما بعد انفجار',
    }
    parts = []
    for d in drivers[:4]:
        feat = d.get('feature', '')
        val  = d.get('value', 0)
        if feat in labels:
            if isinstance(val, float):
                parts.append(f"{labels[feat]}={val:.2f}")
            else:
                parts.append(f"{labels[feat]}={val}")
    return ' | '.join(parts) if parts else 'بيانات غير متاحة'


def latest_ohlcv_date(conn) -> str:
    row = conn.execute(
        "SELECT MAX(date(bar_time, 'unixepoch')) AS latest FROM ohlcv_history WHERE close > 0"
    ).fetchone()
    return row['latest'] if row and row['latest'] else None


def final_signal_is_client_safe(row) -> bool:
    """Production gate: final signal + risk structure + quality proof."""
    try:
        if int(row['actionable'] or 0) != 1 or row['veto_reason']:
            return False
        entry = float(row['entry_price'] or 0)
        entry_high = float(row['entry_high'] or 0)
        stop = float(row['stop_loss'] or 0)
        t1 = float(row['t1_target'] or 0)
        rr = float(row['r_ratio'] or 0)
        if not (entry > 0 and entry_high >= entry and stop > 0 and stop < entry and t1 > entry and rr >= 1.3):
            return False
        bd = json.loads(row['source_breakdown'] or '{}')
        if bd.get('quality_gate_passed') is not True:
            return False
        if float(bd.get('anti_law', 0) or 0) <= 0:
            return False
        return True
    except Exception:
        return False


# ── Main Report ───────────────────────────────────────────────────────────────

def generate_report(pred_date: str = None, json_only: bool = False) -> dict:
    if pred_date is None:
        pred_date = datetime.date.today().isoformat()

    conn = get_db()

    latest_data = latest_ohlcv_date(conn)

    # ── Regime ────────────────────────────────────────────────────────────────
    regime_row = conn.execute(
        "SELECT regime FROM regime_history WHERE date<=? ORDER BY date DESC LIMIT 1",
        (pred_date,)
    ).fetchone()
    today_regime = str(regime_row['regime'] or 'UNKNOWN').upper() if regime_row else 'UNKNOWN'

    # Regime thresholds
    thresholds   = {'BULL': 0.60, 'NEUTRAL': 0.68, 'BEAR': 0.82}
    max_signals  = {'BULL': 7,    'NEUTRAL': 5,     'BEAR': 3}
    regime_key   = today_regime if today_regime in thresholds else 'NEUTRAL'
    threshold    = thresholds[regime_key]
    max_sig      = max_signals[regime_key]

    # ── Signals: final_signals is the only client source ──────────────────────
    rows = conn.execute("""
        SELECT f.*,
               COALESCE(su.sector, sp.archetype, '—') as sector,
               su.name as company_name
        FROM final_signals f
        LEFT JOIN stock_profiles sp ON f.symbol = sp.symbol
        LEFT JOIN stock_universe su ON f.symbol = su.symbol
        WHERE f.trade_date = ?
          AND f.actionable = 1
          AND f.veto_reason IS NULL
        ORDER BY f.score DESC
        LIMIT ?
    """, (pred_date, max_sig * 3)).fetchall()

    rows = [r for r in rows if final_signal_is_client_safe(r)][:max_sig]
    if latest_data and latest_data < pred_date:
        rows = []

    # Also pull all final scored for context table; no raw ML fallback.
    all_scored = conn.execute("""
        SELECT f.symbol, f.score, f.confidence, f.veto_reason, f.actionable,
               COALESCE(su.sector, sp.archetype, '—') as sector,
               su.name as company_name
        FROM final_signals f
        LEFT JOIN stock_profiles sp ON f.symbol = sp.symbol
        LEFT JOIN stock_universe su ON f.symbol = su.symbol
        WHERE f.trade_date = ?
        ORDER BY f.score DESC
        LIMIT 20
    """, (pred_date,)).fetchall()

    # ── Build signals list ────────────────────────────────────────────────────
    signals = []
    for r in rows:
        bd = json.loads(r['source_breakdown'] or '{}')
        entry = r['entry_price']
        stop = r['stop_loss']
        t1 = r['t1_target']
        t2 = r['t2_target']
        rr = r['r_ratio']
        risk = abs(entry - stop) / entry * 100 if entry and stop else None
        kelly = None
        expiry = 5
        ml = bd.get('ml')
        vol = bd.get('vol_ratio') or bd.get('volume_ratio')

        sig = {
            'symbol':      r['symbol'],
            'company':     (r['company_name'] or r['symbol'])[:30],
            'sector':      (r['sector'] or '—')[:20],
            'prob':        round(float(r['score'] or 0), 1),
            'tier':        'HIGH' if float(r['confidence'] or 0) >= 0.70 else 'MEDIUM',
            'tier_stars':  tier_star('HIGH' if float(r['confidence'] or 0) >= 0.70 else 'MEDIUM'),
            'tier_ar':     tier_ar('HIGH' if float(r['confidence'] or 0) >= 0.70 else 'MEDIUM'),
            'entry_price': entry,
            'stop_loss':   stop,
            'target_1':    t1,
            'target_2':    t2,
            'risk_pct':    risk,
            'rr_ratio':    rr,
            'kelly_pct':   kelly,
            'expiry_days': expiry,
            'lgbm':        ml,
            'xgb':         None,
            'rf':          None,
            'vol_ratio':   vol,
            'drivers_text': 'final_signals + quality_gate + R:R',
        }
        signals.append(sig)

    conn.close()

    report = {
        'generated_at':   datetime.datetime.now().isoformat(timespec='seconds'),
        'pred_date':      pred_date,
        'regime':         today_regime,
        'regime_ar':      regime_ar(today_regime),
        'threshold':      threshold,
        'max_signals':    max_sig,
        'n_signals':      len(signals),
        'signals':        signals,
        'latest_data':    latest_data,
        'model_version':  'final_signals production gate',
        'disclaimer':     ('هذا النظام للمساعدة البحثية فقط وليس توصية استثمارية. '
                           'إدارة المخاطر مسؤولية المستثمر.'),
    }

    if json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    # ── Text Report ───────────────────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║          نظام EGX ML — تقرير الإشارات اليومي                   ║")
    print("║          EGX ML SYSTEM — Daily Signal Report                    ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print(f"  التاريخ / Date  : {pred_date}")
    if latest_data and latest_data < pred_date:
        print(f"  ⛔ البيانات غير محدثة: آخر OHLCV موثوق {latest_data} — لا توجد توصيات عميل")
    print(f"  وضع السوق / Regime: {regime_ar(today_regime)} ({today_regime})")
    print(f"  معيار الثقة    : ≥ {threshold*100:.0f}%   |   الحد الأقصى: {max_sig} إشارة")
    print(f"  نموذج          : final_signals production gate")
    print()

    if not signals:
        print("  ── لا توجد إشارات تتجاوز معيار الثقة اليوم ──")
        print(f"  (وضع {today_regime} يتطلب ثقة عالية ≥{threshold*100:.0f}%)")
        print()
    else:
        print(risk_warning(today_regime, len(signals)))
        print()
        print(f"  {'رقم':>3}  {'السهم':^8}  {'القطاع':^15}  {'الثقة':^6}  {'الدرجة':^7}  "
              f"{'الدخول':^8}  {'الوقف':^8}  {'الهدف1':^8}  {'الهدف2':^8}  {'هيلي%':^7}  {'R:R':^5}")
        print("  " + "─"*100)

        for i, s in enumerate(signals, 1):
            entry_str = f"{s['entry_price']:.2f}" if s['entry_price'] else "—"
            stop_str  = f"{s['stop_loss']:.2f}"  if s['stop_loss']  else "—"
            t1_str    = f"{s['target_1']:.2f}"   if s['target_1']   else "—"
            t2_str    = f"{s['target_2']:.2f}"   if s['target_2']   else "—"
            kelly_str = f"{s['kelly_pct']:.1f}%" if s['kelly_pct']  else "—"
            rr_str    = f"{s['rr_ratio']:.1f}x"  if s['rr_ratio']   else "—"
            risk_str  = f"{s['risk_pct']:.1f}%"  if s['risk_pct']   else "—"

            print(f"  {'─'*96}")
            print(f"  {i}. {s['symbol']}  —  {s['company']}")
            print(f"     القطاع: {s['sector']}   |   الدرجة: {s['prob']:.1f}%  {s['tier_stars']}  ({s['tier_ar']})")
            print(f"     الدخول:  {entry_str:>8}  |  الوقف:   {stop_str:>8}  (خطر: {risk_str})")
            print(f"     الهدف1:  {t1_str:>8}  |  الهدف2:  {t2_str:>8}  (R:R {rr_str})")
            print(f"     الحجم (Kelly): {kelly_str}  |  الصلاحية: {s['expiry_days']} أيام تداول")
            print(f"     المؤشرات: {s['drivers_text']}")
            print()

    # Context table (all scored ≥30%)
    if all_scored:
        print()
        print("  ── حالة بوابة final_signals ──")
        n_total = len(all_scored)
        n_actionable = sum(
            1 for r in all_scored
            if int(r['actionable'] or 0) == 1 and not r['veto_reason']
        )
        print(f"  فُحصت {n_total} إشارة نهائية في العينة المعروضة؛ القابلة للتنفيذ: {n_actionable}.")
        if n_actionable == 0:
            print("  لا يتم عرض رموز مرفوضة للعميل حتى لا تُفهم كتوصيات مراقبة.")

    print()
    print("━"*68)
    print(f"  ⚠️  {report['disclaimer']}")
    print("━"*68)
    print()

    return report


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    json_only = '--json' in args
    args = [a for a in args if a != '--json']
    date_arg = args[0] if args else None
    generate_report(date_arg, json_only)
