#!/usr/bin/env python3
"""
Phase 73 — Portfolio Optimizer (PyPortfolioOpt)
"تحسين المحفظة — توزيع رأس المال على الإشارات الأعلى ثقة"

Commands:
  kelly_sizing      — Kelly-fraction position sizes for today's HIGH signals
  efficient_frontier — Mean-variance optimal weights
  risk_parity       — Equal-risk-contribution weights
  max_sharpe        — Maximum Sharpe ratio portfolio
  report            — Full portfolio report with all methods
"""
import sys, json, sqlite3, datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_high_signals(conn, min_prob=0.65, date=None):
    """Return today's high-conviction explosion signals."""
    if date is None:
        date = datetime.date.today().isoformat()
    rows = conn.execute("""
        SELECT symbol, explosion_prob, confidence_tier
        FROM explosion_predictions
        WHERE pred_date = (SELECT MAX(pred_date) FROM explosion_predictions)
          AND explosion_prob >= ?
        ORDER BY explosion_prob DESC
    """, (min_prob,)).fetchall()
    return [dict(r) for r in rows]


def _build_returns_matrix(conn, symbols, lookback_days=120):
    """Build daily returns matrix for given symbols."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return None, None

    if not symbols:
        return None, None

    ph = ','.join('?' * len(symbols))
    rows = conn.execute(f"""
        SELECT symbol, date(bar_time,'unixepoch') AS bar_date, close
        FROM ohlcv_history_execution
        WHERE symbol IN ({ph})
        ORDER BY bar_time
    """, symbols).fetchall()

    if not rows:
        return None, None

    df = pd.DataFrame(rows, columns=['symbol', 'bar_date', 'close'])
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    wide = df.pivot_table(index='bar_date', columns='symbol', values='close', aggfunc='last')
    wide = wide.sort_index()

    # Keep only last lookback_days trading bars
    wide = wide.iloc[-lookback_days:] if len(wide) > lookback_days else wide
    # Drop symbols with too many NaNs
    wide = wide.dropna(axis=1, thresh=int(len(wide) * 0.7))
    returns = wide.pct_change().dropna()

    return returns, wide


def cmd_kelly_sizing(params):
    """Half-Kelly fractional position sizing based on ML probability and historical win rate.

    params:
      min_prob    : float (default 0.65)
      total_capital: float (default 100000)
      max_position : float (default 0.15) — max weight per position
      kelly_frac   : float (default 0.5)  — half-Kelly multiplier
    """
    min_prob      = float(params.get('min_prob', 0.65))
    total_capital = float(params.get('total_capital', 100_000))
    max_pos       = float(params.get('max_position', 0.15))
    kelly_frac    = float(params.get('kelly_frac', 0.5))

    conn = get_db()
    signals = _get_high_signals(conn, min_prob)

    if not signals:
        conn.close()
        return {'success': False, 'error': f'No signals with prob>={min_prob}'}

    # Historical precision per tier from ml_model_scores
    model_row = conn.execute(
        "SELECT precision_at_50, precision_at_70 FROM ml_model_scores ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prec50 = float(model_row['precision_at_50'] or 0.5) if model_row else 0.5
    prec70 = float(model_row['precision_at_70'] or 0.6) if model_row else 0.6
    conn.close()

    positions = []
    total_weight = 0.0

    for s in signals:
        prob = s['explosion_prob']
        tier = s['confidence_tier'] or 'MED'

        # Estimate win_rate based on prob tier
        if prob >= 0.70:
            win_rate = prec70
        else:
            win_rate = prec50

        # Kelly formula: f = (p*b - q) / b
        # where b = avg_gain/avg_loss, p=win_rate, q=1-p
        # Assume EGX avg explosion = +5% gain, avg loss on false = -2%
        avg_gain = 0.05
        avg_loss = 0.02
        b = avg_gain / avg_loss

        kelly_f = (win_rate * b - (1 - win_rate)) / b
        kelly_f = max(0.0, kelly_f)         # never negative
        half_kelly = kelly_f * kelly_frac    # apply Kelly fraction
        weight = min(half_kelly, max_pos)    # cap per-position

        positions.append({
            'symbol':    s['symbol'],
            'prob':      round(prob, 3),
            'win_rate':  round(win_rate, 3),
            'kelly_f':   round(kelly_f, 3),
            'weight':    round(weight, 4),
        })
        total_weight += weight

    # Normalize weights if total > 1
    if total_weight > 1.0:
        for p in positions:
            p['weight'] = round(p['weight'] / total_weight, 4)

    # Compute EGP amounts
    for p in positions:
        p['amount_egp'] = round(p['weight'] * total_capital, 0)

    positions.sort(key=lambda x: -x['weight'])

    return {
        'success':       True,
        'method':        'half-kelly',
        'n_positions':   len(positions),
        'total_capital': total_capital,
        'positions':     positions,
        'cash_reserve':  round((1 - sum(p['weight'] for p in positions)) * total_capital, 0),
    }


def cmd_max_sharpe(params):
    """Maximum Sharpe Ratio portfolio from today's HIGH signals using PyPortfolioOpt."""
    try:
        from pypfopt import EfficientFrontier, risk_models, expected_returns
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'PyPortfolioOpt not installed: pip install PyPortfolioOpt'}

    min_prob      = float(params.get('min_prob', 0.65))
    total_capital = float(params.get('total_capital', 100_000))
    max_pos       = float(params.get('max_position', 0.30))
    min_pos       = float(params.get('min_position', 0.02))

    conn = get_db()
    signals = _get_high_signals(conn, min_prob)

    if len(signals) < 2:
        conn.close()
        return {'success': False, 'error': f'Need ≥2 signals (found {len(signals)})'}

    symbols = [s['symbol'] for s in signals]
    returns, _ = _build_returns_matrix(conn, symbols)
    conn.close()

    if returns is None or len(returns.columns) < 2:
        return {'success': False, 'error': 'Could not build returns matrix (need ≥2 symbols with data)'}

    # Filter signals to only symbols with data
    valid_syms = list(returns.columns)
    signals = [s for s in signals if s['symbol'] in valid_syms]

    try:
        mu  = expected_returns.mean_historical_return(returns, returns_data=True, frequency=252)
        cov = risk_models.sample_cov(returns, returns_data=True, frequency=252)

        ef = EfficientFrontier(mu, cov, weight_bounds=(min_pos, max_pos))
        weights = ef.max_sharpe(risk_free_rate=0.20)  # Egyptian risk-free ~20% (T-bills)
        cleaned = ef.clean_weights()
        perf    = ef.portfolio_performance(verbose=False, risk_free_rate=0.20)

        positions = []
        for sym, w in sorted(cleaned.items(), key=lambda x: -x[1]):
            if w > 0.001:
                sig = next((s for s in signals if s['symbol'] == sym), {})
                positions.append({
                    'symbol':    sym,
                    'weight':    round(w, 4),
                    'amount_egp': round(w * total_capital, 0),
                    'prob':      round(sig.get('explosion_prob', 0), 3),
                })

        return {
            'success':            True,
            'method':             'max_sharpe',
            'n_positions':        len(positions),
            'total_capital':      total_capital,
            'expected_return':    round(float(perf[0]) * 100, 2),
            'expected_volatility': round(float(perf[1]) * 100, 2),
            'sharpe_ratio':       round(float(perf[2]), 3),
            'positions':          positions,
            'cash_reserve':       round((1 - sum(p['weight'] for p in positions)) * total_capital, 0),
        }

    except Exception as e:
        return {'success': False, 'error': f'Optimization failed: {str(e)}'}


def cmd_risk_parity(params):
    """Equal Risk Contribution (Risk Parity) weights using PyPortfolioOpt."""
    try:
        from pypfopt import EfficientFrontier, risk_models, expected_returns
        from pypfopt.efficient_frontier import EfficientFrontier
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'success': False, 'error': 'PyPortfolioOpt not installed'}

    min_prob      = float(params.get('min_prob', 0.65))
    total_capital = float(params.get('total_capital', 100_000))

    conn = get_db()
    signals = _get_high_signals(conn, min_prob)

    if len(signals) < 2:
        conn.close()
        return {'success': False, 'error': f'Need ≥2 signals (found {len(signals)})'}

    symbols = [s['symbol'] for s in signals]
    returns, _ = _build_returns_matrix(conn, symbols)
    conn.close()

    if returns is None or len(returns.columns) < 2:
        return {'success': False, 'error': 'Could not build returns matrix'}

    valid_syms = list(returns.columns)

    # Manual risk parity: weight inversely proportional to volatility
    vols  = returns[valid_syms].std()
    inv_v = 1.0 / vols.replace(0, float('nan'))
    weights = (inv_v / inv_v.sum()).fillna(0)

    positions = []
    for sym in valid_syms:
        w = float(weights.get(sym, 0))
        if w > 0.001:
            sig = next((s for s in signals if s['symbol'] == sym), {})
            positions.append({
                'symbol':     sym,
                'weight':     round(w, 4),
                'amount_egp': round(w * total_capital, 0),
                'volatility': round(float(vols.get(sym, 0)) * 100, 2),
                'prob':       round(sig.get('explosion_prob', 0), 3),
            })

    positions.sort(key=lambda x: -x['weight'])

    return {
        'success':       True,
        'method':        'risk_parity',
        'n_positions':   len(positions),
        'total_capital': total_capital,
        'positions':     positions,
        'cash_reserve':  round((1 - sum(p['weight'] for p in positions)) * total_capital, 0),
    }


def cmd_report(params):
    """Full portfolio optimization report: Kelly + Max Sharpe + Risk Parity."""
    kelly  = cmd_kelly_sizing(params)
    sharpe = cmd_max_sharpe(params)
    parity = cmd_risk_parity(params)

    return {
        'success': True,
        'kelly':   kelly,
        'max_sharpe': sharpe,
        'risk_parity': parity,
    }


COMMANDS = {
    'kelly_sizing':       cmd_kelly_sizing,
    'max_sharpe':         cmd_max_sharpe,
    'risk_parity':        cmd_risk_parity,
    'report':             cmd_report,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'report'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    print(json.dumps(handler(params), default=str))
