"""
Phase 82 — Event-Driven Backtesting
More realistic backtesting with event-driven fills, partial fills,
slippage, portfolio constraints.
"""

import sys
import json
import sqlite3
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

# ── Optional libs ─────────────────────────────────────────────────────────────
try:
    from backtesting import Backtest, Strategy
    BT_OK = True
except ImportError:
    BT_OK = False

try:
    import vectorbt as vbt
    VBT_OK = True
except ImportError:
    VBT_OK = False

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('event_backtest')

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_ohlcv(symbol: str) -> pd.DataFrame:
    """Return OHLCV DataFrame with DatetimeIndex required by backtesting.py."""
    conn = _get_conn()
    query = """
        SELECT date(bar_time, 'unixepoch') AS Date,
               open  AS Open,
               high  AS High,
               low   AS Low,
               close AS Close,
               volume AS Volume
        FROM ohlcv_history_execution
        WHERE symbol = ?
        ORDER BY bar_time
    """
    df = pd.read_sql_query(query, conn, params=(symbol,))
    conn.close()

    if df.empty:
        return df

    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date')
    df = df.sort_index()
    # Drop duplicate dates, keep last
    df = df[~df.index.duplicated(keep='last')]
    # Ensure numeric
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
    return df


def _load_explosion_dates(symbol: str, direction: str = 'UP') -> List[str]:
    """Return sorted list of explosion_date strings for a symbol."""
    conn = _get_conn()
    cur = conn.execute(
        "SELECT explosion_date FROM explosive_moves WHERE symbol=? AND direction=? ORDER BY explosion_date",
        (symbol, direction)
    )
    dates = [row[0] for row in cur.fetchall()]
    conn.close()
    return dates


def _load_top_predicted_symbols(n: int = 5) -> List[str]:
    """Return top N symbols by latest explosion_prob."""
    conn = _get_conn()
    cur = conn.execute("""
        SELECT symbol, MAX(explosion_prob) AS p
        FROM explosion_predictions
        GROUP BY symbol
        ORDER BY p DESC
        LIMIT ?
    """, (n,))
    syms = [row[0] for row in cur.fetchall()]
    conn.close()
    return syms if syms else ['COMI', 'HRHO', 'ORAS', 'EAST', 'SWDY']


def _sharpe(returns: pd.Series, risk_free: float = 0.0, periods: int = 252) -> float:
    """Annualised Sharpe ratio from a daily returns series."""
    if returns.std() == 0 or len(returns) < 2:
        return 0.0
    return float((returns.mean() - risk_free / periods) / returns.std() * math.sqrt(periods))


def _max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    return float(dd.min())


# ── Command 1: run_strategy ────────────────────────────────────────────────────

def cmd_run_strategy(params: dict) -> dict:
    """Run backtesting.py Strategy driven by explosion event dates."""
    try:
        symbol       = params.get('symbol', 'COMI')
        holding_days = int(params.get('holding_days', 5))
        cash         = float(params.get('cash', 100_000))
        commission   = float(params.get('commission', 0.015))

        ohlcv = _load_ohlcv(symbol)
        if len(ohlcv) < 20:
            return {'success': False, 'error': f'Insufficient OHLCV data for {symbol}: {len(ohlcv)} bars'}

        explosion_dates = set(_load_explosion_dates(symbol, direction='UP'))

        # Build binary signal aligned to OHLCV index
        signal_series = pd.Series(0, index=ohlcv.index, dtype=int)
        for d in explosion_dates:
            try:
                dt = pd.Timestamp(d)
                if dt in signal_series.index:
                    signal_series[dt] = 1
            except Exception:
                pass

        # Use SMA crossover fallback if no explosion dates align
        if signal_series.sum() == 0:
            log.info('%s: no explosion dates align to OHLCV — using SMA(5/20) crossover', symbol)
            close = ohlcv['Close']
            fast  = close.rolling(5).mean()
            slow  = close.rolling(20).mean()
            signal_series = ((fast > slow) & (fast.shift(1) <= slow.shift(1))).astype(int)

        # Inject signal into OHLCV so Strategy can read it
        ohlcv = ohlcv.copy()
        ohlcv['Signal'] = signal_series.values

        if not BT_OK:
            # Fallback: manual vectorised backtest
            return _manual_strategy_backtest(ohlcv, holding_days, cash, commission, symbol)

        # ── backtesting.py Strategy ───────────────────────────────────────────
        holding_days_param = holding_days

        class ExplosionStrategy(Strategy):
            _holding = holding_days_param

            def init(self):
                self.signal = self.I(lambda s: s, self.data.Signal, name='Signal')
                self._bars_held = 0

            def next(self):
                if self.position:
                    self._bars_held += 1
                    if self._bars_held >= self._holding:
                        self.position.close()
                        self._bars_held = 0
                elif self.signal[-1] == 1:
                    self.buy()
                    self._bars_held = 0

        bt     = Backtest(ohlcv, ExplosionStrategy, cash=cash,
                          commission=commission, exclusive_orders=True)
        stats  = bt.run()

        def _safe(v):
            try:
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    return None
                return v
            except Exception:
                return str(v)

        return {
            'success':      True,
            'symbol':       symbol,
            'holding_days': holding_days,
            'cash':         cash,
            'commission':   commission,
            'n_explosion_dates': len(explosion_dates),
            'n_ohlcv_bars': len(ohlcv),
            'results': {
                'return_pct':    _safe(stats.get('Return [%]')),
                'sharpe':        _safe(stats.get('Sharpe Ratio')),
                'max_drawdown':  _safe(stats.get('Max. Drawdown [%]')),
                'win_rate':      _safe(stats.get('Win Rate [%]')),
                'avg_trade_pct': _safe(stats.get('Avg. Trade [%]')),
                'n_trades':      _safe(stats.get('# Trades')),
                'start':         str(stats.get('Start', '')),
                'end':           str(stats.get('End', '')),
                'duration':      str(stats.get('Duration', '')),
            },
        }
    except Exception as e:
        log.error('cmd_run_strategy failed: %s', e)
        return {'success': False, 'error': str(e)}


def _manual_strategy_backtest(ohlcv: pd.DataFrame, holding_days: int,
                               cash: float, commission: float, symbol: str) -> dict:
    """Vectorised manual backtest when backtesting.py is not installed."""
    close   = ohlcv['Close'].values
    signals = ohlcv['Signal'].values
    n       = len(close)

    equity     = [cash]
    position   = 0     # shares held
    hold_count = 0
    trades     = []

    for i in range(1, n):
        price = close[i]
        if position == 0 and signals[i - 1] == 1:
            # Buy at open of next bar (approximated as close)
            shares     = int((equity[-1] * (1 - commission)) / price)
            cost       = shares * price * (1 + commission)
            position   = shares
            entry_price = price
            entry_idx   = i
            hold_count  = 0
        elif position > 0:
            hold_count += 1
            if hold_count >= holding_days:
                proceeds   = position * price * (1 - commission)
                pnl        = proceeds - position * entry_price
                ret_pct    = pnl / (position * entry_price) * 100
                equity.append(equity[-1] + pnl)
                trades.append({'ret_pct': ret_pct, 'bars': hold_count})
                position  = 0
                hold_count = 0
                continue
        equity.append(equity[-1])

    eq_series = pd.Series(equity)
    daily_ret = eq_series.pct_change().dropna()
    total_ret = (eq_series.iloc[-1] / eq_series.iloc[0] - 1) * 100

    wins    = [t for t in trades if t['ret_pct'] > 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0
    avg_trade = np.mean([t['ret_pct'] for t in trades]) if trades else 0.0

    return {
        'success':      True,
        'symbol':       symbol,
        'holding_days': holding_days,
        'backend':      'manual_vectorised',
        'n_ohlcv_bars': n,
        'results': {
            'return_pct':    round(total_ret, 2),
            'sharpe':        round(_sharpe(daily_ret), 3),
            'max_drawdown':  round(_max_drawdown(eq_series) * 100, 2),
            'win_rate':      round(win_rate, 1),
            'avg_trade_pct': round(avg_trade, 2),
            'n_trades':      len(trades),
        },
    }


# ── Command 2: portfolio_backtest ──────────────────────────────────────────────

def cmd_portfolio_backtest(params: dict) -> dict:
    """Backtest portfolio of top N symbols."""
    try:
        n_syms     = int(params.get('n_symbols', 5))
        max_weight = float(params.get('max_weight', 0.15))
        commission = float(params.get('commission', 0.015))
        holding    = int(params.get('holding_days', 5))

        symbols = _load_top_predicted_symbols(n_syms)
        log.info('Portfolio backtest on: %s', symbols)

        # Collect price and signal data per symbol
        price_dfs  = {}
        signal_dfs = {}

        for sym in symbols:
            ohlcv = _load_ohlcv(sym)
            if len(ohlcv) < 20:
                continue
            price_dfs[sym]  = ohlcv['Close']
            explosion_dates = set(_load_explosion_dates(sym, 'UP'))
            sig = pd.Series(0, index=ohlcv.index, dtype=float)
            for d in explosion_dates:
                dt = pd.Timestamp(d)
                if dt in sig.index:
                    sig[dt] = 1.0
            signal_dfs[sym] = sig

        if not price_dfs:
            return {'success': False, 'error': 'No usable price data for any symbol'}

        # Align to common date range
        price_matrix  = pd.DataFrame(price_dfs).sort_index().fillna(method='ffill').dropna()
        signal_matrix = pd.DataFrame(signal_dfs).reindex(price_matrix.index).fillna(0)

        returns_matrix = price_matrix.pct_change().fillna(0)

        # Simple event-driven portfolio engine
        n_dates  = len(price_matrix)
        n_assets = len(price_matrix.columns)
        syms_list = list(price_matrix.columns)

        cash     = 100_000.0
        holdings = {s: 0.0 for s in syms_list}  # share count
        held_for = {s: 0   for s in syms_list}
        equity_curve = [cash]

        for i in range(1, n_dates):
            date   = price_matrix.index[i]
            prices = price_matrix.iloc[i]
            sigs   = signal_matrix.iloc[i - 1]

            # Liquidate positions past holding period
            portfolio_value = cash + sum(holdings[s] * prices[s] for s in syms_list)

            for s in syms_list:
                if holdings[s] > 0:
                    held_for[s] += 1
                    if held_for[s] >= holding:
                        proceeds = holdings[s] * prices[s] * (1 - commission)
                        cash     += proceeds
                        holdings[s] = 0
                        held_for[s] = 0

            # Open new positions
            buy_candidates = [s for s in syms_list if sigs[s] == 1 and holdings[s] == 0]
            if buy_candidates:
                portfolio_value = cash + sum(holdings[s] * prices[s] for s in syms_list)
                alloc_per_sym   = min(portfolio_value / max(len(buy_candidates), 1),
                                      portfolio_value * max_weight)
                for s in buy_candidates:
                    p = prices[s]
                    if p <= 0 or cash < alloc_per_sym * 0.5:
                        continue
                    spend  = min(alloc_per_sym, cash * 0.95)
                    shares = int((spend * (1 - commission)) / p)
                    if shares > 0:
                        cost = shares * p * (1 + commission)
                        cash -= cost
                        holdings[s] = shares
                        held_for[s] = 0

            pv = cash + sum(holdings[s] * prices[s] for s in syms_list)
            equity_curve.append(pv)

        eq = pd.Series(equity_curve, index=price_matrix.index[:len(equity_curve)])
        daily_ret = eq.pct_change().dropna()
        total_ret = (eq.iloc[-1] / eq.iloc[0] - 1) * 100

        # Average correlation between assets
        corr_vals = []
        if len(syms_list) > 1:
            corr_matrix = returns_matrix.corr()
            for i2 in range(len(syms_list)):
                for j2 in range(i2 + 1, len(syms_list)):
                    v = corr_matrix.iloc[i2, j2]
                    if not math.isnan(v):
                        corr_vals.append(v)
        avg_corr = float(np.mean(corr_vals)) if corr_vals else 0.0

        avg_positions = sum(1 for s in syms_list if any(
            signal_matrix[s].values > 0
        ))

        return {
            'success':            True,
            'symbols':            syms_list,
            'n_symbols':          len(syms_list),
            'date_range':         f'{price_matrix.index[0].date()} → {price_matrix.index[-1].date()}',
            'portfolio_return':   round(total_ret, 2),
            'sharpe':             round(_sharpe(daily_ret), 3),
            'max_drawdown':       round(_max_drawdown(eq) * 100, 2),
            'avg_correlation':    round(avg_corr, 3),
            'avg_n_positions':    avg_positions,
            'max_weight':         max_weight,
            'commission':         commission,
        }
    except Exception as e:
        log.error('cmd_portfolio_backtest failed: %s', e)
        return {'success': False, 'error': str(e)}


# ── Command 3: walk_forward_bt ─────────────────────────────────────────────────

def cmd_walk_forward_bt(params: dict) -> dict:
    """Walk-forward backtest with realistic execution costs."""
    try:
        symbol        = params.get('symbol', 'COMI')
        is_months     = int(params.get('is_months', 12))
        oos_months    = int(params.get('oos_months', 3))
        commission_rt = float(params.get('commission', 0.015))   # round-trip
        slippage      = float(params.get('slippage', 0.005))
        settlement_d  = int(params.get('settlement_days', 2))
        holding       = int(params.get('holding_days', 5))

        ohlcv = _load_ohlcv(symbol)
        if len(ohlcv) < 60:
            return {'success': False, 'error': f'Insufficient data for {symbol}'}

        explosion_dates = set(_load_explosion_dates(symbol, 'UP'))

        sig = pd.Series(0, index=ohlcv.index, dtype=int)
        for d in explosion_dates:
            dt = pd.Timestamp(d)
            if dt in sig.index:
                sig[dt] = 1

        dates     = ohlcv.index
        start_dt  = dates[0]
        end_dt    = dates[-1]

        windows   = []
        current   = start_dt + pd.DateOffset(months=is_months)

        while current + pd.DateOffset(months=oos_months) <= end_dt + pd.DateOffset(days=1):
            is_start  = current - pd.DateOffset(months=is_months)
            is_end_dt = current - pd.DateOffset(days=1)
            oos_start = current
            oos_end   = current + pd.DateOffset(months=oos_months) - pd.DateOffset(days=1)

            is_mask   = (dates >= is_start)  & (dates <= is_end_dt)
            oos_mask  = (dates >= oos_start) & (dates <= oos_end)

            is_sig  = sig[is_mask]
            oos_sig = sig[oos_mask]
            is_price  = ohlcv['Close'][is_mask]
            oos_price = ohlcv['Close'][oos_mask]

            if len(oos_price) < 5:
                current += pd.DateOffset(months=1)
                continue

            # IS: determine signal threshold (calibrate to win rate)
            n_is_signals = int(is_sig.sum())

            # OOS: simulate trades with costs
            oos_returns = []
            i = 0
            oos_closes = oos_price.values
            oos_sigs   = oos_sig.reindex(oos_price.index).fillna(0).values

            while i < len(oos_closes):
                if oos_sigs[i] == 1:
                    entry_i  = min(i + settlement_d, len(oos_closes) - 1)
                    entry_p  = oos_closes[entry_i] * (1 + slippage)
                    exit_i   = min(entry_i + holding, len(oos_closes) - 1)
                    exit_p   = oos_closes[exit_i] * (1 - slippage)
                    raw_ret  = exit_p / entry_p - 1
                    net_ret  = raw_ret - commission_rt
                    oos_returns.append(net_ret)
                    i = exit_i + 1
                else:
                    i += 1

            n_trades = len(oos_returns)
            if n_trades > 0:
                win_rate   = sum(1 for r in oos_returns if r > 0) / n_trades
                avg_ret    = float(np.mean(oos_returns))
                total_ret  = float(np.prod([1 + r for r in oos_returns]) - 1)
            else:
                win_rate  = 0.0
                avg_ret   = 0.0
                total_ret = 0.0

            windows.append({
                'is_start':    str(is_start.date()),
                'is_end':      str(is_end_dt.date()),
                'oos_start':   str(oos_start.date()),
                'oos_end':     str(oos_end.date()),
                'n_is_signals': n_is_signals,
                'n_trades':    n_trades,
                'win_rate':    round(win_rate * 100, 1),
                'avg_ret_pct': round(avg_ret * 100, 2),
                'total_ret_pct': round(total_ret * 100, 2),
            })

            current += pd.DateOffset(months=1)

        if not windows:
            return {'success': False, 'error': 'No walk-forward windows generated'}

        all_rets     = [w['total_ret_pct'] for w in windows]
        stability    = round(float(np.std(all_rets)), 2) if len(all_rets) > 1 else 0.0
        ruin_prob    = round(sum(1 for r in all_rets if r < -20) / len(all_rets) * 100, 1)
        avg_oos_ret  = round(float(np.mean(all_rets)), 2)

        return {
            'success':      True,
            'symbol':       symbol,
            'n_windows':    len(windows),
            'windows':      windows,
            'summary': {
                'avg_oos_return_pct': avg_oos_ret,
                'stability_std':      stability,
                'ruin_prob_pct':      ruin_prob,
                'positive_windows':   sum(1 for r in all_rets if r > 0),
                'commission_rt':      commission_rt,
                'slippage':           slippage,
                'settlement_days':    settlement_d,
            },
        }
    except Exception as e:
        log.error('cmd_walk_forward_bt failed: %s', e)
        return {'success': False, 'error': str(e)}


# ── Command 4: execution_cost ──────────────────────────────────────────────────

def cmd_execution_cost(params: dict) -> dict:
    """Estimate execution cost model for EGX."""
    try:
        symbol       = params.get('symbol', None)
        lookback     = int(params.get('lookback_days', 252))
        order_size   = float(params.get('order_size_egp', 50_000))
        risk_free    = float(params.get('risk_free_annual', 0.225))    # EGX approx

        conn = _get_conn()
        where_sym = f"AND symbol = '{symbol}'" if symbol else ''
        query = f"""
            SELECT symbol,
                   date(bar_time, 'unixepoch') AS date,
                   high, low, close, volume
            FROM ohlcv_history_execution
            WHERE bar_time >= strftime('%s', date('now', '-{lookback} days'))
            {where_sym}
            ORDER BY bar_time DESC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            return {'success': False, 'error': 'No OHLCV data found'}

        df = df.dropna(subset=['high', 'low', 'close', 'volume'])
        df = df[df['close'] > 0]

        # Bid-ask spread proxy: (High - Low) / Close
        df['hl_spread_pct'] = (df['high'] - df['low']) / df['close']

        # Daily dollar volume
        df['dollar_vol'] = df['close'] * df['volume']

        per_sym = df.groupby('symbol').agg(
            avg_spread_pct=('hl_spread_pct', 'mean'),
            avg_daily_vol_egp=('dollar_vol', 'mean'),
            avg_close=('close', 'mean'),
            avg_volume=('volume', 'mean'),
            volatility=('close', lambda x: x.pct_change().std() * math.sqrt(252)),
        ).reset_index()

        results_per_sym = {}
        for _, row in per_sym.iterrows():
            sym               = row['symbol']
            avg_spread        = row['avg_spread_pct']
            avg_vol_egp       = row['avg_daily_vol_egp']
            avg_vol_shares    = row['avg_volume']
            vol               = row['volatility'] if not math.isnan(row['volatility']) else 0.15

            # Market impact: sqrt(order_size / avg_daily_vol_egp) * vol
            if avg_vol_egp > 0:
                mkt_impact = math.sqrt(order_size / avg_vol_egp) * vol
            else:
                mkt_impact = vol * 0.1

            # T+2 settlement opportunity cost
            settlement_cost = 2 * risk_free / 252

            half_spread = avg_spread / 2

            total_rt = (half_spread * 2) + mkt_impact + settlement_cost

            results_per_sym[sym] = {
                'avg_spread_pct':      round(avg_spread * 100, 3),
                'half_spread_pct':     round(half_spread * 100, 3),
                'market_impact_pct':   round(mkt_impact * 100, 3),
                'settlement_cost_pct': round(settlement_cost * 100, 4),
                'total_roundtrip_pct': round(total_rt * 100, 3),
                'avg_daily_vol_egp':   round(avg_vol_egp, 0),
                'volatility_annual':   round(vol * 100, 1),
            }

        # Aggregate across all symbols
        spreads   = [v['avg_spread_pct'] for v in results_per_sym.values()]
        impacts   = [v['market_impact_pct'] for v in results_per_sym.values()]
        roundtrips = [v['total_roundtrip_pct'] for v in results_per_sym.values()]

        return {
            'success':            True,
            'n_symbols':          len(results_per_sym),
            'lookback_days':      lookback,
            'order_size_egp':     order_size,
            'risk_free_annual':   risk_free,
            'aggregate': {
                'avg_spread_pct':      round(float(np.mean(spreads)), 3) if spreads else 0,
                'avg_market_impact_pct': round(float(np.mean(impacts)), 3) if impacts else 0,
                'settlement_cost_pct': round(2 * risk_free / 252 * 100, 4),
                'avg_roundtrip_pct':   round(float(np.mean(roundtrips)), 3) if roundtrips else 0,
                'market_impact_model': 'sqrt(order_size / avg_daily_vol) * annual_vol',
            },
            'per_symbol': results_per_sym,
        }
    except Exception as e:
        log.error('cmd_execution_cost failed: %s', e)
        return {'success': False, 'error': str(e)}


# ── Command 5: report ──────────────────────────────────────────────────────────

def cmd_report(params: dict) -> dict:
    """Combined report: execution costs + portfolio backtest for top 5 symbols."""
    try:
        cost_result = cmd_execution_cost({**params, 'lookback_days': params.get('lookback_days', 252)})
        port_result = cmd_portfolio_backtest({**params, 'n_symbols': params.get('n_symbols', 5)})

        return {
            'success':            True,
            'execution_costs':    cost_result,
            'portfolio_backtest': port_result,
            'summary': {
                'avg_roundtrip_cost_pct': cost_result.get('aggregate', {}).get('avg_roundtrip_pct'),
                'portfolio_return_pct':   port_result.get('portfolio_return'),
                'portfolio_sharpe':       port_result.get('sharpe'),
                'portfolio_max_dd_pct':   port_result.get('max_drawdown'),
            },
        }
    except Exception as e:
        log.error('cmd_report failed: %s', e)
        return {'success': False, 'error': str(e)}


# ── Dispatch ──────────────────────────────────────────────────────────────────
COMMANDS = {
    'run_strategy':        cmd_run_strategy,
    'portfolio_backtest':  cmd_portfolio_backtest,
    'walk_forward_bt':     cmd_walk_forward_bt,
    'execution_cost':      cmd_execution_cost,
    'report':              cmd_report,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'success': False,
                          'error': 'Usage: event_backtest.py <command> [json_params]',
                          'commands': list(COMMANDS)}))
        sys.exit(1)

    command = sys.argv[1]
    params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    handler = COMMANDS.get(command)
    if handler is None:
        print(json.dumps({'success': False, 'error': f'Unknown command: {command}',
                          'available': list(COMMANDS)}))
        sys.exit(1)

    result = handler(params)
    print(json.dumps(result, default=str, indent=2))


if __name__ == '__main__':
    main()
