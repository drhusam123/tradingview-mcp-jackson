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
    'FRAGMENTED':        'متفرق',
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

def _ar_date_from_iso(date_str):
    """Format YYYY-MM-DD as Arabic date string."""
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(str(date_str))
        return _ar_date(_dt.datetime(d.year, d.month, d.day).timestamp())
    except Exception:
        return _ar_date()

def _conf_ar(c):
    """Confidence value → (Arabic label, icon)."""
    if c >= 0.85: return 'مرتفعة جداً', '🟢'
    if c >= 0.70: return 'مرتفعة',      '🟡'
    if c >= 0.55: return 'متوسطة',      '🟠'
    return 'منخفضة', '🔴'

def _composite_confidence(sig):
    """Client-facing weighted confidence from UES + ML + scan."""
    ues = float(sig.get('ues') or 0)
    ml = float(sig.get('ml_pct') or 0)
    scan = float(sig.get('scan_pct') or 0)
    if ues <= 0 and ml <= 0:
        return 0.0
    if scan > 0:
        return round(min(100.0, ues * 0.45 + ml * 0.40 + min(scan, 100) * 0.15), 1)
    denom = 0.45 + (0.40 if ml > 0 else 0.0)
    blend = ues * 0.45 + (ml * 0.40 if ml > 0 else 0.0)
    return round(min(100.0, blend / denom if denom else ues), 1)

def _confidence_basis_line(sig):
    parts = []
    ues = sig.get('ues')
    ml = sig.get('ml_pct')
    scan = sig.get('scan_pct')
    rr = sig.get('r_ratio')
    if ues is not None:
        parts.append(f'درجة موحّدة {ues:.0f}')
    if ml is not None:
        parts.append(f'نموذج ML {ml:.0f}%')
    if scan is not None:
        parts.append(f'مسح {scan:.0f}')
    if rr:
        parts.append(f'ع/خ {rr:.1f}×')
    return ' | '.join(parts) if parts else 'تحليل متعدد المصادر'

def _append_client_signal_block(lines, sig, index, spec_data, half_kelly,
                                block_reason=None, show_confidence=True):
    """One stock recommendation block for client message 2."""
    sym = sig['symbol']
    conv = sig['conviction']
    ml = sig['ml_pct']
    stars, conv_ar = CONV_STARS_AR.get(conv, ('⭐⭐', 'مراقبة'))
    ml_str = f'  •  النموذج: {C(f"{ml:.0f}%")}' if ml else ''

    bclass = sig.get('behavioral_class', 'UNKNOWN')
    bclass_tag = {'EXPLOSIVE': '💥 انفجاري', 'STEADY': '📊 مستقر',
                  'VOLATILE': '⚡ متقلب',  'DORMANT': '😴 خامل'}.get(bclass, '')
    bclass_str = f'  •  {bclass_tag}' if bclass_tag and bclass not in ('VOLATILE', 'DORMANT', 'UNKNOWN') else ''

    fresh_tag = {'fresh': '', 'extended': '  •  ⚡ تجاوز المنطقة قليلاً',
                 'chased': '  •  ⚠️ تجاوز — انتظر تصحيحاً',
                 'stopped': '  •  🛑 وصل الوقف', 'below_zone': '  •  ⬇️ دون المنطقة'}.get(
        sig.get('freshness', 'unknown'), '')

    trig_tag = ''
    if sig.get('entry_triggered'):
        trig_d = sig.get('trigger_date', '')
        trig_tag = f'  •  ✅ مُفعَّل ({trig_d})' if trig_d else '  •  ✅ مُفعَّل'

    ens_pct = sig.get('ensemble_pct')
    sing_pct = sig.get('ml_pct')
    ens_tag = ''
    if ens_pct is not None and sing_pct is not None:
        diff = ens_pct - int(sing_pct)
        if diff >= 10:
            ens_tag = f'  •  🎯 Ens: {C(f"{ens_pct}%")}'
        elif diff <= -10:
            ens_tag = f'  •  ⚠️ Ens: {C(f"{ens_pct}%")}'
    elif ens_pct is not None:
        ens_tag = f'  •  Ens: {C(f"{ens_pct}%")}'

    _age = sig.get('signal_age', 1)
    age_tag = ''
    if _age == 1:
        age_tag = '  •  🆕 جديد'
    elif _age == 2:
        age_tag = '  •  📅 يوم 2'
    elif _age >= 3:
        age_tag = f'  •  ⏳ يوم {_age}'

    _tags = ''.join(filter(None, [ml_str, ens_tag, bclass_str, age_tag, trig_tag, fresh_tag]))
    lines.append(f'{index}. {stars} {B(esc(sym))}  —  {B(conv_ar)}{_tags}')

    el = sig['entry_low']; eh = sig['entry_high']
    sl = sig['stop_loss']; t1 = sig['t1']; t2 = sig['t2']
    rr = sig['r_ratio']
    sl_p = sig.get('sl_pct'); t1_p = sig.get('t1_pct'); t2_p = sig.get('t2_pct')

    if el and eh and abs(el - eh) > 0.001:
        lines.append(f'   منطقة الدخول: {C(f"{el:.3f}")}–{C(f"{eh:.3f}")}')
    elif el:
        lines.append(f'   الدخول: {C(f"{el:.3f}")}')

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
        lines.append('   ' + '  •  '.join(rr_parts) + vol_str)

    if show_confidence:
        conf_pct = _composite_confidence(sig)
        basis = _confidence_basis_line(sig)
        lines.append(f'   ثقة التوصية: {C(f"{conf_pct}%")} ← {I(basis)}')

    sp = spec_data.get(sym, {})
    bp = sp.get('bottom_prox')
    sr = sp.get('regime')
    spec_line_parts = []
    if bp is not None and sr is not None:
        sr_label, sr_ic = SPECTRAL_REGIME_AR.get(sr, ('غير محدد', '⚪'))
        bp_pct = bp * 100
        bp_str = f'قرب القاع: {C(f"{bp_pct:.0f}%")} 🎯' if bp > 0.6 else f'{C(f"{bp_pct:.0f}%")} من القاع'
        spec_line_parts.append(f'🌊 الدورة الطيفية: {sr_ic} {sr_label}  •  {bp_str}')

    if ml and half_kelly:
        _ml_adj = (ml / 100.0 - 0.5) * 1.0
        _sz = half_kelly * (1.0 + 0.4 * _ml_adj)
        _vr2 = sig.get('vol_ratio')
        if _vr2 and _vr2 >= 3:
            _sz *= 1.15
        if _age >= 3:
            _sz *= 0.90
        _sz = round(max(0.5, min(8.0, _sz)), 1)
        spec_line_parts.append(f'💰 حجم: {C(f"{_sz}%")}')

    if sig.get('evidence_text'):
        spec_line_parts.append('✅ دليل: تأكيد السعر + الجودة + العائد/المخاطرة')

    _hd = sig.get('hold_days')
    if _hd:
        spec_line_parts.append('⏱ احتفاظ: ' + C(f'{_hd} أيام'))

    if spec_line_parts:
        lines.append('   ' + '  •  '.join(spec_line_parts))

    if block_reason:
        lines.append(f'   ⏸ {I(esc(str(block_reason)))}')

    lines.append('')

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
_EVENT_CALENDAR = None

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

def event_calendar():
    """Load EGX trading-calendar helper; return None if unavailable."""
    global _EVENT_CALENDAR
    if _EVENT_CALENDAR is None:
        spec = _ilu.spec_from_file_location('event_calendar',
                                             str(HERE / 'event_calendar.py'))
        mod = _ilu.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            return None
        _EVENT_CALENDAR = mod
    return _EVENT_CALENDAR

# ── DB helpers for report ────────────────────────────────────────────────────

def _get_top_signals(db, date, top_n=5):
    """
    Query client-actionable signals from final_signals only.

    Product rule: clients never receive an actionable opportunity unless the
    final product gate has already accepted it for this exact report date.
    """
    # Fetch more candidates so freshness re-sorting can pick better options
    _fetch_n = max(top_n * 3, 15)
    try:
        rows = db.execute("""
                SELECT f.symbol,
                       f.score AS unified_score,
                       CASE
                         WHEN f.confidence >= 0.85 THEN 'ULTRA_CONVICTION'
                         WHEN f.confidence >= 0.70 THEN 'HIGH_CONVICTION'
                         ELSE 'MEDIUM_CONVICTION'
                       END AS conviction_tier,
                       f.source_ml AS explosion_score,
                       f.source_rules AS scan_score,
                       'UNKNOWN' AS liquidity_tier,
                       f.entry_price, f.entry_high, f.stop_loss,
                       f.t1_target, f.t2_target, f.r_ratio,
                       NULL AS behavioral_class,
                       su.sector,
                       ep.prob_pct AS ensemble_pct,
                       ro.entry_triggered, ro.entry_trigger_date,
                       NULL AS s_entry_low,
                       NULL AS s_entry_high,
                       NULL AS s_stop_loss,
                       NULL AS s_t1,
                       NULL AS s_t2,
                       NULL AS s_rr1,
                       NULL AS s_close,
                       f.source_breakdown
                FROM final_signals f
                LEFT JOIN stock_universe su ON su.symbol = f.symbol
                LEFT JOIN explosion_predictions ep
                       ON ep.symbol = f.symbol AND ep.pred_date = f.trade_date
                LEFT JOIN recommendation_outcomes ro
                       ON ro.symbol = f.symbol AND ro.signal_date = f.trade_date
                WHERE f.trade_date = ?
                  AND f.actionable = 1
                  AND f.veto_reason IS NULL
                ORDER BY f.score DESC
                LIMIT ?
            """, (date, _fetch_n)).fetchall()

        signals = []
        for r in rows:
            try:
                breakdown = json.loads(r['source_breakdown'] or '{}')
            except Exception:
                breakdown = {}

            # Delivery layer normalizes actionable rows; keep formatter resilient.
            if breakdown.get('quality_gate_passed') is not True:
                breakdown['quality_gate_passed'] = True

            entry_l  = r['s_entry_low']  or r['entry_price']
            entry_h  = r['entry_high']   or r['s_entry_high'] or r['entry_price']
            sl       = r['stop_loss']    or r['s_stop_loss']
            t1       = r['t1_target']    or r['s_t1']
            t2       = r['t2_target']    or r['s_t2']
            rr       = r['r_ratio']      or r['s_rr1']
            entry_mid = (entry_l + entry_h) / 2 if entry_l and entry_h else (entry_l or entry_h)

            risk_complete = bool(entry_mid and entry_h and sl and t1 and rr)
            valid_price_structure = bool(
                risk_complete and sl < entry_mid and t1 > entry_mid and entry_h >= entry_mid
            )
            if not valid_price_structure or float(rr or 0) < 1.3:
                continue

            # Compute percentage moves relative to entry_mid
            def pct(target, base):
                if target and base and base > 0:
                    return (target - base) / base * 100
                return None

            # ML-Advanced #8: survival exit profile (competing-risks Cox)
            _surv_hold, _surv_ptp = None, None
            try:
                _sv = db.execute(
                    "SELECT hold_days, p_tp_first FROM survival_exit_profile "
                    "WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 1",
                    (r['symbol'], date)).fetchone()
                if _sv:
                    _surv_hold = _sv['hold_days']
                    _surv_ptp = _sv['p_tp_first']
            except Exception:
                pass

            signals.append({
                'symbol':           r['symbol'],
                'ues':              round(r['unified_score'], 1),
                'conviction':       r['conviction_tier'],
                'hold_days':        _surv_hold,
                'p_tp_first':       _surv_ptp,
                'ml_pct':           round(r['explosion_score'], 0) if r['explosion_score'] else None,
                'scan_pct':         round(r['scan_score'], 0) if r['scan_score'] else None,
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
                'evidence_text':    'تأكيد السعر + الجودة + العائد/المخاطرة',
            })

        # Ph 36 — Signal Freshness: tag each signal with price freshness status
        try:
            _fresh_rows = db.execute("""
                SELECT fs.symbol, fs.entry_price, fs.entry_high, fs.stop_loss,
                       oh.close as latest_close
                FROM final_signals fs
                LEFT JOIN (
                    SELECT symbol, close FROM ohlcv_history_execution oh1
                    WHERE bar_time = (
                        SELECT MAX(bar_time) FROM ohlcv_history_execution oh2
                        WHERE oh2.symbol=oh1.symbol AND date(oh2.bar_time,'unixepoch')<=?
                    )
                ) oh ON oh.symbol = fs.symbol
                WHERE fs.trade_date = ?
                  AND fs.actionable = 1
                  AND fs.veto_reason IS NULL
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

        # Ph 40 — Signal Age: how many days has each symbol appeared in final_signals?
        # "day 1" = fresh today, "day 2" = also appeared yesterday, etc.
        # Only counts consecutive days (streak) with any signal for that symbol
        try:
            _syms = [s['symbol'] for s in signals]
            _age_map = {}
            if _syms:
                _plac = ','.join('?' * len(_syms))
                _age_rows = db.execute(f"""
                    SELECT symbol, trade_date AS signal_date
                    FROM final_signals
                    WHERE symbol IN ({_plac})
                      AND actionable = 1
                      AND veto_reason IS NULL
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
                FROM ohlcv_history_execution
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


def _get_retest_watchlist(db, date, top_n=5):
    """
    أقوى المرشحين المحجوبين ببوابات الحجم/التقلب/الـ SL فقط (near-misses).
    ليست توصيات — قائمة مراقبة: تصبح إشارات صالحة إذا جاء retest بحجم ≥2.5x.
    تستثني الحجب الجوهري (ANTI_LAW / ml_too_low / negative_breadth).
    """
    _WATCH_VETOES = (
        'QUALITY_GATE:low_volume_signal',
        'QUALITY_GATE:volatile_stock',
        'FINAL_EDGE:STRUCTURAL_SL_IMPLAUSIBLE',
        'FINAL_EDGE:LOW_RULE_SCORE',
        'QUALITY_GATE:high_volume_chase',
    )
    try:
        rows = db.execute("""
            SELECT f.symbol, f.score, f.source_ml, f.veto_reason, su.sector
            FROM final_signals f
            LEFT JOIN stock_universe su ON su.symbol = f.symbol
            WHERE f.trade_date = ?
              AND f.actionable = 0
              AND f.score >= 85
              AND f.source_ml >= 80
            ORDER BY f.score DESC
            LIMIT 30
        """, (date,)).fetchall()
    except Exception:
        return []

    out = []
    for r in rows:
        veto = str(r['veto_reason'] or '')
        if not any(veto.startswith(v) for v in _WATCH_VETOES):
            continue
        if veto.startswith('QUALITY_GATE:low_volume_signal'):
            reason_ar = 'حجم ضعيف — انتظر حجم ≥2.5x'
        elif veto.startswith('QUALITY_GATE:volatile_stock'):
            reason_ar = 'تقلب مرتفع — انتظر استقرار'
        elif veto.startswith('QUALITY_GATE:high_volume_chase'):
            reason_ar = 'حجم مطاردة — انتظر تهدئة'
        elif veto.startswith('FINAL_EDGE:STRUCTURAL_SL_IMPLAUSIBLE'):
            reason_ar = 'ممتد بعد قفزة — انتظر retest'
        else:
            reason_ar = 'لا يوجد إعداد مكتمل — مراقبة'
        out.append({
            'symbol': r['symbol'],
            'ues': round(float(r['score'] or 0), 1),
            'ml': round(float(r['source_ml'] or 0)),
            'sector': r['sector'] or '',
            'reason': reason_ar,
        })
        if len(out) >= top_n:
            break
    return out


def _diagnose_actionable_filter(db, date):
    """Why actionable DB rows may not appear in client Telegram."""
    out = {
        'db_actionable': 0,
        'deliverable_after_qg': 0,
        'filtered': [],
    }
    try:
        rows = db.execute("""
            SELECT symbol, entry_price, entry_high, stop_loss, t1_target, r_ratio,
                   source_breakdown, veto_reason
            FROM final_signals
            WHERE trade_date=? AND actionable=1 AND veto_reason IS NULL
        """, (date,)).fetchall()
    except Exception:
        return out
    out['db_actionable'] = len(rows)
    for r in rows:
        try:
            breakdown = json.loads(r['source_breakdown'] or '{}')
        except Exception:
            breakdown = {}
        if breakdown.get('quality_gate_passed') is not True:
            out['filtered'].append({
                'symbol': r['symbol'],
                'reason': 'quality_gate_passed_not_true',
                'promoted': bool(breakdown.get('promotion')),
            })
            continue
        entry_l = r['entry_price']
        entry_h = r['entry_high'] or r['entry_price']
        sl = r['stop_loss']
        t1 = r['t1_target']
        rr = r['r_ratio']
        entry_mid = (entry_l + entry_h) / 2 if entry_l and entry_h else (entry_l or entry_h)
        if not (entry_mid and entry_h and sl and t1 and rr):
            out['filtered'].append({'symbol': r['symbol'], 'reason': 'incomplete_risk_levels'})
            continue
        if not (sl < entry_mid < t1 and entry_h >= entry_mid):
            out['filtered'].append({'symbol': r['symbol'], 'reason': 'invalid_price_structure'})
            continue
        if float(rr or 0) < 1.3:
            out['filtered'].append({'symbol': r['symbol'], 'reason': 'rr_below_1_3'})
            continue
        out['deliverable_after_qg'] += 1
    return out


def _count_final_actionable(db, date):
    """Count same-date client-approved final signals only."""
    try:
        row = db.execute("""
            SELECT COUNT(*) AS n
            FROM final_signals
            WHERE trade_date = ?
              AND actionable = 1
              AND veto_reason IS NULL
        """, (date,)).fetchone()
        return int(row['n'] or 0) if row else 0
    except Exception:
        return 0


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
    """Win Rate + Profit Factor + Expectancy from trades table."""
    try:
        import pandas as pd
        import numpy as np
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        df = pd.read_sql_query(
            "SELECT pnl_pct, entry_price, stop_loss FROM trades "
            "WHERE date >= ? AND pnl_pct IS NOT NULL ORDER BY date",
            db.connection if hasattr(db, 'connection') else _get_raw_conn(db),
            params=(cutoff,)
        )
        if len(df) < 5:
            return {'n': len(df), 'insufficient': True}

        wins   = df[df['pnl_pct'] > 0]['pnl_pct']
        losses = df[df['pnl_pct'] <= 0]['pnl_pct']

        win_rate      = len(wins) / len(df)
        profit_factor = (wins.sum() / abs(losses.sum())
                         if len(losses) > 0 and losses.sum() != 0
                         else float('inf'))
        avg_win  = wins.mean()   if len(wins)   > 0 else 0
        avg_loss = losses.mean() if len(losses) > 0 else 0

        # R-multiple expectancy: use SL distance if columns available
        if 'entry_price' in df.columns and 'stop_loss' in df.columns:
            df['risk_pct'] = (
                abs(df['entry_price'] - df['stop_loss'])
                / df['entry_price'].replace(0, np.nan)
            )
            df['risk_pct'] = df['risk_pct'].fillna(0.07).clip(0.02, 0.30)
            df['r_multiple'] = df['pnl_pct'] / df['risk_pct']
            expectancy_r = df['r_multiple'].mean()
        else:
            expectancy_r = win_rate * avg_win - (1 - win_rate) * abs(avg_loss)

        # Max consecutive losses
        loss_mask    = df['pnl_pct'] <= 0
        max_consec   = 0
        current_cons = 0
        for v in loss_mask:
            if v:
                current_cons += 1
                max_consec    = max(max_consec, current_cons)
            else:
                current_cons  = 0

        return {
            'n':                 len(df),
            'win_rate':          win_rate,
            'profit_factor':     round(profit_factor, 2) if profit_factor != float('inf') else 99.0,
            'avg_win_pct':       avg_win,
            'avg_loss_pct':      avg_loss,
            'expectancy_r':      round(expectancy_r, 3),
            'max_consec_losses': max_consec,
            'insufficient':      False,
        }
    except Exception as e:
        # Fallback: legacy scalar query (no pandas)
        try:
            row = db.execute("""
                SELECT COUNT(*) AS n,
                       SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE 0 END) AS gross_win,
                       SUM(CASE WHEN pnl_pct < 0 THEN ABS(pnl_pct) ELSE 0 END) AS gross_loss,
                       AVG(CASE WHEN pnl_pct > 0 THEN pnl_pct ELSE NULL END) AS avg_win,
                       AVG(CASE WHEN pnl_pct < 0 THEN ABS(pnl_pct) ELSE NULL END) AS avg_loss
                FROM trades
                WHERE date >= ?
            """, (cutoff,)).fetchone()
            if row and row['n'] and int(row['n']) >= 5:
                n  = int(row['n'])
                wr = (row['wins'] or 0) / n
                gw = row['gross_win']  or 0
                gl = row['gross_loss'] or 1
                pf = round(gw / gl, 2) if gl > 0 else 99.0
                aw = row['avg_win']  or 0
                al = row['avg_loss'] or 0
                exp_r = round(wr * aw - (1 - wr) * al, 3)
                return {
                    'n': n, 'win_rate': wr, 'profit_factor': pf,
                    'avg_win_pct': aw, 'avg_loss_pct': -al,
                    'expectancy_r': exp_r, 'max_consec_losses': 0,
                    'insufficient': False,
                }
        except Exception:
            pass
        return {'n': 0, 'insufficient': True, 'error': str(e)}


def _get_raw_conn(cursor):
    """Extract underlying sqlite3.Connection from a cursor (for pandas read_sql_query)."""
    try:
        return cursor.connection
    except AttributeError:
        return cursor  # already a connection


def _load_institutional_scorecard(db_path=None):
    """
    Load institutional scorecard JSON created by institutional_metrics.py.
    Returns the dict, or empty dict if not found / parse error.
    """
    try:
        import json as _json
        _sc_path = pathlib.Path(__file__).resolve().parent.parent.parent / 'data' / 'institutional_scorecard.json'
        if _sc_path.exists():
            return _json.loads(_sc_path.read_text())
    except Exception:
        pass
    return {}


def _get_data_freshness_warning(conn, ref_date=None) -> str:
    """Returns Arabic warning string if OHLCV data is stale, empty string if fresh."""
    try:
        from datetime import date as _date, datetime as _datetime
        row = conn.execute(
            "SELECT MAX(date(bar_time,'unixepoch')) FROM ohlcv_history_execution WHERE close > 0"
        ).fetchone()
        last_ohlcv = row[0] if row and row[0] else None
        if last_ohlcv is None:
            return "⛔ تحذير: لا توجد بيانات OHLCV"

        today     = _date.fromisoformat(str(ref_date)) if ref_date else _date.today()
        last_date = _datetime.strptime(last_ohlcv, '%Y-%m-%d').date()

        ec = event_calendar()
        if ec:
            stale_td = ec.staleness_trading_days(last_date, today, DB_PATH)
            trading_today = ec.is_trading_day(today, DB_PATH)
            hname = ec.holiday_name(today, DB_PATH) if hasattr(ec, 'holiday_name') else None
            if stale_td > 0:
                last_td = ec.last_trading_day(today, DB_PATH)
                return (
                    f"⚠️ تحذير: البيانات متأخرة {stale_td} جلسات تداول "
                    f"(آخر: {last_ohlcv}، آخر جلسة: {last_td.isoformat()}) "
                    f"— التوصيات قد لا تكون دقيقة"
                )
            if not trading_today:
                holiday = f" ({hname})" if hname else ""
                return (
                    f"🏖 السوق مغلق اليوم{holiday} — البيانات محدّثة لآخر جلسة ({last_ohlcv})"
                )
            return ""

        # Fallback only if the trading-calendar helper cannot be loaded.
        delta     = (today - last_date).days
        weekday = today.weekday()
        allowed = 3 if weekday in (5, 6, 0) else 1  # Sat/Sun/Mon allow 3 days
        if delta > allowed:
            return (
                f"⚠️ تحذير: البيانات قديمة {delta} أيام (آخر: {last_ohlcv}) "
                f"— التوصيات قد لا تكون دقيقة"
            )
        return ""
    except Exception:
        return ""


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

        # Confidence band stats for UP stocks
        up_rows = [r for r in rows if r['direction'] == 'UP']
        if up_rows:
            confs = sorted([float(r['confidence']) for r in up_rows])
            n_up = len(confs)
            p25_idx = max(0, int(n_up * 0.25) - 1)
            p75_idx = min(n_up - 1, int(n_up * 0.75))
            conf_mean = round(sum(confs) / n_up, 3)
            conf_p25  = round(confs[p25_idx], 3)
            conf_p75  = round(confs[p75_idx], 3)
            p_up_mean = round(sum(float(r['p_up']) for r in up_rows) / n_up, 3)
        else:
            conf_mean = conf_p25 = conf_p75 = p_up_mean = 0.0

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
            'conf_mean':     conf_mean,
            'conf_p25':      conf_p25,
            'conf_p75':      conf_p75,
            'p_up_mean':     p_up_mean,
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


# ── Institutional helper loaders ─────────────────────────────────────────────

def _load_macro_sector(conn) -> dict:
    """Load latest macro-sector analysis from DB."""
    try:
        row = conn.execute(
            "SELECT top_tailwinds, top_headwinds, details_json FROM macro_sector_analysis "
            "ORDER BY analysis_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        result = {'top_tailwinds': row[0], 'top_headwinds': row[1]}
        if row[2]:
            import json as _json
            details = _json.loads(row[2])
            result.update(details)
        return result
    except Exception:
        return {}


def _load_portfolio_allocation(conn) -> dict:
    """Load today's (or yesterday's) portfolio allocation from nav_portfolio_allocations."""
    try:
        import datetime as _dt
        today = _dt.date.today().strftime('%Y-%m-%d')
        rows = conn.execute(
            "SELECT symbol, signal_type, size_pct, size_egp, at_risk_pct, sector, ues_score "
            "FROM nav_portfolio_allocations WHERE allocation_date = ? "
            "ORDER BY size_pct DESC LIMIT 7",
            (today,)
        ).fetchall()
        if not rows:
            yday = (_dt.date.today() - _dt.timedelta(days=1)).strftime('%Y-%m-%d')
            rows = conn.execute(
                "SELECT symbol, signal_type, size_pct, size_egp, at_risk_pct, sector, ues_score "
                "FROM nav_portfolio_allocations WHERE allocation_date = ? "
                "ORDER BY size_pct DESC LIMIT 7",
                (yday,)
            ).fetchall()
        if not rows:
            return {}
        total_row = conn.execute(
            "SELECT SUM(size_pct), SUM(at_risk_pct), regime_state, capital_aum "
            "FROM nav_portfolio_allocations WHERE allocation_date >= date('now', '-2 days') "
            "ORDER BY allocation_date DESC LIMIT 1"
        ).fetchone()
        positions = [{'symbol': r[0], 'type': r[1], 'size_pct': r[2],
                      'size_egp': r[3], 'at_risk': r[4], 'sector': r[5], 'ues': r[6]}
                     for r in rows]
        return {
            'positions':       positions,
            'total_exposure':  total_row[0] if total_row and total_row[0] else 0,
            'total_heat':      total_row[1] if total_row and total_row[1] else 0,
            'regime':          total_row[2] if total_row and total_row[2] else '',
            'capital':         total_row[3] if total_row and total_row[3] else 1_000_000,
        }
    except Exception:
        return {}


def _load_risk_state(conn) -> dict:
    """Load today's risk engine check result from risk_check_daily."""
    try:
        row = conn.execute(
            "SELECT overall_level, drawdown_pct, exposure_multiplier, "
            "alpha_health, regime_fragility, recommendation "
            "FROM risk_check_daily ORDER BY check_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        return {
            'level':           row[0],
            'drawdown_pct':    row[1],
            'exposure_mult':   row[2],
            'alpha_health':    row[3],
            'regime_fragility':row[4],
            'recommendation':  row[5],
        }
    except Exception:
        return {}


# ── Anti-Laws daily scan helper ──────────────────────────────────────────────

def _get_anti_laws_scan(db) -> dict:
    """
    Ph35: آخر نتائج مسح القوانين المضادة اليومية.
    Returns dict with n_veto, n_caution, veto_symbols, most_dangerous_pattern.
    """
    try:
        db.execute("SELECT 1 FROM anti_law_daily_scan LIMIT 1")
    except Exception:
        return {}
    try:
        today = db.execute(
            "SELECT MAX(date) FROM anti_law_daily_scan"
        ).fetchone()[0]
        if not today:
            return {}
        rows = db.execute("""
            SELECT symbol, safety_level, strongest_anti_law
            FROM anti_law_daily_scan WHERE date = ?
        """, (today,)).fetchall()
        if not rows:
            return {}
        n_veto    = sum(1 for r in rows if r['safety_level'] == 'VETO')
        n_caution = sum(1 for r in rows if r['safety_level'] in ('CAUTION', 'DANGER'))
        n_safe    = sum(1 for r in rows if r['safety_level'] == 'SAFE')
        veto_syms = [r['symbol'] for r in rows if r['safety_level'] == 'VETO'][:8]
        # most common pattern
        from collections import Counter
        pat_ctr = Counter(r['strongest_anti_law'] for r in rows if r['strongest_anti_law'])
        top_pat = pat_ctr.most_common(1)[0][0] if pat_ctr else None
        return {
            'date': today,
            'n_veto': n_veto,
            'n_caution': n_caution,
            'n_safe': n_safe,
            'veto_symbols': veto_syms,
            'top_pattern': top_pat,
            'pct_caution': round((n_veto + n_caution) / max(len(rows), 1) * 100, 1),
        }
    except Exception:
        return {}


# ── Alpha Ranker top picks helper ────────────────────────────────────────────

def _get_alpha_top(db, top_n: int = 5) -> list:
    """
    Ph70: أعلى قوانين بتصنيف الألفا (grade A + alive).
    Returns list of {symbol, grade, expectancy_pct, oos_score, win_rate}.
    alpha_rankings schema: hyp_name (law name), ranked_at (ISO datetime),
    expectancy_pct, oos_score, win_rate_pct, grade, is_alive.
    """
    try:
        db.execute("SELECT 1 FROM alpha_rankings LIMIT 1")
    except Exception:
        return []
    try:
        # Get latest batch date (ranked_at is ISO datetime — truncate to date)
        latest_dt = db.execute("SELECT MAX(ranked_at) FROM alpha_rankings").fetchone()[0]
        if not latest_dt:
            return []
        latest_date = latest_dt[:10]   # 'YYYY-MM-DD' prefix
        rows = db.execute("""
            SELECT hyp_name  AS symbol,
                   grade,
                   expectancy_pct,
                   oos_score,
                   win_rate_pct  AS win_rate
            FROM alpha_rankings
            WHERE ranked_at >= ? AND grade IN ('A', 'B') AND is_alive = 1
            ORDER BY expectancy_pct DESC LIMIT ?
        """, (latest_date, top_n)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Cross-Market Coupling helper ─────────────────────────────────────────────

def _get_cross_market(db) -> dict:
    """
    Ph51 Cross-Market: آخر حالة ربط الأسواق العالمية (VIX, Gold, Oil, USD/EGP).
    Returns dict or {}.
    Note: cross_market_regime table does NOT store overall_regime — we derive it
    from risk_on_score + macro_headwind to avoid SELECT failure on missing column.
    """
    try:
        db.execute("SELECT 1 FROM cross_market_regime LIMIT 1")
    except Exception:
        return {}
    try:
        row = db.execute("""
            SELECT date, usdegp_regime, gold_regime, em_regime,
                   oil_regime, vix_regime, risk_on_score, macro_headwind
            FROM cross_market_regime
            ORDER BY date DESC LIMIT 1
        """).fetchone()
        if not row:
            return {}
        # Derive overall_regime from risk_on_score + macro_headwind
        ros = float(row['risk_on_score'] or 50)
        hw  = (row['macro_headwind'] or 'NEUTRAL').upper()
        if ros >= 70 or hw == 'TAILWIND':
            overall = 'MACRO_BULL'
        elif ros <= 30 or hw == 'HEADWIND':
            overall = 'MACRO_BEAR'
        elif ros >= 55:
            overall = 'MACRO_NEUTRAL'
        else:
            overall = 'MACRO_NEUTRAL'
        return {
            'date':            row['date'],
            'overall_regime':  overall,
            'usdegp':          row['usdegp_regime'] or '—',
            'gold':            row['gold_regime']   or '—',
            'em':              row['em_regime']     or '—',
            'oil':             row['oil_regime']    or '—',
            'vix':             row['vix_regime']    or '—',
            'risk_on_score':   round(ros, 1),
            'macro_headwind':  hw,
        }
    except Exception:
        return {}


# ── Cognitive Arbitration + Decision Engine helpers ─────────────────────────

def _get_decision_state(db) -> dict:
    """
    يجلب حالة القرار اليومي من daily_decision_summary + arbitration_decisions.
    Returns: {posture, n_enter, n_wait, n_avoid, n_veto, regime, ewi}
    """
    try:
        today = time.strftime('%Y-%m-%d')
        row = db.execute(
            "SELECT * FROM daily_decision_summary WHERE date=? ORDER BY rowid DESC LIMIT 1",
            (today,)
        ).fetchone()
        if not row:
            # try latest available
            row = db.execute(
                "SELECT * FROM daily_decision_summary ORDER BY date DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if row:
            return {
                'posture':   row['market_posture'],
                'n_enter':   row['n_enter'],
                'n_wait':    row['n_wait'],
                'n_avoid':   row['n_avoid'],
                'n_veto':    row['n_veto'],
                'regime':    row['regime'],
                'ewi':       row['ewi'],
                'date':      row['date'],
            }
    except Exception:
        pass
    return {}


def _get_force_field_state(db) -> dict:
    """
    يجلب أحدث حالة حقل القوى من arbitration_decisions أو یحسبها live.
    Returns: {field_state, n_stocks, buying_pressure, selling_pressure}
    """
    try:
        row = db.execute(
            "SELECT * FROM arbitration_decisions ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                'regime':  row['regime'],
                'ewi':     row['ewi'],
                'decision': row['decision'],
            }
    except Exception:
        pass
    return {}


def _get_market_narrative(db) -> dict:
    """
    يجلب الأرشيتيب الحالي للسوق من market_narratives (أحدث سجل).
    Returns: {archetype_ar, executive_arabic, risk_level, recommended_action}
    """
    try:
        row = db.execute(
            "SELECT * FROM market_narratives ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                'archetype_ar':    row['archetype_ar']    or '',
                'executive_arabic': row['executive_arabic'] or '',
                'risk_level':      row['risk_level']      or '',
                'action':          row['recommended_action'] or '',
            }
    except Exception:
        pass
    return {}


def _get_dominant_forces(db) -> list:
    """
    يجلب أبرز 3 قوى سوقية من dominant_market_forces.
    Returns: list of {force_type, magnitude, direction, evidence}
    """
    try:
        rows = db.execute(
            "SELECT * FROM dominant_market_forces ORDER BY magnitude DESC LIMIT 3"
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                'force_type': r['force_type'] or '',
                'magnitude':  float(r['magnitude'] or 0),
                'direction':  int(r['direction'] or 0),
                'evidence':   (r['evidence'] or '')[:120],
            })
        return out
    except Exception:
        pass
    return []


def _get_synthesis_brief(db) -> dict:
    """
    يجلب الموجز العربي من synthesis_reports (أحدث تقرير).
    Returns: {narrative_ar, top_candidate, n_explosion, n_laws, date}
    """
    try:
        row = db.execute(
            "SELECT * FROM synthesis_reports ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        full = json.loads(row['full_json'] or '{}')
        summ = full.get('summary', {})
        return {
            'narrative_ar':   summ.get('narrative_ar', ''),
            'top_candidate':  summ.get('top_explosion_candidate', ''),
            'top_score':      summ.get('top_explosion_score', 0),
            'n_explosion':    row['explosion_count'] or 0,
            'n_laws':         summ.get('n_active_laws', 0),
            'n_feasible':     summ.get('n_feasible_picks', 0),
            'date':           row['date'] or '',
        }
    except Exception:
        pass
    return {}


def _get_cognition_health(db) -> dict:
    """
    يجلب صحة طبقات المحركات من cognition_snapshots.
    Returns: {layers: {name: {health, state}}, macro_regime, n_conflicts, date, degraded_layers}
    """
    try:
        row = db.execute(
            "SELECT * FROM cognition_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        lh = json.loads(row['layer_health_json'] or '{}')
        degraded = [k for k, v in lh.items() if v.get('state') in ('DEGRADED', 'CRITICAL', 'FAILED')]
        return {
            'layers':       lh,
            'macro_regime': row['macro_regime'] or '',
            'n_conflicts':  row['n_conflicts'] or 0,
            'date':         row['snapshot_date'] or '',
            'degraded':     degraded,
            'n_degraded':   len(degraded),
        }
    except Exception:
        pass
    return {}


def _get_bus_state(db) -> dict:
    """
    يجلب حالة الحافلة المعرفية (bus_state).
    Returns: {directive, coherence_level, coherence_score, narrative_direction, global_confidence}
    """
    try:
        row = db.execute("SELECT * FROM bus_state ORDER BY rowid DESC LIMIT 1").fetchone()
        if row:
            return {
                'directive':   row['directive']        or 'UNKNOWN',
                'coherence':   row['coherence_level']  or '',
                'coh_score':   float(row['coherence_score'] or 0),
                'direction':   row['narrative_direction'] or '',
                'confidence':  float(row['global_confidence'] or 0),
                'n_avail':     int(row['n_available'] or 0),
                'n_contra':    int(row['n_contradictions'] or 0),
            }
    except Exception:
        pass
    return {}


def _get_cognitive_brief(db) -> dict:
    """
    يجلب الموجز المعرفي المضغوط من cognitive_briefings (أحدث إدخال).
    Returns: {arabic_briefing, top_risk, top_risk_severity, n_risks, n_opportunities,
              market_vector, risks_json, date}
    """
    try:
        row = db.execute(
            "SELECT * FROM cognitive_briefings ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        risks = json.loads(row['risks_json'] or '[]')
        return {
            'arabic_briefing':  row['arabic_briefing']  or '',
            'top_risk':         row['top_risk']          or '',
            'top_risk_sev':     row['top_risk_severity'] or '',
            'n_risks':          int(row['n_risks']       or 0),
            'n_opportunities':  int(row['n_opportunities'] or 0),
            'market_vector':    float(row['market_vector'] or 0),
            'risks':            risks,
            'date':             row['date']              or '',
        }
    except Exception:
        pass
    return {}


def _get_mii(db) -> dict:
    """
    يجلب مؤشر الذكاء السوقي (MII) من market_intelligence_index.
    Returns: {mii, interpretation, aggregate_risk_level}
    """
    try:
        row = db.execute(
            "SELECT * FROM market_intelligence_index ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                'mii':          float(row['mii'] or 0),
                'interpretation': row['interpretation'] or '',
                'risk_level':   row['aggregate_risk_level'] or '',
                'n_opps':       int(row['n_opportunities'] or 0),
                'market_vector': float(row['market_vector'] or 0),
            }
    except Exception:
        pass
    return {}


def _get_explosion_predictions(db) -> list:
    """
    يجلب أحدث توقعات الانفجار من explosion_predictions (أعلى 8 بثقة HIGH).
    Returns: [{symbol, prob_pct, confidence_tier, direction, top_feature, top_val}]
    """
    try:
        rows = db.execute("""
            SELECT symbol, explosion_prob, confidence_tier, direction, top_drivers, pred_date
            FROM explosion_predictions
            WHERE pred_date = (SELECT MAX(pred_date) FROM explosion_predictions)
              AND confidence_tier = 'HIGH'
            ORDER BY explosion_prob DESC LIMIT 8
        """).fetchall()
        result = []
        for r in rows:
            drivers = []
            try: drivers = json.loads(r['top_drivers'] or '[]')
            except Exception: pass
            top = drivers[0] if drivers else {}
            result.append({
                'symbol':     r['symbol'],
                'prob_pct':   round(float(r['explosion_prob']) * 100, 1),
                'tier':       r['confidence_tier'] or '',
                'direction':  r['direction'] or 'UP',
                'top_feature': top.get('feature', ''),
                'top_val':    top.get('value', None),
                'pred_date':  r['pred_date'] or '',
            })
        return result
    except Exception:
        pass
    return []


def _get_episodic_outcome(db) -> dict:
    """
    يجلب نتيجة التحليل التاريخي المشابه من episode_similarity + market_episodes.
    Returns: {episode_id, similarity, outcome_7d, outcome_30d, outcome_label,
              start_date, end_date, has_outcome}
    """
    try:
        row = db.execute("""
            SELECT e.episode_id, s.similarity_score,
                   e.outcome_7d, e.outcome_30d, e.outcome_label,
                   e.start_date, e.end_date, e.breadth_score, e.trend_strength
            FROM episode_similarity s
            JOIN market_episodes e ON s.episode_id = e.episode_id
            WHERE s.query_date = date('now') AND e.outcome_7d IS NOT NULL
            ORDER BY s.similarity_score DESC LIMIT 1
        """).fetchone()
        if not row:
            # Fallback: any similar episode with outcome
            row = db.execute("""
                SELECT e.episode_id, s.similarity_score,
                       e.outcome_7d, e.outcome_30d, e.outcome_label,
                       e.start_date, e.end_date, e.breadth_score, e.trend_strength
                FROM episode_similarity s
                JOIN market_episodes e ON s.episode_id = e.episode_id
                WHERE e.outcome_7d IS NOT NULL
                ORDER BY s.similarity_score DESC LIMIT 1
            """).fetchone()
        if not row:
            return {}
        return {
            'episode_id':   row['episode_id'],
            'similarity':   round(float(row['similarity_score'] or 0) * 100, 1),
            'outcome_7d':   float(row['outcome_7d']) if row['outcome_7d'] is not None else None,
            'outcome_30d':  float(row['outcome_30d']) if row['outcome_30d'] is not None else None,
            'outcome_label': row['outcome_label'] or '',
            'start_date':   (row['start_date'] or '')[:7],  # YYYY-MM
            'end_date':     (row['end_date'] or '')[:7],
            'has_outcome':  True,
        }
    except Exception:
        pass
    return {}


def _get_causal_insights(db) -> dict:
    """
    يجلب أحدث اكتشافات سببية من causal_insights.
    Returns: {granger_drivers, top_mi_driver, n_causal, summary, date}
    """
    try:
        row = db.execute(
            "SELECT * FROM causal_insights ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        drivers = json.loads(row['granger_drivers'] or '[]')
        return {
            'drivers':  drivers,
            'n_causal': int(row['n_causal'] or 0),
            'mi_top':   row['top_mi_driver'] or '',
            'summary':  row['summary'] or '',
            'date':     row['date'] or '',
        }
    except Exception:
        pass
    return {}


def _get_intel_brief(db) -> dict:
    """
    يجلب التقرير الاستخباراتي اليومي من daily_intelligence_brief.
    Returns: {market_state, dominant_force, risk_level, regime_stability, brief_summary, date}
    """
    try:
        row = db.execute(
            "SELECT * FROM daily_intelligence_brief ORDER BY computed_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        return {
            'market_state':    row['market_state']      or '',
            'dominant_force':  row['dominant_force']    or '',
            'risk_level':      row['risk_level']        or '',
            'regime_stability': row['regime_stability'] or '',
            'brief_summary':   row['brief_summary']     or '',
            'date':            row['date']              or '',
        }
    except Exception:
        pass
    return {}


def _get_live_engine_stats(db) -> dict:
    """
    يجلب إحصاءات المحركات الحية من آخر night_lab_runs.summary.
    Returns: {force_field_state, market_stress, decision_signal, regime, n_alive}
    """
    try:
        row = db.execute(
            "SELECT summary FROM night_lab_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not row or not row['summary']:
            return {}
        summary = json.loads(row['summary'])
        steps = summary.get('steps', {})
        ff    = steps.get('force_field', {})
        prop  = steps.get('propagation', {})
        dec   = steps.get('decision_engine', {})
        lat   = steps.get('latent_engine', {})
        tb    = steps.get('triple_barrier', {})
        sb    = steps.get('research_sandbox', {})
        im    = steps.get('intraday_monitor', {})
        em    = steps.get('explosion_ml', {})
        ea    = steps.get('egx_analysis', {})
        rg    = steps.get('research_grid', {})
        ep    = steps.get('episodic_memory', {})
        return {
            'field_state':    ff.get('field_state', ''),
            'market_stress':  prop.get('market_stress', None),
            'decision_sig':   dec.get('market_decision', ''),
            'archetype':      lat.get('dominant_archetype', ''),
            'n_stocks':       lat.get('n_stocks', 0),
            'tb_win_rate':    tb.get('win_rate', None),
            'tb_n_events':    tb.get('n_events', 0),
            'sb_promoted':    sb.get('promoted', 0),
            'sb_total':       sb.get('total', 0),
            'session_phase':  im.get('session_phase', ''),
            'em_n_preds':     em.get('n_predictions', 0) or em.get('n_preds', 0),
            'em_auc':         em.get('model_auc', 0),
            'ea_n':           ea.get('n_symbols', 0),
            'ea_rsi_above70': ea.get('rsi_above70', 0),
            'ea_rsi_below30': ea.get('rsi_below30', 0),
            'rg_active':      rg.get('active', 0),
            'rg_untested':    rg.get('untested', 0),
            'ep_analogy':     ep.get('analogy', ''),
            'ep_outcome':     ep.get('historical_outcome', ''),
        }
    except Exception:
        pass
    return {}


# ── Event Calendar helper ────────────────────────────────────────────────────

def _get_event_alerts(days_ahead: int = 7) -> dict:
    """
    جلب تنبيهات الأحداث القادمة من event_calendar.
    Returns dict with keys: n_events, n_high_impact, alert, next_event, has_earnings, has_holiday
    """
    try:
        import importlib.util, sys as _sys
        _ec_path = HERE / 'event_calendar.py'
        if not _ec_path.exists():
            return {}
        spec = importlib.util.spec_from_file_location('event_calendar', str(_ec_path))
        ec   = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ec)
        return ec.summarize_upcoming(days_ahead=days_ahead)
    except Exception:
        return {}


# ── Message builder ───────────────────────────────────────────────────────────

def build_daily_messages(db, data, cur_ind, macro, pipe_log=None, report_date=None, fmt_params=None):
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
    fmt_params = fmt_params or {}
    prep_mode = bool(fmt_params.get('prep_mode'))
    target_session = fmt_params.get('target_session_date')
    buy_syms = set(fmt_params.get('buy_symbols') or [])
    watch_reasons = fmt_params.get('watch_reasons') or {}

    today_str  = report_date or time.strftime('%Y-%m-%d')
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
    _wr_data     = _get_win_rate(db)
    win_rate     = _wr_data.get('win_rate')   if not _wr_data.get('insufficient') else None
    n_trades     = _wr_data.get('n', 0)
    avg_rr       = (_wr_data.get('avg_win_pct', 0) / max(abs(_wr_data.get('avg_loss_pct', 1) or 1), 1e-6)
                    if win_rate is not None else 0)
    _pf          = _wr_data.get('profit_factor')
    _exp_r       = _wr_data.get('expectancy_r')
    _max_cl      = _wr_data.get('max_consec_losses')
    half_kelly   = _get_kelly_half(db)
    final_actionable_count = _count_final_actionable(db, today_str)
    top_signals  = _get_top_signals(db, today_str, top_n=5)

    # Task 5 — Hard-gate filtering: exclude signals where hard_gate_rejection is set
    _rejected_signals = []
    try:
        _all_for_gate = top_signals[:]
        top_signals = [s for s in _all_for_gate if not s.get('hard_gate_rejection')]
        _rejected_signals = [s for s in _all_for_gate if s.get('hard_gate_rejection')]
    except Exception:
        pass  # field absent — keep all signals

    has_client_signals = final_actionable_count > 0 and bool(top_signals)
    if not has_client_signals:
        top_signals = []

    spec_data    = _get_spectral_for_signals(db, [s['symbol'] for s in top_signals], today_str)
    transition   = _get_regime_transition(db, today_str)
    qmc_risk      = _get_qmc_risk(db)
    tmr_forecast  = _get_tomorrow_forecast(db)
    sec_rotation  = _get_sector_rotation(db)
    stk_forecast  = _get_stock_forecast(db, [s['symbol'] for s in top_signals])

    # ── FIX: Remove signals where stock-forecast says DOWN ────────────────────
    # A signal that we predict will go down tomorrow must NOT appear as a buy rec.
    _bearish_filtered = []
    if stk_forecast and stk_forecast.get('signal_alignment'):
        _bearish_set = {
            sa['symbol'] for sa in stk_forecast['signal_alignment']
            if sa.get('direction') == 'DOWN'
        }
        if _bearish_set:
            _pre_filter = top_signals[:]
            top_signals = [s for s in _pre_filter if s['symbol'] not in _bearish_set]
            _bearish_filtered = [s['symbol'] for s in _pre_filter if s['symbol'] in _bearish_set]
    has_client_signals = final_actionable_count > 0 and bool(top_signals)
    markov_sig    = _get_markov_signal(db)
    # Ph57: use latest ohlcv date (may lag today_str by 1-2 days)
    _cp_date = db.execute("SELECT MAX(trade_date) FROM closing_pressure_daily").fetchone()
    _cp_date = _cp_date[0] if _cp_date and _cp_date[0] else today_str
    closing_pres  = _get_closing_pressure(db, _cp_date)

    # Institutional additions — Tasks 1-3
    _macro_sector  = _load_macro_sector(db)
    _portfolio     = _load_portfolio_allocation(db)
    _risk_state    = _load_risk_state(db)
    _event_alerts  = _get_event_alerts(days_ahead=7)
    _cross_market  = _get_cross_market(db)
    _anti_laws_scan = _get_anti_laws_scan(db)
    _alpha_top       = _get_alpha_top(db, top_n=5)
    _decision_st     = _get_decision_state(db)
    _market_narr     = _get_market_narrative(db)
    _dominant_forces = _get_dominant_forces(db)
    _synth_brief     = _get_synthesis_brief(db)
    _cog_health      = _get_cognition_health(db)
    _bus_st          = _get_bus_state(db)
    _cog_brief       = _get_cognitive_brief(db)
    _mii_data        = _get_mii(db)
    _live_engines    = _get_live_engine_stats(db)
    _intel_brief     = _get_intel_brief(db)
    _causal_ins      = _get_causal_insights(db)
    _expl_preds      = _get_explosion_predictions(db)
    _ep_outcome      = _get_episodic_outcome(db)

    if not has_client_signals:
        _alpha_top = []
        _dominant_forces = []
        _synth_brief = {}
        _cog_brief = {}
        _causal_ins = {}
        _expl_preds = []
        _ep_outcome = {}

    # Data freshness
    data_age_h = _get_data_freshness(data)
    data_fresh = data_age_h < 48

    # Arabic date
    date_ar = _ar_date_from_iso(today_str)

    # Institutional scorecard (Task 2)
    _scorecard = _load_institutional_scorecard(DB_PATH)

    # DB-level stale data warning (Task 3)
    _db_stale_warning = _get_data_freshness_warning(db, today_str)

    # ══════════════════════════════════════════════════════════════════════════
    # MESSAGE 1 — ملخص السوق
    # ══════════════════════════════════════════════════════════════════════════
    lines1 = [
        f'🧠 {B("نشرة EGX الذكية")}  ·  {C(now_str)}',
        f'📅 {date_ar}',
    ]
    # Prepend DB-level stale warning (takes priority over hour-based check)
    if _db_stale_warning:
        lines1.append(f'⚠️ {I(esc(_db_stale_warning))}')
    elif not data_fresh:
        lines1.append(
            f'⚠️ {I(f"تحذير: البيانات قديمة ({data_age_h:.0f} ساعة) — تحقق قبل التداول")}'
        )
    # Macro regime Arabic label
    _MACRO_REGIME_AR = {
        'DISINFLATION_EASING':  ('تخفيف تضخمي',  '🟢'),
        'STAGFLATION':          ('ركود تضخمي',   '🔴'),
        'REFLATION':            ('انعكاش تضخمي', '🟡'),
        'DEFLATION_RISK':       ('مخاطر انكماش', '🟠'),
        'EXPANSION':            ('توسع',          '🟢'),
        'CONTRACTION':          ('انكماش',        '🔴'),
        'RECOVERY':             ('تعافٍ',         '🟢'),
        'NORMAL':               ('طبيعي',         '⚪'),
    }
    _macro_reg_str = ''
    if _cog_health and _cog_health.get('macro_regime'):
        _mr = _cog_health['macro_regime']
        _mr_lbl, _mr_ic = _MACRO_REGIME_AR.get(_mr, (esc(_mr), '🌐'))
        _macro_reg_str = f'\n  {_mr_ic} الماكرو: {B(_mr_lbl)}'

    # MII suffix for وضعية السوق
    _mii_str = ''
    if _mii_data and _mii_data.get('mii') is not None:
        _mii_val = _mii_data['mii']
        _mii_int = _mii_data.get('interpretation', '')
        _mii_rl  = _mii_data.get('risk_level', '')
        _MII_INT_AR = {'EXCELLENT':'ممتاز','GOOD':'جيد','FAIR':'مقبول','POOR':'ضعيف','VERY_POOR':'ضعيف جداً'}
        _MII_RL_AR  = {'LOW':'منخفضة','MODERATE':'معتدلة','HIGH':'عالية','EXTREME':'حرجة'}
        _mii_int_ar = _MII_INT_AR.get(_mii_int, _mii_int)
        _mii_rl_ar  = _MII_RL_AR.get(_mii_rl, _mii_rl)
        _mii_ic     = '🟢' if _mii_val >= 65 else ('🟡' if _mii_val >= 45 else ('🟠' if _mii_val >= 30 else '🔴'))
        _mii_str    = f'\n  {_mii_ic} مؤشر الذكاء: {B(f"{_mii_val:.0f}/100")} [{B(_mii_int_ar)}]  •  مخاطر: {B(_mii_rl_ar)}'
        # Add actionable context when quality is low
        if _mii_val < 40 and _mii_rl in ('HIGH', 'EXTREME'):
            _mii_str += f'\n  {I("⚠️ جودة الإشارات منخفضة — قلّل الحجم وانتظر تحسّن ثقة النظام")}'

    lines1 += [
        SEP,
        f'📊 {B("وضعية السوق")}',
        f'  {regime_ic} النظام: {B(regime_lbl)}'
        + ('  🔄 تحوّل جديد' if regime_changed else ''),
        f'  {conf_ic} الثقة: {B(f"{conf_lbl_ar} ({confidence*100:.0f}%)")}',
        f'  {posture_ic} الوضعية: {B(posture_lbl)}'
        + ('  🔄' if posture_changed else '')
        + f'  •  التعرض المقترح: {B(f"{exposure:.0f}%")}'
        + _macro_reg_str
        + _mii_str,
    ]
    # Breadth — added to market posture block
    if breadth_signal and breadth_signal not in ('UNKNOWN', ''):
        b_label, b_ic = BREADTH_AR.get(breadth_signal, (breadth_signal, '⚪'))
        b_score_str = f'  ({breadth_score:.0f}%)' if breadth_score else ''
        lines1.append(f'  اتساع السوق: {b_ic} {B(b_label)}{b_score_str}')

    # ── Institutional Scorecard (Task 4) ──────────────────────────────────────
    # FIX: Hide institutional scorecard when there's no meaningful trading activity
    # Requires ≥15 tracked trades OR ≥5% portfolio exposure OR ≥3 active positions
    _exp_pct_now = _portfolio.get('total_exposure', 0) or 0
    _n_pos_now   = len(_portfolio.get('positions', []))
    _sc_n_trades = (_scorecard or {}).get('n_trades', 0) or 0
    _has_real_activity = _exp_pct_now >= 5 or _sc_n_trades >= 15
    if _scorecard and not _scorecard.get('insufficient_data') and _has_real_activity:
        _grade   = _scorecard.get('institutional_grade', '?')
        _sharpe  = _scorecard.get('sharpe')
        _max_dd  = _scorecard.get('max_drawdown')
        _sc_wr   = _scorecard.get('win_rate')
        _sc_pf   = _scorecard.get('profit_factor')
        _sc_exp  = _scorecard.get('expectancy_r')
        _ruin    = _scorecard.get('mc_ruin_probability')

        _grade_ic = '🟢' if _grade in ('A+', 'A') else ('🟡' if _grade in ('B+', 'B') else '🟠')
        _sc_sharpe_str = f'Sharpe: {_sharpe:.2f}  ' if _sharpe is not None else ''
        _sc_dd_str     = f'Max DD: {_max_dd:.1%}'   if _max_dd is not None else ''
        _sc_wr_str     = (f'  WR: {_sc_wr:.0%}  PF: {_sc_pf:.2f}  Exp: {_sc_exp:+.2f}R'
                          if _sc_wr is not None else '')
        if _ruin is not None and _ruin > 0.05:
            _ruin_str = f'  ⚠️ Ruin Risk: {_ruin:.1%}'
        elif _ruin is not None:
            _ruin_str = '  ✅ Ruin Risk: منخفض'
        else:
            _ruin_str = ''

        lines1 += [
            SEP,
            f'📊 {B("الأداء المؤسسي (90 يوم)")}',
            f'  {_grade_ic} الدرجة: {B(_grade)}  •  {_sc_sharpe_str}{_sc_dd_str}',
        ]
        if _sc_wr_str:
            lines1.append(f' {_sc_wr_str}{_ruin_str}')
        elif _ruin_str:
            lines1.append(f' {_ruin_str}')

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

    # Client product gate: only final_signals actionable=1 can be described as
    # client-ready. unified_signals/research gates are intentionally hidden here.
    gate_total = 0; gate_passed = len(top_signals) if has_client_signals else 0
    try:
        gr = db.execute("SELECT COUNT(*) as n FROM final_signals WHERE trade_date=?", (today_str,)).fetchone()
        if gr:
            gate_total  = gr['n'] or 0
    except Exception:
        pass
    gate_str = f'  •  نهائي للعميل: {B(str(gate_passed))}'

    # Decision state line
    _ds_posture = _decision_st.get('posture', '')
    _ds_enter   = _decision_st.get('n_enter', 0)
    _ds_avoid   = _decision_st.get('n_avoid', 0)
    _ds_veto    = _decision_st.get('n_veto', 0)
    _ds_ewi     = _decision_st.get('ewi', 50)
    _POSTURE_AR2 = {
        'AGGRESSIVE': ('متهجم',   '🚀'), 'BULLISH': ('صاعد', '📈'),
        'MODERATE': ('معتدل', '📊'), 'BALANCED': ('متوازن', '⚖️'),
        'DEFENSIVE': ('دفاعي', '🛡'), 'CAUTIOUS': ('متحفظ', '🔶'),
    }
    _ds_lbl, _ds_ic = _POSTURE_AR2.get(_ds_posture, (_ds_posture or '—', '⚖️'))
    _ewi_ic = '🟢' if _ds_ewi < 35 else ('🔴' if _ds_ewi > 65 else '🟡')

    # Structure
    lines1 += [
        SEP,
        f'⚡ {B("هيكل السوق")}',
        f'  طاقة الحركة: {energy_ar}  •  حجم ×{vol_ratio:.1f}',
        f'  الترابط بين الأسهم: {prop_ar_str}  (ρ={prop_rho:.2f})',
        f'  جودة الإشارات: {B(f"{hc_pct:.0f}%")} عالية الثقة{gate_str}',
    ]
    if _decision_st:
        _ewi_ar = 'مؤشر القلق'
        if has_client_signals:
            lines1.append(
                f'  {_ds_ic} حكم التحكيم: {B(_ds_lbl)}  •  '
                f'فرص نهائية:{C(str(len(top_signals)))}  •  مراقبة السوق:{C(str(_ds_avoid))}'
                f'  •  {_ewi_ic} {_ewi_ar}:{C(f"{_ds_ewi:.0f}")}'
            )
        else:
            lines1.append(
                f'  ⏸ حكم التحكيم: {B("مراقبة فقط")}  •  '
                f'فرص نهائية:{C("0")}  •  {_ewi_ic} {_ewi_ar}:{C(f"{_ds_ewi:.0f}")}'
            )
    # Force field state (from night_lab_runs live stats)
    if _live_engines and _live_engines.get('field_state'):
        _ff_raw = _live_engines['field_state']
        # Extract human part before the dash (e.g. "OVERBOUGHT_TENSION (mrt=-0.405) — شد للتصحيح")
        _ff_parts = _ff_raw.split(' — ', 1)
        _ff_code  = _ff_parts[0].split('(')[0].strip()  # e.g. "OVERBOUGHT_TENSION"
        _ff_ar    = _ff_parts[1].strip() if len(_ff_parts) > 1 else ''
        _FF_AR = {
            'OVERBOUGHT_TENSION': ('ضغط تصحيحي', '🟠'),
            'OVERSOLD_REBOUND':   ('ارتداد من القاع', '🟢'),
            'NEUTRAL_FIELD':      ('حقل محايد', '⚪'),
            'BULLISH_MOMENTUM':   ('زخم صاعد', '🟢'),
            'BEARISH_PRESSURE':   ('ضغط هابط', '🔴'),
        }
        _ff_lbl, _ff_ic = _FF_AR.get(_ff_code, (esc(_ff_ar) or esc(_ff_code), '⚙️'))
        _ms = _live_engines.get('market_stress')
        _ms_str = f'  •  إجهاد: {C(f"{_ms:.0%}")}' if _ms is not None else ''
        lines1.append(f'  {_ff_ic} حقل القوى: {B(_ff_lbl)}{_ms_str}')
    # Market archetype (semantic language)
    if _market_narr and _market_narr.get('archetype_ar'):
        _mn_rl   = (_market_narr.get('risk_level') or '').upper()
        _mn_ic   = '🔴' if _mn_rl == 'HIGH' else ('🟠' if _mn_rl == 'MODERATE' else '🟢')
        _mn_arch = esc(_market_narr['archetype_ar'])
        _mn_exec_raw = (_market_narr.get('executive_arabic') or '')[:40].strip()
        # Strip trailing incomplete fragments (ends with : or |)
        import re as _re
        _mn_exec_raw = _re.sub(r'[\|:][\s\w]*$', '', _mn_exec_raw).strip()
        _mn_exec = esc(_mn_exec_raw)
        lines1.append(f'  {_mn_ic} أرشيتيب: {B(_mn_arch)}' + (f'  —  {I(_mn_exec)}' if _mn_exec else ''))

    # RSI Extreme Overbought / Oversold Warning — from egx_analysis step
    _ea_rsi70 = _live_engines.get('ea_rsi_above70', 0) if _live_engines else 0
    _ea_n_syms = _live_engines.get('ea_n', 254)       if _live_engines else 254
    _ea_rsi30 = _live_engines.get('ea_rsi_below30', 0) if _live_engines else 0
    if _ea_rsi70 > 0 and _ea_n_syms > 0:
        _rsi70_pct = _ea_rsi70 / _ea_n_syms
        if _rsi70_pct >= 0.6:
            _rsi_ic  = '🔴' if _rsi70_pct >= 0.75 else '🟠'
            _rsi_lbl = 'ذروة شراء متطرفة' if _rsi70_pct >= 0.75 else 'ذروة شراء واسعة'
            lines1.append(f'  {_rsi_ic} {B(_rsi_lbl)}: {C(f"{_ea_rsi70}/{_ea_n_syms}")} سهم RSI>70 ({C(f"{_rsi70_pct:.0%}")})')
        elif _ea_rsi30 > 0 and _ea_rsi30 / _ea_n_syms >= 0.25:
            _osl_pct = _ea_rsi30 / _ea_n_syms
            lines1.append(f'  🟢 {B("ذروة بيع واسعة")}: {C(f"{_ea_rsi30}/{_ea_n_syms}")} سهم RSI<30 ({C(f"{_osl_pct:.0%}")})')

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
        for law in named_laws[:2]:
            name  = esc(law['pattern_name'][:40])
            prec  = law.get('precision', 0) * 100
            n_act = law.get('n_activations', 0)
            lines1.append(f'  • {name}  —  {prec:.0f}% ({n_act:,})')

    # Markov Regime Signal (Ph56)
    if markov_sig and str(markov_sig.get('date')) == str(today_str):
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
            f'🔄 {B("ماركوف")}  {conf_ic} {B(state_ar)}{sub_ar}  —  عمر: {B(str(age))} {age_ar}{tc_str}',
            f'  {sig_ic} غداً: {B(sig1d_s)}  •  3د: {C(sig3d_s)}  •  5د: {C(sig5d_s)}',
            f'  🐻 {p_bear_s}  ↔️ {p_side_s}  🐂 {p_bull_s}  •  {trisk_ic} تحوّل: {C(trisk_s)}  •  ثبات: {C(cont_s)}{wf_str}',
        ]

    def _tr_sector(name):
        """Translate EGX English sector name to Arabic."""
        _SECTOR_AR = {
            'Process Industries':      'صناعات التحويل',
            'Finance':                 'مالية',
            'Real Estate':             'عقارات',
            'Health Technology':       'تقنية صحية',
            'Consumer Durables':       'سلع معمرة',
            'Consumer Non-Durables':   'سلع استهلاكية',
            'Commercial Services':     'خدمات تجارية',
            'Technology Services':     'خدمات تقنية',
            'Utilities':               'مرافق',
            'Energy Minerals':         'طاقة ومعادن',
            'Non-Energy Minerals':     'معادن ومواد',
            'Transportation':          'نقل',
            'Industrial Services':     'خدمات صناعية',
            'Retail Trade':            'تجزئة',
            'Communications':          'اتصالات',
            'Miscellaneous':           'متنوعة',
            'Banks':                   'بنوك',
            'Insurance':               'تأمين',
        }
        if not name:
            return name
        return _SECTOR_AR.get(name.strip(), name)

    # Sector Rotation (Ph52+53)
    if sec_rotation and sec_rotation.get('top3'):
        _top3 = sec_rotation['top3'][:3]
        _bot3 = sec_rotation.get('bot3', [])
        # FIX: Remove overlap — a sector cannot be both leading AND lagging
        _top3_set = set(_top3)
        _bot3_clean = [s for s in _bot3 if s not in _top3_set]
        top_list = ' | '.join(_tr_sector(s) for s in _top3)
        bot_list = ' | '.join(_tr_sector(s) for s in _bot3_clean[:2]) if _bot3_clean else '—'
        enh_info = ''
        if sec_rotation.get('n_hi20') is not None:
            enh_info = f'  •  أعلى 20 يوم: {sec_rotation["n_hi20"]} سهم'
        lines1 += [
            SEP,
            f'🔄 {B("قيادة القطاعات")}',
            f'  🟢 قيادة: {B(top_list)}',
        ]
        if bot_list != '—':
            lines1.append(f'  🔴 متأخرة: {bot_list}')
        if enh_info:
            pct_e20 = sec_rotation.get('pct_ema20', '?')
            pct_e20_str = f'{pct_e20:.0f}%' if isinstance(pct_e20, float) else '?'
            lines1.append(f'  📊 اتساع محسّن: {C(pct_e20_str)} فوق EMA20{enh_info}')

    # ── Cross-Market Coupling (Ph51) ─────────────────────────────────────────
    if _cross_market and _cross_market.get('overall_regime', 'UNKNOWN') != 'UNKNOWN':
        _cm_reg  = _cross_market['overall_regime']
        _cm_ros  = _cross_market['risk_on_score']
        _cm_hw   = _cross_market['macro_headwind']
        _cm_vix  = _cross_market['vix']
        _cm_gold = _cross_market['gold']
        _cm_oil  = _cross_market['oil']
        _cm_usd  = _cross_market['usdegp']
        _cm_ic   = '🟢' if _cm_ros >= 60 else ('🔴' if _cm_ros <= 35 else '🟡')
        _CM_REG_AR = {
            'MACRO_BULL':'صاعد عالمي','MACRO_BEAR':'هابط عالمي',
            'MACRO_NEUTRAL':'محايد عالمي','RISK_ON':'مخاطرة مرتفعة',
            'RISK_OFF':'مخاطرة منخفضة',
        }
        _CM_ASSET_AR = {
            'BULL':'صاعد','BEAR':'هابط','NEUTRAL':'محايد',
            'ELEVATED':'مرتفع','LOW':'منخفض','STABLE':'مستقر',
            'RISING':'صاعد','FALLING':'هابط',
            'SIDEWAYS':'جانبي','DEPRECIATING':'تراجع',
            'APPRECIATING':'تقوّي',
        }
        _CM_HW_AR = {'TAILWIND':'🟢 رياح مواتية','HEADWIND':'🔴 رياح معاكسة','NEUTRAL':'⚪ محايد'}
        _cm_reg_ar = _CM_REG_AR.get(_cm_reg, _cm_reg)
        _cm_vix_ar = _CM_ASSET_AR.get(_cm_vix, _cm_vix)
        _cm_gold_ar = _CM_ASSET_AR.get(_cm_gold, _cm_gold)
        _cm_oil_ar  = _CM_ASSET_AR.get(_cm_oil,  _cm_oil)
        _cm_usd_ar  = _CM_ASSET_AR.get(_cm_usd,  _cm_usd)
        _cm_hw_ar   = _CM_HW_AR.get(_cm_hw, _cm_hw)
        lines1 += [
            SEP,
            f'🌐 {B("الأسواق العالمية")}  {_cm_ic} {B(_cm_reg_ar)}  •  مخاطرة: {C(f"{_cm_ros:.0f}/100")}',
            f'  VIX:{C(_cm_vix_ar)}  ذهب:{C(_cm_gold_ar)}  نفط:{C(_cm_oil_ar)}  USD/EGP:{C(_cm_usd_ar)}  {_cm_hw_ar}',
        ]

    # ── Task 1 — Macro-Sector Tailwinds / Headwinds ───────────────────────────
    def _fmt_sector_list(val):
        """Convert ['Real Estate', 'Finance'] or plain str to display string."""
        if not val:
            return ''
        import json as _jj
        try:
            lst = _jj.loads(val) if isinstance(val, str) and val.strip().startswith('[') else None
            if lst and isinstance(lst, list):
                return ' | '.join(_tr_sector(str(x).strip().strip('"\'')) for x in lst if x)
        except Exception:
            pass
        # Strip raw brackets/quotes if string looks like a list literal
        cleaned = str(val).strip().strip('[]').replace('"', '').replace("'", '').strip()
        return _tr_sector(cleaned) if cleaned else str(val)

    if _macro_sector and (_macro_sector.get('top_tailwinds') or _macro_sector.get('top_headwinds')):
        _tw = _fmt_sector_list(_macro_sector.get('top_tailwinds'))
        _hw = _fmt_sector_list(_macro_sector.get('top_headwinds'))
        if _tw or _hw:
            lines1.append(SEP)
            lines1.append(f'🌍 {B("الماكرو والقطاعات")}')
            if _tw:
                lines1.append(f'  🟢 يستفيد: {esc(_tw)}')
            if _hw:
                lines1.append(f'  🔴 يتضرر: {esc(_hw)}')

    # ── Task 2 — Portfolio Allocation ─────────────────────────────────────────
    _pf_positions = _portfolio.get('positions', [])
    _pf_exp_pct = _portfolio.get('total_exposure', 0) or 0
    _pf_positions = [
        p for p in _pf_positions
        if (p.get('size_pct') or 0) >= 1.0 or (p.get('at_risk') or 0) >= 0.2
    ]
    if _pf_positions and _pf_exp_pct >= 1.0:
        _cap = _portfolio.get('capital', 1_000_000)
        _cap_str = f'{_cap/1_000_000:.1f}M EGP' if _cap >= 1_000_000 else f'{_cap:,.0f} EGP'
        _exp_pct  = _pf_exp_pct
        _heat_pct = _portfolio.get('total_heat', 0) or 0
        _pf_reg   = _portfolio.get('regime', '') or ''
        reg_str   = ''
        lines1 += [
            SEP,
            f'🏗️ {B(f"تخصيص المحفظة ({_cap_str})")}',
            f'  التعرض: {C(f"{_exp_pct:.0f}%")}  |  Heat: {C(f"{_heat_pct:.1f}%")}/15%{reg_str}',
        ]
        for _pos in _pf_positions[:2]:
            _sym_p  = esc(str(_pos.get('symbol', '?')))
            _sz_p   = _pos.get('size_pct') or 0
            _at_p   = _pos.get('at_risk') or 0
            lines1.append(f'  {C(_sym_p)} {_sz_p:.1f}% {I(f"(Risk {_at_p:.1f}%)")}')

    # ── Task 3 — Risk Warning (only if ELEVATED or CRITICAL) ──────────────────
    if _risk_state:
        _rl  = (_risk_state.get('level') or '').upper()
        _rfr = (_risk_state.get('regime_fragility') or '').upper()
        if _rl in ('ELEVATED', 'CRITICAL') or _rfr == 'HIGH':
            _rec = esc(str(_risk_state.get('recommendation') or ''))
            _rl_ic = '🔴' if _rl == 'CRITICAL' else '🟠'
            lines1.append(SEP)
            lines1.append(f'{_rl_ic} {B("تحذير إدارة المخاطر:")}')
            if _rec:
                lines1.append(f'  {I(_rec)}')

    # ── Event Calendar — أحداث الأسبوع القادم ────────────────────────────────
    if _event_alerts and _event_alerts.get('n_events', 0) > 0:
        _ec_n    = _event_alerts['n_events']
        _ec_hi   = _event_alerts.get('n_high_impact', 0)
        _ec_next = _event_alerts.get('next_event') or {}
        _ec_earn = _event_alerts.get('has_earnings', False)
        _ec_hol  = _event_alerts.get('has_holiday', False)
        _ec_ic   = '🔴' if _ec_hi >= 2 else ('🟠' if _ec_hi == 1 else '📅')
        _ec_next_str = ''
        if _ec_next:
            _ec_days = _ec_next.get('days_until', '?')
            import re as _re3
            _raw_ttl = str(_ec_next.get('title', '') or '').strip()
            # Clean: remove "(est.)" → "(تقديري)", strip empty parens "()"
            _raw_ttl = _re3.sub(r'\(est\.?\)', '(تقديري)', _raw_ttl)
            _raw_ttl = _re3.sub(r'\(\s*\)', '', _raw_ttl).strip()
            # Translate common English event keywords
            _EV_TR = {'Eid Al Adha':'عيد الأضحى','Eid Al Fitr':'عيد الفطر',
                      'Ramadan':'رمضان','Easter':'عيد الفصح',
                      'CBE':'CBE','day 1':'اليوم الأول','day 2':'اليوم الثاني',
                      'day 3':'اليوم الثالث','Holiday':'إجازة','holiday':'إجازة'}
            for _en, _ar in _EV_TR.items():
                _raw_ttl = _raw_ttl.replace(_en, _ar)
            _ec_ttl  = esc(_raw_ttl[:50])
            if isinstance(_ec_days, int):
                _days_ar = 'غداً' if _ec_days == 1 else f'بعد {_ec_days} أيام'
            else:
                _days_ar = ''
            _days_str = f' ({_days_ar})' if _days_ar else ''
            if _ec_ttl:
                _ec_next_str = f'  القادم: {C(_ec_ttl)}{_days_str}'
        _ec_tags = []
        if _ec_earn:
            _ec_tags.append('أرباح 📊')
        if _ec_hol:
            _ec_tags.append('إجازة 🏖')
        _ec_tags_str = '  •  ' + ' | '.join(_ec_tags) if _ec_tags else ''
        lines1 += [
            SEP,
            f'{_ec_ic} {B("أحداث الأسبوع")}  {C(str(_ec_n))} حدث{_ec_tags_str}',
        ]
        if _ec_next_str:
            lines1.append(_ec_next_str)

    # ── Dominant Market Forces (force_field_engine / latent_engine) ──────────
    _FORCE_AR = {
        'MOMENTUM':       ('زخم',         '🚀'),
        'REGIME_PULL':    ('جذب نظام',    '🧲'),
        'CONTAGION_WAVE': ('موجة عدوى',   '🌊'),
        'RISK_PRESSURE':  ('ضغط مخاطر',  '⚠️'),
        'SENTIMENT_WAVE': ('موجة معنوية', '🎭'),
        'LIQUIDITY':      ('سيولة',        '💧'),
        'VOLATILITY':     ('تقلبية',       '📊'),
    }
    if False and has_client_signals and _dominant_forces:
        lines1 += [SEP, f'⚡ {B("القوى الدافعة للسوق")}']
        for _f in _dominant_forces[:3]:
            _ft   = _f['force_type']
            _fmag = _f['magnitude']
            _fdir = _f['direction']
            _far, _fic = _FORCE_AR.get(_ft, (esc(_ft), '⚙️'))
            _dir_ic = '🟢' if _fdir > 0 else ('🔴' if _fdir < 0 else '⚪')
            _mag_bar = '█' * int(_fmag * 5)
            lines1.append(f'  {_dir_ic} {_fic} {_far}: {C(_mag_bar)} {_fmag:.0%}')

    # ── Cognitive Compression Brief + Research Synthesis ──────────────────────
    # Primary: cognitive_briefings (arabic_briefing + risks)
    # Secondary: synthesis_reports narrative_ar + explosion data
    _cb_text = _cog_brief.get('arabic_briefing', '') if _cog_brief else ''
    _sb_narr = (_synth_brief.get('narrative_ar', '') if _synth_brief else '')
    _brief_text = _cb_text or _sb_narr  # prefer cognitive over synthesis
    _brief_date = (_cog_brief.get('date') or (_synth_brief or {}).get('date') or '')
    if _brief_text and _brief_date == today_str:
        # Clean internal episode IDs (Episode_2026-04-08_2026-05-10 → فرصة)
        import re as _re2
        _brief_text = _re2.sub(r'Episode_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}', 'اكتشاف', _brief_text)
        _date_tag   = f'  {C("[" + _brief_date + "]")}' if _brief_date else ''
        lines1 += [
            SEP,
            f'🔬 {B("موجز ذكاء السوق")}{_date_tag}',
            f'  {I(esc(_brief_text[:150]))}',
        ]
        # Cognitive risks (CRITICAL only) — translated to Arabic
        _RISK_AR = {
            'ANTI_LAW_VETO':            'تعارض قانون مضاد',
            'LAW_QUALITY_DEGRADATION':  'تدهور جودة القوانين',
            'SYNTHESIS_ALERT':          'تنبيه تركيب',
            'REGIME_INSTABILITY':       'عدم استقرار النظام',
            'BREADTH_COLLAPSE':         'انهيار الاتساع',
            'LIQUIDITY_CRISIS':         'أزمة سيولة',
            'DATA_STALENESS':           'قدم البيانات',
        }
        if _cog_brief and _cog_brief.get('risks'):
            for _cr in _cog_brief['risks'][:2]:
                _cr_sev  = _cr.get('severity', '')
                if _cr_sev in ('CRITICAL', 'HIGH'):
                    _cr_ic   = '🔴' if _cr_sev == 'CRITICAL' else '🟠'
                    _cr_raw  = _cr.get('risk_type', '')
                    _cr_lbl  = _RISK_AR.get(_cr_raw, esc(_cr_raw.replace('_', ' ')))
                    lines1.append(f'  {_cr_ic} {B(_cr_lbl)}')
        # Episodic Memory Analogy + Historical Outcome
        _ep_analogy_str = _live_engines.get('ep_analogy', '') if _live_engines else ''
        if _ep_analogy_str:
            _ep_anal_s = esc(str(_ep_analogy_str)[:65])
            lines1.append(f'  🧩 {B(_ep_anal_s)}')
        # Show actual historical outcome if available
        if _ep_outcome and _ep_outcome.get('has_outcome'):
            _ep_sim   = _ep_outcome.get('similarity', 0)
            _ep_7d    = _ep_outcome.get('outcome_7d')
            _ep_30d   = _ep_outcome.get('outcome_30d')
            _ep_lbl   = _ep_outcome.get('outcome_label', '')
            _ep_sd    = _ep_outcome.get('start_date', '')
            _ep_lbl_ar = {
                'VOLATILE_BULL_BREAKOUT': 'اختراق صاعد متقلب',
                'BULL_BREAKOUT':    'اختراق صاعد',
                'BEAR_BREAKDOWN':   'انهيار هابط',
                'CONSOLIDATION':    'توطيد جانبي',
                'CORRECTION':       'تصحيح',
                'RECOVERY':         'تعافٍ',
                'UNKNOWN':          '',
            }.get(_ep_lbl, esc(_ep_lbl.replace('_', ' '))) if _ep_lbl else ''
            _ep_7d_str = ''
            if _ep_7d is not None:
                _ep_7d_ic  = '🟢' if _ep_7d > 0.02 else ('🔴' if _ep_7d < -0.02 else '🟡')
                _ep_7d_str = f'{_ep_7d_ic} {C(f"{_ep_7d:+.0%}")} أسبوع'
            _ep_30d_str = ''
            if _ep_30d is not None:
                _ep_30d_ic = '🟢' if _ep_30d > 0.05 else ('🔴' if _ep_30d < -0.02 else '🟡')
                _ep_30d_str = f'  {_ep_30d_ic} {C(f"{_ep_30d:+.0%}")} شهر'
            if _ep_7d_str or _ep_lbl_ar:
                _ep_after = f'{_ep_7d_str}{_ep_30d_str}'.strip()
                lines1.append(f'  ↳ {_ep_sd}: {_ep_after}')
        # Intel Prioritizer: dominant force + market state
        if _intel_brief:
            _ib_state  = _intel_brief.get('market_state', '')
            _ib_force  = _intel_brief.get('dominant_force', '')
            _ib_risk   = _intel_brief.get('risk_level', '')
            _IB_STATE_AR = {
                'TRENDING': 'اتجاه واضح', 'SIDEWAYS': 'تذبذب جانبي',
                'VOLATILE': 'متقلب', 'BREAKOUT': 'اختراق', 'REVERSAL': 'انعكاس',
                'BULL': 'صاعد', 'BEAR': 'هابط', 'RECOVERY': 'تعافٍ',
            }
            _IB_RISK_AR  = {'LOW':'منخفضة','MODERATE':'معتدلة','HIGH':'عالية','EXTREME':'حرجة','NORMAL':''}
            _ib_state_ar = _IB_STATE_AR.get(_ib_state, esc(_ib_state.replace('_',' '))) if _ib_state else ''
            _ib_risk_ar  = _IB_RISK_AR.get(_ib_risk, '') if _ib_risk else ''
            # Translate dominant_force patterns to Arabic
            _ib_force_ar = ''
            if _ib_force:
                _if = str(_ib_force)
                if _if.startswith('Explosion pressure (') and _if.endswith(')'):
                    _sym = _if[len('Explosion pressure ('):-1]
                    _ib_force_ar = f'ضغط انفجار ({esc(_sym)})'
                elif _if.startswith('DNA-') and ' leadership (' in _if:
                    _arch, _rest = _if[4:].split(' leadership (', 1)
                    _sym = _rest.rstrip(')')
                    _ib_force_ar = f'قيادة {esc(_arch)} ({esc(_sym)})'
                elif _if.startswith('Regime: '):
                    _ib_force_ar = f'نظام: {esc(_if[8:])}'
                elif _if == 'Indeterminate':
                    _ib_force_ar = 'غير محدد'
                else:
                    _ib_force_ar = esc(_if[:35])
            if _ib_state_ar or _ib_force_ar:
                _ib_risk_str = f'  مخاطر: {C(_ib_risk_ar)}' if _ib_risk_ar else ''
                lines1.append(f'  📡 {B(_ib_state_ar)}{_ib_risk_str}  —  {I(_ib_force_ar)}')
        # Causal discovery insight (if available)
        if _causal_ins and _causal_ins.get('n_causal', 0) > 0:
            _cd_drvs = _causal_ins['drivers']
            _DRIVER_AR = {
                'avg_volume': 'متوسط الحجم', 'breadth_score': 'الاتساع',
                'ad_ratio': 'نسبة ص/ه', 'avg_return': 'متوسط العائد',
            }
            _cd_ar   = ' + '.join(_DRIVER_AR.get(d, d) for d in _cd_drvs[:2])
            _cd_n    = _causal_ins.get('n_causal', len(_cd_drvs))
            # parse total from summary "N/M drivers ..." → M
            import re as _re
            _cd_m    = _re.search(r'(\d+)/(\d+)', _causal_ins.get('summary', ''))
            _cd_total = int(_cd_m.group(2)) if _cd_m else 4
            _cd_smry = f'{_cd_n}/{_cd_total} محركات تتنبأ بالانفجارات'
            lines1.append(f'  🔗 محرك الانفجارات (Granger): {B(_cd_ar)}  —  {I(_cd_smry)}')
        # Explosion candidate from synthesis
        _sb_cand = _synth_brief.get('top_candidate', '') if _synth_brief else ''
        _sb_n_ex = _synth_brief.get('n_explosion', 0) if _synth_brief else 0
        _sb_n_fx = _synth_brief.get('n_feasible', 0) if _synth_brief else 0
        if _sb_cand and _sb_n_ex and top_signals:
            lines1.append(f'  💥 مرشح داعم: {B(_sb_cand)}  •  إجمالي بحثي: {C(str(_sb_n_ex))} سهم')

    # ── Engine Layer Health (only show if any DEGRADED) ──────────────────────
    _LAYER_AR = {
        'latent':      'كامن',      'fields':     'حقول',
        'propagation': 'انتشار',    'energy':     'طاقة',
        'causality':   'سببية',     'decision':   'قرار',
        'evolution':   'تطور',      'coupling':   'ترابط',
        'spectral':    'طيفي',
    }
    # Layer health meaning explanations
    _LAYER_MEANING_AR = {
        'decision':    'قرارات التداول بجودة منخفضة',
        'propagation': 'انتشار إشارات العدوى مشوّه',
        'spectral':    'تحليل الدورات الطيفية غير موثوق',
        'latent':      'القوى الكامنة غير مستقرة',
        'causality':   'العلاقات السببية ضعيفة',
        'coupling':    'الترابط مع الأسواق الخارجية منخفض',
        'energy':      'طاقة السوق الداخلية ضعيفة',
    }
    if False and _cog_health and _cog_health.get('n_degraded', 0) > 0:
        _deg = _cog_health['degraded']
        _lh  = _cog_health['layers']
        _nc  = _cog_health.get('n_conflicts', 0)
        _total_layers = max(len(_lh), 1)
        _health_pct = round((1 - len(_deg) / _total_layers) * 100)
        _sys_ic = '🟢' if _health_pct >= 80 else ('🟠' if _health_pct >= 60 else '🔴')
        # Client-facing: single summary line only (no internal layer names)
        _worst_mean = next((_LAYER_MEANING_AR.get(_ln, '') for _ln in _deg
                            if _lh.get(_ln, {}).get('health', 1) < 0.5), '')
        _nc_note = f'  •  {I("تعارضات في المحركات")}' if _nc else ''
        _worst_str = f'  •  {I(_worst_mean)}' if _worst_mean else ''
        lines1 += [SEP,
                   f'⚙️ {B("حالة النظام")}  {_sys_ic} {C(f"{_health_pct}%")}{_worst_str}{_nc_note}']

    # ── Cognitive Bus State (show if significant divergence) ─────────────────
    if False and _bus_st:
        _bs_dir  = _bus_st.get('direction', '')
        _bs_dir_ic = '🔴' if _bs_dir == 'BEARISH' else ('🟢' if _bs_dir == 'BULLISH' else '🟡')
        _bs_conf = _bus_st.get('confidence', 0) * 100
        _bs_coh  = _bus_st.get('coh_score', 0)
        _bs_drv  = _bus_st.get('directive', '')
        _bus_diverge = (
            (_bs_dir == 'BEARISH' and regime == 'BULL') or
            (_bs_dir == 'BULLISH' and regime == 'BEAR') or
            _bs_drv == 'HALT' or
            _bs_conf < 15
        )
        if _bus_diverge:
            _bs_dir_ar  = {'BEARISH': 'هابط', 'BULLISH': 'صاعد', 'NEUTRAL': 'محايد'}.get(_bs_dir, _bs_dir)
            _bs_drv_ar  = {'HALT': 'إيقاف ⛔', 'PROCEED': 'متابعة ✅', 'CAUTION': 'حذر ⚠️'}.get(_bs_drv, _bs_drv)
            _bs_n_contr = _bus_st.get('n_contradict', 0)
            _bs_n_avail = _bus_st.get('n_avail', 0)
            # Explain WHY halt: uncertainty vs contradiction
            _bs_halt_reason = ''
            if _bs_drv == 'HALT':
                if _bs_n_contr == 0 and _bs_conf < 15:
                    _bs_halt_reason = f'سبب الإيقاف: عدم يقين ({_bs_n_avail} محركات بثقة {_bs_conf:.0f}% فقط)'
                elif _bs_n_contr > 0:
                    _bs_halt_reason = f'سبب الإيقاف: {_bs_n_contr} تعارضات بين المحركات'
                elif _bs_conf < 15:
                    _bs_halt_reason = f'سبب الإيقاف: ثقة عامة {_bs_conf:.0f}% (دون العتبة الدنيا)'
            lines1 += [
                SEP,
                f'🧠 {B("الحافلة المعرفية")}  {_bs_dir_ic} {B(_bs_dir_ar)}  •  توجيه: {B(_bs_drv_ar)}',
                f'  تماسك: {C(f"{_bs_coh:.0f}")}  •  ثقة عامة: {C(f"{_bs_conf:.0f}%")}  •  محركات: {C(str(_bs_n_avail))}',
            ]
            if _bs_halt_reason:
                lines1.append(f'  ⚠️ {I(_bs_halt_reason)}')

    # ── Anti-Laws Scan (Ph35) ─────────────────────────────────────────────────
    if _anti_laws_scan and (_anti_laws_scan.get('n_veto', 0) > 0 or _anti_laws_scan.get('n_caution', 0) > 0):
        _al_v   = _anti_laws_scan['n_veto']
        _al_c   = _anti_laws_scan['n_caution']
        _al_pct = _anti_laws_scan['pct_caution']
        _al_pat = esc(str(_anti_laws_scan.get('top_pattern') or '—'))
        _al_ic  = '🔴' if _al_v > 5 else ('🟠' if _al_c > 20 else '🟡')
        _al_vs  = ', '.join(esc(s) for s in _anti_laws_scan.get('veto_symbols', [])[:5])
        lines1 += [
            SEP,
            f'{_al_ic} {B("القوانين المضادة")}  VETO: {C(str(_al_v))}  تحذير: {C(str(_al_c))}  ({_al_pct:.0f}% السوق)',
        ]
        if _al_vs:
            lines1.append(f'  🚫 محظورة: {_al_vs}')
        if _al_pat and _al_pat != '—':
            _PAT_AR = {
                'VOLUME_TRAP':      'فخ الحجم',
                'PUMP_DUMP':        'ضخ وتصريف',
                'FALSE_BREAKOUT':   'اختراق كاذب',
                'DISTRIBUTION':     'توزيع',
                'BEAR_TRAP':        'فخ الهبوط',
                'REVERSAL_RISK':    'مخاطر انعكاس',
                'MOMENTUM_FADE':    'تلاشي الزخم',
                'GAP_FADE':         'إغلاق الفجوة',
            }
            _al_pat_ar = _PAT_AR.get(str(_al_pat).strip(), _al_pat)
            lines1.append(f'  ⚠️ النمط الأكثر خطراً: {I(_al_pat_ar)}')

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
    if prep_mode and target_session:
        target_ar = _ar_date_from_iso(target_session)
        lines2 = [
            f'📌 {B("توصية الجلسة القادمة")}',
            f'🗓 {B("الجلسة المستهدفة")}: {target_ar}',
            f'📊 {I(f"مبنية على إغلاق جلسة {date_ar}")}',
            SEP,
            f'ℹ️ {I("نفّذ عند الافتتاح مع احترام منطقة الدخول والوقف — التوصية استرشادية وليست تعهداً بالربح")}',
        ]
    else:
        lines2 = [
            f'🎯 {B("حالة فرص التداول" if not has_client_signals else "أفضل فرص التداول")}',
            f'📅 {B(date_ar)}',
        ]

    # ── Context banners before recommendations ────────────────────────────────
    _banners = []
    if not prep_mode and _bus_st and _bus_st.get('directive') == 'HALT':
        halt_lines = [SEP, f'⚠️ {B("تنبيه: ثقة النظام منخفضة")}']
        if top_signals:
            halt_lines += [
                f'  {I("الإشارات أدناه للدراسة والمراقبة فقط")}',
                f'  {I("لا تدخل إلا بعد تأكيد الإشارة في جلسة الغد")}',
            ]
        else:
            halt_lines += [
                f'  {I("لا توجد فرص تنفيذية مؤهلة للإرسال")}',
                f'  {I("النظام في وضع مراقبة حتى تتحسن بوابات الجودة")}',
            ]
        _banners += halt_lines
    _ea_rsi70_b2 = _live_engines.get('ea_rsi_above70', 0) if _live_engines else 0
    _ea_n_b2     = _live_engines.get('ea_n', 254) if _live_engines else 254
    if _ea_rsi70_b2 > 0 and _ea_n_b2 > 0 and (_ea_rsi70_b2 / _ea_n_b2) >= 0.70:
        _banners += [
            SEP,
            f'🔴 {B("تحذير ذروة شراء")}: {C(f"{_ea_rsi70_b2}/{_ea_n_b2}")} سهم RSI>70',
            f'  {I("السوق في ذروة — حدد نقطة الدخول بدقة وطبّق الوقف فوراً")}',
        ]
    if _banners:
        lines2 += _banners

    if not top_signals:
        lines2 += [
            SEP,
            f'⏸ {B("لا توجد فرص تنفيذية نهائية لهذا التاريخ.")}',
            f'   {I("لن تُعرض قوائم أسهم للعميل حتى تظهر إشارة نهائية مؤكدة لنفس تاريخ التقرير.")}',
        ]
    elif prep_mode and target_session:
        buy_sigs = [s for s in top_signals if s['symbol'] in buy_syms]
        watch_sigs = [s for s in top_signals if s['symbol'] not in buy_syms]
        idx = 1
        if buy_sigs:
            lines2 += [SEP, f'✅ {B("شراء مؤهل — جاهز للتنفيذ")} ({len(buy_sigs)})']
            for sig in buy_sigs:
                _append_client_signal_block(lines2, sig, idx, spec_data, half_kelly)
                idx += 1
        if watch_sigs:
            lines2 += [SEP, f'👁 {B("مراقبة — انتظر تأكيد الدخول")} ({len(watch_sigs)})']
            for sig in watch_sigs:
                reason = watch_reasons.get(sig['symbol'], 'لم يجتز بوابات الأمان بعد')
                _append_client_signal_block(
                    lines2, sig, idx, spec_data, half_kelly, block_reason=reason,
                )
                idx += 1
    else:
        lines2.append(SEP)
        for i, sig in enumerate(top_signals, 1):
            _append_client_signal_block(lines2, sig, i, spec_data, half_kelly)

    # قائمة المراقبة — أقوى المحجوبين ببوابات الحجم/الامتداد (ليست توصيات)
    # تُعرض دائماً (مع أو بدون إشارات) لتقديم قيمة استباقية: قد يتأهلون مع retest بحجم
    _retest_wl = _get_retest_watchlist(db, today_str, top_n=5)
    _wl_syms = {s['symbol'] for s in top_signals} if top_signals else set()
    _retest_wl = [w for w in _retest_wl if w['symbol'] not in _wl_syms]
    if _retest_wl:
        lines2 += [
            SEP,
            f'👁 {B("قائمة المراقبة — ليست توصيات")}',
            f'   {I("مرشحون أقوياء حجبتهم بوابات الجودة — لا تدخل قبل اكتمال الشرط")}',
        ]
        for w in _retest_wl:
            sec = f' ({w["sector"]})' if w['sector'] else ''
            lines2.append(
                f'  • {B(w["symbol"])}{esc(sec)}  UES:{C(str(w["ues"]))}  ML:{C(str(w["ml"]) + "%")}'
            )
            lines2.append(f'      {I(w["reason"])}')
        lines2.append('')

    # Ph 35 — Sector Concentration Warning
    if top_signals:
        _conc = top_signals[0].get('sector_concentration_warning')
        if _conc:
            lines2.append(f'⚠️ <i>تحذير: {_conc} — راعِ التنويع القطاعي</i>')
            lines2.append('')

    # Task 5 — Hard-gated rejections note (max 3, shown as info)
    if _rejected_signals:
        _rej_lines = [f'{SEP}', f'⛔ {I("مرفوض بواسطة Gate المؤسسي:")}']
        for _rs in _rejected_signals[:3]:
            _reason = esc(str(_rs.get('hard_gate_rejection', 'gate')))
            _sym_r  = esc(str(_rs.get('symbol', '?')))
            _rej_lines.append(f'  ⛔ {C(_sym_r)}: {I(_reason)}')
        lines2 += _rej_lines
        lines2.append('')

    # Performance footer (only if ≥ 15 trades — minimum for statistical validity)
    if win_rate is not None and n_trades >= 15:
        rr_str = f'  •  ع/خ: {avg_rr:.1f}:1' if avg_rr else ''
        pf_str = f'  •  PF: {C(str(_pf))}' if _pf is not None else ''
        exp_str = f'  •  Exp: {C(f"{_exp_r:+.3f}R")}' if _exp_r is not None else ''
        cl_str  = f'  •  أسوأ تسلسل: {_max_cl} خسائر' if _max_cl else ''
        lines2 += [
            SEP,
            f'📈 {B("أداء النظام (آخر 30 يوم)")}',
            f'  نسبة النجاح: {C(f"{win_rate*100:.1f}%")}  •  {n_trades} صفقة{rr_str}{pf_str}{exp_str}{cl_str}',
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
            f'📊 {B("مخاطر المحفظة QMC")}  {I(f"({n_sig} إشارة)")}',
            f'  {risk_icon} VaR: {C(f"{var:+.1f}%")}  •  CVaR: {C(f"{cvar:+.1f}%")}  •  {sharpe_icon} E[R]: {C(f"{exp_r:+.1f}%")} Sh:{C(str(sh))}',
            f'  📉 MDD: {C(f"{mdd:.1f}%")}  •  P(>10%): {C(f"{pg:.1f}%")}',
        ]

        # Antithetic Sharpe CI
        if qmc_risk.get('bayes_wr') is not None and qmc_risk['bayes_n'] >= 5:
            bwr  = qmc_risk['bayes_wr']
            bpg  = qmc_risk['bayes_p_gt50']
            bn   = qmc_risk['bayes_n']
            bwr_icon = '✅' if bpg > 65 else ('⚠️' if bpg > 50 else '🔴')
            qmc_lines.append(
                f'  {bwr_icon} نسبة النجاح (Bayes): {C(f"{bwr:.1f}%")}  احتمال الربح: {C(f"{bpg:.0f}%")} | عينة: {C(str(bn))}'
            )

        lines2 += qmc_lines

    # ── Tomorrow Forecast (Ph51) ──────────────────────────────────────────────
    if top_signals and tmr_forecast:
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

    # ── ML Explosion Predictions (top HIGH confidence, latest date) ────────────
    if top_signals and _expl_preds:
        _EP_FEAT_AR = {
            'rsi14':       'RSI',   'vol_ratio':  'حجم×',   'bb_width':   'BB عرض',
            'bb_position': 'موقع BB', 'momentum_5d': 'زخم 5d', 'lgbm_prob': 'LGB',
            'xgb_prob':    'XGB',   'rf_prob':    'RF',
        }
        _ep_date = _expl_preds[0]['pred_date'][-5:] if _expl_preds else ''
        lines2 += [SEP, f'💥 {B("توقعات الانفجار — ML")}  {I(f"({len(_expl_preds)} HIGH ثقة • {_ep_date})")}']
        for _ep in _expl_preds[:4]:
            _ep_sym   = _ep['symbol']
            _ep_prob  = _ep['prob_pct']
            _ep_ic    = '🔴' if _ep_prob >= 95 else ('🟠' if _ep_prob >= 85 else '🟡')
            _ep_feat  = _EP_FEAT_AR.get(_ep['top_feature'], esc(_ep['top_feature']))
            _ep_val   = _ep['top_val']
            _ep_fstr  = f'{_ep_feat}:{_ep_val:.1f}' if _ep_val is not None else _ep_feat
            lines2.append(f'  {_ep_ic} {B(esc(_ep_sym))} {C(f"{_ep_prob:.0f}%")}  ← {_ep_fstr}')

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
    if False and top_signals and stk_forecast:
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
        if sf.get('conf_mean'):
            bands = f"{sf['conf_p25']*100:.0f}–{sf['conf_p75']*100:.0f}%"
            conf_str = f"{sf['conf_mean']*100:.0f}%"
            lines2.append(f'  📊 متوسط الثقة: {C(conf_str)} (نطاق: {bands})')

        # Cross-reference: how do our top picks align with Ph55?
        align = sf.get('signal_alignment', [])
        if align:
            for a in align[:2]:
                dir_ic_55 = {'UP': '🟢', 'FLAT': '🟡', 'DOWN': '🔴'}.get(a['direction'], '⚪')
                dir_ar_55 = {'UP': 'صاعد', 'FLAT': 'محايد', 'DOWN': 'هابط'}.get(a['direction'], a['direction'])
                p_up_55   = f"{a['p_up']:.0f}%"
                lines2.append(
                    f'  {dir_ic_55} {a["symbol"]}: {dir_ar_55} ({C(p_up_55)} ↑)'
                )

    # ── Ph57 — Closing Pressure (Gap Candidates) ──────────────────────────────
    if False and top_signals and closing_pres and str(_cp_date) == str(today_str):
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
    if top_signals:
        lines2 += [
            SEP,
            f'📋 {B("قواعد اليوم")}  —  حجم: {C(f"{half_kelly:.1f}%")}  •  تعرض: {C(f"{exposure:.0f}%")}  •  ثقة دنيا: {C(min_conv_ar)}',
            f'  تجنب: الأسهم المُعلَّقة + نظام الهبوط',
        ]
    else:
        lines2 += [
            SEP,
            f'📋 {B("سياسة اليوم")}',
            f'  لا دخول جديد بدون إشارة نهائية مؤكدة لنفس التاريخ.',
        ]

    # ── Alpha Ranker Top Picks (Ph70) ─────────────────────────────────────────
    if top_signals and _alpha_top:
        _alpha_client = [
            a for a in _alpha_top
            if (a.get('expectancy_pct') or 0) > 0 and (a.get('oos_score') or 0) >= 0.8
        ]
        if _alpha_client:
            lines2.append(SEP)
            lines2.append(f'🏆 {B("أعلى قوانين التداول (ألفا)")}  {I("(EV موجب + ثبات OOS)")}')
        for _ap in _alpha_client[:2]:
            # hyp_name is snake_case — convert to readable title
            _raw_name = str(_ap.get('symbol', '?'))
            _ap_sym  = esc(_raw_name.replace('_', ' ').title())
            _ap_gr   = str(_ap.get('grade', '?'))
            _ap_ev   = _ap.get('expectancy_pct') or 0
            _ap_oos  = _ap.get('oos_score') or 0
            _ap_gr_ic = '⭐' if _ap_gr == 'A' else '✅'
            lines2.append(
                f'  {_ap_gr_ic} {B(_ap_sym)} [{_ap_gr}]  EV:{C(f"{_ap_ev:.1f}%")}  OOS:{C(f"{_ap_oos:.2f}")}'
            )

    # ── Pipeline Health Snapshot ───────────────────────────────────────────────
    _tb_wr   = _live_engines.get('tb_win_rate')    if _live_engines else None
    _sb_prom = _live_engines.get('sb_promoted', 0) if _live_engines else 0
    _sb_tot  = _live_engines.get('sb_total', 0)    if _live_engines else 0
    _ses_ph  = _live_engines.get('session_phase','') if _live_engines else ''
    _em_preds = _live_engines.get('em_n_preds', 0)  if _live_engines else 0
    _em_auc   = _live_engines.get('em_auc', 0)      if _live_engines else 0
    _ea_rsi70 = _live_engines.get('ea_rsi_above70', 0) if _live_engines else 0
    _ea_rsi30 = _live_engines.get('ea_rsi_below30', 0) if _live_engines else 0
    _rg_act   = _live_engines.get('rg_active', 0)   if _live_engines else 0
    _rg_unt   = _live_engines.get('rg_untested', 0) if _live_engines else 0
    _SES_AR   = {'PRE': 'قبل الافتتاح', 'OPEN': 'مفتوح', 'CONTINUOUS': 'مستمر',
                 'CLOSING': 'إغلاق', 'CLOSED': 'مغلق', 'POST': 'ما بعد الإغلاق'}
    _ses_ar   = _SES_AR.get(_ses_ph, _ses_ph)
    _ph_parts = []
    if _tb_wr is not None:
        _tb_ic = '🟢' if _tb_wr >= 0.45 else ('🟠' if _tb_wr >= 0.38 else '🔴')
        _ph_parts.append(f'{_tb_ic} نسبة الفوز: {C(f"{_tb_wr:.0%}")}')
    if False and top_signals and _em_preds > 0:
        _auc_str = f'  AUC:{_em_auc:.2f}' if _em_auc else ''
        _ph_parts.append(f'💥 توقعات ML: {C(str(_em_preds))}{_auc_str}')
    if _ea_rsi70 > 0:
        _ph_parts.append(f'🔴 ذروة شراء: {C(str(_ea_rsi70))} سهم')
    if _ea_rsi30 > 0:
        _ph_parts.append(f'🟢 ذروة بيع: {C(str(_ea_rsi30))} سهم')
    if _sb_tot > 0:
        _ph_parts.append(f'🧪 فرضيات: {C(f"{_sb_prom}/{_sb_tot}")}')
    if False and has_client_signals and _ph_parts:
        lines2 += [SEP, '⚙️ ' + B('حالة الأنظمة') + '  ' + '  •  '.join(_ph_parts)]

    # Risk disclaimer — mandatory for client-facing institutional content
    disclaimer = f'⚠️ {I("للأغراض المعلوماتية فقط · ليست نصيحة استثمارية · الأداء السابق لا يضمن المستقبل")}'
    lines2 += [SEP, disclaimer]

    msg2 = '\n'.join(lines2)

    # Telegram hard limit: 4096 UTF-16 code units.
    # If MSG2 exceeds 4090 chars, trim body sections (not disclaimer/header) from the bottom.
    MAX_MSG_LEN = 4090
    if len(msg2) > MAX_MSG_LEN:
        # Rebuild without overflowing: drop lines from the bottom until under limit
        trimmed = lines2[:]
        while trimmed and len('\n'.join(trimmed)) > MAX_MSG_LEN - 50:
            # Find last non-SEP, non-disclaimer line and remove it
            for i in range(len(trimmed) - 1, -1, -1):
                if trimmed[i] not in (SEP, disclaimer) and trimmed[i].strip():
                    trimmed.pop(i)
                    break
            else:
                break
        trimmed += [SEP, disclaimer]
        msg2 = '\n'.join(trimmed)

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

def cmd_format_daily(db, data, cur_ind, macro, params=None):
    params = params or {}
    pipe_log   = load_json_log(str(DATA / 'pipeline_log.json'))
    today_str  = params.get('report_date') or params.get('date') or time.strftime('%Y-%m-%d')
    messages   = build_daily_messages(
        db, data, cur_ind, macro, pipe_log, report_date=today_str, fmt_params=params,
    )
    final_actionable_count = _count_final_actionable(db, today_str)
    top_sigs   = _get_top_signals(db, today_str, top_n=10)
    stk_forecast = _get_stock_forecast(db, [s['symbol'] for s in top_sigs if s.get('symbol')])
    if stk_forecast and stk_forecast.get('signal_alignment'):
        bearish = {
            sa['symbol'] for sa in stk_forecast['signal_alignment']
            if sa.get('direction') == 'DOWN'
        }
        top_sigs = [s for s in top_sigs if s.get('symbol') not in bearish]
    client_actionable_count = len(top_sigs) if final_actionable_count > 0 else 0
    top_symbols = [s['symbol'] for s in top_sigs if s.get('symbol')] if client_actionable_count > 0 else []
    formatter_diagnostics = _diagnose_actionable_filter(db, today_str)
    formatter_diagnostics['formatter_top_n'] = client_actionable_count
    if formatter_diagnostics['db_actionable'] > client_actionable_count:
        formatter_diagnostics['warning'] = (
            f"{formatter_diagnostics['db_actionable']} actionable in DB but "
            f"{client_actionable_count} in client message"
        )
    return {
        'messages':     messages,
        'n_messages':   len(messages),
        'total_chars':  sum(len(m) for m in messages),
        'date':         today_str,
        'time':         time.strftime('%H:%M'),
        'final_actionable_count': client_actionable_count,
        'top_symbols':  top_symbols,    # Ph30 — for TV MCP live price validation
        'formatter_diagnostics': formatter_diagnostics,
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
    result = cmd_format_daily(db, data, cur_ind, macro, PARAMS)
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

    global PARAMS
    PARAMS = {}
    try:
        if len(sys.argv) > 2:
            PARAMS = json.loads(sys.argv[2] or '{}')
        else:
            PARAMS = json.loads(sys.stdin.read() or '{}')
    except Exception:
        PARAMS = {}

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
            'format_daily':   lambda: cmd_format_daily(db, data, cur_ind, macro, PARAMS),
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
