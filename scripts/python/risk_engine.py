"""
Risk Engine — EGX Navigator  Ph81
===================================
طبقة الحماية المؤسسية: تحكم كامل في المخاطر على مستوى المحفظة.

المكونات:
  1. DrawdownProtectionEngine — قواعد خفض التعرض عند الانهيار
  2. AlphaDecayMonitor — رصد تآكل الـ edge يومياً
  3. RegimeTransitionWarning — تحذير مبكر عند تحول الـ regime
  4. BehavioralGuardrails — قواعد الانضباط المؤسسي

CLI:
  python3 risk_engine.py check              # تقييم الوضع الراهن
  python3 risk_engine.py check --json       # JSON output
  python3 risk_engine.py alpha_decay        # تحليل Alpha Decay فقط
  python3 risk_engine.py regime_warning     # تحليل Regime fragility فقط
"""

import sqlite3
import json
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent.parent / 'data' / 'egx_trading.db'


# ─────────────────────────────────────────────────────────────────────────────
# 1. DrawdownProtectionEngine
# ─────────────────────────────────────────────────────────────────────────────

class DrawdownProtectionEngine:
    """قواعد خفض التعرض عند الانهيار."""

    DRAWDOWN_RULES = [
        (-0.05,  'REDUCE',    0.70),   # -5%: خفض 30%
        (-0.08,  'REDUCE',    0.40),   # -8%: خفض 60%
        (-0.12,  'DEFENSIVE', 0.15),   # -12%: وضع دفاعي
        (-0.15,  'HALT',      0.00),   # -15%: إيقاف كامل
    ]

    def compute_current_drawdown(self, trades_df: pd.DataFrame) -> float:
        """Build equity curve and return current drawdown from all-time peak."""
        if trades_df.empty:
            return 0.0
        df = trades_df.sort_values('date').copy()
        df['equity'] = (1 + df['pnl_pct']).cumprod()
        peak = df['equity'].cummax()
        drawdown_series = (df['equity'] - peak) / peak
        return float(drawdown_series.iloc[-1])

    def evaluate(self, trades_df: pd.DataFrame, rolling_30d_sharpe: float = None) -> dict:
        """Evaluate current risk status and return action recommendation."""
        drawdown = self.compute_current_drawdown(trades_df)

        # Default (no breach)
        action = 'NORMAL'
        exposure_multiplier = 1.00
        for threshold, act, mult in self.DRAWDOWN_RULES:
            if drawdown <= threshold:
                action = act
                exposure_multiplier = mult
            else:
                break  # rules are ordered from least to most severe

        # Determine worst applicable rule
        applicable_action = 'NORMAL'
        applicable_mult = 1.00
        for threshold, act, mult in self.DRAWDOWN_RULES:
            if drawdown <= threshold:
                applicable_action = act
                applicable_mult = mult

        action = applicable_action
        exposure_multiplier = applicable_mult

        # Edge decay check
        edge_decaying = False
        if rolling_30d_sharpe is not None and rolling_30d_sharpe < 0.5:
            edge_decaying = True

        # Alert level
        if drawdown <= -0.15:
            alert_level = 'RED'
        elif drawdown <= -0.12:
            alert_level = 'RED'
        elif drawdown <= -0.08:
            alert_level = 'ORANGE'
        elif drawdown <= -0.05:
            alert_level = 'YELLOW'
        else:
            alert_level = 'GREEN'

        # Recommendation text
        if action == 'HALT':
            recommendation = 'إيقاف كامل للتداول — مراجعة الاستراتيجية مطلوبة'
        elif action == 'DEFENSIVE':
            recommendation = 'وضع دفاعي — خفض الحجم إلى 15% والتركيز على الحفاظ على رأس المال'
        elif action == 'REDUCE':
            recommendation = f'خفض التعرض إلى {int(exposure_multiplier * 100)}% من الحجم الطبيعي'
        else:
            rec_parts = ['متابعة الاستراتيجية الحالية']
            if edge_decaying:
                rec_parts.append('مراقبة تآكل الـ edge (Sharpe منخفض)')
            recommendation = ' — '.join(rec_parts)

        return {
            'drawdown_pct': round(drawdown * 100, 2),
            'action': action,
            'exposure_multiplier': exposure_multiplier,
            'edge_decaying': edge_decaying,
            'rolling_30d_sharpe': rolling_30d_sharpe,
            'recommendation': recommendation,
            'alert_level': alert_level,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. AlphaDecayMonitor
# ─────────────────────────────────────────────────────────────────────────────

class AlphaDecayMonitor:
    """رصد تآكل الـ edge يومياً عبر مقارنة الأداء على نوافذ زمنية مختلفة."""

    def _compute_pf(self, trades_subset: pd.DataFrame) -> float:
        """Profit factor = gross profit / gross loss. Returns inf if no losses, 0 if empty."""
        if trades_subset.empty:
            return 0.0
        wins = trades_subset.loc[trades_subset['pnl_pct'] > 0, 'pnl_pct'].sum()
        losses = trades_subset.loc[trades_subset['pnl_pct'] < 0, 'pnl_pct'].sum()
        if losses == 0:
            return float('inf')
        return float(wins / abs(losses))

    def check(self, trades_df: pd.DataFrame, min_trades: int = 15) -> dict:
        """Check for alpha decay by comparing recent vs historical performance."""
        if trades_df.empty:
            return {
                'system_health': 'UNKNOWN',
                'pf_30d': 0.0, 'pf_90d': 0.0,
                'wr_30d': 0.0, 'wr_90d': 0.0,
                'decay_detected': False, 'decay_magnitude_pct': 0.0,
                'suggested_action': 'بيانات غير كافية للتحليل',
                'n_trades_30d': 0, 'n_trades_90d': 0,
            }

        df = trades_df.sort_values('date').copy()
        now = pd.Timestamp(df['date'].max())

        cutoff_30d = now - pd.Timedelta(days=30)
        cutoff_90d = now - pd.Timedelta(days=90)

        df['date'] = pd.to_datetime(df['date'])
        trades_30d = df[df['date'] >= cutoff_30d]
        trades_90d = df[df['date'] >= cutoff_90d]

        n_30 = len(trades_30d)
        n_90 = len(trades_90d)

        pf_30 = self._compute_pf(trades_30d)
        pf_90 = self._compute_pf(trades_90d)

        wr_30 = float((trades_30d['pnl_pct'] > 0).mean()) if n_30 > 0 else 0.0
        wr_90 = float((trades_90d['pnl_pct'] > 0).mean()) if n_90 > 0 else 0.0

        # Decay: >30% decline in profit factor
        if pf_90 == 0 or pf_90 == float('inf') or n_30 < min_trades:
            decay_detected = False
            decay_magnitude = 0.0
        else:
            decay_detected = pf_30 < pf_90 * 0.70
            if pf_90 > 0 and pf_90 != float('inf'):
                decay_magnitude = round((1 - pf_30 / pf_90) * 100, 1)
            else:
                decay_magnitude = 0.0

        # Health classification
        if n_30 < min_trades:
            health = 'UNKNOWN'
        elif decay_detected and pf_30 < 1.0:
            health = 'FAILING'
        elif decay_detected:
            health = 'DEGRADING'
        else:
            health = 'HEALTHY'

        # Suggested action
        if health == 'FAILING':
            suggested_action = 'إيقاف فوري + مراجعة كاملة للاستراتيجية'
        elif health == 'DEGRADING':
            suggested_action = 'خفض حجم الصفقات 30% + مراقبة يومية'
        elif health == 'UNKNOWN':
            suggested_action = 'بيانات غير كافية — الاستمرار بحذر'
        else:
            suggested_action = 'الاستمرار وفق الخطة الحالية'

        return {
            'system_health': health,
            'pf_30d': round(pf_30, 3) if pf_30 != float('inf') else 999.0,
            'pf_90d': round(pf_90, 3) if pf_90 != float('inf') else 999.0,
            'wr_30d': round(wr_30, 3),
            'wr_90d': round(wr_90, 3),
            'decay_detected': decay_detected,
            'decay_magnitude_pct': decay_magnitude,
            'suggested_action': suggested_action,
            'n_trades_30d': n_30,
            'n_trades_90d': n_90,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. RegimeTransitionWarning
# ─────────────────────────────────────────────────────────────────────────────

class RegimeTransitionWarning:
    """تحذير مبكر عند تحول الـ regime."""

    def check(self, conn: sqlite3.Connection) -> dict:
        """Detect regime fragility from Markov signals and breadth data."""
        signals_checked = []
        fragility_factors = []

        # ── Load Markov signal data ──────────────────────────────────────────
        markov_df = pd.DataFrame()
        try:
            markov_df = pd.read_sql_query(
                "SELECT date, regime, signal_1d, stickiness, transition_risk "
                "FROM markov_signal_daily ORDER BY date DESC LIMIT 10",
                conn
            )
            markov_df = markov_df.sort_values('date').reset_index(drop=True)
            signals_checked.append('markov_signal_daily')
        except Exception:
            pass

        # ── Load breadth data ────────────────────────────────────────────────
        breadth_df = pd.DataFrame()
        try:
            breadth_df = pd.read_sql_query(
                "SELECT date, regime, ad_ratio, pct_above_ema20 "
                "FROM market_breadth_enhanced ORDER BY date DESC LIMIT 10",
                conn
            )
            breadth_df = breadth_df.sort_values('date').reset_index(drop=True)
            signals_checked.append('market_breadth_enhanced')
        except Exception:
            pass

        current_regime = 'UNKNOWN'
        fragility_score = 0
        warning_parts = []

        # ── Markov-based fragility ────────────────────────────────────────────
        if not markov_df.empty:
            current_regime = str(markov_df['regime'].iloc[-1])

            # Stickiness declining over last 5 rows
            if len(markov_df) >= 5 and 'stickiness' in markov_df.columns:
                stick_vals = markov_df['stickiness'].tail(5).dropna().values
                if len(stick_vals) >= 3:
                    slope = float(np.polyfit(np.arange(len(stick_vals)), stick_vals, 1)[0])
                    if slope < 0:
                        fragility_factors.append(('stickiness_declining', 25))
                        warning_parts.append('تراجع الـ stickiness')

            # High transition risk
            if 'transition_risk' in markov_df.columns:
                latest_risk = markov_df['transition_risk'].dropna().iloc[-1] if not markov_df['transition_risk'].dropna().empty else 0.0
                if float(latest_risk) > 0.30:
                    fragility_factors.append(('high_transition_risk', 30))
                    warning_parts.append(f'transition_risk مرتفع ({latest_risk:.2f})')

            # Signal_1d declining (regime weakening)
            if 'signal_1d' in markov_df.columns and len(markov_df) >= 3:
                sig_vals = markov_df['signal_1d'].tail(3).dropna().values
                if len(sig_vals) >= 2:
                    if sig_vals[-1] < sig_vals[0]:
                        fragility_factors.append(('signal_declining', 15))
                        warning_parts.append('تراجع إشارة الـ regime')

        # ── Breadth-based fragility ───────────────────────────────────────────
        if not breadth_df.empty:
            if current_regime == 'UNKNOWN' and 'regime' in breadth_df.columns:
                current_regime = str(breadth_df['regime'].iloc[-1])

            # AD ratio declining over 5 rows
            if 'ad_ratio' in breadth_df.columns and len(breadth_df) >= 5:
                ad_vals = breadth_df['ad_ratio'].tail(5).dropna().values
                if len(ad_vals) >= 3:
                    slope = float(np.polyfit(np.arange(len(ad_vals)), ad_vals, 1)[0])
                    if slope < 0:
                        fragility_factors.append(('ad_ratio_declining', 20))
                        warning_parts.append('تراجع نسبة الاتساع')

            # pct_above_ema20 low
            if 'pct_above_ema20' in breadth_df.columns:
                pct_ema = breadth_df['pct_above_ema20'].dropna()
                if not pct_ema.empty and float(pct_ema.iloc[-1]) < 35:
                    fragility_factors.append(('breadth_weak', 10))
                    warning_parts.append('اتساع السوق ضعيف')

        # ── Aggregate fragility score ─────────────────────────────────────────
        fragility_score = min(100, sum(v for _, v in fragility_factors))

        if fragility_score >= 60:
            fragility_level = 'HIGH'
        elif fragility_score >= 30:
            fragility_level = 'MODERATE'
        else:
            fragility_level = 'LOW'

        transition_imminent = fragility_score >= 60

        if warning_parts:
            warning_message = 'تحذير: ' + ' | '.join(warning_parts)
        elif fragility_level == 'LOW':
            warning_message = 'الـ regime مستقر — لا توجد تحذيرات'
        else:
            warning_message = f'مراقبة الـ regime (fragility: {fragility_level})'

        return {
            'current_regime': current_regime,
            'fragility_score': fragility_score,
            'fragility_level': fragility_level,
            'transition_imminent': transition_imminent,
            'warning_message': warning_message,
            'signals_checked': signals_checked,
            'fragility_factors': fragility_factors,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. BehavioralGuardrails
# ─────────────────────────────────────────────────────────────────────────────

class BehavioralGuardrails:
    """قواعد الانضباط المؤسسي."""

    MAX_SIGNALS_PER_DAY = 7
    MAX_SECTOR_CONCENTRATION = 0.25
    MIN_HOLDING_DAYS = {'SHORT_SWING': 2, 'LONG_SWING': 5}
    MAX_CONSECUTIVE_LOSSES = 3

    def check_violations(
        self,
        trades_df: pd.DataFrame,
        today_signals_count: int,
        portfolio_state: dict,
    ) -> list:
        """Check for behavioral violations and return list of violation dicts."""
        violations = []

        # Over-trading check
        if today_signals_count > self.MAX_SIGNALS_PER_DAY:
            violations.append({
                'violation_type': 'OVERTRADING',
                'severity': 'HIGH',
                'message': (
                    f'تجاوز الحد اليومي: {today_signals_count} إشارات '
                    f'(الحد الأقصى {self.MAX_SIGNALS_PER_DAY})'
                ),
            })

        # Consecutive losses check
        if not trades_df.empty:
            df = trades_df.sort_values('date')
            tail_pnls = df['pnl_pct'].tail(self.MAX_CONSECUTIVE_LOSSES + 1).tolist()
            consecutive_losses = 0
            for pnl in reversed(tail_pnls):
                if pnl < 0:
                    consecutive_losses += 1
                else:
                    break

            if consecutive_losses >= self.MAX_CONSECUTIVE_LOSSES:
                violations.append({
                    'violation_type': 'CONSECUTIVE_LOSSES',
                    'severity': 'HIGH',
                    'message': (
                        f'{consecutive_losses} خسائر متتالية — '
                        'مراجعة إلزامية قبل أي صفقة جديدة'
                    ),
                    'mandatory_review': True,
                })

        # Sector concentration check
        sector_concentration = portfolio_state.get('sector_concentration', {})
        for sector, weight in sector_concentration.items():
            if weight > self.MAX_SECTOR_CONCENTRATION:
                violations.append({
                    'violation_type': 'SECTOR_CONCENTRATION',
                    'severity': 'MEDIUM',
                    'message': (
                        f'تركيز مفرط في قطاع {sector}: '
                        f'{weight:.1%} (الحد {self.MAX_SECTOR_CONCENTRATION:.0%})'
                    ),
                })

        return violations


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def _load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load trades table into a DataFrame, returning empty DF on missing table."""
    try:
        df = pd.read_sql_query(
            "SELECT date, symbol, pnl_pct, signal_type, entry_price, exit_price "
            "FROM trades ORDER BY date",
            conn
        )
        df['date'] = pd.to_datetime(df['date'])
        return df
    except Exception:
        return pd.DataFrame(columns=['date', 'symbol', 'pnl_pct', 'signal_type',
                                      'entry_price', 'exit_price'])


def _compute_rolling_sharpe(trades_df: pd.DataFrame, window: int = 30):
    """Compute rolling Sharpe ratio for the last `window` days."""
    if trades_df.empty:
        return None
    df = trades_df.sort_values('date').copy()
    df['date'] = pd.to_datetime(df['date'])
    cutoff = pd.Timestamp(df['date'].max()) - pd.Timedelta(days=window)
    recent = df[df['date'] >= cutoff]['pnl_pct']
    if len(recent) < 5:
        return None
    mean_r = recent.mean()
    std_r = recent.std()
    if std_r == 0:
        return None
    return float(mean_r / std_r * np.sqrt(252))


def run_risk_check(db_path: Path = DB_PATH) -> dict:
    """Run all risk checks, persist to DB, and return full result dict."""
    conn = sqlite3.connect(str(db_path))
    try:
        trades_df = _load_trades(conn)
        sharpe_30d = _compute_rolling_sharpe(trades_df, window=30)

        # ── Engine checks ─────────────────────────────────────────────────────
        drawdown_engine = DrawdownProtectionEngine()
        drawdown_result = drawdown_engine.evaluate(trades_df, rolling_30d_sharpe=sharpe_30d)

        alpha_monitor = AlphaDecayMonitor()
        alpha_result = alpha_monitor.check(trades_df)

        regime_warning = RegimeTransitionWarning()
        regime_result = regime_warning.check(conn)

        guardrails = BehavioralGuardrails()
        violations = guardrails.check_violations(
            trades_df,
            today_signals_count=0,
            portfolio_state={},
        )

        # ── Overall risk level ────────────────────────────────────────────────
        alert_level = drawdown_result['alert_level']
        alpha_health = alpha_result['system_health']
        fragility_level = regime_result['fragility_level']

        if alert_level == 'RED' or drawdown_result['action'] == 'HALT':
            overall_level = 'HALTED'
        elif alert_level == 'ORANGE' or alpha_health == 'FAILING' or fragility_level == 'HIGH':
            overall_level = 'CRITICAL'
        elif alert_level == 'YELLOW' or alpha_health == 'DEGRADING' or fragility_level == 'MODERATE':
            overall_level = 'ELEVATED'
        else:
            overall_level = 'NORMAL'

        # ── Overall recommendation ────────────────────────────────────────────
        if overall_level == 'HALTED':
            overall_recommendation = 'إيقاف كامل للتداول — مراجعة عاجلة مطلوبة'
        elif overall_level == 'CRITICAL':
            overall_recommendation = 'خفض التعرض بشكل حاد — تجنب الصفقات الجديدة'
        elif overall_level == 'ELEVATED':
            overall_recommendation = 'تداول بحذر — خفض الحجم 30-50%'
        else:
            overall_recommendation = 'يمكن التداول وفق الخطة الاعتيادية'

        check_date = datetime.now().strftime('%Y-%m-%d')

        full_result = {
            'check_date': check_date,
            'overall_level': overall_level,
            'overall_recommendation': overall_recommendation,
            'drawdown': drawdown_result,
            'alpha_decay': alpha_result,
            'regime_warning': regime_result,
            'behavioral_violations': violations,
        }

        # ── Persist to DB ─────────────────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_check_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_date TEXT UNIQUE,
                drawdown_pct REAL,
                exposure_multiplier REAL,
                alpha_health TEXT,
                regime_fragility TEXT,
                overall_level TEXT,
                recommendation TEXT,
                details_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO risk_check_daily
                (check_date, drawdown_pct, exposure_multiplier, alpha_health,
                 regime_fragility, overall_level, recommendation, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            check_date,
            drawdown_result['drawdown_pct'],
            drawdown_result['exposure_multiplier'],
            alpha_result['system_health'],
            regime_result['fragility_level'],
            overall_level,
            overall_recommendation,
            json.dumps(full_result, ensure_ascii=False),
        ))
        conn.commit()

    finally:
        conn.close()

    return full_result


# ─────────────────────────────────────────────────────────────────────────────
# Reporting helpers
# ─────────────────────────────────────────────────────────────────────────────

LEVEL_COLORS = {
    'GREEN': '🟢',
    'YELLOW': '🟡',
    'ORANGE': '🟠',
    'RED': '🔴',
    'NORMAL': '🟢',
    'ELEVATED': '🟡',
    'CRITICAL': '🟠',
    'HALTED': '🔴',
}


def print_risk_report(result: dict) -> None:
    """Print a human-readable risk report to stdout."""
    icon = LEVEL_COLORS.get(result['overall_level'], '⚪')
    print()
    print('🛡️  تقرير إدارة المخاطر — EGX Navigator')
    print('━' * 50)
    print(f"  التاريخ: {result['check_date']}")
    print(f"  مستوى المخاطر: {icon} {result['overall_level']}")
    print(f"  التوصية: {result['overall_recommendation']}")
    print()

    # Drawdown
    dd = result['drawdown']
    dd_icon = LEVEL_COLORS.get(dd['alert_level'], '⚪')
    print(f"📉 Drawdown: {dd_icon} {dd['drawdown_pct']:.2f}%")
    print(f"   الإجراء: {dd['action']} | التعرض: {dd['exposure_multiplier']:.0%}")
    if dd['edge_decaying']:
        print('   ⚠️  تحذير: تآكل الـ edge (Sharpe منخفض)')
    print()

    # Alpha decay
    ad = result['alpha_decay']
    health_icon = {'HEALTHY': '🟢', 'DEGRADING': '🟡', 'FAILING': '🔴', 'UNKNOWN': '⚪'}.get(
        ad['system_health'], '⚪')
    print(f"📊 Alpha Decay: {health_icon} {ad['system_health']}")
    print(f"   PF(30d): {ad['pf_30d']:.2f}  |  PF(90d): {ad['pf_90d']:.2f}")
    print(f"   WR(30d): {ad['wr_30d']:.1%}  |  WR(90d): {ad['wr_90d']:.1%}")
    if ad['decay_detected']:
        print(f"   ⚠️  تآكل الـ edge: {ad['decay_magnitude_pct']:.1f}%")
    print(f"   {ad['suggested_action']}")
    print()

    # Regime warning
    rw = result['regime_warning']
    frag_icon = {'LOW': '🟢', 'MODERATE': '🟡', 'HIGH': '🔴'}.get(rw['fragility_level'], '⚪')
    print(f"🔄 Regime: {frag_icon} {rw['current_regime']} (fragility: {rw['fragility_score']}/100)")
    print(f"   {rw['warning_message']}")
    if rw['transition_imminent']:
        print('   ⚠️  تحول الـ regime وشيك — تقليص الصفقات المفتوحة')
    print()

    # Behavioral violations
    violations = result.get('behavioral_violations', [])
    if violations:
        print('⚖️  انتهاكات سلوكية:')
        for v in violations:
            sev_icon = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '🟢'}.get(v['severity'], '⚪')
            print(f"   {sev_icon} [{v['violation_type']}] {v['message']}")
    else:
        print('✅ لا توجد انتهاكات سلوكية')
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='EGX Navigator — Risk Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command')

    # check
    check_p = sub.add_parser('check', help='تقييم الوضع الراهن')
    check_p.add_argument('--json', action='store_true', help='JSON output')
    check_p.add_argument('--db', default=str(DB_PATH), help='مسار قاعدة البيانات')

    # alpha_decay
    ad_p = sub.add_parser('alpha_decay', help='تحليل Alpha Decay فقط')
    ad_p.add_argument('--db', default=str(DB_PATH))

    # regime_warning
    rw_p = sub.add_parser('regime_warning', help='تحليل Regime fragility فقط')
    rw_p.add_argument('--db', default=str(DB_PATH))

    args = parser.parse_args()

    if args.command == 'check':
        result = run_risk_check(Path(args.db))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_risk_report(result)

    elif args.command == 'alpha_decay':
        conn = sqlite3.connect(args.db)
        try:
            trades_df = _load_trades(conn)
        finally:
            conn.close()
        monitor = AlphaDecayMonitor()
        result = monitor.check(trades_df)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == 'regime_warning':
        conn = sqlite3.connect(args.db)
        try:
            warning = RegimeTransitionWarning()
            result = warning.check(conn)
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
