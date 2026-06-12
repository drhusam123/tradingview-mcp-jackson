#!/usr/bin/env python3
"""
Phase 68 — Hypothesis DSL Engine
"محرك فرضيات ذكي — يولّد ويخزّن ويقيّم فرضيات السوق تلقائياً"

Commands: generate | add | list | evaluate | describe | build_full
"""
import sys, json, sqlite3, hashlib, itertools
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

# ── Features available in indicators_cache (non-null rate > 80%) ────────────
AVAILABLE_FEATURES = {
    'rsi14':        {'type': 'float', 'range': (0, 100),   'desc': 'RSI(14)'},
    'macd_hist':    {'type': 'float', 'range': (-10, 10),  'desc': 'MACD Histogram'},
    'vol_ratio_20': {'type': 'float', 'range': (0, 10),    'desc': 'Volume / 20d avg'},
    'adx14':        {'type': 'float', 'range': (0, 100),   'desc': 'ADX Trend Strength'},
    'momentum_5d':  {'type': 'float', 'range': (-30, 30),  'desc': '5d Price Momentum %'},
    'momentum_20d': {'type': 'float', 'range': (-50, 50),  'desc': '20d Price Momentum %'},
    'close_position': {'type': 'float', 'range': (0, 1),   'desc': 'Candle Close Position'},
    'price_vs_ath': {'type': 'float', 'range': (0, 1),     'desc': 'Price vs All-Time-High'},
    'cci20':        {'type': 'float', 'range': (-300, 300), 'desc': 'CCI(20)'},
    'atr14':        {'type': 'float', 'range': (0, 50),    'desc': 'ATR(14) absolute'},
}

# ── Hypothesis templates with known EGX edge ─────────────────────────────────
HYPOTHESIS_TEMPLATES = [
    # Compression → Expansion (RSI oversold + low momentum + volume surge)
    {
        'name': 'compression_breakout',
        'conditions': [
            {'col': 'rsi14',        'op': '<',  'val': 35.0},
            {'col': 'momentum_5d',  'op': '>',  'val': -5.0},
            {'col': 'vol_ratio_20', 'op': '>',  'val': 1.5},
        ],
        'direction': 'LONG', 'holding_days': 5, 'category': 'explosion',
    },
    # Momentum Continuation (RSI neutral + strong 20d + volume)
    {
        'name': 'momentum_continuation',
        'conditions': [
            {'col': 'rsi14',        'op': '>',  'val': 50.0},
            {'col': 'rsi14',        'op': '<',  'val': 70.0},
            {'col': 'momentum_20d', 'op': '>',  'val': 5.0},
            {'col': 'vol_ratio_20', 'op': '>',  'val': 1.2},
        ],
        'direction': 'LONG', 'holding_days': 10, 'category': 'trend',
    },
    # Oversold Reversal (deep RSI + MACD turning)
    {
        'name': 'oversold_reversal',
        'conditions': [
            {'col': 'rsi14',       'op': '<',  'val': 30.0},
            {'col': 'macd_hist',   'op': '>',  'val': 0.0},
            {'col': 'adx14',       'op': '<',  'val': 30.0},
        ],
        'direction': 'LONG', 'holding_days': 5, 'category': 'reversal',
    },
    # Trend Strength Entry (high ADX + RSI mid + positive MACD)
    {
        'name': 'strong_trend_entry',
        'conditions': [
            {'col': 'adx14',       'op': '>',  'val': 25.0},
            {'col': 'rsi14',       'op': '>',  'val': 45.0},
            {'col': 'rsi14',       'op': '<',  'val': 65.0},
            {'col': 'macd_hist',   'op': '>',  'val': 0.0},
        ],
        'direction': 'LONG', 'holding_days': 10, 'category': 'trend',
    },
    # Volume Spike + Positive Close (accumulation signal)
    {
        'name': 'volume_spike_accumulation',
        'conditions': [
            {'col': 'vol_ratio_20',  'op': '>',  'val': 2.0},
            {'col': 'close_position','op': '>',  'val': 0.6},
            {'col': 'rsi14',         'op': '>',  'val': 40.0},
        ],
        'direction': 'LONG', 'holding_days': 5, 'category': 'volume',
    },
    # Overbought Distribution (RSI high + weak volume + negative MACD)
    {
        'name': 'overbought_distribution',
        'conditions': [
            {'col': 'rsi14',       'op': '>',  'val': 70.0},
            {'col': 'macd_hist',   'op': '<',  'val': 0.0},
            {'col': 'vol_ratio_20','op': '<',  'val': 0.8},
        ],
        'direction': 'SHORT', 'holding_days': 5, 'category': 'reversal',
    },
    # ATH Proximity Breakout (near ATH + volume + momentum)
    {
        'name': 'ath_breakout',
        'conditions': [
            {'col': 'price_vs_ath', 'op': '>',  'val': 0.90},
            {'col': 'vol_ratio_20', 'op': '>',  'val': 1.5},
            {'col': 'momentum_5d',  'op': '>',  'val': 2.0},
        ],
        'direction': 'LONG', 'holding_days': 10, 'category': 'breakout',
    },
    # Silent Accumulation (low volume + RSI recovering + MACD crossing)
    {
        'name': 'silent_accumulation',
        'conditions': [
            {'col': 'rsi14',       'op': '>',  'val': 35.0},
            {'col': 'rsi14',       'op': '<',  'val': 55.0},
            {'col': 'macd_hist',   'op': '>',  'val': 0.0},
            {'col': 'momentum_20d','op': '<',  'val': -5.0},
        ],
        'direction': 'LONG', 'holding_days': 15, 'category': 'accumulation',
    },
    # Breadth Divergence Pullback (stock down, market up)
    {
        'name': 'pullback_in_uptrend',
        'conditions': [
            {'col': 'momentum_5d',  'op': '<',  'val': -3.0},
            {'col': 'momentum_20d', 'op': '>',  'val': 5.0},
            {'col': 'rsi14',        'op': '<',  'val': 50.0},
            {'col': 'rsi14',        'op': '>',  'val': 30.0},
        ],
        'direction': 'LONG', 'holding_days': 5, 'category': 'pullback',
    },
    # CCI Extreme Oversold (panic selling)
    {
        'name': 'cci_panic_reversal',
        'conditions': [
            {'col': 'cci20',       'op': '<',  'val': -150.0},
            {'col': 'vol_ratio_20','op': '>',  'val': 1.3},
            {'col': 'macd_hist',   'op': '>',  'val': -0.5},
        ],
        'direction': 'LONG', 'holding_days': 5, 'category': 'reversal',
    },
]

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS hypotheses (
        hyp_id       TEXT PRIMARY KEY,
        hyp_name     TEXT,
        category     TEXT,
        conditions_json TEXT NOT NULL,
        direction    TEXT DEFAULT 'LONG',
        holding_days INTEGER DEFAULT 5,
        regime_filter TEXT,
        source       TEXT DEFAULT 'auto',
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        description  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_hyp_category ON hypotheses(category);
    """)
    conn.commit()

def make_hyp_id(conditions, direction, holding_days):
    key = json.dumps({'c': conditions, 'd': direction, 'h': holding_days}, sort_keys=True)
    return 'HYP_' + hashlib.sha1(key.encode()).hexdigest()[:10].upper()

def describe_hypothesis(hyp):
    """Human-readable description of a hypothesis."""
    conds = hyp.get('conditions', [])
    lines = []
    for c in conds:
        col   = c['col']
        op    = c['op']
        val   = c['val']
        fname = AVAILABLE_FEATURES.get(col, {}).get('desc', col)
        lines.append(f"  {fname} {op} {val}")
    dir_  = hyp.get('direction', 'LONG')
    days  = hyp.get('holding_days', 5)
    name  = hyp.get('hyp_name') or hyp.get('name', '?')
    return f"[{name}] {dir_} {days}d:\n" + "\n".join(lines)

# ─────────────────────────────────────────────
# Generate hypotheses (templates + auto-combos)
# ─────────────────────────────────────────────
def generate(params):
    mode      = params.get('mode', 'templates')  # 'templates' | 'auto' | 'both'
    n_auto    = params.get('n_auto', 50)
    conn      = db()
    ensure_tables(conn)

    inserted = 0
    hyps_out = []

    # 1. Template hypotheses
    if mode in ('templates', 'both'):
        for tmpl in HYPOTHESIS_TEMPLATES:
            conds  = tmpl['conditions']
            hyp_id = make_hyp_id(conds, tmpl['direction'], tmpl['holding_days'])
            conn.execute("""
                INSERT OR IGNORE INTO hypotheses
                (hyp_id, hyp_name, category, conditions_json, direction, holding_days, source, description)
                VALUES (?,?,?,?,?,?,?,?)
            """, (hyp_id, tmpl['name'], tmpl.get('category','general'),
                  json.dumps(conds), tmpl['direction'], tmpl['holding_days'],
                  'template', describe_hypothesis({**tmpl, 'hyp_name': tmpl['name']})))
            cur = conn.execute("SELECT changes()")
            inserted += cur.fetchone()[0]
            hyps_out.append({'hyp_id': hyp_id, 'name': tmpl['name'], 'source': 'template'})

    # 2. Auto-generate from feature grid
    if mode in ('auto', 'both'):
        features    = list(AVAILABLE_FEATURES.keys())
        thresholds  = {
            'rsi14':         [25, 30, 35, 45, 55, 65, 70, 75],
            'macd_hist':     [-0.5, 0.0, 0.5],
            'vol_ratio_20':  [1.2, 1.5, 2.0, 2.5],
            'adx14':         [20, 25, 30, 40],
            'momentum_5d':   [-5, -2, 0, 2, 5],
            'momentum_20d':  [-10, -5, 0, 5, 10, 20],
            'close_position':  [0.5, 0.6, 0.7],
            'price_vs_ath':  [0.80, 0.90, 0.95],
            'cci20':         [-200, -100, 100, 200],
        }
        ops = ['<', '>']
        combos_tried = 0
        for feat in ['rsi14', 'momentum_5d', 'vol_ratio_20', 'macd_hist', 'adx14']:
            for thresh in thresholds.get(feat, []):
                for op in ops:
                    for feat2 in ['vol_ratio_20', 'macd_hist', 'momentum_20d', 'adx14']:
                        if feat2 == feat: continue
                        for thresh2 in thresholds.get(feat2, [])[:3]:
                            for op2 in ops:
                                if combos_tried >= n_auto: break
                                conds = [
                                    {'col': feat,  'op': op,  'val': thresh},
                                    {'col': feat2, 'op': op2, 'val': thresh2},
                                ]
                                hyp_id = make_hyp_id(conds, 'LONG', 5)
                                name   = f'auto_{feat}_{op}{thresh}_{feat2}_{op2}{thresh2}'
                                conn.execute("""
                                    INSERT OR IGNORE INTO hypotheses
                                    (hyp_id, hyp_name, category, conditions_json, direction, holding_days, source)
                                    VALUES (?,?,?,?,?,?,?)
                                """, (hyp_id, name, 'auto',
                                      json.dumps(conds), 'LONG', 5, 'auto'))
                                cur2 = conn.execute("SELECT changes()")
                                inserted += cur2.fetchone()[0]
                                hyps_out.append({'hyp_id': hyp_id, 'name': name, 'source': 'auto'})
                                combos_tried += 1

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    conn.close()

    return {
        'success': True,
        'mode': mode,
        'n_inserted': inserted,
        'total_hypotheses': total,
        'sample': hyps_out[:10],
    }

# ─────────────────────────────────────────────
# List stored hypotheses
# ─────────────────────────────────────────────
def list_hypotheses(params):
    category = params.get('category', None)
    source   = params.get('source', None)
    limit    = params.get('limit', 30)
    conn     = db()
    ensure_tables(conn)

    q  = "SELECT hyp_id, hyp_name, category, direction, holding_days, source, created_at FROM hypotheses"
    wh = []
    ar = []
    if category: wh.append("category=?"); ar.append(category)
    if source:   wh.append("source=?");   ar.append(source)
    if wh: q += " WHERE " + " AND ".join(wh)
    q += f" ORDER BY created_at DESC LIMIT {int(limit)}"

    rows = conn.execute(q, ar).fetchall()
    conn.close()

    return {
        'success': True,
        'n_hypotheses': len(rows),
        'hypotheses': [dict(r) for r in rows],
    }

# ─────────────────────────────────────────────
# Add manual hypothesis
# ─────────────────────────────────────────────
def add_hypothesis(params):
    conditions   = params.get('conditions', [])
    direction    = params.get('direction', 'LONG')
    holding_days = int(params.get('holding_days', 5))
    name         = params.get('name', 'manual')
    category     = params.get('category', 'manual')
    regime_filter= params.get('regime_filter', None)

    if not conditions:
        return {"success": False, "error": "conditions required"}

    hyp_id = make_hyp_id(conditions, direction, holding_days)
    desc   = describe_hypothesis({'conditions': conditions, 'direction': direction,
                                  'holding_days': holding_days, 'hyp_name': name})
    conn = db()
    ensure_tables(conn)
    conn.execute("""
        INSERT OR REPLACE INTO hypotheses
        (hyp_id, hyp_name, category, conditions_json, direction, holding_days,
         regime_filter, source, description)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (hyp_id, name, category, json.dumps(conditions), direction,
          holding_days, regime_filter, 'manual', desc))
    conn.commit()
    conn.close()

    return {'success': True, 'hyp_id': hyp_id, 'description': desc}

# ─────────────────────────────────────────────
# Evaluate a single hypothesis (quick check)
# ─────────────────────────────────────────────
def evaluate(params):
    hyp_id = params.get('hyp_id', None)
    conn   = db()
    ensure_tables(conn)

    if hyp_id:
        row = conn.execute("SELECT * FROM hypotheses WHERE hyp_id=?", (hyp_id,)).fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": f"Hypothesis {hyp_id} not found"}
        hyp = dict(row)
        hyp['conditions'] = json.loads(hyp['conditions_json'])
    else:
        hyp = params

    conditions   = hyp.get('conditions', [])
    direction    = hyp.get('direction', 'LONG')
    holding_days = int(hyp.get('holding_days', 5))

    # Build SQL WHERE clause
    where_parts = []
    where_vals  = []
    for c in conditions:
        col = c['col']
        op  = c['op']
        val = c['val']
        if col not in AVAILABLE_FEATURES:
            continue
        if op not in ('<', '>', '<=', '>=', '==', '!='):
            continue
        sql_op = '=' if op == '==' else op
        where_parts.append(f"ic.{col} {sql_op} ?")
        where_vals.append(float(val))

    if not where_parts:
        conn.close()
        return {"success": False, "error": "No valid conditions"}

    # Find activations in indicators_cache
    where_sql = " AND ".join(where_parts) + " AND " + " AND ".join(
        f"ic.{c['col']} IS NOT NULL" for c in conditions if c['col'] in AVAILABLE_FEATURES
    )

    activation_query = f"""
        SELECT ic.symbol, ic.bar_date,
               o_entry.close as entry_close,
               o_exit.close  as exit_close
        FROM indicators_cache ic
        JOIN ohlcv_history_execution o_entry ON (
            o_entry.symbol = ic.symbol
            AND date(o_entry.bar_time, 'unixepoch') = ic.bar_date
        )
        LEFT JOIN ohlcv_history_execution o_exit ON (
            o_exit.symbol = ic.symbol
            AND o_exit.bar_time = (
                SELECT bar_time FROM ohlcv_history_execution
                WHERE symbol = ic.symbol
                  AND bar_time > o_entry.bar_time
                ORDER BY bar_time
                LIMIT 1 OFFSET {holding_days - 1}
            )
        )
        WHERE {where_sql}
        ORDER BY ic.bar_date
        LIMIT 2000
    """

    try:
        rows = conn.execute(activation_query, where_vals).fetchall()
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e), "query": activation_query[:200]}

    # Compute metrics
    n_total = 0
    n_hits  = 0
    returns = []
    is_hits = 0; is_total = 0
    oos_hits= 0; oos_total = 0

    COST_BPS = 150  # EGX avg round-trip cost (commission + spread)

    for r in rows:
        if r['entry_close'] and r['exit_close'] and r['entry_close'] > 0:
            gross_ret = (r['exit_close'] / r['entry_close'] - 1.0) * 100
            if direction == 'SHORT':
                gross_ret = -gross_ret
            net_ret = gross_ret - (COST_BPS / 100)
            returns.append(net_ret)
            win = net_ret > 0
            n_total += 1
            if win: n_hits += 1
            if r['bar_date'] < '2024-01-01':
                is_total += 1
                if win: is_hits += 1
            else:
                oos_total += 1
                if win: oos_hits += 1

    conn.close()

    if n_total == 0:
        return {
            "success": True,
            "hyp_id": hyp_id,
            "n_activations": 0,
            "message": "No activations found — conditions too strict or data missing",
        }

    precision   = n_hits / n_total
    avg_ret     = sum(returns) / len(returns)
    wins        = [r for r in returns if r > 0]
    losses      = [r for r in returns if r <= 0]
    avg_win     = sum(wins)/len(wins)   if wins   else 0
    avg_loss    = sum(losses)/len(losses) if losses else 0
    expectancy  = (precision * avg_win) + ((1-precision) * avg_loss)
    is_prec     = is_hits  / is_total  if is_total  > 0 else None
    oos_prec    = oos_hits / oos_total if oos_total > 0 else None
    oos_score   = (oos_prec / is_prec) if (is_prec and oos_prec) else None

    return {
        "success":       True,
        "hyp_id":        hyp_id or 'inline',
        "n_activations": n_total,
        "n_hits":        n_hits,
        "precision":     round(precision, 4),
        "win_rate_pct":  round(precision * 100, 1),
        "avg_net_return_pct": round(avg_ret, 3),
        "avg_win_pct":   round(avg_win, 3),
        "avg_loss_pct":  round(avg_loss, 3),
        "expectancy_pct":round(expectancy, 3),
        "is_precision":  round(is_prec, 4)  if is_prec   else None,
        "oos_precision": round(oos_prec, 4) if oos_prec  else None,
        "oos_score":     round(oos_score, 3) if oos_score else None,
        "is_samples":    is_total,
        "oos_samples":   oos_total,
        "description":   describe_hypothesis({**hyp, 'hyp_name': hyp.get('hyp_name', hyp_id)}),
    }

# ─────────────────────────────────────────────
# Build full
# ─────────────────────────────────────────────
def build_full(params):
    gen  = generate({'mode': 'templates'})
    lst  = list_hypotheses({'limit': 5})
    return {
        "success": True,
        "hypotheses_generated": gen['n_inserted'],
        "total_hypotheses":     gen['total_hypotheses'],
        "sample":               lst['hypotheses'][:5],
        "next": "Run research_grid.py run_grid to test all hypotheses",
    }

# ─────────────────────────────────────────────
if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'build_full'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    dispatch = {
        'generate':         generate,
        'list_hypotheses':  list_hypotheses,
        'add_hypothesis':   add_hypothesis,
        'evaluate':         evaluate,
        'build_full':       build_full,
    }

    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(params), default=str))
    else:
        print(json.dumps({"error": f"Unknown: {cmd}", "available": list(dispatch.keys())}))
