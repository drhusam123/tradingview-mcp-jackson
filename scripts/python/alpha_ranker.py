#!/usr/bin/env python3
"""
Phase 70 — Alpha Ranker + Decay Monitor
"مُصنِّف الألفا — يرقّي الفائز، يقتل الفاشل، يراقب الاضمحلال"

Commands: rank_all | kill_weak | decay_check | leaderboard | evolve | build_full
"""
import sys, json, sqlite3, math
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'

# ── Scoring weights — v2 (OOS stability prioritized) ─────────────────────────
WEIGHTS = {
    'expectancy':   0.30,   # positive expectancy after costs
    'oos_score':    0.35,   # OOS stability (king metric — overfit kills live performance)
    'win_rate':     0.12,   # secondary (psychological & compounding importance)
    'n_acts':       0.08,   # statistical significance (log-scaled)
    'robustness':   0.15,   # robustness score (OOS-based)
}

# Kill thresholds — v3 (raised for better quality — only keep hypotheses with real edge)
KILL_IF = {
    'expectancy_pct_lt': 1.2,      # raised from 1.0 (2026-05-22): our signal system now targets 4%+ EV
    'oos_score_lt':      0.5,      # OOS must be ≥50% of IS performance
    'n_activations_lt':  20,       # minimum 20 samples for statistical validity
    'win_rate_lt':       0.41,     # raised from 0.38: with quality gates we see 55%+ WR; kill raw<41%
    # Composite kill: mediocre WR AND low EV together = not worth tracking
    'composite_wr_lt':   0.44,     # raised from 0.42: if WR < 44% AND EV < 1.5% → kill
    'composite_ev_lt':   1.5,      # used in conjunction with composite_wr_lt
}

# Promotion thresholds (for sandbox hypotheses → research_results)
PROMOTE_IF = {
    'min_precision':     0.54,     # raised from 0.52: minimum 54% win rate for promotion (2026-05-22)
    'min_n_samples':     30,       # minimum 30 samples before promotion
    'min_expectancy':    1.5,      # raised from 1.0: minimum 1.5% expectancy per trade
    'min_oos_score':     0.55,     # OOS must be at least 55% of IS
}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS alpha_rankings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        hyp_id         TEXT NOT NULL,
        hyp_name       TEXT,
        ranked_at      TEXT DEFAULT CURRENT_TIMESTAMP,
        composite_score REAL,
        expectancy_pct REAL,
        oos_score      REAL,
        win_rate_pct   REAL,
        n_activations  INTEGER,
        grade          TEXT,
        is_alive       INTEGER DEFAULT 1,
        kill_reason    TEXT,
        UNIQUE(hyp_id)
    );
    CREATE INDEX IF NOT EXISTS idx_ar_score ON alpha_rankings(composite_score);
    CREATE INDEX IF NOT EXISTS idx_ar_alive ON alpha_rankings(is_alive);

    CREATE TABLE IF NOT EXISTS alpha_decay_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        hyp_id      TEXT,
        checked_at  TEXT DEFAULT CURRENT_TIMESTAMP,
        decay_score REAL,
        is_decaying INTEGER DEFAULT 0,
        notes       TEXT
    );

    CREATE TABLE IF NOT EXISTS evolved_hypotheses (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        parent_hyp_id TEXT,
        child_hyp_id  TEXT,
        evolution_type TEXT,
        created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
        description  TEXT
    );
    """)
    conn.commit()

def compute_composite(exp, oos, wr, n_acts):
    """Compute 0-100 composite alpha score. v2 — OOS-prioritized."""
    # Expectancy score (0-100, anchored at 1% = 50 — minimum viable expectancy)
    # Calibration: 0.5% EV = 40, 1% = 50, 2% = 60, 5% = 80, 10% = 100
    if exp <= 0:
        exp_score = max(0, 30 + exp * 10)     # negative EV: 0→30, -2%→10
    elif exp <= 1.0:
        exp_score = 40 + exp * 10             # 0%=40, 1%=50
    elif exp <= 3.0:
        exp_score = 50 + (exp - 1.0) * 10    # 1%=50, 3%=70
    elif exp <= 7.0:
        exp_score = 70 + (exp - 3.0) * 5     # 3%=70, 7%=90
    else:
        exp_score = min(100, 90 + (exp - 7.0) * 2)  # 7%=90, capped at 100

    # OOS score (0-100) — tightened thresholds
    if oos is None:
        oos_score = 45  # no OOS data → slight penalty vs neutral 50
    elif oos >= 1.1:
        oos_score = 100  # OOS better than IS — exceptional generalization
    elif oos >= 1.0:
        oos_score = 92   # OOS >= IS
    elif oos >= 0.85:
        oos_score = 80
    elif oos >= 0.70:
        oos_score = 68
    elif oos >= 0.55:
        oos_score = 52
    elif oos >= 0.40:
        oos_score = 38   # moderate overfit
    else:
        oos_score = 15   # severe overfit → kill candidate

    # Win rate score — anchored at 35% (new minimum)
    wr_score   = max(0, min(100, (wr - 35) * 2.5))  # 35%→0, 75%→100

    # Sample significance (log-scaled) — minimum 20 samples for credibility
    n_score    = min(100, math.log1p(max(0, n_acts - 20)) / math.log(500) * 100)

    # Robustness = function of OOS + win rate combo
    if oos and oos >= 0.85 and wr >= 45:
        robust = 100  # excellent OOS + solid win rate
    elif oos and oos >= 0.70:
        robust = 90
    elif oos and oos >= 0.55:
        robust = 65
    elif not oos:
        robust = 40  # no OOS data → can't trust IS
    else:
        robust = max(0, oos * 60)  # below 0.55 OOS → proportional penalty

    composite = (
        WEIGHTS['expectancy'] * exp_score +
        WEIGHTS['oos_score']  * oos_score +
        WEIGHTS['win_rate']   * wr_score  +
        WEIGHTS['n_acts']     * n_score   +
        WEIGHTS['robustness'] * robust
    )
    return round(composite, 2)

def assign_grade(composite):
    if composite >= 80: return 'S'
    if composite >= 70: return 'A'
    if composite >= 60: return 'B'
    if composite >= 50: return 'C'
    if composite >= 40: return 'D'
    return 'F'

# ─────────────────────────────────────────────
# Rank all tested hypotheses
# ─────────────────────────────────────────────
def rank_all(params):
    conn = db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT r.hyp_id, r.hyp_name, r.expectancy_pct, r.oos_score,
               r.win_rate_pct, r.n_activations, r.is_precision, r.oos_precision,
               r.avg_net_return, r.status, h.category
        FROM research_results r
        LEFT JOIN hypotheses h ON r.hyp_id = h.hyp_id
        WHERE r.status = 'ACTIVE'
        ORDER BY r.expectancy_pct DESC
    """).fetchall()

    ranked = []
    for r in rows:
        exp  = r['expectancy_pct'] or 0
        oos  = r['oos_score']
        wr   = r['win_rate_pct'] or 0
        n    = r['n_activations'] or 0
        comp = compute_composite(exp, oos, wr, n)
        grade= assign_grade(comp)
        ranked.append({
            'hyp_id':         r['hyp_id'],
            'hyp_name':       r['hyp_name'],
            'composite_score':comp,
            'grade':          grade,
            'expectancy_pct': round(exp, 3),
            'oos_score':      round(oos, 3) if oos else None,
            'win_rate_pct':   round(wr, 1),
            'n_activations':  n,
            'category':       r['category'],
        })

    ranked.sort(key=lambda x: -x['composite_score'])

    # Save rankings
    now = datetime.now().isoformat()
    for i, r in enumerate(ranked):
        conn.execute("""
            INSERT OR REPLACE INTO alpha_rankings
            (hyp_id, hyp_name, ranked_at, composite_score, expectancy_pct,
             oos_score, win_rate_pct, n_activations, grade, is_alive)
            VALUES (?,?,?,?,?,?,?,?,?,1)
        """, (r['hyp_id'], r['hyp_name'], now, r['composite_score'],
              r['expectancy_pct'], r['oos_score'], r['win_rate_pct'],
              r['n_activations'], r['grade']))
    conn.commit()
    conn.close()

    grade_dist = {}
    for r in ranked:
        grade_dist[r['grade']] = grade_dist.get(r['grade'], 0) + 1

    return {
        "success":          True,
        "n_ranked":         len(ranked),
        "grade_distribution": grade_dist,
        "top_10":           ranked[:10],
        "avg_composite":    round(sum(r['composite_score'] for r in ranked)/len(ranked), 1) if ranked else 0,
    }

# ─────────────────────────────────────────────
# Kill weak hypotheses
# ─────────────────────────────────────────────
def kill_weak(params):
    dry_run   = params.get('dry_run', True)
    exp_floor = float(params.get('min_expectancy', KILL_IF['expectancy_pct_lt']))
    oos_floor = float(params.get('min_oos_score',  KILL_IF['oos_score_lt']))
    n_floor   = int(params.get('min_activations',  KILL_IF['n_activations_lt']))
    wr_floor  = float(params.get('min_win_rate',   KILL_IF['win_rate_lt']))

    conn = db()
    ensure_tables(conn)

    candidates = conn.execute("""
        SELECT hyp_id, hyp_name, expectancy_pct, oos_score, win_rate_pct, n_activations
        FROM research_results
        WHERE status = 'ACTIVE'
    """).fetchall()

    killed = []
    for r in candidates:
        reasons = []
        if (r['expectancy_pct'] or 0) < exp_floor:
            reasons.append(f"expectancy={r['expectancy_pct']:.3f}<{exp_floor}")
        if r['oos_score'] and r['oos_score'] < oos_floor and r['n_activations'] >= 30:
            reasons.append(f"oos_score={r['oos_score']:.3f}<{oos_floor}")
        if (r['n_activations'] or 0) < n_floor:
            reasons.append(f"n_acts={r['n_activations']}<{n_floor}")
        if (r['win_rate_pct'] or 0) < wr_floor * 100:
            reasons.append(f"win_rate={r['win_rate_pct']:.1f}%<{wr_floor*100}%")

        # Composite kill: mediocre WR AND low EV together (v3)
        comp_wr_thr = float(params.get('composite_wr', KILL_IF.get('composite_wr_lt', 0.42)))
        comp_ev_thr = float(params.get('composite_ev', KILL_IF.get('composite_ev_lt', 1.5)))
        if (r['win_rate_pct'] or 0) < comp_wr_thr * 100 and (r['expectancy_pct'] or 0) < comp_ev_thr:
            reasons.append(
                f"composite_weak: WR={r['win_rate_pct']:.1f}%<{comp_wr_thr*100:.0f}% "
                f"AND EV={r['expectancy_pct']:.2f}%<{comp_ev_thr}%"
            )

        if reasons:
            killed.append({'hyp_id': r['hyp_id'], 'hyp_name': r['hyp_name'],
                           'reasons': reasons})

    if not dry_run and killed:
        for k in killed:
            reason_str = "; ".join(k['reasons'])
            conn.execute("""
                UPDATE research_results SET status='KILLED' WHERE hyp_id=?
            """, (k['hyp_id'],))
            conn.execute("""
                UPDATE alpha_rankings SET is_alive=0, kill_reason=? WHERE hyp_id=?
            """, (reason_str, k['hyp_id']))
        conn.commit()

    conn.close()

    return {
        "success":    True,
        "dry_run":    dry_run,
        "n_killed":   len(killed),
        "killed":     killed[:20],
        "message":    "dry_run — no changes" if dry_run else f"Killed {len(killed)} hypotheses",
    }

# ─────────────────────────────────────────────
# Decay check — is alpha degrading over time?
# ─────────────────────────────────────────────
def decay_check(params):
    conn = db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT r.hyp_id, r.hyp_name, r.is_precision, r.oos_precision,
               r.is_samples, r.oos_samples, r.expectancy_pct, r.n_activations
        FROM research_results r
        WHERE r.status = 'ACTIVE'
          AND r.is_precision IS NOT NULL
          AND r.oos_precision IS NOT NULL
          AND r.is_samples >= 20
          AND r.oos_samples >= 10
    """).fetchall()

    decaying = []
    stable   = []

    for r in rows:
        is_p  = r['is_precision']
        oos_p = r['oos_precision']
        decay = (is_p - oos_p) / is_p if is_p > 0 else 0
        is_decaying = decay > 0.30  # more than 30% performance drop

        entry = {
            'hyp_id':       r['hyp_id'],
            'hyp_name':     r['hyp_name'],
            'is_precision':  round(is_p, 4),
            'oos_precision': round(oos_p, 4),
            'decay_pct':     round(decay * 100, 1),
            'is_decaying':   is_decaying,
        }

        # Log decay
        conn.execute("""
            INSERT INTO alpha_decay_log (hyp_id, decay_score, is_decaying, notes)
            VALUES (?,?,?,?)
        """, (r['hyp_id'], decay, 1 if is_decaying else 0,
              f"IS={is_p:.3f} OOS={oos_p:.3f} drop={decay*100:.1f}%"))

        if is_decaying:
            decaying.append(entry)
        else:
            stable.append(entry)

    conn.commit()
    conn.close()

    return {
        "success":     True,
        "n_checked":   len(rows),
        "n_decaying":  len(decaying),
        "n_stable":    len(stable),
        "decaying":    sorted(decaying, key=lambda x: -x['decay_pct'])[:10],
        "stable_top5": sorted(stable, key=lambda x: x['decay_pct'])[:5],
    }

# ─────────────────────────────────────────────
# Leaderboard — top alive strategies
# ─────────────────────────────────────────────
def leaderboard(params):
    limit = int(params.get('limit', 20))
    conn  = db()
    ensure_tables(conn)

    rows = conn.execute("""
        SELECT ar.*, h.category, h.conditions_json,
               r.avg_net_return, r.is_samples, r.oos_samples
        FROM alpha_rankings ar
        JOIN research_results r ON ar.hyp_id = r.hyp_id
        LEFT JOIN hypotheses h ON ar.hyp_id = h.hyp_id
        WHERE ar.is_alive = 1
        ORDER BY ar.composite_score DESC
        LIMIT ?
    """, (limit,)).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        try:
            conds = json.loads(d.pop('conditions_json', '[]'))
            d['conditions_summary'] = ' AND '.join(
                f"{c['col']}{c['op']}{c['val']}" for c in conds)
        except: d.pop('conditions_json', None)
        result.append(d)

    # Grade distribution
    all_grades = conn.execute("""
        SELECT grade, COUNT(*) n FROM alpha_rankings WHERE is_alive=1 GROUP BY grade
    """).fetchall()
    grade_dist = {r['grade']: r['n'] for r in all_grades}

    conn.close()

    return {
        "success":        True,
        "n_alive":        sum(grade_dist.values()),
        "grade_dist":     grade_dist,
        "leaderboard":    result,
    }

# ─────────────────────────────────────────────
# Evolve — mutate top hypotheses to create new ones
# ─────────────────────────────────────────────
def evolve(params):
    n_top    = int(params.get('n_top', 5))
    n_mutate = int(params.get('n_mutate', 3))
    conn     = db()
    ensure_tables(conn)

    top = conn.execute("""
        SELECT ar.hyp_id, r.expectancy_pct, r.oos_score, h.conditions_json,
               h.direction, h.holding_days, h.hyp_name
        FROM alpha_rankings ar
        JOIN research_results r ON ar.hyp_id = r.hyp_id
        JOIN hypotheses h ON ar.hyp_id = h.hyp_id
        WHERE ar.is_alive = 1 AND ar.composite_score >= 50
        ORDER BY ar.composite_score DESC
        LIMIT ?
    """, (n_top,)).fetchall()

    if not top:
        conn.close()
        return {"success": True, "message": "No high-quality strategies to evolve yet", "n_children": 0}

    children   = []
    import hashlib

    MUTATIONS = [
        ('tighten_threshold', 0.8),   # multiply thresholds by 0.8 (stricter)
        ('loosen_threshold',  1.2),   # multiply by 1.2 (looser)
        ('extend_holding',    None),  # double holding days
        ('add_volume_filter', None),  # add vol_ratio_20 > 1.3
        ('add_macd_filter',   None),  # add macd_hist > 0
    ]

    for parent in top:
        try:
            conds_orig = json.loads(parent['conditions_json'])
        except:
            continue

        for i, (mut_type, factor) in enumerate(MUTATIONS[:n_mutate]):
            new_conds   = []
            new_holding = parent['holding_days']

            if mut_type == 'tighten_threshold':
                for c in conds_orig:
                    nc = dict(c)
                    nc['val'] = round(c['val'] * factor, 3)
                    new_conds.append(nc)
            elif mut_type == 'loosen_threshold':
                for c in conds_orig:
                    nc = dict(c)
                    nc['val'] = round(c['val'] * factor, 3)
                    new_conds.append(nc)
            elif mut_type == 'extend_holding':
                new_conds   = list(conds_orig)
                new_holding = min(20, parent['holding_days'] * 2)
            elif mut_type == 'add_volume_filter':
                new_conds = list(conds_orig)
                if not any(c['col'] == 'vol_ratio_20' for c in new_conds):
                    new_conds.append({'col': 'vol_ratio_20', 'op': '>', 'val': 1.3})
            elif mut_type == 'add_macd_filter':
                new_conds = list(conds_orig)
                if not any(c['col'] == 'macd_hist' for c in new_conds):
                    new_conds.append({'col': 'macd_hist', 'op': '>', 'val': 0.0})

            if not new_conds:
                continue

            key    = json.dumps({'c': new_conds, 'd': parent['direction'], 'h': new_holding}, sort_keys=True)
            hyp_id = 'EVO_' + hashlib.sha1(key.encode()).hexdigest()[:10].upper()
            name   = f"evolved_{parent['hyp_name']}_{mut_type}"

            conn.execute("""
                INSERT OR IGNORE INTO hypotheses
                (hyp_id, hyp_name, category, conditions_json, direction, holding_days, source)
                VALUES (?,?,?,?,?,?,?)
            """, (hyp_id, name, 'evolved', json.dumps(new_conds),
                  parent['direction'], new_holding, 'evolved'))

            if conn.execute("SELECT changes()").fetchone()[0]:
                conn.execute("""
                    INSERT INTO evolved_hypotheses
                    (parent_hyp_id, child_hyp_id, evolution_type, description)
                    VALUES (?,?,?,?)
                """, (parent['hyp_id'], hyp_id, mut_type, f"{parent['hyp_name']} → {mut_type}"))
                children.append({'parent': parent['hyp_id'], 'child': hyp_id,
                                 'mutation': mut_type})

    conn.commit()
    conn.close()

    return {
        "success":    True,
        "n_parents":  len(top),
        "n_children": len(children),
        "children":   children,
        "next":       "Run research_grid run_grid to test evolved hypotheses",
    }

# ─────────────────────────────────────────────
# Build full
# ─────────────────────────────────────────────
def build_full(params):
    rank   = rank_all({})
    kill   = kill_weak({'dry_run': True})
    decay  = decay_check({})
    leader = leaderboard({'limit': 10})
    evo    = evolve({'n_top': 5, 'n_mutate': 3})

    return {
        "success":    True,
        "ranked":     rank['n_ranked'],
        "grade_dist": rank['grade_distribution'],
        "kill_candidates": kill['n_killed'],
        "decaying":   decay['n_decaying'],
        "evolved_children": evo['n_children'],
        "top_5":      leader['leaderboard'][:5],
    }

# ─────────────────────────────────────────────
if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'leaderboard'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    dispatch = {
        'rank_all':    rank_all,
        'kill_weak':   kill_weak,
        'decay_check': decay_check,
        'leaderboard': leaderboard,
        'evolve':      evolve,
        'build_full':  build_full,
    }

    fn = dispatch.get(cmd)
    if fn:
        print(json.dumps(fn(params), default=str))
    else:
        print(json.dumps({"error": f"Unknown: {cmd}", "available": list(dispatch.keys())}))
