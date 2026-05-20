#!/usr/bin/env python3
"""
EGX Telegram Intelligence Report Formatter — Phase 11
=======================================================
Formats the full cognitive orchestration state into institutional-grade
Telegram messages (HTML format, split into <4000-char chunks).

Commands:
  format_daily     Full daily intelligence briefing (2-3 messages)
  format_alert     Single alert as a Telegram message
  format_posture   Posture-only update (short)
  format_delta     What changed since yesterday
  test_format      Run full format, return without sending (dry-run)
"""
import sys, json, time, pathlib, traceback

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent.parent
DATA = ROOT / 'data'

DB_PATH  = str(DATA / 'egx_trading.db')
ORCH_LOG = str(DATA / 'orchestrator_log.json')

COMMANDS = {'format_daily', 'format_alert', 'format_posture', 'format_delta', 'test_format'}

# ── HTML helpers ──────────────────────────────────────────────────────────────

def esc(t):
    """Escape HTML special chars for Telegram HTML mode."""
    return str(t).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')

def B(t):   return f'<b>{esc(t)}</b>'
def I(t):   return f'<i>{esc(t)}</i>'
def C(t):   return f'<code>{esc(t)}</code>'
SEP = '━' * 24

# ── Icons ─────────────────────────────────────────────────────────────────────

REGIME_ICONS = {'BULL':'🐂', 'BEAR':'🐻', 'MIXED':'⚖️'}
CONF_ICONS   = {'VERY_HIGH':'🟢', 'HIGH':'🟡', 'MODERATE':'🟠', 'LOW':'🔴'}
POSTURE_ICONS = {
    'AGGRESSIVE_LONG': '🚀', 'BULLISH': '📈', 'MODERATE_LONG': '📊',
    'NEUTRAL': '⚖️', 'DEFENSIVE': '🛡', 'AVOID': '🚫',
}
CONVICTION_STARS = {
    'ULTRA_CONVICTION': '⭐⭐⭐⭐⭐',
    'HIGH_CONVICTION':  '⭐⭐⭐⭐',
    'MEDIUM_CONVICTION':'⭐⭐⭐',
    'WATCH':            '⭐⭐',
}
CONVICTION_LABEL = {
    'ULTRA_CONVICTION': 'ULTRA', 'HIGH_CONVICTION': 'HIGH',
    'MEDIUM_CONVICTION': 'MED',  'WATCH': 'WATCH',
}

# ── Arabic label maps (client-facing) ─────────────────────────────────────────
REGIME_AR = {
    'BULL':  ('صاعد',      '📈'),
    'BEAR':  ('هابط',      '📉'),
    'MIXED': ('متذبذب',    '⚖️'),
}
POSTURE_AR = {
    'AGGRESSIVE_LONG': ('متشدد صعوداً', '🚀'),
    'BULLISH':         ('إيجابي',       '📈'),
    'MODERATE_LONG':   ('متحفظ صعوداً','📊'),
    'NEUTRAL':         ('محايد',        '⚖️'),
    'DEFENSIVE':       ('دفاعي',        '🛡'),
    'AVOID':           ('تجنب الدخول', '🚫'),
}
BREADTH_AR = {
    'BULL_WIDE':          ('صاعد واسع',    '🟢'),
    'BULL':               ('صاعد',         '🟢'),
    'BREADTH_BULL':       ('صاعد',         '🟢'),
    'LEAN_BULL':          ('مائل للصعود',  '🟡'),
    'BREADTH_LEAN_BULL':  ('مائل للصعود',  '🟡'),
    'MODERATE_BULL':      ('صاعد متوسط',   '🟢'),
    'BEAR_WIDE':          ('هابط واسع',    '🔴'),
    'BEAR':               ('هابط',         '🔴'),
    'BREADTH_BEAR':       ('هابط',         '🔴'),
    'LEAN_BEAR':          ('مائل للهبوط',  '🟠'),
    'BREADTH_LEAN_BEAR':  ('مائل للهبوط',  '🟠'),
    'MODERATE_BEAR':      ('هابط متوسط',   '🔴'),
    'MIXED':              ('متذبذب',       '🟡'),
    'NEUTRAL':            ('محايد',        '⚪'),
    'BREADTH_NEUTRAL':    ('محايد',        '⚪'),
    'UNKNOWN':            ('غير محدد',     '⚪'),
}
ENERGY_AR = {
    'VERY_HIGH': 'مرتفعة جداً 🔋🔋',
    'HIGH':      'مرتفعة 🔋',
    'NORMAL':    'طبيعية ✅',
    'LOW':       'منخفضة 🪫',
    'VERY_LOW':  'منخفضة جداً 🪫',
}
PROP_AR = {
    'NORMAL':           'طبيعي ✅',
    'HIGH_CONTAGION':   'ترابط مرتفع ⚠️',
    'CRISIS_CONTAGION': 'أزمة ترابط 🔴',
}
SPECTRAL_REGIME_AR = {
    0: ('دوري 🔄',            '🟢'),
    1: ('غير منتظم',          '⚪'),
    2: ('ضغط ما قبل الانطلاق','🔵'),
    3: ('توسع',               '🟠'),
}
CONV_STARS_AR = {
    'ULTRA_CONVICTION': ('⭐⭐⭐⭐⭐', 'استثنائية'),
    'HIGH_CONVICTION':  ('⭐⭐⭐⭐',   'عالية'),
    'MEDIUM_CONVICTION':('⭐⭐⭐',     'متوسطة'),
    'WATCH':            ('⭐⭐',       'مراقبة'),
}
RATE_TR_AR  = {'HOLD': 'ثابت', 'RISING': 'صاعد', 'FALLING': 'نازل', 'CUTTING': 'نازل'}
INFL_TR_AR  = {'UP': 'صاعد', 'DOWN': 'نازل', 'STABLE': 'مستقر', 'ACCELERATING': 'متسارع'}
FX_TR_AR    = {'STABLE': 'مستقر', 'APPRECIATING': 'تقوّي', 'DEPRECIATING': 'تراجع'}

LAYER_HEALTH_ICONS = {
    'HEALTHY':'🟢', 'DEGRADED':'🟡', 'CRITICAL':'🔴', 'UNKNOWN':'⚪',
}
SEV_ICONS = {'CRITICAL':'🔴', 'HIGH':'🟠', 'MEDIUM':'🟡', 'LOW':'🔵', 'INFO':'ℹ️'}

def conf_label(c):
    if c >= 0.85: return 'VERY HIGH', '🟢'
    if c >= 0.70: return 'HIGH', '🟡'
    if c >= 0.55: return 'MODERATE', '🟠'
    return 'LOW', '🔴'

def health_pct(h):
    return f"{h*100:.0f}%"

def trend_arrow(curr, prev, thresh=0.02):
    if curr is None or prev is None:
        return '→'
    d = curr - prev
    if d > thresh:  return '↑'
    if d < -thresh: return '↓'
    return '→'

def _ar_date(ts=None):
    """Format a timestamp as Arabic date string."""
    import time as _t
    t = _t.localtime(ts) if ts else _t.localtime()
    _days = {0:'الإثنين',1:'الثلاثاء',2:'الأربعاء',
             3:'الخميس', 4:'الجمعة', 5:'السبت',  6:'الأحد'}
    _months = {1:'يناير',2:'فبراير',3:'مارس',4:'أبريل',5:'مايو',
               6:'يونيو',7:'يوليو',8:'أغسطس',9:'سبتمبر',
               10:'أكتوبر',11:'نوفمبر',12:'ديسمبر'}
    return f"{_days.get(t.tm_wday,'')} {t.tm_mday} {_months.get(t.tm_mon,'')} {t.tm_year}"

def _conf_ar(c):
    """Confidence value → (Arabic label, icon)."""
    if c >= 0.85: return 'مرتفعة جداً', '🟢'
    if c >= 0.70: return 'مرتفعة',      '🟡'
    if c >= 0.55: return 'متوسطة',      '🟠'
    return 'منخفضة', '🔴'

# ── Log loader ────────────────────────────────────────────────────────────────

def load_json_log(path):
    try:
        p = pathlib.Path(path)
        return json.loads(p.read_text()) if p.exists() else []
    except Exception:
        return []

def orch_yesterday(orch_log):
    """Get yesterday's orch_log entry (second-to-last)."""
    if not isinstance(orch_log, list) or len(orch_log) < 2:
        return {}
    return orch_log[-2] if isinstance(orch_log[-2], dict) else {}

# ── Module loader ─────────────────────────────────────────────────────────────

import importlib.util as _ilu
_ORCH = None

def orch():
    global _ORCH
    if _ORCH is None:
        spec = _ilu.spec_from_file_location('cognitive_orchestrator',
                                             str(HERE / 'cognitive_orchestrator.py'))
        mod  = _ilu.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            return None
        _ORCH = mod
    return _ORCH

# ── DB helpers for report ────────────────────────────────────────────────────

def _get_top_signals(db, date, top_n=5):
    """
    Query top actionable signals from unified_signals + scans.
    Ph 27: Prefers quality_gate_passed=1 signals first; falls back to all if too few.
    Ph 36: Fetches 3× candidates, sorts by freshness, returns top_n best.
    """
    # Fetch more candidates so freshness re-sorting can pick better options
    _fetch_n = max(top_n * 3, 15)
    try:
        # Ph 27: try quality-gated signals first
        # Ph 41: LEFT JOIN explosion_predictions for ensemble confirmation
        # Ph 44: LEFT JOIN recommendation_outcomes for entry trigger status
        gated_rows = db.execute("""
            SELECT u.symbol,
                   u.unified_score, u.conviction_tier, u.explosion_score,
                   u.scan_score,    u.liquidity_tier,
                   u.entry_price,   u.entry_high,  u.stop_loss,
                   u.t1_target,     u.t2_target,   u.r_ratio,
                   u.behavioral_class,
                   su.sector,
                   ep.prob_pct AS ensemble_pct,
                   ro.entry_triggered,   ro.entry_trigger_date,
                   s.entry_low  AS s_entry_low,
                   s.entry_high AS s_entry_high,
                   s.stop_loss  AS s_stop_loss,
                   s.t1         AS s_t1,
                   s.t2         AS s_t2,
                   s.rr1        AS s_rr1,
                   s.close_price AS s_close
            FROM unified_signals u
            LEFT JOIN stock_universe su ON su.symbol = u.symbol
            LEFT JOIN explosion_predictions ep
                   ON ep.symbol = u.symbol AND ep.pred_date = u.signal_date
            LEFT JOIN recommendation_outcomes ro
                   ON ro.symbol = u.symbol AND ro.signal_date = u.signal_date
            LEFT JOIN (
                SELECT symbol, MAX(score) AS top_sc,
                       entry_low, entry_high, stop_loss, t1, t2, rr1, close_price
                FROM scans
                WHERE scan_date = ? AND rejected = 0
                GROUP BY symbol
            ) s ON s.symbol = u.symbol
            WHERE u.signal_date = ?
              AND u.conviction_tier IN ('ULTRA_CONVICTION','HIGH_CONVICTION','MEDIUM_CONVICTION')
              AND u.is_anti_law_triggered = 0
              AND u.quality_gate_passed = 1
            ORDER BY u.unified_score DESC
            LIMIT ?
        """, (date, date, _fetch_n)).fetchall()

        # Fallback: if fewer than 3 gated signals, include non-gated to fill report
        if len(gated_rows) >= 3:
            rows = gated_rows
        else:
            rows = db.execute("""
                SELECT u.symbol,
                       u.unified_score, u.conviction_tier, u.explosion_score,
                       u.scan_score,    u.liquidity_tier,
                       u.entry_price,   u.entry_high,  u.stop_loss,
                       u.t1_target,     u.t2_target,   u.r_ratio,
                       u.behavioral_class,
                       su.sector,
                       ep.prob_pct AS ensemble_pct,
                       ro.entry_triggered,   ro.entry_trigger_date,
                       s.entry_low  AS s_entry_low,
                       s.entry_high AS s_entry_high,
                       s.stop_loss  AS s_stop_loss,
                       s.t1         AS s_t1,
                       s.t2         AS s_t2,
                       s.rr1        AS s_rr1,
                       s.close_price AS s_close
                FROM unified_signals u
                LEFT JOIN stock_universe su ON su.symbol = u.symbol
                LEFT JOIN explosion_predictions ep
                       ON ep.symbol = u.symbol AND ep.pred_date = u.signal_date
                LEFT JOIN recommendation_outcomes ro
                       ON ro.symbol = u.symbol AND ro.signal_date = u.signal_date
                LEFT JOIN (
                    SELECT symbol, MAX(score) AS top_sc,
                           entry_low, entry_high, stop_loss, t1, t2, rr1, close_price
                    FROM scans
                    WHERE scan_date = ? AND rejected = 0
                    GROUP BY symbol
                ) s ON s.symbol = u.symbol
                WHERE u.signal_date = ?
                  AND u.conviction_tier IN ('ULTRA_CONVICTION','HIGH_CONVICTION','MEDIUM_CONVICTION')
                  AND u.is_anti_law_triggered = 0
                ORDER BY u.unified_score DESC
                LIMIT ?
            """, (date, date, _fetch_n)).fetchall()

        signals = []
        for r in rows:
            # Prefer unified_signals entry data; fall back to scans
            entry_l  = r['s_entry_low']  or r['entry_price']
            entry_h  = r['entry_high']   or r['s_entry_high'] or r['entry_price']
            sl       = r['stop_loss']    or r['s_stop_loss']
            t1       = r['t1_target']    or r['s_t1']
            t2       = r['t2_target']    or r['s_t2']
            rr       = r['r_ratio']      or r['s_rr1']
            entry_mid = (entry_l + entry_h) / 2 if entry_l and entry_h else (entry_l or entry_h)

            # Compute percentage moves relative to entry_mid
            def pct(target, base):
                if target and base and base > 0:
                    return (target - base) / base * 100
                return None

            signals.append({
                'symbol':           r['symbol'],
                'ues':              round(r['unified_score'], 1),
                'conviction':       r['conviction_tier'],
                'ml_pct':           round(r['explosion_score'], 0) if r['explosion_score'] else None,
                'liq':              r['liquidity_tier'] or 'UNKNOWN',
                'entry_low':        entry_l,
                'entry_high':       entry_h,
                'stop_loss':        sl,
                't1':               t1,
                't2':               t2,
                'r_ratio':          rr,
                'sl_pct':           pct(sl, entry_mid),
                't1_pct':           pct(t1, entry_mid),
                't2_pct':           pct(t2, entry_mid),
                'behavioral_class': (r['behavioral_class'] or 'UNKNOWN') if 'behavioral_class' in r.keys() else 'UNKNOWN',  # Ph 28
                'sector':           r['sector'] if 'sector' in r.keys() else None,   # Ph 35
                'ensemble_pct':     int(r['ensemble_pct']) if (r['ensemble_pct'] is not None) else None,  # Ph 41
                'entry_triggered':  bool(r['entry_triggered']) if r['entry_triggered'] else False,  # Ph 44
                'trigger_date':     r['entry_trigger_date'] if 'entry_trigger_date' in r.keys() else None,
            })

        # Ph 36 — Signal Freshness: tag each signal with price freshness status
        try:
            _fresh_rows = db.execute("""
                SELECT us.symbol, us.entry_price, us.entry_high, us.stop_loss,
                       oh.close as latest_close
                FROM unified_signals us
                LEFT JOIN (
                    SELECT symbol, close FROM ohlcv_history oh1
                    WHERE bar_time = (
                        SELECT MAX(bar_time) FROM ohlcv_history oh2
                        WHERE oh2.symbol=oh1.symbol AND date(oh2.bar_time,'unixepoch')<=?
                    )
                ) oh ON oh.symbol = us.symbol
                WHERE us.signal_date = ?
            """, (date, date)).fetchall()
            _fresh_map = {}
            for fr in _fresh_rows:
                _eh  = (fr['entry_high']  or fr['entry_price'] or 0)
                _el  = (fr['entry_price'] or 0)
                _sl  = (fr['stop_loss']   or 0)
                _cl  = (fr['latest_close'] or 0)
                # Sanity: skip if price ratio > 10× (stale entry data)
                _ratio = (_cl / _eh) if (_cl and _eh) else None
                _stale = _ratio is not None and (_ratio > 10.0 or _ratio < 0.1)
                if not _cl or not _eh or _stale:
                    _st = 'no_price'
                elif _sl and _cl <= _sl and not (_cl/_sl > 10 or _sl/_cl > 10):
                    _st = 'stopped'
                elif _cl > _eh * 1.05:
                    _st = 'chased'
                elif _cl > _eh * 1.02:
                    _st = 'extended'
                elif _cl >= _el * 0.99:
                    _st = 'fresh'
                else:
                    _st = 'below_zone'
                _fresh_map[fr['symbol']] = _st
            for s in signals:
                s['freshness'] = _fresh_map.get(s['symbol'], 'unknown')
        except Exception:
            for s in signals:
                s['freshness'] = 'unknown'

        # Ph 40 — Signal Age: how many days has each symbol appeared in unified_signals?
        # "day 1" = fresh today, "day 2" = also appeared yesterday, etc.
        # Only counts consecutive days (streak) with any signal for that symbol
        try:
            _syms = [s['symbol'] for s in signals]
            _age_map = {}
            if _syms:
                _plac = ','.join('?' * len(_syms))
                _age_rows = db.execute(f"""
                    SELECT symbol, signal_date
                    FROM unified_signals
                    WHERE symbol IN ({_plac})
                    ORDER BY symbol, signal_date DESC
                """, _syms).fetchall()
                _by_sym = {}
                for ar in _age_rows:
                    _by_sym.setdefault(ar['symbol'], []).append(ar['signal_date'])
                import datetime as _dt
                _today_d = _dt.date.fromisoformat(date)
                for sym, dates in _by_sym.items():
                    streak = 0
                    prev_d = _today_d + _dt.timedelta(days=1)  # start one day ahead
                    for ds in dates:
                        d_ = _dt.date.fromisoformat(ds)
                        # Allow up to 3 calendar days gap (weekends/holidays)
                        if (prev_d - d_).days <= 3:
                            streak += 1
                            prev_d = d_
                        else:
                            break  # streak broken
                    _age_map[sym] = streak
            for s in signals:
                s['signal_age'] = _age_map.get(s['symbol'], 1)
        except Exception:
            for s in signals:
                s['signal_age'] = 1

        # Ph 36b — Re-sort: fresh first, stopped last (don't remove stopped — just deprioritize)
        # no_price / unknown = neutral (don't know, can't penalize or reward)
        _freshness_order = {'fresh': 0, 'below_zone': 1, 'extended': 2,
                            'no_price': 3, 'unknown': 3, 'chased': 4, 'stopped': 5}
        signals.sort(key=lambda s: (
            _freshness_order.get(s.get('freshness', 'unknown'), 3),
            -s['ues']
        ))
        # Trim to top_n after re-sort
        signals = signals[:top_n]

        # Ph 42 — Volume Surge: compare latest bar volume vs 20-bar avg
        try:
            _vsyms = [s['symbol'] for s in signals]
            _vplac = ','.join('?' * len(_vsyms))
            _vol_rows = db.execute(f"""
                SELECT symbol, bar_time, volume
                FROM ohlcv_history
                WHERE symbol IN ({_vplac})
                ORDER BY symbol, bar_time DESC
            """, _vsyms).fetchall()
            _vol_by_sym = {}
            for vr in _vol_rows:
                _vol_by_sym.setdefault(vr['symbol'], []).append(vr['volume'])
            _vol_map = {}
            for sym, vols in _vol_by_sym.items():
                if len(vols) >= 2:
                    today_v = vols[0] or 0
                    avg_v   = sum(v for v in vols[1:21] if v) / max(len([v for v in vols[1:21] if v]), 1)
                    _vol_map[sym] = round(today_v / avg_v, 2) if avg_v > 0 else None
            for s in signals:
                s['vol_ratio'] = _vol_map.get(s['symbol'])
        except Exception:
            for s in signals:
                s['vol_ratio'] = None

        # Ph 35 — Sector Diversification: if >3 signals from same sector, add tag
        _sector_counts = {}
        for s in signals:
            sec = s.get('sector') or 'Unknown'
            _sector_counts[sec] = _sector_counts.get(sec, 0) + 1
        _dominant_sector = max(_sector_counts, key=_sector_counts.get) if _sector_counts else None
        _dominant_n      = _sector_counts.get(_dominant_sector, 0)
        if _dominant_n >= 3 and _dominant_sector and _dominant_sector != 'Unknown':
            for s in signals:
                s['sector_concentration_warning'] = f'تركيز في {_dominant_sector} ({_dominant_n}/{len(signals)})'
        return signals
    except Exception:
        return []


def _get_breadth_info(db, date):
    """Get latest market breadth data."""
    try:
        row = db.execute(
            "SELECT breadth_score, signal FROM market_breadth_daily WHERE date<=? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        if row:
            return row['breadth_score'], row['signal']
    except Exception:
        pass
    return None, 'UNKNOWN'


def _get_active_laws(db, min_precision=0.52, max_laws=4):
    """Get active laws with precision above threshold (exclude very common/ARCHIVED)."""
    try:
        rows = db.execute("""
            SELECT pattern_id, pattern_name, precision, n_activations, best_regime, law_status
            FROM universal_laws_p16
            WHERE precision >= ?
              AND n_activations >= 10
              AND (law_status IS NULL OR law_status != 'ARCHIVED')
            ORDER BY precision DESC
            LIMIT ?
        """, (min_precision, max_laws)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_win_rate(db, days=30):
    """Estimate win rate from closed trades or backtests."""
    try:
        row = db.execute("""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                   AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE NULL END) AS avg_win,
                   AVG(CASE WHEN pnl_pct < 0 THEN ABS(pnl_pct) ELSE NULL END) AS avg_loss
            FROM trades
            WHERE exit_date >= date('now', ?)
        """, (f'-{days} days',)).fetchone()
        if row and row['n'] and row['n'] >= 5:
            wr = row['wins'] / row['n']
            avg_w = row['avg_win']  or 0
            avg_l = row['avg_loss'] or 1
            rr = avg_w / avg_l if avg_l > 0 else 0
            return wr, row['n'], rr
    except Exception:
        pass
    return None, 0, 0


def _get_regime_transition(db, date):
    """Get regime transition risk from regime_transition_signals."""
    try:
        row = db.execute(
            "SELECT prob_5d, prob_10d, current_regime, most_likely_next FROM regime_transition_signals WHERE date<=? ORDER BY date DESC LIMIT 1",
            (date,)
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass
    return {}


def _get_data_freshness(data):
    """Check OHLCV data age in hours."""
    try:
        import time as _t
        max_ts = max((bars[-1]['bar_time'] for bars in data.values() if bars), default=0)
        return (_t.time() - max_ts) / 3600 if max_ts else 9999
    except Exception:
        return 9999


def _get_kelly_half(db):
    """Get half-Kelly from latest bet-sizing run."""
    try:
        row = db.execute(
            "SELECT half_kelly FROM backtests WHERE half_kelly IS NOT NULL ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row and row['half_kelly']:
            return float(row['half_kelly']) * 100  # return as %
    except Exception:
        pass
    return 4.2  # reasonable default


def _get_qmc_risk(db):
    """
    Ph47 + Ph48: جلب أحدث نتائج QMC Portfolio Risk والـ Antithetic Backtest.
    يُعيد dict جاهز للعرض أو None إذا لا توجد بيانات.
    """
    try:
        qmc = db.execute("""
            SELECT var_95, cvar_95, expected_return, sharpe_qmc,
                   p_gain_10pct, p_loss_5pct, max_drawdown_mean, kelly_fraction,
                   n_signals, run_date
            FROM qmc_portfolio_risk
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        if not qmc:
            return None

        av = db.execute("""
            SELECT sharpe_standard, sharpe_av, var_reduction_pct,
                   ci_lower_95, ci_upper_95, win_rate_av
            FROM antithetic_backtest_results
            ORDER BY id DESC LIMIT 1
        """).fetchone()

        bwr = db.execute("""
            SELECT mean_wr, ci_lower, ci_upper, p_gt_50, n_obs, label
            FROM bayesian_wr
            WHERE run_date = (SELECT MAX(run_date) FROM bayesian_wr)
            ORDER BY n_obs DESC LIMIT 1
        """).fetchone()

        return {
            'var_95':         round(float(qmc['var_95']) * 100, 1),
            'cvar_95':        round(float(qmc['cvar_95']) * 100, 1),
            'expected_return':round(float(qmc['expected_return']) * 100, 1),
            'sharpe':         round(float(qmc['sharpe_qmc']), 2),
            'p_gain':         round(float(qmc['p_gain_10pct']) * 100, 1),
            'max_dd':         round(float(qmc['max_drawdown_mean']) * 100, 1),
            'kelly':          round(float(qmc['kelly_fraction']) * 100, 1),
            'n_signals':      int(qmc['n_signals']),
            'run_date':       qmc['run_date'],
            # Antithetic
            'sharpe_av':      round(float(av['sharpe_av']), 2) if av else None,
            'ci_lower':       round(float(av['ci_lower_95']), 2) if av else None,
            'ci_upper':       round(float(av['ci_upper_95']), 2) if av else None,
            'var_reduction':  round(float(av['var_reduction_pct']), 0) if av else None,
            # Bayesian WR
            'bayes_wr':       round(float(bwr['mean_wr']) * 100, 1) if bwr else None,
            'bayes_ci_lo':    round(float(bwr['ci_lower']) * 100, 1) if bwr else None,
            'bayes_ci_hi':    round(float(bwr['ci_upper']) * 100, 1) if bwr else None,
            'bayes_p_gt50':   round(float(bwr['p_gt_50']) * 100, 1) if bwr else None,
            'bayes_n':        int(bwr['n_obs']) if bwr else 0,
        }
    except Exception:
        return None


def _get_tomorrow_forecast(db):
    """
    Ph51: جلب أحدث توقع لجلسة الغد (Tomorrow Direction Forecast).
    يُعيد dict جاهز للعرض أو None إذا لا توجد بيانات.
    """
    try:
        db.execute("SELECT 1 FROM tomorrow_forecast LIMIT 1")
    except Exception:
        return None
    try:
        row = db.execute("""
            SELECT forecast_date, direction, p_up, p_flat, p_down,
                   expected_move_lo, expected_move_hi,
                   gap_up_prob, volatility_regime,
                   model_accuracy, model_auc, n_training_days
            FROM tomorrow_forecast
            ORDER BY id DESC LIMIT 1
        """).fetchone()
        if not row:
            return None
        return {
            'forecast_date':   row['forecast_date'],
            'direction':       row['direction'],
            'p_up':            round(float(row['p_up'])   * 100, 1),
            'p_flat':          round(float(row['p_flat']) * 100, 1),
            'p_down':          round(float(row['p_down']) * 100, 1),
            'move_lo':         round(float(row['expected_move_lo']), 2),
            'move_hi':         round(float(row['expected_move_hi']), 2),
            'gap_up_prob':     round(float(row['gap_up_prob']) * 100, 1),
            'vol_regime':      row['volatility_regime'],
            'accuracy':        round(float(row['model_accuracy']) * 100, 1),
            'auc':             round(float(row['model_auc']), 3),
            'n_days':          int(row['n_training_days']),
        }
    except Exception:
        return None


def _get_sector_rotation(db):
    """
    Ph52+53: جلب أحدث بيانات قطاعية (sector rotation + enhanced breadth).
    يُعيد dict أو None إذا لا توجد بيانات.
    """
    try:
        db.execute("SELECT 1 FROM sector_breadth_daily LIMIT 1")
    except Exception:
        return None
    try:
        rot = db.execute("""
            SELECT date, leading_sector, lagging_sector,
                   rotation_score, sector_dispersion,
                   top3_sectors, bot3_sectors
            FROM sector_rotation_daily ORDER BY date DESC LIMIT 1
        """).fetchone()
        if not rot:
            return None

        top3 = json.loads(rot['top3_sectors']) if rot['top3_sectors'] else []
        bot3 = json.loads(rot['bot3_sectors'])  if rot['bot3_sectors']  else []

        # Also get enhanced breadth for today
        enh = db.execute("""
            SELECT date, n_stocks, ad_ratio, pct_above_ema20,
                   up_vol_ratio, breadth_score, signal,
                   n_new_highs_20d, n_new_lows_20d,
                   rsi_mean, pct_oversold
            FROM market_breadth_enhanced ORDER BY date DESC LIMIT 1
        """).fetchone()

        return {
            'date':          rot['date'],
            'leading':       rot['leading_sector'],
            'lagging':       rot['lagging_sector'],
            'rotation_score':round(float(rot['rotation_score']), 1),
            'dispersion':    round(float(rot['sector_dispersion']), 2),
            'top3':          top3,
            'bot3':          bot3,
            # Enhanced breadth
            'enh_date':      enh['date'] if enh else None,
            'pct_ema20':     round(float(enh['pct_above_ema20']) * 100, 1) if enh else None,
            'up_vol_ratio':  round(float(enh['up_vol_ratio']) * 100, 1)    if enh else None,
            'breadth_score': round(float(enh['breadth_score']), 1)         if enh else None,
            'n_hi20':        int(enh['n_new_highs_20d'])                   if enh else 0,
            'n_lo20':        int(enh['n_new_lows_20d'])                    if enh else 0,
            'rsi_mean':      round(float(enh['rsi_mean']), 1)              if enh else None,
            'pct_oversold':  round(float(enh['pct_oversold']) * 100, 1)   if enh else None,
        }
    except Exception:
        return None


def _get_closing_pressure(db, date, top_n=6):
    """
    Ph57: جلب أقوى أسهم ضغط الإغلاق (gap_potential=1) لتاريخ معين.
    يُعيد list of dicts أو None.
    """
    try:
        db.execute("SELECT 1 FROM closing_pressure_daily LIMIT 1")
    except Exception:
        return None
    try:
        rows = db.execute("""
            SELECT symbol, close_pos, vol_surge, closing_pressure,
                   gap_potential, intraday_reversal
            FROM closing_pressure_daily
            WHERE trade_date = ?
              AND gap_potential = 1
            ORDER BY closing_pressure DESC
            LIMIT ?
        """, (date, top_n)).fetchall()
        if not rows:
            return None
        return [dict(r) for r in rows]
    except Exception:
        return None


def _get_stock_forecast(db, top_signals=None):
    """
    Ph55: جلب توقعات الأسهم الفردية لجلسة الغد من stock_tomorrow_forecast.
    يُعيد dict مع قائمة أسهم UP + إحصائيات أو None.
    """
    try:
        db.execute("SELECT 1 FROM stock_tomorrow_forecast LIMIT 1")
    except Exception:
        return None
    try:
        today = db.execute(
            "SELECT MAX(forecast_date) FROM stock_tomorrow_forecast"
        ).fetchone()[0]
        if not today:
            return None

        rows = db.execute("""
            SELECT symbol, direction, p_up, p_flat, p_down, confidence, sector_rank
            FROM stock_tomorrow_forecast
            WHERE forecast_date = ?
            ORDER BY p_up DESC
        """, (today,)).fetchall()

        if not rows:
            return None

        dir_counts = {'UP': 0, 'FLAT': 0, 'DOWN': 0}
        for r in rows:
            dir_counts[r['direction']] = dir_counts.get(r['direction'], 0) + 1

        total = sum(dir_counts.values())
        pct_up   = round(dir_counts['UP']   / total * 100, 1) if total else 0
        pct_down = round(dir_counts['DOWN'] / total * 100, 1) if total else 0

        # Top UP stocks by p_up
        top_up = [r['symbol'] for r in rows if r['direction'] == 'UP'][:10]

        # Cross-reference with top trade signals (if provided)
        signal_alignment = []
        if top_signals:
            sig_set = set(top_signals)
            for r in rows:
                if r['symbol'] in sig_set:
                    signal_alignment.append({
                        'symbol':    r['symbol'],
                        'direction': r['direction'],
                        'p_up':      round(float(r['p_up']) * 100, 1),
                    })

        return {
            'forecast_date': today,
            'n_total':       total,
            'n_up':          dir_counts['UP'],
            'n_flat':        dir_counts['FLAT'],
            'n_down':        dir_counts['DOWN'],
            'pct_up':        pct_up,
            'pct_down':      pct_down,
            'top_up':        top_up,
            'signal_alignment': signal_alignment,
        }
    except Exception:
        return None


def _get_markov_signal(db):
    """
    Ph56: جلب أحدث إشارة Markov Regime من markov_signal_daily.
    يُعيد dict مع الحالة الحالية، الإشارة، الإنتروبيا، العمر، إلخ — أو None.
    """
    try:
        db.execute("SELECT 1 FROM markov_signal_daily LIMIT 1")
    except Exception:
        return None
    try:
        row = db.execute("""
            SELECT ms.date, ms.current_state, ms.regime_age,
                   ms.signal_1d, ms.p_bear_1d, ms.p_side_1d, ms.p_bull_1d,
                   ms.continuation_confidence, ms.transition_risk, ms.entropy,
                   ms.signal_3d, ms.signal_5d,
                   ms.triple_confirmed, ms.wf_signal_correct,
                   mr.sub_label, mr.base_confidence, mr.roll20_pct
            FROM markov_signal_daily ms
            LEFT JOIN markov_regime_daily mr ON ms.date = mr.date
            ORDER BY ms.date DESC LIMIT 1
        """).fetchone()
        if not row:
            return None

        # WF accuracy (last 60 obs)
        wf_stats = db.execute("""
            SELECT COUNT(*) n,
                   SUM(CASE WHEN wf_signal_correct=1 THEN 1 ELSE 0 END) hits
            FROM markov_signal_daily
            WHERE wf_signal_correct IS NOT NULL
              AND date >= date((SELECT MAX(date) FROM markov_signal_daily), '-90 days')
        """).fetchone()
        wf_acc = None
        if wf_stats and wf_stats['n'] and wf_stats['n'] > 0:
            wf_acc = round(float(wf_stats['hits']) / float(wf_stats['n']) * 100, 1)

        return {
            'date':                    row['date'],
            'current_state':           row['current_state'] or 'SIDE',
            'sub_label':               row['sub_label'] or 'neutral',
            'base_confidence':         row['base_confidence'] or 'strong',
            'regime_age':              int(row['regime_age'] or 1),
            'signal_1d':               round(float(row['signal_1d'] or 0), 3),
            'p_bear':                  round(float(row['p_bear_1d'] or 0) * 100, 1),
            'p_side':                  round(float(row['p_side_1d'] or 0) * 100, 1),
            'p_bull':                  round(float(row['p_bull_1d'] or 0) * 100, 1),
            'continuation':            round(float(row['continuation_confidence'] or 0) * 100, 1),
            'transition_risk':         round(float(row['transition_risk'] or 0) * 100, 1),
            'entropy':                 round(float(row['entropy'] or 0), 2),
            'signal_3d':               round(float(row['signal_3d'] or 0), 3),
            'signal_5d':               round(float(row['signal_5d'] or 0), 3),
            'triple_confirmed':        bool(row['triple_confirmed'] == 1),
            'roll20_pct':              round(float(row['roll20_pct'] or 0) * 100, 2),
            'wf_accuracy_90d':         wf_acc,
        }
    except Exception:
        return None


def _get_spectral_for_signals(db, symbols, date):
    """Get spectral regime + cycle_bottom_prox for a list of symbols."""
    if not symbols:
        return {}
    try:
        placeholders = ','.join('?' * len(symbols))
        rows = db.execute(f"""
            SELECT symbol,
                   MAX(CASE WHEN feature_name='fft_cycle_bottom_prox' THEN feature_value END) AS bottom_prox,
                   MAX(CASE WHEN feature_name='spectral_regime'        THEN feature_value END) AS regime
            FROM feature_store
            WHERE symbol IN ({placeholders})
              AND feature_date <= ?
              AND feature_name IN ('fft_cycle_bottom_prox','spectral_regime')
            GROUP BY symbol
        """, symbols + [date]).fetchall()
        result = {}
        for r in rows:
            bp = r['bottom_prox']
            sr = r['regime']
            result[r['symbol']] = {
                'bottom_prox': float(bp) if bp is not None else None,
                'regime':      int(float(sr)) if sr is not None else None,
            }
        return result
    except Exception:
        return {}


# ── Message builder ───────────────────────────────────────────────────────────

def build_daily_messages(db, data, cur_ind, macro, pipe_log=None):
    """
    2-message institutional Arabic Telegram briefing:
      MSG 1 — ملخص السوق  (Market Summary)
      MSG 2 — توصيات التداول  (Trading Recommendations)
    No internal system labels are exposed to clients.
    """
    o = orch()
    if o is None:
        return ['❌ خطأ: تعذّر تحميل محرك التحليل. يرجى المراجعة.']

    # ── Core computation ──────────────────────────────────────────────────────
    layers     = o.run_all_layers(data, cur_ind, macro)
    confidence = o.compute_confidence(layers)
    conflicts  = o.detect_conflicts(layers)
    posture_r  = o.compute_posture(layers, conflicts, confidence, macro)
    watch      = o.cmd_instability_watch(layers, conflicts, confidence)

    orch_log = load_json_log(ORCH_LOG)
    if not isinstance(orch_log, list):
        orch_log = []
    prev = orch_log[-1] if orch_log else {}

    # ── Key values ────────────────────────────────────────────────────────────
    today_str  = time.strftime('%Y-%m-%d')
    now_str    = time.strftime('%H:%M')
    regime     = layers['latent']['regime']
    posture    = posture_r.get('posture', 'NEUTRAL')
    exposure   = posture_r.get('exposure_pct', 0)
    escalation = watch.get('escalation_level', 'NOMINAL')
    energy_st  = layers['energy'].get('energy_state', 'NORMAL')
    prop_st    = layers['propagation'].get('contagion_state', 'NORMAL')
    prop_rho   = layers['propagation'].get('contagion_score', 0)
    hc_pct     = layers['decision'].get('high_conviction_pct', 0) * 100
    vol_ratio  = layers['energy'].get('avg_vol_ratio', 1.0)

    # Arabic labels
    conf_lbl_ar, conf_ic = _conf_ar(confidence)
    regime_lbl, regime_ic = REGIME_AR.get(regime, (regime, '📊'))
    posture_lbl, posture_ic = POSTURE_AR.get(posture, (posture, '⚖️'))
    energy_ar   = ENERGY_AR.get(energy_st, energy_st)
    prop_ar_str = PROP_AR.get(prop_st, prop_st)

    # Delta vs yesterday
    prev_regime   = prev.get('regime')
    prev_posture  = prev.get('posture')
    regime_changed  = bool(prev_regime  and prev_regime  != regime)
    posture_changed = bool(prev_posture and prev_posture != posture)

    # DB data
    breadth_score, breadth_signal = _get_breadth_info(db, today_str)
    active_laws  = _get_active_laws(db, min_precision=0.55, max_laws=3)
    win_rate, n_trades, avg_rr = _get_win_rate(db)
    half_kelly   = _get_kelly_half(db)
    top_signals  = _get_top_signals(db, today_str, top_n=5)
    spec_data    = _get_spectral_for_signals(db, [s['symbol'] for s in top_signals], today_str)
    transition   = _get_regime_transition(db, today_str)
    qmc_risk      = _get_qmc_risk(db)
    tmr_forecast  = _get_tomorrow_forecast(db)
    sec_rotation  = _get_sector_rotation(db)
    stk_forecast  = _get_stock_forecast(db, [s['symbol'] for s in top_signals])
    markov_sig    = _get_markov_signal(db)
    # Ph57: use latest ohlcv date (may lag today_str by 1-2 days)
    _cp_date = db.execute("SELECT MAX(trade_date) FROM closing_pressure_daily").fetchone()
    _cp_date = _cp_date[0] if _cp_date and _cp_date[0] else today_str
    closing_pres  = _get_closing_pressure(db, _cp_date)

    # Data freshness
    data_age_h = _get_data_freshness(data)
    data_fresh = data_age_h < 48

    # Arabic date
    date_ar = _ar_date()

    # ══════════════════════════════════════════════════════════════════════════
    # MESSAGE 1 — ملخص السوق
    # ══════════════════════════════════════════════════════════════════════════
    lines1 = [
        f'🧠 {B("نشرة EGX الذكية")}  ·  {C(now_str)}',
        f'📅 {date_ar}',
    ]
    if not data_fresh:
        lines1.append(
            f'⚠️ {I(f"تحذير: البيانات قديمة ({data_age_h:.0f} ساعة) — تحقق قبل التداول")}'
        )
    lines1 += [
        SEP,
        f'📊 {B("وضعية السوق")}',
        f'  {regime_ic} النظام: {B(regime_lbl)}'
        + ('  🔄 تحوّل جديد' if regime_changed else ''),
        f'  {conf_ic} الثقة: {B(f"{conf_lbl_ar} ({confidence*100:.0f}%)")}',
        f'  {posture_ic} الوضعية: {B(posture_lbl)}'
        + ('  🔄' if posture_changed else '')
        + f'  •  التعرض المقترح: {B(f"{exposure:.0f}%")}',
    ]

    # Breadth
    if breadth_signal and breadth_signal not in ('UNKNOWN', ''):
        b_label, b_ic = BREADTH_AR.get(breadth_signal, (breadth_signal, '⚪'))
        b_score_str = f'  ({breadth_score:.0f}%)' if breadth_score else ''
        lines1.append(f'  اتساع السوق: {b_ic} {B(b_label)}{b_score_str}')

    # Macro
    if macro and macro.get('available'):
        cbe     = macro.get('cbe_rate')
        infl    = macro.get('inflation_yoy')
        usd_egp = macro.get('usd_egp')
        real_r  = macro.get('real_interest_rate')
        rate_tr = RATE_TR_AR.get(macro.get('rate_cycle', ''), '')
        infl_tr = INFL_TR_AR.get(macro.get('inflation_momentum', ''), '')
        fx_tr   = FX_TR_AR.get(macro.get('fx_trend', ''), '')
        lines1 += [SEP, f'🌍 {B("المؤشرات الاقتصادية")}']
        if cbe is not None:
            cbe_trend = f' ({rate_tr})' if rate_tr else ''
            infl_part = ''
            if infl:
                infl_trend = f' ({infl_tr})' if infl_tr else ''
                infl_part = f'  •  التضخم: {C(f"{infl:.1f}%")}{infl_trend}'
            lines1.append(f'  🏦 فائدة CBE: {C(f"{cbe:.2f}%")}{cbe_trend}{infl_part}')
        if usd_egp:
            fx_trend_str = f' ({fx_tr})' if fx_tr else ''
            real_str = f'  •  الفائدة الحقيقية: {C(f"{real_r:+.1f}%")}' if real_r is not None else ''
            lines1.append(f'  💵 USD/EGP: {C(f"{usd_egp:.2f}")}{fx_trend_str}{real_str}')

    # Ph 27 — Quality gate stats from today's unified_signals
    gate_total = 0; gate_passed = 0
    try:
        gr = db.execute("""
            SELECT COUNT(*) as n,
                   SUM(CASE WHEN quality_gate_passed=1 THEN 1 ELSE 0 END) as gp
            FROM unified_signals WHERE signal_date=? AND is_anti_law_triggered=0
            AND conviction_tier IN ('HIGH_CONVICTION','ULTRA_CONVICTION','MEDIUM_CONVICTION')
        """, (today_str,)).fetchone()
        if gr:
            gate_total  = gr['n'] or 0
            gate_passed = gr['gp'] or 0
    except Exception:
        pass
    gate_str = (f'  •  {B(str(gate_passed))} من {gate_total} اجتازت البوابة 🛡'
                if gate_total > 0 else '')

    # Structure
    lines1 += [
        SEP,
        f'⚡ {B("هيكل السوق")}',
        f'  طاقة الحركة: {energy_ar}  •  حجم ×{vol_ratio:.1f}',
        f'  الترابط بين الأسهم: {prop_ar_str}  (ρ={prop_rho:.2f})',
        f'  جودة الإشارات: {B(f"{hc_pct:.0f}%")} عالية الثقة{gate_str}',
    ]

    # Regime transition risk (if notable)
    if transition:
        p5  = transition.get('prob_5d',  0) * 100
        p10 = transition.get('prob_10d', 0) * 100
        nxt = transition.get('most_likely_next', '')
        if p5 > 15:
            risk_ic = '🔴' if p5 > 30 else '🟠'
            nxt_label, _ = REGIME_AR.get(nxt, (nxt, ''))
            nxt_str = f'  •  أرجح تحوّل: {B(nxt_label)}' if nxt and nxt != regime else ''
            lines1.append(
                f'  {risk_ic} احتمال تحوّل النظام: {C(f"{p5:.0f}%")} خلال 5 أيام{nxt_str}'
            )

    # Active laws (clean names only, no internal IDs)
    named_laws = [l for l in active_laws if l.get('pattern_name')]
    if named_laws:
        lines1 += [SEP, f'⚖️ {B("أبرز قوانين السوق النشطة")}']
        for law in named_laws:
            name  = esc(law['pattern_name'][:45])
            prec  = law.get('precision', 0) * 100
            n_act = law.get('n_activations', 0)
            lines1.append(f'  • {name}  —  دقة {prec:.0f}% ({n_act:,} مرة)')

    # Markov Regime Signal (Ph56)
    if markov_sig:
        state_ar = {'BULL': '📈 صاعد', 'SIDE': '↔️ محايد', 'BEAR': '📉 هابط'}.get(
            markov_sig['current_state'], markov_sig['current_state'])
        sub_ar   = {
            'bullish_lean': ' (ميل صعودي)',
            'bearish_lean': ' (ميل هبوطي)',
            'conflicted':   ' (تعارض)',
            'neutral':      '',
        }.get(markov_sig['sub_label'], '')
        conf_ic  = '🟢' if markov_sig['base_confidence'] == 'strong' else \
                   '🟡' if markov_sig['base_confidence'] == 'weak' else '🔴'
        tc_str   = '  ✅ مؤكد ثلاثياً' if markov_sig['triple_confirmed'] else ''
        sig1d    = markov_sig['signal_1d']
        sig3d    = markov_sig['signal_3d']
        sig5d    = markov_sig['signal_5d']
        sig_ic   = '🟢' if sig1d > 0.1 else ('🔴' if sig1d < -0.1 else '🟡')
        age      = markov_sig['regime_age']
        age_ar   = 'يوم' if age == 1 else 'أيام'
        ent_str  = f'H={markov_sig["entropy"]:.2f} بت'
        trisk    = markov_sig['transition_risk']
        trisk_ic = '🔴' if trisk > 40 else ('🟠' if trisk > 25 else '🟢')
        wf_str   = f'  •  دقة 90 يوم: {markov_sig["wf_accuracy_90d"]:.0f}%' \
                   if markov_sig.get('wf_accuracy_90d') is not None else ''
        mk_date  = markov_sig['date']
        sig1d_s  = f'{sig1d:+.3f}'
        sig3d_s  = f'{sig3d:+.3f}'
        sig5d_s  = f'{sig5d:+.3f}'
        trisk_s  = f'{trisk:.0f}%'
        cont_s   = f'{markov_sig["continuation"]:.0f}%'
        p_bear_s = f'{markov_sig["p_bear"]:.0f}%'
        p_side_s = f'{markov_sig["p_side"]:.0f}%'
        p_bull_s = f'{markov_sig["p_bull"]:.0f}%'
        lines1 += [
            SEP,
            f'🔄 {B("ماركوف")}  {conf_ic} {B(state_ar)}{sub_ar}  —  عمر: {B(str(age))} {age_ar}{tc_str}  {C("[" + mk_date + "]")}',
            f'  {sig_ic} غداً: {B(sig1d_s)}  •  3د: {C(sig3d_s)}  •  5د: {C(sig5d_s)}',
            f'  🐻 {p_bear_s}  ↔️ {p_side_s}  🐂 {p_bull_s}  •  {trisk_ic} تحوّل: {C(trisk_s)}  •  ثبات: {C(cont_s)}{wf_str}',
        ]

    # Sector Rotation (Ph52+53)
    if sec_rotation and sec_rotation.get('top3'):
        top_list  = ' | '.join(sec_rotation['top3'][:3])
        bot_list  = ' | '.join(sec_rotation['bot3'][:2])
        lead      = sec_rotation.get('leading') or '—'
        lag       = sec_rotation.get('lagging') or '—'
        enh_info  = ''
        if sec_rotation.get('n_hi20') is not None:
            enh_info = f'  •  أعلى 20 يوم: {sec_rotation["n_hi20"]} سهم'
        lines1 += [
            SEP,
            f'🔄 {B("قيادة القطاعات")}',
            f'  🟢 قيادة: {B(top_list)}',
            f'  🔴 متأخرة: {bot_list}',
        ]
        if enh_info:
            pct_e20 = sec_rotation.get('pct_ema20', '?')
            pct_e20_str = f'{pct_e20:.0f}%' if isinstance(pct_e20, float) else '?'
            lines1.append(f'  📊 اتساع محسّن: {C(pct_e20_str)} فوق EMA20{enh_info}')

    # Warnings (critical/high only, max 2, no internal IDs)
    critical = [c for c in conflicts if c.get('severity') in ('CRITICAL', 'HIGH')]
    if critical:
        lines1 += [SEP, f'⚠️ {B(f"تحذيرات هيكلية ({len(critical)})")}']
        for c_item in critical[:2]:
            sev  = c_item.get('severity', '')
            desc = (c_item.get('desc') or '')[:65]
            sic  = '🔴' if sev == 'CRITICAL' else '🟠'
            if desc:
                lines1.append(f'  {sic} {I(esc(desc))}')
    else:
        lines1 += [SEP, f'✅ {I("لا توجد تحذيرات هيكلية نشطة")}']

    msg1 = '\n'.join(lines1)

    # ══════════════════════════════════════════════════════════════════════════
    # MESSAGE 2 — توصيات التداول
    # ══════════════════════════════════════════════════════════════════════════
    lines2 = [
        f'🎯 {B("أفضل فرص التداول")}',
        f'📅 {B(date_ar)}',
    ]

    if not top_signals:
        lines2 += [
            SEP,
            f'🔍 {I("لا توجد إشارات قابلة للتنفيذ لهذا اليوم.")}',
            f'   {I("الأسباب المحتملة: البيانات غير محدثة، أو جميع الإشارات في طور المراقبة.")}',
        ]
    else:
        lines2.append(SEP)
        for i, sig in enumerate(top_signals, 1):
            sym  = sig['symbol']
            conv = sig['conviction']
            ml   = sig['ml_pct']
            stars, conv_ar = CONV_STARS_AR.get(conv, ('⭐⭐', 'مراقبة'))
            ml_str = f'  •  ML: {C(f"{ml:.0f}%")}' if ml else ''

            # Ph 28 — behavioral class tag
            bclass = sig.get('behavioral_class', 'UNKNOWN')
            bclass_tag = {'EXPLOSIVE': '💥 انفجاري', 'STEADY': '📊 مستقر',
                          'VOLATILE': '⚡ متقلب',  'DORMANT': '😴 خامل'}.get(bclass, '')
            bclass_str = f'  •  {bclass_tag}' if bclass_tag and bclass not in ('VOLATILE', 'DORMANT', 'UNKNOWN') else ''

            # Ph 36 — freshness tag
            fresh_tag = {'fresh': '', 'extended': '  •  ⚡ تجاوز المنطقة قليلاً',
                         'chased': '  •  ⚠️ تجاوز — انتظر تصحيحاً',
                         'stopped': '  •  🛑 وصل الوقف', 'below_zone': '  •  ⬇️ دون المنطقة'}.get(
                sig.get('freshness', 'unknown'), '')

            # Ph 44 — Entry Trigger tag
            trig_tag = ''
            if sig.get('entry_triggered'):
                trig_d = sig.get('trigger_date', '')
                trig_tag = f'  •  ✅ مُفعَّل ({trig_d})' if trig_d else '  •  ✅ مُفعَّل'

            # Ph 41 — Ensemble Confirmation tag
            ens_pct  = sig.get('ensemble_pct')
            sing_pct = sig.get('ml_pct')
            ens_tag  = ''
            if ens_pct is not None and sing_pct is not None:
                diff = ens_pct - int(sing_pct)
                if diff >= 10:
                    ens_tag = f'  •  🎯 Ens: {C(f"{ens_pct}%")}'
                elif diff <= -10:
                    ens_tag = f'  •  ⚠️ Ens: {C(f"{ens_pct}%")}'
                # If within ±10pt, no tag needed (confirmed)
            elif ens_pct is not None:
                ens_tag = f'  •  Ens: {C(f"{ens_pct}%")}'

            # Ph 40 — Signal Age tag
            _age = sig.get('signal_age', 1)
            age_tag = ''
            if _age == 1:
                age_tag = '  •  🆕 جديد'
            elif _age == 2:
                age_tag = f'  •  📅 يوم 2'
            elif _age >= 3:
                age_tag = f'  •  ⏳ يوم {_age}'

            # Build tags cleanly, grouping them so the header line stays readable
            _tags = ''.join(filter(None, [
                ml_str, ens_tag, bclass_str, age_tag, trig_tag, fresh_tag
            ]))
            lines2.append(f'{i}. {stars} {B(esc(sym))}  —  {B(conv_ar)}{_tags}')

            # Entry / SL / T1 / T2
            el  = sig['entry_low'];  eh  = sig['entry_high']
            sl  = sig['stop_loss'];  t1  = sig['t1'];    t2  = sig['t2']
            rr  = sig['r_ratio']
            sl_p = sig.get('sl_pct'); t1_p = sig.get('t1_pct'); t2_p = sig.get('t2_pct')

            if el and eh and abs(el - eh) > 0.001:
                lines2.append(f'   منطقة الدخول: {C(f"{el:.3f}")}–{C(f"{eh:.3f}")}')
            elif el:
                lines2.append(f'   الدخول: {C(f"{el:.3f}")}')

            # Ph 42 — Volume Surge tag
            _vr = sig.get('vol_ratio')
            vol_str = ''
            if _vr is not None:
                if _vr >= 10:
                    vol_str = f'  •  🚀 حجم ×{_vr:.1f}'
                elif _vr >= 3:
                    vol_str = f'  •  💧 حجم ×{_vr:.1f}'
                elif _vr >= 2:
                    vol_str = f'  •  📈 حجم ×{_vr:.1f}'
                elif _vr < 0.5:
                    vol_str = f'  •  ⚠️ حجم ضعيف ×{_vr:.1f}'

            rr_parts = []
            if sl_p is not None:
                rr_parts.append(f'الوقف: {C(f"{sl_p:.1f}%")}')
            if t1_p is not None:
                rr_parts.append(f'هدف 1: {C(f"+{t1_p:.1f}%")}')
            if t2_p is not None:
                rr_parts.append(f'هدف 2: {C(f"+{t2_p:.1f}%")}')
            if rr:
                rr_parts.append(f'ع/خ: {C(f"{rr:.1f}×")}')
            if rr_parts:
                lines2.append('   ' + '  •  '.join(rr_parts) + vol_str)

            # Spectral cycle (only if data available)
            sp = spec_data.get(sym, {})
            bp = sp.get('bottom_prox')
            sr = sp.get('regime')
            spec_line_parts = []
            if bp is not None and sr is not None:
                sr_label, sr_ic = SPECTRAL_REGIME_AR.get(sr, ('غير محدد', '⚪'))
                bp_pct = bp * 100
                bp_str = f'قرب القاع: {C(f"{bp_pct:.0f}%")} 🎯' if bp > 0.6 else f'{C(f"{bp_pct:.0f}%")} من القاع'
                spec_line_parts.append(f'🌊 الدورة الطيفية: {sr_ic} {sr_label}  •  {bp_str}')

            # Ph 43 — Per-Signal Position Sizing (Scaled Half-Kelly)
            if ml and half_kelly:
                _ml_adj = (ml / 100.0 - 0.5) * 1.0   # +0.5 (90%ML) .. -0.5 (10%ML)
                _sz = half_kelly * (1.0 + 0.4 * _ml_adj)
                # Boost for volume surge
                _vr2 = sig.get('vol_ratio')
                if _vr2 and _vr2 >= 3:
                    _sz *= 1.15
                # Penalty for old signals (age ≥ 3 days)
                if _age >= 3:
                    _sz *= 0.90
                _sz = round(max(0.5, min(8.0, _sz)), 1)
                spec_line_parts.append(f'💰 حجم: {C(f"{_sz}%")}')

            if spec_line_parts:
                lines2.append('   ' + '  •  '.join(spec_line_parts))

            lines2.append('')  # blank line between signals

    # Ph 35 — Sector Concentration Warning
    if top_signals:
        _conc = top_signals[0].get('sector_concentration_warning')
        if _conc:
            lines2.append(f'⚠️ <i>تحذير: {_conc} — راعِ التنويع القطاعي</i>')
            lines2.append('')

    # Performance footer (only if ≥ 15 trades — minimum for statistical validity)
    if win_rate is not None and n_trades >= 15:
        rr_str = f'  •  متوسط ع/خ: {avg_rr:.1f}:1' if avg_rr else ''
        lines2 += [
            SEP,
            f'📈 {B("أداء النظام (آخر 30 يوم)")}',
            f'  نسبة النجاح: {C(f"{win_rate*100:.1f}%")}  •  {n_trades} صفقة{rr_str}',
        ]

    # ── Ph47/Ph48: QMC Portfolio Risk Block ──────────────────────────────────
    if qmc_risk:
        var    = qmc_risk['var_95']
        cvar   = qmc_risk['cvar_95']
        exp_r  = qmc_risk['expected_return']
        sh     = qmc_risk['sharpe']
        pg     = qmc_risk['p_gain']
        mdd    = qmc_risk['max_dd']
        kelly_ = qmc_risk['kelly']
        n_sig  = qmc_risk['n_signals']
        # Icons
        risk_icon = '🟢' if var > -10 else ('🟡' if var > -20 else '🔴')
        sharpe_icon = '🔥' if sh > 3 else ('✅' if sh > 1 else '⚠️')

        qmc_lines = [
            SEP,
            f'📊 {B("تحليل مخاطر المحفظة — QMC Sobol")}  {I(f"({n_sig} إشارة، 4096 سيناريو)")}',
            f'  {risk_icon} VaR@95%: {C(f"{var:+.1f}%")}  •  CVaR: {C(f"{cvar:+.1f}%")}',
            f'  {sharpe_icon} E[R]: {C(f"{exp_r:+.1f}%")}  •  Sharpe: {C(str(sh))}',
            f'  📉 أقصى تراجع متوقع: {C(f"{mdd:.1f}%")}  •  P(ربح>10%): {C(f"{pg:.1f}%")}',
        ]

        # Antithetic Sharpe CI
        if qmc_risk.get('sharpe_av') is not None:
            ci_lo = qmc_risk['ci_lower']
            ci_hi = qmc_risk['ci_upper']
            vr    = qmc_risk['var_reduction']
            _shav = qmc_risk['sharpe_av']
            qmc_lines.append(
                f'  🎯 Sharpe AV: {C(str(_shav))}  '
                f'CI=[{C(str(ci_lo))},{C(str(ci_hi))}]  '
                f'{I(f"تخفيض تباين: {vr:.0f}%")}'
            )

        # Bayesian WR (only if ≥5 outcomes)
        if qmc_risk.get('bayes_wr') is not None and qmc_risk['bayes_n'] >= 5:
            bwr  = qmc_risk['bayes_wr']
            bclo = qmc_risk['bayes_ci_lo']
            bchi = qmc_risk['bayes_ci_hi']
            bpg  = qmc_risk['bayes_p_gt50']
            bn   = qmc_risk['bayes_n']
            bwr_icon = '✅' if bpg > 65 else ('⚠️' if bpg > 50 else '🔴')
            qmc_lines.append(
                f'  {bwr_icon} نسبة النجاح Bayesian: {C(f"{bwr:.1f}%")}  '
                f'CI=[{C(f"{bclo:.0f}%")},{C(f"{bchi:.0f}%")}]  '
                f'{I(f"P(WR>50%)={bpg:.0f}% | n={bn}")})'
            )
        elif qmc_risk.get('bayes_n', 0) < 5:
            qmc_lines.append(f'  ℹ️ {I("Bayesian WR: يتراكم — تفعّل بعد 5+ نتائج")}')

        lines2 += qmc_lines

    # ── Tomorrow Forecast (Ph51) ──────────────────────────────────────────────
    if tmr_forecast:
        tf   = tmr_forecast
        dir_ = tf['direction']
        dir_ar = {'UP': 'صاعد ↑', 'FLAT': 'محايد ↔', 'DOWN': 'هابط ↓'}.get(dir_, dir_)
        dir_ic = {'UP': '🟢', 'FLAT': '🟡', 'DOWN': '🔴'}.get(dir_, '⚪')
        vol_ar = {'HIGH': 'عالية', 'MEDIUM': 'متوسطة', 'LOW': 'منخفضة'}.get(tf['vol_regime'], tf['vol_regime'])

        # dominant probability bar (5-cell)
        bars      = round(tf['p_up'] / 20) * '█' + round(tf['p_flat'] / 20) * '░' + round(tf['p_down'] / 20) * '▒'
        move_sign = '+' if tf['move_lo'] >= 0 else ''
        move_hi_sign = '+' if tf['move_hi'] >= 0 else ''

        p_up_str   = f"{tf['p_up']:.0f}%"
        p_flat_str = f"{tf['p_flat']:.0f}%"
        p_down_str = f"{tf['p_down']:.0f}%"
        gap_str    = f"{tf['gap_up_prob']:.0f}%"
        move_str   = f"{move_sign}{tf['move_lo']}% → {move_hi_sign}{tf['move_hi']}%"
        acc_str    = f"دقة (OOS): {tf['accuracy']:.0f}%  •  AUC: {tf['auc']:.3f}  •  تدريب: {tf['n_days']:,} جلسة"

        # Rolling real-world accuracy from outcomes table
        real_acc_str = ''
        try:
            oc = db.execute("""
                SELECT COUNT(*) n, SUM(correct) hits,
                       SUM(CASE WHEN forecast_date >= date('now','-30 days') THEN correct END) h30,
                       COUNT(CASE WHEN forecast_date >= date('now','-30 days') THEN 1 END) n30
                FROM tomorrow_forecast_outcomes
            """).fetchone()
            if oc and oc['n'] and int(oc['n']) >= 3:
                n_t = int(oc['n']); hits = int(oc['hits'] or 0)
                n30 = int(oc['n30'] or 0); h30 = int(oc['h30'] or 0)
                acc_real = round(hits / n_t * 100)
                real30   = round(h30 / n30 * 100) if n30 else None
                real30_str = f" / 30d: {real30}% ({n30})" if real30 is not None else ""
                real_acc_str = f"  ✅ {I('دقة فعلية: ' + str(acc_real) + '% (' + str(n_t) + ' توقع)' + real30_str)}"
        except Exception:
            pass

        lines2 += [
            SEP,
            f'{dir_ic} {B("توقع جلسة الغد")}  {B(dir_ar)}',
            f'  🟢 {C(p_up_str)} صعود  •  🟡 {C(p_flat_str)} محايد  •  🔴 {C(p_down_str)} هبوط',
            f'  📏 {C(move_str)}  •  فجوة: {C(gap_str)}  •  تقلبية: {C(vol_ar)}',
            f'  {I(acc_str)}',
        ]
        if real_acc_str:
            lines2.append(real_acc_str)

    # ── Per-Stock Tomorrow Forecast (Ph55) ─────────────────────────────────────
    if stk_forecast:
        sf = stk_forecast
        n_total  = sf['n_total']
        pct_up   = sf['pct_up']
        pct_down = sf['pct_down']
        pct_flat = round(100 - pct_up - pct_down, 1)
        top_up   = sf['top_up'][:8]

        breadth_icon = '🟢' if pct_up >= 50 else ('🟡' if pct_up >= 35 else '🔴')

        lines2 += [
            SEP,
            f'📈 {B("توقع الأسهم الفردية غداً")}  {I(f"({n_total} سهم)")}',
            f'  🟢 صاعد: {C(f"{pct_up:.0f}%")} '
            f'•  🟡 محايد: {C(f"{pct_flat:.0f}%")} '
            f'•  🔴 هابط: {C(f"{pct_down:.0f}%")}  {breadth_icon}',
        ]
        if top_up:
            lines2.append(f'  🏆 أعلى احتمالية صعود: {C(", ".join(top_up))}')

        # Cross-reference: how do our top picks align with Ph55?
        align = sf.get('signal_alignment', [])
        if align:
            for a in align:
                dir_ic_55 = {'UP': '🟢', 'FLAT': '🟡', 'DOWN': '🔴'}.get(a['direction'], '⚪')
                dir_ar_55 = {'UP': 'صاعد', 'FLAT': 'محايد', 'DOWN': 'هابط'}.get(a['direction'], a['direction'])
                p_up_55   = f"{a['p_up']:.0f}%"
                lines2.append(
                    f'  {dir_ic_55} {a["symbol"]}: {dir_ar_55} ({C(p_up_55)} ↑)'
                )

    # ── Ph57 — Closing Pressure (Gap Candidates) ──────────────────────────────
    if closing_pres:
        cp_syms = [C(r['symbol']) for r in closing_pres[:6]]
        cp_date_str = _cp_date[-5:] if _cp_date else ''   # MM-DD
        lines2 += [
            SEP,
            f'🕯️ {B("ضغط الإغلاق — مرشحو الفجوة الصاعدة")}  {I(f"({cp_date_str})")}',
            f'  {", ".join(cp_syms)}',
        ]
        # Show top 3 with scores
        for r in closing_pres[:3]:
            _cp_sym  = r['symbol']
            _cp_pos  = r['close_pos']
            _cp_sur  = r['vol_surge']
            _cp_pres = r['closing_pressure']
            _cp_rev  = r.get('intraday_reversal', 0)
            cp_ic    = '🚀' if _cp_pres >= 5 else ('⚡' if _cp_pres >= 2 else '📈')
            rev_str  = '  🔄 انعكاس' if _cp_rev else ''
            lines2.append(
                f'  {cp_ic} {C(_cp_sym)}  '
                f'موضع: {_cp_pos:.0%}  '
                f'×حجم: {_cp_sur:.1f}  '
                f'ضغط: {C(str(round(_cp_pres, 1)))}{rev_str}'
            )

    # Trading rules
    min_conv_ar = ('عالية الثقة وما فوق' if posture in ('NEUTRAL', 'DEFENSIVE', 'AVOID')
                   else 'متوسطة وما فوق')
    lines2 += [
        SEP,
        f'📋 {B("قواعد اليوم")}  —  حجم: {C(f"{half_kelly:.1f}%")}  •  تعرض: {C(f"{exposure:.0f}%")}  •  ثقة دنيا: {C(min_conv_ar)}',
        f'  تجنب: الأسهم المُعلَّقة + نظام الهبوط',
    ]

    # Risk disclaimer — mandatory for client-facing institutional content
    lines2 += [
        SEP,
        f'⚠️ {I("إخلاء المسؤولية: هذه التوصيات للأغراض المعلوماتية والتعليمية فقط ولا تُعدّ نصيحة استثمارية. الاستثمار في البورصة ينطوي على مخاطر رأس المال. قرارات التداول مسؤولية المستثمر وحده. الأداء السابق لا يضمن نتائج مستقبلية.")}',
    ]

    msg2 = '\n'.join(lines2)

    return [msg1, msg2]


def format_alert_message(alert):
    """Format a single alert as a concise Telegram message."""
    sev  = alert.get('severity', 'INFO')
    atype = alert.get('type', 'ALERT')
    msg  = alert.get('message', '')
    date = alert.get('date', time.strftime('%Y-%m-%d'))
    sic  = SEV_ICONS.get(sev, '⚪')
    title_map = {
        'REGIME_SHIFT':           '🔄 Regime Shift Detected',
        'CONFIDENCE_COLLAPSE':    '📉 Confidence Collapse',
        'TOPOLOGY_FRAGMENTATION': '🔗 Topology Fragmented',
        'CAUSAL_INSTABILITY':     '🔎 Causal Instability',
        'VOLATILITY_RELEASE':     '🌊 Volatility Release',
        'CONTAGION_SPIKE':        '🦠 Contagion Spike',
        'EXPOSURE_REDUCTION':     '🛡 Exposure Reduction',
        'HIGH_CONVICTION_OPP':    '🚀 High-Conviction Environment',
        'CRITICAL_SYSTEM_FAILURE':'⚠️ Critical System Failure',
        'MACRO_REGIME_CHANGE':    '🌍 Macro Regime Change',
    }
    title = title_map.get(atype, f'🚨 {atype}')
    lines = [
        f'{sic} {B(title)}',
        f'{C(date)}  [{sev}]',
        SEP,
        esc(msg),
    ]
    return '\n'.join(lines)


def format_posture_update(posture_r, confidence, regime):
    """Short posture update message."""
    posture  = posture_r.get('posture', 'NEUTRAL')
    exposure = posture_r.get('exposure_pct', 0)
    pos_ic   = POSTURE_ICONS.get(posture, '⚖️')
    conf_lbl, conf_ic = conf_label(confidence)
    reg_ic   = REGIME_ICONS.get(regime, '📊')
    lines = [
        f'📊 {B("EGX Posture Update")}',
        f'{C(time.strftime("%Y-%m-%d %H:%M"))}',
        SEP,
        f'{reg_ic} Regime: {B(regime)}',
        f'{conf_ic} Confidence: {B(f"{conf_lbl} ({confidence*100:.1f}%)")}',
        f'{pos_ic} {B("Posture:")} {B(posture)}',
        f'   💼 Exposure: {B(f"{exposure:.1f}%")}',
    ]
    for line in (posture_r.get('rationale') or [])[:3]:
        lines.append(f'   • {I(esc(line))}')
    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

def cmd_format_daily(db, data, cur_ind, macro):
    pipe_log   = load_json_log(str(DATA / 'pipeline_log.json'))
    messages   = build_daily_messages(db, data, cur_ind, macro, pipe_log)
    today_str  = time.strftime('%Y-%m-%d')
    top_sigs   = _get_top_signals(db, today_str, top_n=10)
    top_symbols = [s['symbol'] for s in top_sigs if s.get('symbol')]
    return {
        'messages':     messages,
        'n_messages':   len(messages),
        'total_chars':  sum(len(m) for m in messages),
        'date':         today_str,
        'time':         time.strftime('%H:%M'),
        'top_symbols':  top_symbols,    # Ph30 — for TV MCP live price validation
    }


def cmd_format_alert(db, data, cur_ind, macro, alert_type=None):
    """Format the most recent unformatted alert as a Telegram message."""
    import sqlite3 as _sq
    alerts = []
    try:
        rows = db.execute("""
            SELECT alert_type, severity, message, alert_date
            FROM alert_history ORDER BY alert_date DESC LIMIT 5
        """).fetchall()
        alerts = [dict(r) for r in rows]
    except Exception:
        pass

    if not alerts:
        return {'message': I('No alerts to format.'), 'n_messages': 0}

    target = next((a for a in alerts if not alert_type or a['alert_type']==alert_type), alerts[0])
    msg = format_alert_message({
        'type': target.get('alert_type', ''),
        'severity': target.get('severity', 'INFO'),
        'message': target.get('message', ''),
        'date': target.get('alert_date', ''),
    })
    return {'message': msg, 'messages': [msg], 'n_messages': 1}


def cmd_format_posture(db, data, cur_ind, macro):
    o = orch()
    if o is None:
        return {'error': 'Orchestrator unavailable'}
    snaps      = o.latest_snapshot(data, cur_ind)
    layers     = o.run_all_layers(data, cur_ind, macro)
    confidence = o.compute_confidence(layers)
    conflicts  = o.detect_conflicts(layers)
    posture_r  = o.compute_posture(layers, conflicts, confidence, macro)
    msg = format_posture_update(posture_r, confidence, layers['latent']['regime'])
    return {'message': msg, 'messages': [msg], 'n_messages': 1}


def cmd_format_delta(db, data, cur_ind, macro):
    """What changed since yesterday — concise Telegram message."""
    o        = orch()
    orch_log = load_json_log(ORCH_LOG)
    if not isinstance(orch_log, list): orch_log = []
    prev     = orch_log[-1] if orch_log else {}

    layers     = o.run_all_layers(data, cur_ind, macro)
    confidence = o.compute_confidence(layers)
    conflicts  = o.detect_conflicts(layers)
    posture_r  = o.compute_posture(layers, conflicts, confidence, macro)
    regime     = layers['latent']['regime']

    prev_conf   = prev.get('global_confidence')
    prev_regime = prev.get('regime')
    prev_posture = prev.get('posture')
    conf_arrow  = trend_arrow(confidence, prev_conf)

    lines = [
        f'📈 {B("EGX Delta Report")}',
        f'{C(time.strftime("%Y-%m-%d"))}',
        SEP,
    ]
    if prev_regime and prev_regime != regime:
        lines.append(f'🔄 {B("REGIME SHIFT:")} {prev_regime} → {B(regime)}')
    else:
        lines.append(f'🎯 Regime: {regime} (unchanged)')

    if prev_conf is not None:
        diff = (confidence - prev_conf) * 100
        conf_lbl, conf_ic = conf_label(confidence)
        lines.append(f'{conf_ic} Confidence: {confidence*100:.1f}% {conf_arrow} ({diff:+.1f}pp)')
    if prev_posture and prev_posture != posture_r.get('posture'):
        lines.append(f'🔄 {B("POSTURE SHIFT:")} {prev_posture} → {B(posture_r.get("posture",""))}')

    # New conflicts since yesterday
    lines += [SEP]
    if conflicts:
        lines.append(f'⚔️ Active conflicts ({len(conflicts)}):')
        for c in conflicts[:3]:
            sic = SEV_ICONS.get(c.get('severity',''), '⚪')
            lines.append(f'   {sic} {c.get("id","")}')
    else:
        lines.append('✅ No active conflicts')

    msg = '\n'.join(lines)
    return {'message': msg, 'messages': [msg], 'n_messages': 1}


def cmd_test_format(db, data, cur_ind, macro):
    """Format daily report without sending — for validation."""
    result = cmd_format_daily(db, data, cur_ind, macro)
    return {
        **result,
        'dry_run': True,
        'preview': {
            'msg1_chars': len(result['messages'][0]) if result['messages'] else 0,
            'msg2_chars': len(result['messages'][1]) if len(result['messages']) > 1 else 0,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'test_format'
    if cmd not in COMMANDS:
        print(json.dumps({'error': f'Unknown command: {cmd}', 'available': sorted(COMMANDS)}))
        sys.exit(1)

    try:
        json.loads(sys.stdin.read() or '{}')
    except Exception:
        pass

    import sqlite3 as _sq
    con = _sq.connect(DB_PATH)
    con.row_factory = _sq.Row
    db  = con.cursor()

    try:
        o = orch()
        if o is None:
            raise RuntimeError('cognitive_orchestrator module failed to load')

        data, cur_ind = o.load_ohlcv(db)
        o.enrich(data)
        macro = o.load_macro(db)

        dispatch = {
            'format_daily':   lambda: cmd_format_daily(db, data, cur_ind, macro),
            'format_alert':   lambda: cmd_format_alert(db, data, cur_ind, macro),
            'format_posture': lambda: cmd_format_posture(db, data, cur_ind, macro),
            'format_delta':   lambda: cmd_format_delta(db, data, cur_ind, macro),
            'test_format':    lambda: cmd_test_format(db, data, cur_ind, macro),
        }
        result = dispatch[cmd]()
        print(json.dumps(result, default=str, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({'error': str(e), 'trace': traceback.format_exc()}))
    finally:
        con.close()


if __name__ == '__main__':
    main()
