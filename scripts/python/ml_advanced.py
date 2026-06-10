#!/usr/bin/env python3
"""
ML Advanced Suite — 11 upgrades (López de Prado stack + EGX-specific intelligence)
===================================================================================
 1. Triple-Barrier events + Meta-Labeling model      (build_events / meta_train / meta_score)
 2. Purged K-Fold CV + Embargo                       (purged_cv — honest AUC)
 3. Deflated Sharpe Ratio + PBO (CSCV)               (dsr_pbo — vets quant_discovery rules)
 4. Gate Shadow Book + Thompson Sampling             (shadow_update / shadow_outcomes / thompson)
 5. Lead-Lag Network pulse                           (leadlag — consumes stock_lead_lag)
 6. DOM Microstructure features                      (dom_features)
 7. Mixture-of-Experts by regime                     (trained in meta_train, scored in meta_score)
 8. Survival Analysis — competing risks Cox          (survival_train / survival_score)
 9. Pattern Embeddings (random-kernel ROCKET + kNN)  (embed_build / embed_score)
10. Conformal (Venn-ABERS) win-prob bounds           (conformal)
11. Drift Detection / Adversarial Validation         (drift)

Orchestration:
    python3 ml_advanced.py weekly   — heavy training set   (cron: weekly)
    python3 ml_advanced.py daily    — daily scoring set    (pipeline: before score_all)
    python3 ml_advanced.py status   — dashboard
    python3 ml_advanced.py selftest — end-to-end test of every component
"""
import os, sys, json, math, sqlite3, datetime, pickle, warnings
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, 'data', 'egx_trading.db')
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models', 'ml_advanced')
os.makedirs(MODELS_DIR, exist_ok=True)

HORIZON = 7          # triple-barrier max holding (bars)
EMBARGO_DAYS = 10    # purged CV embargo (trading bars ≈ 14 calendar days)
WINDOW = 40          # embedding window (bars)
SEED = 42

META_FEATURES = [
    'vol_ratio', 'ret1', 'ret3', 'ret5', 'ret20', 'atr_pct', 'range_pct',
    'close_pos', 'rsi14', 'dist_ath300', 'hi20_break', 'bb_pct',
    'ema20_dist', 'ema50_dist', 'vol_std20', 'dow', 'is_bull', 'is_bear',
    'mom_consist', 'vol_trend5',
]


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, default=str), flush=True)


def log_run(conn, command, payload):
    conn.execute(
        "INSERT INTO ml_adv_runs(run_date, command, payload_json) VALUES (?,?,?)",
        (datetime.date.today().isoformat(), command, json.dumps(payload, default=str)))
    conn.commit()


def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ml_adv_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT, command TEXT, payload_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ml_adv_events (
        symbol TEXT, date TEXT, label INTEGER, barrier TEXT, t_days INTEGER,
        fwd5 REAL, mfe5 REAL, mae5 REAL, features_json TEXT,
        PRIMARY KEY (symbol, date)
    );
    CREATE TABLE IF NOT EXISTS meta_label_scores (
        symbol TEXT, date TEXT, meta_prob REAL, moe_prob REAL,
        regime_weights TEXT, size_frac REAL,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (symbol, date)
    );
    CREATE TABLE IF NOT EXISTS gate_shadow_book (
        trade_date TEXT, symbol TEXT, gate TEXT, ues REAL, ml REAL,
        vol_ratio REAL, entry_close REAL,
        win INTEGER, ret5 REAL, mfe5 REAL, mae5 REAL, outcome_date TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (trade_date, symbol)
    );
    CREATE TABLE IF NOT EXISTS pattern_analogs (
        symbol TEXT, date TEXT, analog_wr REAL, analog_n INTEGER,
        avg_fwd5 REAL, top_analogs TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (symbol, date)
    );
    CREATE TABLE IF NOT EXISTS conformal_scores (
        symbol TEXT, date TEXT, p_lo REAL, p_hi REAL, confident INTEGER,
        cal_n INTEGER, created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (symbol, date)
    );
    CREATE TABLE IF NOT EXISTS survival_exit_profile (
        symbol TEXT, date TEXT, p_tp_first REAL, p_sl_first REAL,
        expected_days_tp REAL, hold_days INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (symbol, date)
    );
    CREATE TABLE IF NOT EXISTS adaptive_gate_params (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        param_name TEXT NOT NULL,
        param_value REAL NOT NULL,
        basis TEXT,
        n_obs INTEGER DEFAULT 0,
        confidence REAL DEFAULT 0.5,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_adg_date ON adaptive_gate_params(run_date);
    """)
    for col, typ in (('dsr', 'REAL'), ('oos_rank_med', 'REAL'), ('vetted', 'INTEGER')):
        try:
            conn.execute(f"ALTER TABLE quant_discovery_rules ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Shared feature pipeline (events, meta scoring, drift — single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

def _load_ohlcv_df(conn):
    df = pd.read_sql_query(
        "SELECT symbol, date, open, high, low, close, volume FROM ohlcv "
        "WHERE close > 0 ORDER BY symbol, date", conn)
    df['date'] = df['date'].astype(str)
    return df


def _rsi(close, period=14):
    d = close.diff()
    gain = d.clip(lower=0).rolling(period).mean()
    loss = (-d.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _regime_map(conn):
    """date → (is_bull, is_bear). Markov first, regime_history fallback."""
    m = {}
    try:
        for r in conn.execute("SELECT date, state_base FROM markov_regime_daily"):
            s = (r['state_base'] or '').upper()
            m[r['date']] = (1 if 'BULL' in s else 0, 1 if 'BEAR' in s else 0)
    except Exception:
        pass
    try:
        for r in conn.execute("SELECT date, regime FROM regime_history"):
            if r['date'] not in m:
                s = (r['regime'] or '').upper()
                m[r['date']] = (1 if 'BULL' in s else 0, 1 if 'BEAR' in s else 0)
    except Exception:
        pass
    return m


def build_feature_frame(conn, df=None):
    """Vectorized per-symbol features. Median volume baseline (pump-robust)."""
    if df is None:
        df = _load_ohlcv_df(conn)
    regimes = _regime_map(conn)
    frames = []
    for sym, g in df.groupby('symbol', sort=False):
        g = g.sort_values('date').reset_index(drop=True)
        if len(g) < 60:
            continue
        c, h, l, v = g['close'], g['high'], g['low'], g['volume']
        f = pd.DataFrame({'symbol': sym, 'date': g['date']})
        vol_med20 = v.shift(1).rolling(20).median()
        f['vol_ratio'] = (v / vol_med20.replace(0, np.nan)).clip(0, 30)
        f['ret1'] = c.pct_change(1)
        f['ret3'] = c.pct_change(3)
        f['ret5'] = c.pct_change(5)
        f['ret20'] = c.pct_change(20)
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        f['atr_pct'] = (tr.rolling(14).mean() / c).clip(0, 0.5)
        f['range_pct'] = ((h - l) / c).clip(0, 0.6)
        rng = (h - l).replace(0, np.nan)
        f['close_pos'] = ((c - l) / rng).fillna(0.5)
        f['rsi14'] = _rsi(c)
        ath = h.rolling(300, min_periods=60).max()
        f['dist_ath300'] = ((ath - c) / ath).clip(0, 1)
        f['hi20_break'] = (c >= h.shift(1).rolling(20).max() * 0.995).astype(int)
        sd20 = c.rolling(20).std()
        m20 = c.rolling(20).mean()
        bbw = (4 * sd20 / m20).replace([np.inf, -np.inf], np.nan)
        f['bb_pct'] = bbw.rolling(80, min_periods=30).rank(pct=True)
        f['ema20_dist'] = (c / c.ewm(span=20).mean() - 1).clip(-0.5, 0.5)
        f['ema50_dist'] = (c / c.ewm(span=50).mean() - 1).clip(-0.5, 0.5)
        f['vol_std20'] = c.pct_change().rolling(20).std().clip(0, 0.3)
        f['vol_trend5'] = (v.rolling(5).mean() / vol_med20.replace(0, np.nan)).clip(0, 20)
        f['mom_consist'] = (c.pct_change() > 0).rolling(5).mean()
        f['dow'] = pd.to_datetime(g['date']).dt.dayofweek
        f['close'] = c
        f['high'] = h
        f['low'] = l
        # data-integrity guard: split/unit jumps poison labels
        f['bad_jump'] = (f['ret1'].abs() > 0.45).rolling(6, min_periods=1).max()
        frames.append(f)
    feat = pd.concat(frames, ignore_index=True)
    rb = feat['date'].map(lambda d: regimes.get(d, (0, 0)))
    feat['is_bull'] = [x[0] for x in rb]
    feat['is_bear'] = [x[1] for x in rb]
    return feat


def _triple_barrier_label(g, i, horizon=HORIZON):
    """First-touch triple barrier from bar i (entry at close). Returns dict or None."""
    entry = g['close'].iat[i]
    atr_pct = g['atr_pct'].iat[i]
    if not entry or entry <= 0 or not np.isfinite(atr_pct):
        return None
    tp = entry * (1 + max(0.05, 1.6 * atr_pct))
    sl = max(g['low'].iat[i] * 0.995, entry * 0.92)
    n = len(g)
    end = min(i + horizon, n - 1)
    if end <= i:
        return None
    label, barrier, t_days = None, 'time', end - i
    for k in range(i + 1, end + 1):
        if g['low'].iat[k] <= sl:
            label, barrier, t_days = 0, 'sl', k - i
            break
        if g['high'].iat[k] >= tp:
            label, barrier, t_days = 1, 'tp', k - i
            break
    if label is None:
        ret_h = g['close'].iat[end] / entry - 1
        label = 1 if ret_h >= 0.02 else 0
    e5 = min(i + 5, n - 1)
    fwd5 = g['close'].iat[e5] / entry - 1
    mfe5 = g['high'].iloc[i + 1:e5 + 1].max() / entry - 1 if e5 > i else 0.0
    mae5 = g['low'].iloc[i + 1:e5 + 1].min() / entry - 1 if e5 > i else 0.0
    if abs(fwd5) > 0.45 or mfe5 > 0.6 or mae5 < -0.45:
        return None
    return {'label': int(label), 'barrier': barrier, 't_days': int(t_days),
            'fwd5': round(fwd5, 5), 'mfe5': round(mfe5, 5), 'mae5': round(mae5, 5)}


def cmd_build_events():
    """#1 — signal-like historical events + triple-barrier outcomes."""
    conn = get_db()
    ensure_tables(conn)
    feat = build_feature_frame(conn)
    rows = []
    for sym, g in feat.groupby('symbol', sort=False):
        g = g.reset_index(drop=True)
        trig = ((g['vol_ratio'] >= 1.3) & (g['range_pct'] >= 0.025) &
                ((g['close_pos'] >= 0.55) | (g['hi20_break'] == 1)) &
                (g['bad_jump'] < 1) & (g['close'] > 0.2) &
                g['rsi14'].notna() & g['vol_ratio'].notna())
        idxs = np.where(trig.values)[0]
        for i in idxs:
            if i < 40 or i >= len(g) - 1:
                continue
            lab = _triple_barrier_label(g, i)
            if lab is None:
                continue
            fv = {k: (None if pd.isna(g[k].iat[i]) else round(float(g[k].iat[i]), 6))
                  for k in META_FEATURES}
            rows.append((sym, g['date'].iat[i], lab['label'], lab['barrier'], lab['t_days'],
                         lab['fwd5'], lab['mfe5'], lab['mae5'], json.dumps(fv)))
    conn.execute("DELETE FROM ml_adv_events")
    conn.executemany(
        "INSERT OR REPLACE INTO ml_adv_events VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    n_pos = sum(1 for r in rows if r[2] == 1)
    res = {'cmd': 'build_events', 'n_events': len(rows), 'n_pos': n_pos,
           'base_rate': round(n_pos / max(len(rows), 1), 4)}
    log_run(conn, 'build_events', res)
    conn.close()
    out(res)
    return res


def _load_events(conn):
    df = pd.read_sql_query(
        "SELECT symbol, date, label, barrier, t_days, fwd5, features_json "
        "FROM ml_adv_events ORDER BY date", conn)
    if df.empty:
        return None, None
    X = pd.DataFrame([json.loads(s) for s in df['features_json']])[META_FEATURES]
    X = X.astype(float).fillna(0.0)
    return df, X


# ─────────────────────────────────────────────────────────────────────────────
# #2 Purged K-Fold CV + Embargo
# ─────────────────────────────────────────────────────────────────────────────

def _purged_folds(dates_sorted_unique, n_folds=5, embargo=EMBARGO_DAYS, horizon=HORIZON):
    """Contiguous calendar blocks; train excludes test ± (horizon + embargo) bars."""
    n = len(dates_sorted_unique)
    folds = []
    bounds = np.linspace(0, n, n_folds + 1).astype(int)
    for k in range(n_folds):
        te_idx = set(range(bounds[k], bounds[k + 1]))
        lo = max(0, bounds[k] - (horizon + embargo))
        hi = min(n, bounds[k + 1] + (horizon + embargo))
        excl = set(range(lo, hi))
        tr_idx = [i for i in range(n) if i not in excl]
        folds.append((tr_idx, sorted(te_idx)))
    return folds


def _lgbm(params=None):
    import lightgbm as lgb
    p = dict(objective='binary', metric='auc', verbosity=-1, seed=SEED,
             num_leaves=31, learning_rate=0.05, feature_fraction=0.8,
             bagging_fraction=0.8, bagging_freq=1, min_data_in_leaf=40)
    if params:
        p.update(params)
    return lgb, p


def cmd_purged_cv():
    """#2 — honest AUC via purged K-fold with embargo, on the event dataset
    with BOTH labels: triple-barrier (meta) and explosion-style (+5% MFE)."""
    conn = get_db()
    ensure_tables(conn)
    df, X = _load_events(conn)
    if df is None or len(df) < 500:
        out({'cmd': 'purged_cv', 'error': 'insufficient events — run build_events'})
        return
    from sklearn.metrics import roc_auc_score
    lgb, p = _lgbm()
    dates = sorted(df['date'].unique())
    d2i = {d: i for i, d in enumerate(dates)}
    di = df['date'].map(d2i).values
    results = {}
    for label_name, y in (('triple_barrier', df['label'].values),
                          ('explosion_style', (df['fwd5'] >= 0.05).astype(int).values)):
        aucs = []
        for tr_d, te_d in _purged_folds(dates):
            tr_m = np.isin(di, tr_d)
            te_m = np.isin(di, te_d)
            if tr_m.sum() < 200 or te_m.sum() < 100 or len(set(y[te_m])) < 2:
                continue
            ds = lgb.Dataset(X[tr_m], label=y[tr_m])
            mdl = lgb.train(p, ds, num_boost_round=220)
            aucs.append(roc_auc_score(y[te_m], mdl.predict(X[te_m])))
        results[label_name] = {'auc_mean': round(float(np.mean(aucs)), 4) if aucs else None,
                               'auc_std': round(float(np.std(aucs)), 4) if aucs else None,
                               'folds': len(aucs)}
    today = datetime.date.today().isoformat()
    for k, v in results.items():
        if v['auc_mean'] is not None:
            conn.execute(
                "INSERT OR REPLACE INTO feature_store(feature_date, symbol, feature_name, feature_value, version, source_table) "
                "VALUES (?,?,?,?,?,?)",
                (today, 'MARKET', f'purged_auc_{k}', v['auc_mean'], 'v1', 'ml_advanced'))
    conn.commit()
    res = {'cmd': 'purged_cv', 'n_events': len(df), 'embargo_bars': EMBARGO_DAYS, **results}
    log_run(conn, 'purged_cv', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #1 + #7 Meta-Labeler + Mixture-of-Experts
# ─────────────────────────────────────────────────────────────────────────────

def cmd_meta_train():
    conn = get_db()
    ensure_tables(conn)
    df, X = _load_events(conn)
    if df is None or len(df) < 800:
        out({'cmd': 'meta_train', 'error': 'insufficient events'})
        return
    from sklearn.metrics import roc_auc_score
    lgb, p = _lgbm()
    y = df['label'].values
    dates = sorted(df['date'].unique())
    # honest holdout: last calendar block, purged
    cut = dates[int(len(dates) * 0.85)]
    emb_lo = dates[max(0, int(len(dates) * 0.85) - (HORIZON + EMBARGO_DAYS))]
    tr_m = (df['date'] < emb_lo).values
    te_m = (df['date'] >= cut).values
    ds = lgb.Dataset(X[tr_m], label=y[tr_m])
    mdl = lgb.train(p, ds, num_boost_round=300)
    holdout_auc = roc_auc_score(y[te_m], mdl.predict(X[te_m])) if te_m.sum() > 50 else None
    # final model on all data
    final = lgb.train(p, lgb.Dataset(X, label=y), num_boost_round=300)
    final.save_model(os.path.join(MODELS_DIR, 'meta_labeler_v1.txt'))
    # MoE experts by regime at event date
    experts = {}
    reg = np.where(X['is_bull'] == 1, 'BULL', np.where(X['is_bear'] == 1, 'BEAR', 'SIDE'))
    moe_meta = {}
    for r in ('BULL', 'BEAR', 'SIDE'):
        m = reg == r
        if m.sum() >= 600 and len(set(y[m])) == 2:
            em = lgb.train(p, lgb.Dataset(X[m], label=y[m]), num_boost_round=200)
            em.save_model(os.path.join(MODELS_DIR, f'moe_{r}_v1.txt'))
            experts[r] = int(m.sum())
            moe_meta[r] = int(m.sum())
    with open(os.path.join(MODELS_DIR, 'meta_meta.json'), 'w') as f:
        json.dump({'features': META_FEATURES, 'holdout_auc': holdout_auc,
                   'trained': datetime.date.today().isoformat(),
                   'n_events': len(df), 'moe_experts': moe_meta}, f)
    res = {'cmd': 'meta_train', 'n_events': len(df),
           'holdout_auc': round(holdout_auc, 4) if holdout_auc else None,
           'moe_experts': experts}
    log_run(conn, 'meta_train', res)
    conn.close()
    out(res)
    return res


def _markov_weights(conn, date):
    try:
        r = conn.execute(
            "SELECT p_bull_1d, p_side_1d, p_bear_1d FROM markov_signal_daily "
            "WHERE date<=? ORDER BY date DESC LIMIT 1", (date,)).fetchone()
        if r:
            w = {'BULL': r['p_bull_1d'] or 0.34, 'SIDE': r['p_side_1d'] or 0.33,
                 'BEAR': r['p_bear_1d'] or 0.33}
            s = sum(w.values()) or 1.0
            return {k: v / s for k, v in w.items()}
    except Exception:
        pass
    return {'BULL': 0.34, 'SIDE': 0.33, 'BEAR': 0.33}


def cmd_meta_score(date=None):
    conn = get_db()
    ensure_tables(conn)
    import lightgbm as lgb
    mp = os.path.join(MODELS_DIR, 'meta_labeler_v1.txt')
    if not os.path.exists(mp):
        out({'cmd': 'meta_score', 'error': 'meta model missing — run meta_train'})
        return
    mdl = lgb.Booster(model_file=mp)
    experts = {}
    for r in ('BULL', 'BEAR', 'SIDE'):
        ep = os.path.join(MODELS_DIR, f'moe_{r}_v1.txt')
        if os.path.exists(ep):
            experts[r] = lgb.Booster(model_file=ep)
    feat = build_feature_frame(conn)
    date = date or feat['date'].max()
    day = feat[feat['date'] == date].copy()
    if day.empty:
        out({'cmd': 'meta_score', 'error': f'no bars for {date}'})
        return
    Xd = day[META_FEATURES].astype(float).fillna(0.0)
    meta_p = mdl.predict(Xd)
    w = _markov_weights(conn, date)
    if experts:
        moe_p = np.zeros(len(Xd))
        wsum = 0.0
        for r, em in experts.items():
            moe_p += w.get(r, 0.0) * em.predict(Xd)
            wsum += w.get(r, 0.0)
        moe_p = moe_p / max(wsum, 1e-9)
    else:
        moe_p = meta_p
    rows = []
    for i, (_, rr) in enumerate(day.iterrows()):
        size = max(0.0, min(1.0, 2 * float(meta_p[i]) - 1))  # Kelly-lite
        rows.append((rr['symbol'], date, round(float(meta_p[i]), 4),
                     round(float(moe_p[i]), 4), json.dumps(w), round(size, 3)))
    conn.executemany(
        "INSERT OR REPLACE INTO meta_label_scores(symbol, date, meta_prob, moe_prob, regime_weights, size_frac) "
        "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    res = {'cmd': 'meta_score', 'date': date, 'n_scored': len(rows),
           'avg_meta': round(float(np.mean(meta_p)), 3),
           'avg_moe': round(float(np.mean(moe_p)), 3), 'regime_weights': w}
    log_run(conn, 'meta_score', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #3 Deflated Sharpe + PBO (CSCV) for quant_discovery
# ─────────────────────────────────────────────────────────────────────────────

def cmd_dsr_pbo():
    conn = get_db()
    ensure_tables(conn)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import quant_discovery as qd
    from scipy.stats import norm
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    data = qd.load_bars(db)
    examples = qd.build_examples(data)
    atom_map = dict(qd.atoms())
    rules = conn.execute(
        "SELECT id, rule_name, conditions_json, n_oos FROM quant_discovery_rules").fetchall()
    if not rules or not examples:
        out({'cmd': 'dsr_pbo', 'error': 'no rules or examples'})
        return
    # per-rule trade series + monthly return matrix
    month_set = sorted({x['date'][:7] for x in examples})
    m2i = {m: i for i, m in enumerate(month_set)}
    rule_ids, monthly, sr_list, per_rule = [], [], [], {}
    for r in rules:
        try:
            conds = json.loads(r['conditions_json'])
            fns = [atom_map[c] for c in conds if c in atom_map]
            if len(fns) != len(conds):
                continue
            sel = [x for x in examples if all(fn(x) for fn in fns)]
            if len(sel) < 60:
                continue
            rets = np.array([x['realized'] for x in sel])
            sr = rets.mean() / (rets.std() + 1e-12)
            from scipy.stats import skew, kurtosis
            per_rule[r['id']] = {'sr': sr, 'n': len(rets),
                                 'skew': float(skew(rets)), 'kurt': float(kurtosis(rets, fisher=False))}
            mrow = np.full(len(month_set), np.nan)
            mdf = {}
            for x in sel:
                mdf.setdefault(x['date'][:7], []).append(x['realized'])
            for mth, vals in mdf.items():
                mrow[m2i[mth]] = np.mean(vals)
            rule_ids.append(r['id'])
            monthly.append(mrow)
            sr_list.append(sr)
        except Exception:
            continue
    if len(rule_ids) < 4:
        out({'cmd': 'dsr_pbo', 'error': 'too few evaluable rules'})
        return
    M = np.array(monthly)  # rules × months
    # ── DSR ──
    a = len(qd.atoms())
    n_trials = a + a * (a - 1) // 2 + 3000  # 1-cond + 2-cond + focused 3-cond space
    var_sr = float(np.var(sr_list)) or 1e-6
    gamma = 0.5772156649
    z1 = norm.ppf(1 - 1.0 / n_trials)
    z2 = norm.ppf(1 - 1.0 / (n_trials * math.e))
    sr0 = math.sqrt(var_sr) * ((1 - gamma) * z1 + gamma * z2)
    updates = []
    for rid in rule_ids:
        pr = per_rule[rid]
        sr, n, sk, ku = pr['sr'], pr['n'], pr['skew'], pr['kurt']
        denom = math.sqrt(max(1e-9, 1 - sk * sr + (ku - 1) / 4.0 * sr * sr))
        dsr = float(norm.cdf(((sr - sr0) * math.sqrt(n - 1)) / denom))
        updates.append((dsr, rid))
    # ── PBO via CSCV (S=8 blocks of months) ──
    valid_m = ~np.all(np.isnan(M), axis=0)
    Mv = np.nan_to_num(M[:, valid_m], nan=0.0)
    T = Mv.shape[1]
    S = 8
    blocks = np.array_split(np.arange(T), S)
    from itertools import combinations
    lambdas = []
    oos_ranks = {rid: [] for rid in rule_ids}
    for combo in combinations(range(S), S // 2):
        is_idx = np.concatenate([blocks[b] for b in combo])
        oos_idx = np.concatenate([blocks[b] for b in range(S) if b not in combo])
        perf_is = Mv[:, is_idx].mean(axis=1)
        perf_oos = Mv[:, oos_idx].mean(axis=1)
        best = int(np.argmax(perf_is))
        rank_oos = (perf_oos < perf_oos[best]).sum() / max(len(perf_oos) - 1, 1)
        lam = math.log(max(rank_oos, 1e-6) / max(1 - rank_oos, 1e-6))
        lambdas.append(lam)
        order = np.argsort(-perf_oos)
        rk = np.empty_like(order, dtype=float)
        rk[order] = np.arange(len(order)) / max(len(order) - 1, 1)
        for j, rid in enumerate(rule_ids):
            oos_ranks[rid].append(rk[j])
    pbo = float(np.mean([1 for l in lambdas if l < 0])) if lambdas else None
    pbo = round(sum(1 for l in lambdas if l < 0) / len(lambdas), 4) if lambdas else None
    # write back: vetted = DSR≥0.60 AND median OOS rank in top half
    n_vetted = 0
    for dsr, rid in updates:
        med_rank = float(np.median(oos_ranks[rid])) if oos_ranks[rid] else 1.0
        vetted = 1 if (dsr >= 0.60 and med_rank <= 0.5) else 0
        n_vetted += vetted
        conn.execute(
            "UPDATE quant_discovery_rules SET dsr=?, oos_rank_med=?, vetted=? WHERE id=?",
            (round(dsr, 4), round(med_rank, 4), vetted, rid))
    today = datetime.date.today().isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO feature_store(feature_date, symbol, feature_name, feature_value, version, source_table) "
        "VALUES (?,?,?,?,?,?)", (today, 'MARKET', 'quant_pbo', pbo, 'v1', 'ml_advanced'))
    conn.commit()
    res = {'cmd': 'dsr_pbo', 'n_rules_eval': len(rule_ids), 'n_vetted': n_vetted,
           'pbo': pbo, 'n_trials_assumed': n_trials,
           'dsr_top5': sorted([round(d, 3) for d, _ in updates], reverse=True)[:5]}
    log_run(conn, 'dsr_pbo', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #4 Gate Shadow Book + Thompson Sampling
# ─────────────────────────────────────────────────────────────────────────────

SHADOW_GATES = ('QUALITY_GATE:low_volume_signal', 'QUALITY_GATE:volatile_stock',
                'QUALITY_GATE:high_volume_chase', 'FINAL_EDGE:STRUCTURAL_SL_IMPLAUSIBLE',
                'FINAL_EDGE:LOW_RULE_SCORE', 'FINAL_EDGE:SL_NOT_BELOW_RECENT_STRUCTURE',
                'FINAL_EDGE:UPPER_THIRD_WEAK_EDGE')


def cmd_shadow_update(date=None):
    conn = get_db()
    ensure_tables(conn)
    if date == '--all':
        dates = [r['d'] for r in conn.execute(
            "SELECT DISTINCT trade_date d FROM final_signals WHERE actionable=0 AND score>=80")]
        total = 0
        for d in dates:
            total += _shadow_update_one(conn, d)
        conn.commit()
        res = {'cmd': 'shadow_update', 'dates': len(dates), 'n_inserted': total}
        log_run(conn, 'shadow_update', res)
        conn.close()
        out(res)
        return res
    if date is None:
        date = conn.execute("SELECT MAX(trade_date) d FROM final_signals").fetchone()['d']
    n = _shadow_update_one(conn, date)
    conn.commit()
    res = {'cmd': 'shadow_update', 'date': date, 'n_inserted': n}
    log_run(conn, 'shadow_update', res)
    conn.close()
    out(res)
    return res


def _shadow_update_one(conn, date):
    rows = conn.execute(
        "SELECT symbol, score, source_ml, veto_reason, source_breakdown FROM final_signals "
        "WHERE trade_date=? AND actionable=0 AND score>=80", (date,)).fetchall()
    n = 0
    for r in rows:
        veto = str(r['veto_reason'] or '')
        gate = next((g for g in SHADOW_GATES if veto.startswith(g)), None)
        if not gate:
            continue
        vol_ratio = None
        try:
            bd = json.loads(r['source_breakdown'] or '{}')
            vol_ratio = bd.get('final_edge_metrics', {}).get('final_vol_ratio') or bd.get('final_vol_ratio')
        except Exception:
            pass
        close = conn.execute(
            "SELECT close FROM ohlcv WHERE symbol=? AND date=?", (r['symbol'], date)).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO gate_shadow_book(trade_date, symbol, gate, ues, ml, vol_ratio, entry_close) "
            "VALUES (?,?,?,?,?,?,?)",
            (date, r['symbol'], gate, r['score'], r['source_ml'], vol_ratio,
             close['close'] if close else None))
        n += 1
    return n


def cmd_shadow_outcomes():
    conn = get_db()
    ensure_tables(conn)
    pend = conn.execute(
        "SELECT trade_date, symbol, entry_close FROM gate_shadow_book WHERE win IS NULL").fetchall()
    last = conn.execute("SELECT MAX(date) d FROM ohlcv").fetchone()['d']
    n = 0
    for r in pend:
        bars = conn.execute(
            "SELECT date, high, low, close FROM ohlcv WHERE symbol=? AND date>? ORDER BY date LIMIT 5",
            (r['symbol'], r['trade_date'])).fetchall()
        if len(bars) < 5 and (not bars or bars[-1]['date'] >= last):
            continue  # outcome window not complete yet
        if not bars:
            continue
        entry = r['entry_close']
        if not entry or entry <= 0:
            continue
        win, mfe, mae = 0, -9, 9
        for b in bars:
            hi_r = b['high'] / entry - 1
            lo_r = b['low'] / entry - 1
            mfe, mae = max(mfe, hi_r), min(mae, lo_r)
            if lo_r <= -0.03 and win == 0:
                win = -1  # stopped first
            if hi_r >= 0.05 and win == 0:
                win = 1
        ret5 = bars[-1]['close'] / entry - 1
        conn.execute(
            "UPDATE gate_shadow_book SET win=?, ret5=?, mfe5=?, mae5=?, outcome_date=? "
            "WHERE trade_date=? AND symbol=?",
            (1 if win == 1 else 0, round(ret5, 5), round(mfe, 5), round(mae, 5),
             bars[-1]['date'], r['trade_date'], r['symbol']))
        n += 1
    conn.commit()
    res = {'cmd': 'shadow_outcomes', 'n_filled': n, 'n_pending': len(pend) - n}
    log_run(conn, 'shadow_outcomes', res)
    conn.close()
    out(res)
    return res


def cmd_thompson():
    """Thompson sampling: per-gate opportunity cost + volume-threshold arm selection."""
    conn = get_db()
    ensure_tables(conn)
    rng = np.random.default_rng(SEED)
    today = datetime.date.today().isoformat()
    # baseline actionable WR (reached T1 among resolved outcomes)
    base = conn.execute(
        "SELECT SUM(CASE WHEN reached_t1_target=1 THEN 1 ELSE 0 END) w, COUNT(*) n "
        "FROM recommendation_outcomes WHERE outcome_filled=1").fetchone()
    base_a, base_b = 1 + (base['w'] or 0), 1 + max((base['n'] or 0) - (base['w'] or 0), 0)
    gates = conn.execute(
        "SELECT gate, SUM(win) w, COUNT(*) n FROM gate_shadow_book "
        "WHERE win IS NOT NULL GROUP BY gate").fetchall()
    gate_report = {}
    for g in gates:
        a, b = 1 + (g['w'] or 0), 1 + (g['n'] - (g['w'] or 0))
        s_g = rng.beta(a, b, 4000)
        s_base = rng.beta(base_a, base_b, 4000)
        p_better = float((s_g > s_base).mean())
        gate_report[g['gate']] = {'n': g['n'], 'shadow_wr': round((g['w'] or 0) / g['n'], 3),
                                  'p_blocked_better_than_actionable': round(p_better, 3)}
        conn.execute(
            "INSERT INTO adaptive_gate_params(run_date, param_name, param_value, basis, n_obs, confidence) "
            "VALUES (?,?,?,?,?,?)",
            (today, f"shadow_wr_{g['gate'].split(':')[-1]}", round((g['w'] or 0) / g['n'], 4),
             'thompson_shadow', g['n'], round(p_better, 3)))
    # volume threshold arms from shadow book vol_ratio buckets
    arms = [(1.5, 2.0), (2.0, 2.2), (2.2, 2.5), (2.5, 2.8), (2.8, 3.2)]
    arm_stats = []
    for lo, hi in arms:
        r = conn.execute(
            "SELECT SUM(win) w, COUNT(*) n FROM gate_shadow_book "
            "WHERE win IS NOT NULL AND vol_ratio>=? AND vol_ratio<?", (lo, hi)).fetchone()
        arm_stats.append((lo, hi, 1 + (r['w'] or 0), 1 + ((r['n'] or 0) - (r['w'] or 0)), r['n'] or 0))
    total_n = sum(a[4] for a in arm_stats)
    rec = None
    if total_n >= 50:
        wins = np.zeros(len(arms))
        for _ in range(4000):
            draws = [rng.beta(a, b) for _, _, a, b, _ in arm_stats]
            wins[int(np.argmax(draws))] += 1
        best = int(np.argmax(wins))
        confidence = wins[best] / 4000
        rec = max(2.2, min(3.0, arm_stats[best][0]))
        conn.execute(
            "INSERT INTO adaptive_gate_params(run_date, param_name, param_value, basis, n_obs, confidence) "
            "VALUES (?,?,?,?,?,?)",
            (today, 'volume_ratio_min', rec, 'thompson_shadow', total_n, round(confidence, 3)))
    conn.commit()
    res = {'cmd': 'thompson', 'gates': gate_report, 'vol_arm_n': total_n,
           'recommended_volume_ratio_min': rec}
    log_run(conn, 'thompson', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #5 Lead-Lag pulse
# ─────────────────────────────────────────────────────────────────────────────

def cmd_leadlag(date=None):
    conn = get_db()
    ensure_tables(conn)
    if date is None:
        date = conn.execute("SELECT MAX(date) d FROM ohlcv").fetchone()['d']
    links = conn.execute(
        "SELECT leader_symbol, follower_symbol, lag_days, correlation FROM stock_lead_lag "
        "WHERE correlation>=0.35 AND granger_pvalue<0.10 AND n_observations>=40 "
        "AND computed_date=(SELECT MAX(computed_date) FROM stock_lead_lag)").fetchall()
    if not links:
        out({'cmd': 'leadlag', 'error': 'no qualified lead-lag links'})
        return
    # trading-date index
    dates = [r['date'] for r in conn.execute(
        "SELECT DISTINCT date FROM ohlcv WHERE date<=? ORDER BY date DESC LIMIT 12", (date,))]
    dates.reverse()
    d2i = {d: i for i, d in enumerate(dates)}
    if date not in d2i:
        out({'cmd': 'leadlag', 'error': f'no bars for {date}'})
        return
    # leader returns by date
    syms = {l['leader_symbol'] for l in links}
    rets = {}
    for s in syms:
        rows = conn.execute(
            "SELECT date, close FROM ohlcv WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT 12",
            (s, date)).fetchall()
        rows = rows[::-1]
        for i in range(1, len(rows)):
            if rows[i - 1]['close']:
                rets[(s, rows[i]['date'])] = rows[i]['close'] / rows[i - 1]['close'] - 1
    pulse = {}
    di = d2i[date]
    for l in links:
        src_i = di - int(l['lag_days'])
        if src_i < 1 or src_i >= len(dates):
            continue
        lr = rets.get((l['leader_symbol'], dates[src_i]))
        if lr is None:
            continue
        contrib = max(l['correlation'], 0) * max(-1.0, min(1.0, lr / 0.05))
        pulse[l['follower_symbol']] = pulse.get(l['follower_symbol'], 0.0) + contrib
    rows = []
    for sym, v in pulse.items():
        score = 1 / (1 + math.exp(-2.5 * v))  # squash to 0..1
        rows.append((date, sym, 'leadlag_pulse', round(score, 4), 'v1', 'ml_advanced'))
    conn.executemany(
        "INSERT OR REPLACE INTO feature_store(feature_date, symbol, feature_name, feature_value, version, source_table) "
        "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    strong = sum(1 for _, _, _, v, _, _ in rows if v >= 0.70)
    res = {'cmd': 'leadlag', 'date': date, 'n_links': len(links),
           'n_followers_scored': len(rows), 'n_strong_pulse': strong}
    log_run(conn, 'leadlag', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #6 DOM microstructure
# ─────────────────────────────────────────────────────────────────────────────

def cmd_dom_features():
    conn = get_db()
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT symbol, dom_data, best_bid, best_ask, total_bid_depth, total_ask_depth, "
        "imbalance_ratio, fetched_at FROM dom_live_snapshots "
        "WHERE id IN (SELECT MAX(id) FROM dom_live_snapshots GROUP BY symbol)").fetchall()
    n = 0
    for r in rows:
        date = (r['fetched_at'] or '')[:10] or datetime.date.today().isoformat()
        feats = {}
        if r['imbalance_ratio'] is not None:
            feats['dom_imbalance_v2'] = round(min(float(r['imbalance_ratio']), 10.0), 4)
        try:
            dd = json.loads(r['dom_data'] or '{}')
            bids = dd.get('bids') or []
            asks = dd.get('asks') or []
            mid = ((r['best_bid'] or 0) + (r['best_ask'] or 0)) / 2
            if bids and asks and mid > 0:
                def near(levels, side):
                    tot = 0.0
                    for lv in levels[:10]:
                        px = float(lv.get('price', 0) or 0)
                        sz = float(lv.get('size', lv.get('volume', 0)) or 0)
                        if px > 0 and abs(px / mid - 1) <= 0.01:
                            tot += sz
                    return tot
                nb, na = near(bids, 'bid'), near(asks, 'ask')
                if na > 0:
                    feats['dom_near_pressure'] = round(min(nb / na, 10.0), 4)
                # depth slope: how fast liquidity decays away from mid (lower = thin book)
                for side, levels, key in (('bid', bids, 'dom_bid_slope'), ('ask', asks, 'dom_ask_slope')):
                    pts = [(abs(float(l.get('price', 0)) / mid - 1),
                            float(l.get('size', l.get('volume', 0)) or 0))
                           for l in levels[:10] if float(l.get('price', 0) or 0) > 0]
                    if len(pts) >= 4:
                        xs = np.array([p[0] for p in pts])
                        ys = np.cumsum([p[1] for p in pts])
                        slope = float(np.polyfit(xs, ys / (ys[-1] or 1), 1)[0])
                        feats[key] = round(max(-1000, min(1000, slope)), 3)
        except Exception:
            pass
        for k, v in feats.items():
            conn.execute(
                "INSERT OR REPLACE INTO feature_store(feature_date, symbol, feature_name, feature_value, version, source_table) "
                "VALUES (?,?,?,?,?,?)", (date, r['symbol'], k, v, 'v2', 'ml_advanced'))
            n += 1
    conn.commit()
    res = {'cmd': 'dom_features', 'n_snapshots': len(rows), 'n_features_written': n}
    log_run(conn, 'dom_features', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #8 Survival — competing risks (cause-specific Cox)
# ─────────────────────────────────────────────────────────────────────────────

SURV_COVARS = ['vol_ratio', 'atr_pct', 'close_pos', 'rsi14', 'ret3', 'dist_ath300', 'is_bull']


def cmd_survival_train():
    conn = get_db()
    ensure_tables(conn)
    df, X = _load_events(conn)
    if df is None or len(df) < 800:
        out({'cmd': 'survival_train', 'error': 'insufficient events'})
        return
    from lifelines import CoxPHFitter
    d = X[SURV_COVARS].copy()
    d['duration'] = df['t_days'].clip(lower=1)
    models = {}
    stats = {}
    for cause in ('tp', 'sl'):
        dd = d.copy()
        dd['event'] = (df['barrier'] == cause).astype(int).values
        cph = CoxPHFitter(penalizer=0.1)
        cph.fit(dd, duration_col='duration', event_col='event')
        models[cause] = cph
        stats[cause] = {'n_events': int(dd['event'].sum()),
                        'concordance': round(float(cph.concordance_index_), 4)}
    with open(os.path.join(MODELS_DIR, 'survival_cox.pkl'), 'wb') as f:
        pickle.dump({'models': models, 'covars': SURV_COVARS}, f)
    res = {'cmd': 'survival_train', 'n': len(df), **{f'{k}_{a}': v for k, s in stats.items() for a, v in s.items()}}
    log_run(conn, 'survival_train', res)
    conn.close()
    out(res)
    return res


def cmd_survival_score(date=None):
    conn = get_db()
    ensure_tables(conn)
    sp = os.path.join(MODELS_DIR, 'survival_cox.pkl')
    if not os.path.exists(sp):
        out({'cmd': 'survival_score', 'error': 'survival model missing'})
        return
    with open(sp, 'rb') as f:
        pack = pickle.load(f)
    feat = build_feature_frame(conn)
    date = date or feat['date'].max()
    day = feat[feat['date'] == date].copy()
    if day.empty:
        out({'cmd': 'survival_score', 'error': f'no bars for {date}'})
        return
    Xd = day[SURV_COVARS].astype(float).fillna(0.0)
    times = np.arange(1, HORIZON + 1)
    surv = {}
    for cause in ('tp', 'sl'):
        cph = pack['models'][cause]
        sf = cph.predict_survival_function(Xd, times=times)  # times × subjects
        surv[cause] = sf.values.T  # subjects × times
    rows = []
    for i, (_, rr) in enumerate(day.iterrows()):
        s_tp, s_sl = surv['tp'][i], surv['sl'][i]
        # discrete cause-specific hazards → Aalen-Johansen CIF
        cif_tp, cif_sl, S_prev = [], [], 1.0
        h_tp_prev, h_sl_prev = 1.0, 1.0
        c_tp = c_sl = 0.0
        for t in range(len(times)):
            h_tp = max(0.0, 1 - s_tp[t] / h_tp_prev)
            h_sl = max(0.0, 1 - s_sl[t] / h_sl_prev)
            c_tp += S_prev * h_tp * (1 - 0.5 * h_sl)
            c_sl += S_prev * h_sl * (1 - 0.5 * h_tp)
            S_prev *= max(0.0, (1 - h_tp) * (1 - h_sl))
            h_tp_prev, h_sl_prev = s_tp[t], s_sl[t]
            cif_tp.append(c_tp)
        p_tp, p_sl = min(c_tp, 1.0), min(c_sl, 1.0)
        if cif_tp[-1] > 0:
            w = np.diff([0] + cif_tp)
            exp_days = float(np.sum(times * w) / cif_tp[-1])
            hold = int(next((t + 1 for t in range(len(times)) if cif_tp[t] >= 0.8 * cif_tp[-1]), HORIZON))
        else:
            exp_days, hold = float(HORIZON), HORIZON
        rows.append((rr['symbol'], date, round(p_tp, 4), round(p_sl, 4),
                     round(exp_days, 2), hold))
    conn.executemany(
        "INSERT OR REPLACE INTO survival_exit_profile(symbol, date, p_tp_first, p_sl_first, expected_days_tp, hold_days) "
        "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    res = {'cmd': 'survival_score', 'date': date, 'n_scored': len(rows),
           'avg_p_tp': round(float(np.mean([r[2] for r in rows])), 3),
           'avg_hold_days': round(float(np.mean([r[5] for r in rows])), 1)}
    log_run(conn, 'survival_score', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #9 Pattern embeddings — random conv kernels (ROCKET-lite) + kNN analogs
# ─────────────────────────────────────────────────────────────────────────────

N_KERNELS = 96
KERNEL_LEN = 9


def _make_kernels():
    rng = np.random.default_rng(SEED)
    W = rng.standard_normal((N_KERNELS, KERNEL_LEN)).astype(np.float32)
    W -= W.mean(axis=1, keepdims=True)
    B = rng.uniform(-0.5, 0.5, N_KERNELS).astype(np.float32)
    return W, B


def _embed_windows(windows):
    """windows: (n, WINDOW, 2) → (n, N_KERNELS*2*2) [PPV+max per kernel per channel]"""
    W, B = _make_kernels()
    n = windows.shape[0]
    feats = np.zeros((n, N_KERNELS * 4), dtype=np.float32)
    L = WINDOW - KERNEL_LEN + 1
    for ch in range(2):
        x = windows[:, :, ch]
        # build sliding windows: (n, L, KERNEL_LEN)
        sw = np.lib.stride_tricks.sliding_window_view(x, KERNEL_LEN, axis=1)
        conv = np.einsum('nlk,mk->nml', sw, W) + B[None, :, None]  # n × kernels × L
        ppv = (conv > 0).mean(axis=2)
        mx = conv.max(axis=2)
        feats[:, ch * N_KERNELS * 2: ch * N_KERNELS * 2 + N_KERNELS] = ppv
        feats[:, ch * N_KERNELS * 2 + N_KERNELS: (ch + 1) * N_KERNELS * 2] = mx
    norms = np.linalg.norm(feats, axis=1, keepdims=True)
    return feats / np.maximum(norms, 1e-9)


def _window_for(g, i):
    """Normalized (WINDOW, 2) window ending at index i (inclusive)."""
    if i < WINDOW:
        return None
    c = g['close'].values[i - WINDOW:i + 1]
    v = g['volume'].values[i - WINDOW + 1:i + 1].astype(float)
    lr = np.diff(np.log(np.maximum(c, 1e-9)))
    if len(lr) != WINDOW or not np.all(np.isfinite(lr)):
        return None
    lr_z = (lr - lr.mean()) / (lr.std() + 1e-9)
    vmed = np.median(v[v > 0]) if np.any(v > 0) else 1.0
    vz = np.log(np.maximum(v, 1.0) / max(vmed, 1.0))
    vz = (vz - vz.mean()) / (vz.std() + 1e-9)
    return np.stack([lr_z, vz], axis=1).astype(np.float32)


def cmd_embed_build():
    conn = get_db()
    ensure_tables(conn)
    ev = pd.read_sql_query("SELECT symbol, date, label, fwd5 FROM ml_adv_events", conn)
    if ev.empty:
        out({'cmd': 'embed_build', 'error': 'no events'})
        return
    df = _load_ohlcv_df(conn)
    idx = {}
    for sym, g in df.groupby('symbol', sort=False):
        g = g.sort_values('date').reset_index(drop=True)
        idx[sym] = (g, {d: i for i, d in enumerate(g['date'])})
    wins, meta = [], []
    for _, e in ev.iterrows():
        pack = idx.get(e['symbol'])
        if not pack:
            continue
        g, dmap = pack
        i = dmap.get(e['date'])
        if i is None:
            continue
        w = _window_for(g, i)
        if w is None:
            continue
        wins.append(w)
        meta.append((e['symbol'], e['date'], int(e['label']), float(e['fwd5'] or 0)))
    if len(wins) < 300:
        out({'cmd': 'embed_build', 'error': f'only {len(wins)} windows'})
        return
    emb = _embed_windows(np.stack(wins))
    np.savez_compressed(
        os.path.join(MODELS_DIR, 'pattern_analogs.npz'),
        emb=emb,
        sym=np.array([m[0] for m in meta]),
        date=np.array([m[1] for m in meta]),
        label=np.array([m[2] for m in meta], dtype=np.int8),
        fwd5=np.array([m[3] for m in meta], dtype=np.float32))
    res = {'cmd': 'embed_build', 'n_windows': len(wins), 'dim': int(emb.shape[1])}
    log_run(conn, 'embed_build', res)
    conn.close()
    out(res)
    return res


def cmd_embed_score(date=None):
    conn = get_db()
    ensure_tables(conn)
    pth = os.path.join(MODELS_DIR, 'pattern_analogs.npz')
    if not os.path.exists(pth):
        out({'cmd': 'embed_score', 'error': 'no analog index — run embed_build'})
        return
    z = np.load(pth, allow_pickle=True)
    df = _load_ohlcv_df(conn)
    date = date or df['date'].max()
    rows = []
    for sym, g in df.groupby('symbol', sort=False):
        g = g.sort_values('date').reset_index(drop=True)
        if g['date'].iat[-1] != date and date not in set(g['date'].values[-2:]):
            continue
        try:
            i = list(g['date']).index(date)
        except ValueError:
            continue
        w = _window_for(g, i)
        if w is None:
            continue
        rows.append((sym, w))
    if not rows:
        out({'cmd': 'embed_score', 'error': f'no windows for {date}'})
        return
    q = _embed_windows(np.stack([w for _, w in rows]))
    sims = q @ z['emb'].T  # cosine (already normalized)
    K = 50
    written = 0
    cutoff = (datetime.date.fromisoformat(date) - datetime.timedelta(days=10)).isoformat()
    hist_dates = z['date']
    for j, (sym, _) in enumerate(rows):
        # exclude look-ahead: only analogs that resolved before the query date
        valid = hist_dates < cutoff
        s = sims[j].copy()
        s[~valid] = -1
        top = np.argsort(-s)[:K]
        top = top[s[top] > 0.1]
        if len(top) < 15:
            continue
        wr = float(z['label'][top].mean())
        avg5 = float(z['fwd5'][top].mean())
        top3 = [f"{z['sym'][t]}@{z['date'][t]}" for t in top[:3]]
        conn.execute(
            "INSERT OR REPLACE INTO pattern_analogs(symbol, date, analog_wr, analog_n, avg_fwd5, top_analogs) "
            "VALUES (?,?,?,?,?,?)",
            (sym, date, round(wr, 4), int(len(top)), round(avg5, 5), json.dumps(top3)))
        written += 1
    conn.commit()
    res = {'cmd': 'embed_score', 'date': date, 'n_scored': written}
    log_run(conn, 'embed_score', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #10 Conformal — Venn-ABERS bounds on explosion win-prob
# ─────────────────────────────────────────────────────────────────────────────

def _venn_abers(cal_x, cal_y, test_x):
    """Per test point: fit isotonic with the point appended as 0 then as 1 → [p0, p1]."""
    from sklearn.isotonic import IsotonicRegression
    p0s, p1s = [], []
    cx = np.asarray(cal_x, dtype=float)
    cy = np.asarray(cal_y, dtype=float)
    for x in test_x:
        lo_hi = []
        for y_hyp in (0.0, 1.0):
            iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds='clip')
            iso.fit(np.append(cx, x), np.append(cy, y_hyp))
            lo_hi.append(float(iso.predict([x])[0]))
        p0s.append(min(lo_hi))
        p1s.append(max(lo_hi))
    return np.array(p0s), np.array(p1s)


def cmd_conformal(date=None):
    conn = get_db()
    ensure_tables(conn)
    if date is None:
        date = conn.execute("SELECT MAX(pred_date) d FROM explosion_predictions").fetchone()['d']
    # calibration: past predictions whose 5-bar outcome window completed
    cal = conn.execute("""
        WITH preds AS (
            SELECT symbol, pred_date, prob_pct FROM explosion_predictions
            WHERE prob_pct IS NOT NULL AND pred_date < date(?, '-9 days')
        )
        SELECT p.symbol, p.pred_date, p.prob_pct,
               (SELECT MAX(high) FROM (
                   SELECT high FROM ohlcv
                   WHERE symbol=p.symbol AND date>p.pred_date
                   ORDER BY date LIMIT 5)) AS max_hi,
               (SELECT o2.close FROM ohlcv o2
                 WHERE o2.symbol=p.symbol AND o2.date<=p.pred_date
                 ORDER BY o2.date DESC LIMIT 1) AS entry
        FROM preds p
    """, (date,)).fetchall()
    cal_x, cal_y = [], []
    for r in cal:
        if r['entry'] and r['max_hi'] and r['entry'] > 0:
            cal_x.append(r['prob_pct'] / 100.0)
            cal_y.append(1.0 if (r['max_hi'] / r['entry'] - 1) >= 0.05 else 0.0)
    if len(cal_x) < 200:
        out({'cmd': 'conformal', 'error': f'calibration too small (n={len(cal_x)})'})
        return
    test = conn.execute(
        "SELECT symbol, prob_pct FROM explosion_predictions WHERE pred_date=? AND prob_pct IS NOT NULL",
        (date,)).fetchall()
    if not test:
        out({'cmd': 'conformal', 'error': f'no predictions for {date}'})
        return
    # dedupe test probs for speed, map back
    uniq = sorted({round(t['prob_pct'] / 100.0, 3) for t in test})
    p0u, p1u = _venn_abers(cal_x, cal_y, uniq)
    pmap = {u: (p0u[i], p1u[i]) for i, u in enumerate(uniq)}
    n = 0
    for t in test:
        p0, p1 = pmap[round(t['prob_pct'] / 100.0, 3)]
        confident = 1 if (p1 - p0) <= 0.15 else 0
        conn.execute(
            "INSERT OR REPLACE INTO conformal_scores(symbol, date, p_lo, p_hi, confident, cal_n) "
            "VALUES (?,?,?,?,?,?)",
            (t['symbol'], date, round(p0, 4), round(p1, 4), confident, len(cal_x)))
        n += 1
    conn.commit()
    res = {'cmd': 'conformal', 'date': date, 'n_scored': n, 'cal_n': len(cal_x),
           'base_rate': round(float(np.mean(cal_y)), 3)}
    log_run(conn, 'conformal', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# #11 Drift — clean adversarial validation (same-source ref vs current)
# ─────────────────────────────────────────────────────────────────────────────

def cmd_drift():
    conn = get_db()
    ensure_tables(conn)
    feat = build_feature_frame(conn)
    dates = sorted(feat['date'].unique())
    if len(dates) < 80:
        out({'cmd': 'drift', 'error': 'history too short'})
        return
    cur_dates = set(dates[-15:])
    ref_dates = set(dates[-150:-40]) if len(dates) >= 150 else set(dates[:-40])
    cols = [c for c in META_FEATURES if c not in ('dow', 'is_bull', 'is_bear')]
    A = feat[feat['date'].isin(ref_dates)][cols].astype(float).dropna()
    B = feat[feat['date'].isin(cur_dates)][cols].astype(float).dropna()
    if len(B) > len(A):
        B = B.sample(len(A), random_state=SEED)
    else:
        A = A.sample(min(len(A), max(len(B) * 4, 2000)), random_state=SEED)
    Xall = pd.concat([A, B], ignore_index=True)
    yall = np.r_[np.zeros(len(A)), np.ones(len(B))]
    from sklearn.model_selection import StratifiedKFold
    from sklearn.metrics import roc_auc_score
    lgb, p = _lgbm({'num_leaves': 15, 'min_data_in_leaf': 80})
    aucs = []
    skf = StratifiedKFold(n_splits=4, shuffle=True, random_state=SEED)
    for tr, te in skf.split(Xall, yall):
        mdl = lgb.train(p, lgb.Dataset(Xall.iloc[tr], label=yall[tr]), num_boost_round=120)
        aucs.append(roc_auc_score(yall[te], mdl.predict(Xall.iloc[te])))
    adv_auc = float(np.mean(aucs))
    # PSI per feature
    psis = {}
    for c in cols:
        ref_v, cur_v = A[c].values, B[c].values
        qs = np.quantile(ref_v, np.linspace(0, 1, 11))
        qs[0], qs[-1] = -np.inf, np.inf
        rh, _ = np.histogram(ref_v, bins=qs)
        ch, _ = np.histogram(cur_v, bins=qs)
        rp = np.maximum(rh / max(rh.sum(), 1), 1e-4)
        cp = np.maximum(ch / max(ch.sum(), 1), 1e-4)
        psis[c] = float(np.sum((cp - rp) * np.log(cp / rp)))
    avg_psi = float(np.mean(list(psis.values())))
    throttle = 1 if (adv_auc > 0.80 or avg_psi > 0.25) else 0
    today = datetime.date.today().isoformat()
    for k, v in (('mladv_adv_auc', adv_auc), ('mladv_avg_psi', avg_psi),
                 ('mladv_drift_throttle', float(throttle))):
        conn.execute(
            "INSERT OR REPLACE INTO feature_store(feature_date, symbol, feature_name, feature_value, version, source_table) "
            "VALUES (?,?,?,?,?,?)", (today, 'MARKET', k, round(v, 4), 'v1', 'ml_advanced'))
    conn.commit()
    res = {'cmd': 'drift', 'adv_auc': round(adv_auc, 4), 'avg_psi': round(avg_psi, 4),
           'drift_throttle': throttle,
           'top_psi': dict(sorted(psis.items(), key=lambda x: -x[1])[:3])}
    log_run(conn, 'drift', res)
    conn.close()
    out(res)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────

def cmd_daily(date=None):
    results = {}
    conn = get_db()
    ensure_tables(conn)
    shadow_resolved = conn.execute(
        "SELECT COUNT(*) n FROM gate_shadow_book WHERE win IS NOT NULL").fetchone()['n']
    conn.close()
    steps = [
        ('leadlag', lambda: cmd_leadlag(date)),
        ('dom_features', cmd_dom_features),
        ('meta_score', lambda: cmd_meta_score(date)),
        ('embed_score', lambda: cmd_embed_score(date)),
        ('conformal', lambda: cmd_conformal(date)),
        ('survival_score', lambda: cmd_survival_score(date)),
        ('shadow_outcomes', cmd_shadow_outcomes),
        ('drift', cmd_drift),
    ]
    # Thompson sampling when shadow book has enough resolved outcomes
    if shadow_resolved >= 30:
        steps.append(('thompson', cmd_thompson))
    for name, fn in steps:
        try:
            results[name] = fn() or 'ok'
        except Exception as e:
            results[name] = f'ERROR: {e}'
    out({'cmd': 'daily', 'done': list(results.keys()),
         'shadow_resolved': shadow_resolved,
         'errors': {k: v for k, v in results.items() if isinstance(v, str) and v.startswith('ERROR')}})


def _run_cross_stock_brain():
    """#5 — refresh stock_lead_lag pairs consumed by cmd_leadlag."""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cross_stock_brain.py')
    proc = subprocess.run(
        [sys.executable, script, 'run'],
        cwd=ROOT, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        return {'cmd': 'cross_stock_brain', 'error': (proc.stderr or proc.stdout)[-500:]}
    try:
        lines = [ln for ln in (proc.stdout or '').splitlines() if ln.strip().startswith('{')]
        payload = json.loads(lines[-1]) if lines else {}
    except Exception:
        payload = {}
    return {'cmd': 'cross_stock_brain', 'ok': True, **payload}


def cmd_weekly():
    results = {}
    for name, fn in (('build_events', cmd_build_events),
                     ('purged_cv', cmd_purged_cv),
                     ('meta_train', cmd_meta_train),
                     ('survival_train', cmd_survival_train),
                     ('embed_build', cmd_embed_build),
                     ('cross_stock_brain', _run_cross_stock_brain),
                     ('dsr_pbo', cmd_dsr_pbo),
                     ('thompson', cmd_thompson)):
        try:
            results[name] = fn() or 'ok'
        except Exception as e:
            results[name] = f'ERROR: {e}'
    out({'cmd': 'weekly', 'done': list(results.keys()),
         'errors': {k: v for k, v in results.items() if isinstance(v, str) and v.startswith('ERROR')}})


def cmd_status():
    conn = get_db()
    ensure_tables(conn)
    q = lambda sql: conn.execute(sql).fetchone()
    info = {
        'events': dict(q("SELECT COUNT(*) n, MAX(date) latest FROM ml_adv_events") or {}),
        'meta_scores': dict(q("SELECT COUNT(*) n, MAX(date) latest FROM meta_label_scores") or {}),
        'shadow_book': dict(q("SELECT COUNT(*) n, SUM(CASE WHEN win IS NOT NULL THEN 1 ELSE 0 END) resolved FROM gate_shadow_book") or {}),
        'analogs': dict(q("SELECT COUNT(*) n, MAX(date) latest FROM pattern_analogs") or {}),
        'conformal': dict(q("SELECT COUNT(*) n, MAX(date) latest FROM conformal_scores") or {}),
        'survival': dict(q("SELECT COUNT(*) n, MAX(date) latest FROM survival_exit_profile") or {}),
        'quant_vetted': dict(q("SELECT SUM(vetted) vetted, COUNT(*) total FROM quant_discovery_rules") or {}),
        'models': {f: os.path.exists(os.path.join(MODELS_DIR, f)) for f in
                   ('meta_labeler_v1.txt', 'survival_cox.pkl', 'pattern_analogs.npz')},
    }
    for k in ('purged_auc_triple_barrier', 'purged_auc_explosion_style',
              'mladv_adv_auc', 'mladv_drift_throttle', 'quant_pbo'):
        r = conn.execute(
            "SELECT feature_value v FROM feature_store WHERE symbol='MARKET' AND feature_name=? "
            "ORDER BY feature_date DESC LIMIT 1", (k,)).fetchone()
        info[k] = r['v'] if r else None
    conn.close()
    out({'cmd': 'status', **info})


def cmd_selftest():
    """End-to-end test of every component on real data."""
    steps = [
        ('build_events', cmd_build_events),
        ('purged_cv', cmd_purged_cv),
        ('meta_train', cmd_meta_train),
        ('meta_score', lambda: cmd_meta_score(None)),
        ('survival_train', cmd_survival_train),
        ('survival_score', lambda: cmd_survival_score(None)),
        ('embed_build', cmd_embed_build),
        ('embed_score', lambda: cmd_embed_score(None)),
        ('conformal', lambda: cmd_conformal(None)),
        ('drift', cmd_drift),
        ('leadlag', lambda: cmd_leadlag(None)),
        ('dom_features', cmd_dom_features),
        ('shadow_update', lambda: cmd_shadow_update(None)),
        ('shadow_outcomes', cmd_shadow_outcomes),
        ('thompson', cmd_thompson),
        ('dsr_pbo', cmd_dsr_pbo),
    ]
    report = {}
    for name, fn in steps:
        t0 = datetime.datetime.now()
        try:
            r = fn()
            ok = bool(r) and not (isinstance(r, dict) and r.get('error'))
            report[name] = {'ok': ok, 'sec': round((datetime.datetime.now() - t0).total_seconds(), 1),
                            'note': (r or {}).get('error') if isinstance(r, dict) else None}
        except Exception as e:
            report[name] = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
    passed = sum(1 for v in report.values() if v.get('ok'))
    out({'cmd': 'selftest', 'passed': passed, 'total': len(steps), 'report': report})


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    dispatch = {
        'build_events': cmd_build_events,
        'purged_cv': cmd_purged_cv,
        'meta_train': cmd_meta_train,
        'meta_score': lambda: cmd_meta_score(arg),
        'dsr_pbo': cmd_dsr_pbo,
        'shadow_update': lambda: cmd_shadow_update(arg),
        'shadow_outcomes': cmd_shadow_outcomes,
        'thompson': cmd_thompson,
        'leadlag': lambda: cmd_leadlag(arg),
        'dom_features': cmd_dom_features,
        'survival_train': cmd_survival_train,
        'survival_score': lambda: cmd_survival_score(arg),
        'embed_build': cmd_embed_build,
        'embed_score': lambda: cmd_embed_score(arg),
        'conformal': lambda: cmd_conformal(arg),
        'drift': cmd_drift,
        'daily': lambda: cmd_daily(arg),
        'weekly': cmd_weekly,
        'status': cmd_status,
        'selftest': cmd_selftest,
    }
    fn = dispatch.get(cmd)
    if not fn:
        print(f'Unknown command: {cmd}. Options: {", ".join(dispatch)}')
        sys.exit(1)
    fn()
