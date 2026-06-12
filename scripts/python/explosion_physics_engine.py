"""
explosion_physics_engine.py — Phase 24
EGX Market Intelligence: Explosion Readiness & Physics Analysis

Usage: python explosion_physics_engine.py <command> '<json_params>'
Commands: compute_readiness, analyze_signatures, false_explosion_anatomy,
          sector_physics, daily_watchlist, build_full
"""

import os
import sys
import json
import math
import sqlite3
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS explosion_readiness (
        er_id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        readiness_score REAL DEFAULT 0,
        compression_index REAL DEFAULT 0,
        liquidity_absorption REAL DEFAULT 0,
        structural_energy REAL DEFAULT 0,
        contagion_alignment REAL DEFAULT 0,
        macro_alignment REAL DEFAULT 0,
        regime_alignment REAL DEFAULT 0,
        matching_archetype TEXT,
        historical_match_pct REAL DEFAULT 0,
        expected_failure_mode TEXT,
        UNIQUE(symbol, date)
    );
    CREATE TABLE IF NOT EXISTS explosion_signatures (
        sig_id INTEGER PRIMARY KEY AUTOINCREMENT,
        archetype_id INTEGER,
        archetype_name TEXT,
        signature_type TEXT,
        sector TEXT,
        regime TEXT,
        feature_vector TEXT,
        precision REAL DEFAULT 0,
        support_count INTEGER DEFAULT 0,
        false_positive_rate REAL DEFAULT 0,
        created_at TEXT
    );
    """)
    db.commit()


# ---------------------------------------------------------------------------
# Math / indicator helpers
# ---------------------------------------------------------------------------

def compute_bb_width(closes, period=20, std_mult=2.0):
    """Bollinger Band Width = (upper - lower) / middle using pure Python."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return (upper - lower) / sma if sma > 0 else None


def rolling_sma(series, period):
    """Return list of SMAs (None for positions before full window)."""
    result = []
    for i in range(len(series)):
        if i < period - 1:
            result.append(None)
        else:
            window = series[i - period + 1: i + 1]
            result.append(sum(window) / period)
    return result


def compute_volume_trend(volumes, period=5):
    """
    Liquidity Absorption proxy:
    Increasing volume over last 5 bars relative to prior 5 bars
    Returns a value in [-1, 1].
    """
    if len(volumes) < period * 2:
        return 0.0
    recent = volumes[-period:]
    prior = volumes[-period * 2:-period]
    avg_recent = sum(recent) / period
    avg_prior = sum(prior) / period
    if avg_prior == 0:
        return 0.0
    ratio = (avg_recent - avg_prior) / avg_prior
    return max(-1.0, min(1.0, ratio))


def compute_compression_index(closes, period=20):
    """
    Compression Index = 1 - (current_bbw / max_bbw over last period bars)
    0 = no compression, 1 = maximum compression.
    """
    if len(closes) < period + period:  # need 2x period for rolling max
        # Fallback: just compare current to recent average
        if len(closes) < period:
            return 0.0
        current_bbw = compute_bb_width(closes, period)
        if current_bbw is None:
            return 0.0
        # No historical max available — treat as no compression
        return 0.0

    # Compute rolling BBW
    bbw_series = []
    for i in range(period, len(closes) + 1):
        w = compute_bb_width(closes[:i], period)
        if w is not None:
            bbw_series.append(w)

    if not bbw_series:
        return 0.0

    current_bbw = bbw_series[-1]
    # Max BBW over last `period` values of the rolling series
    window_bbw = bbw_series[-period:] if len(bbw_series) >= period else bbw_series
    max_bbw = max(window_bbw)

    if max_bbw == 0:
        return 0.0

    return max(0.0, min(1.0, 1.0 - current_bbw / max_bbw))


def compute_structural_energy(compression_index, compression_days, volume_surge):
    """Structural Energy = compression_index * compression_days * volume_surge."""
    return compression_index * compression_days * volume_surge


def estimate_compression_days(closes, volumes, period=20):
    """
    Count consecutive bars where BB Width has been below 50th percentile
    (i.e. in a narrow / compressed state).
    """
    if len(closes) < period:
        return 0

    bbw_series = []
    for i in range(period, len(closes) + 1):
        w = compute_bb_width(closes[:i], period)
        if w is not None:
            bbw_series.append(w)

    if not bbw_series:
        return 0

    sorted_bbw = sorted(bbw_series)
    median_bbw = sorted_bbw[len(sorted_bbw) // 2]

    # Count consecutive bars from end where BBW < median
    count = 0
    for bbw in reversed(bbw_series):
        if bbw <= median_bbw:
            count += 1
        else:
            break
    return count


def get_today_str(params):
    """Resolve 'today' or specific date from params."""
    date_param = params.get('date', 'today')
    if date_param == 'today' or not date_param:
        return datetime.date.today().isoformat()
    return date_param


def fetch_ohlcv(db, symbol, count=40):
    """Fetch last N bars for symbol, ordered oldest first."""
    rows = db.execute(
        "SELECT bar_time, open, high, low, close, volume "
        "FROM ohlcv_history_execution WHERE symbol=? "
        "ORDER BY bar_time DESC LIMIT ?",
        (symbol, count)
    ).fetchall()
    # Reverse so oldest first
    rows = list(reversed(rows))
    return rows


def get_current_regime(db, as_of_date=None):
    """Get the most recent regime on or before as_of_date."""
    if as_of_date:
        row = db.execute(
            "SELECT regime FROM regime_history WHERE date <= ? ORDER BY date DESC LIMIT 1",
            (as_of_date,)
        ).fetchone()
    else:
        row = db.execute(
            "SELECT regime FROM regime_history ORDER BY date DESC LIMIT 1"
        ).fetchone()
    return row['regime'] if row else 'UNKNOWN'


def get_sector_contagion_score(db, sector):
    """Get a contagion alignment score for sector (0-1)."""
    try:
        rows = db.execute(
            "SELECT * FROM sector_contagion WHERE sector=? LIMIT 5",
            (sector,)
        ).fetchall()
        if not rows:
            return 0.5  # neutral
        # Presence in contagion table = sector is active
        return min(1.0, 0.5 + len(rows) * 0.1)
    except Exception:
        return 0.5


def match_archetype(db, compression_index, structural_energy, volume_surge):
    """
    Match symbol metrics against explosion_archetypes.
    Returns (archetype_name, match_pct, expected_failure_mode).
    """
    archetypes = db.execute(
        "SELECT archetype_id, archetype_name, false_breakout_rate, "
        "avg_return_5d, signature_features FROM explosion_archetypes"
    ).fetchall()

    if not archetypes:
        return 'UNKNOWN', 0.0, 'INSUFFICIENT_DATA'

    best_name = 'UNKNOWN'
    best_score = -1.0
    best_fbr = 0.0

    for arch in archetypes:
        try:
            features = json.loads(arch['signature_features']) if arch['signature_features'] else {}
        except Exception:
            features = {}

        # Simple scoring: compare compression to archetype expectations
        arch_comp = features.get('avg_bb_compression', 0.5)
        arch_vol = features.get('avg_volume_surge', 1.0)

        comp_diff = abs(compression_index - arch_comp)
        vol_diff = abs(volume_surge - arch_vol)

        # Score = 1 - normalized distance (closer = better)
        score = 1.0 - min(1.0, (comp_diff * 0.6 + min(vol_diff / 3.0, 1.0) * 0.4))

        if score > best_score:
            best_score = score
            best_name = arch['archetype_name']
            best_fbr = arch['false_breakout_rate'] or 0.0

    match_pct = round(best_score * 100, 1)
    failure_mode = 'FALSE_BREAKOUT' if best_fbr > 35 else 'FADE' if best_fbr > 20 else 'SUSTAINED'
    return best_name, match_pct, failure_mode


# ---------------------------------------------------------------------------
# Command: compute_readiness
# ---------------------------------------------------------------------------

def compute_readiness(params):
    db = get_db()
    try:
        target_date = get_today_str(params)
        regime = get_current_regime(db, target_date)

        # Regime alignment multiplier
        regime_bonus = 1.0 if 'BULL' in regime.upper() else 0.7 if 'BEAR' in regime.upper() else 0.85

        symbols = db.execute(
            "SELECT symbol, sector FROM stock_universe WHERE status='fetched'"
        ).fetchall()

        n_computed = 0
        results = []

        for sym_row in symbols:
            symbol = sym_row['symbol']
            sector = sym_row['sector'] or 'Unknown'

            bars = fetch_ohlcv(db, symbol, count=50)
            if len(bars) < 25:
                continue

            closes = [float(b['close']) for b in bars]
            volumes = [float(b['volume']) for b in bars if b['volume'] is not None]

            if len(closes) < 20:
                continue

            # BB computations
            current_bbw = compute_bb_width(closes, 20)
            if current_bbw is None:
                continue

            compression_idx = compute_compression_index(closes, 20)
            compression_days = estimate_compression_days(closes, volumes, 20)

            # Volume metrics
            avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else (sum(volumes) / len(volumes) if volumes else 1)
            current_vol = volumes[-1] if volumes else avg_vol_20
            volume_surge = current_vol / avg_vol_20 if avg_vol_20 > 0 else 1.0

            # Liquidity absorption (rolling 5d trend)
            liquidity_abs = max(0.0, compute_volume_trend(volumes, 5))

            # Structural energy
            structural_en = compute_structural_energy(compression_idx, max(1, compression_days), volume_surge)
            # Normalize structural energy to 0-1 range
            structural_en_norm = min(1.0, structural_en / 10.0)

            # Contagion alignment
            contagion_score = get_sector_contagion_score(db, sector)

            # Regime alignment (0-1)
            regime_align = regime_bonus

            # Archetype matching
            arch_name, match_pct, failure_mode = match_archetype(
                db, compression_idx, structural_en_norm, volume_surge
            )

            # Historical match contribution
            hist_score = match_pct / 100.0

            # Weighted readiness score
            readiness = (
                compression_idx * 30
                + structural_en_norm * 25
                + contagion_score * 20
                + regime_align * 15
                + hist_score * 10
            )
            readiness = round(min(100.0, readiness), 2)

            # Save to DB
            db.execute("""
                INSERT OR REPLACE INTO explosion_readiness
                (symbol, date, readiness_score, compression_index, liquidity_absorption,
                 structural_energy, contagion_alignment, macro_alignment, regime_alignment,
                 matching_archetype, historical_match_pct, expected_failure_mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, target_date, readiness,
                round(compression_idx, 4),
                round(liquidity_abs, 4),
                round(structural_en_norm, 4),
                round(contagion_score, 4),
                round(hist_score, 4),
                round(regime_align, 4),
                arch_name,
                round(match_pct, 1),
                failure_mode
            ))
            db.commit()

            n_computed += 1
            results.append({
                'symbol': symbol,
                'score': readiness,
                'archetype': arch_name,
                'compression': round(compression_idx, 3),
                'sector': sector,
                'failure_mode': failure_mode
            })

        # Sort and get top candidates
        results.sort(key=lambda x: x['score'], reverse=True)
        top_candidates = results[:20]

        # Score distribution
        scores = [r['score'] for r in results]
        dist = {}
        for r in results:
            bracket = f"{int(r['score'] // 10) * 10}-{int(r['score'] // 10) * 10 + 10}"
            dist[bracket] = dist.get(bracket, 0) + 1

        return {
            'date': target_date,
            'regime': regime,
            'n_computed': n_computed,
            'top_candidates': top_candidates,
            'distribution': dist,
            'avg_score': round(sum(scores) / len(scores), 2) if scores else 0
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: analyze_signatures
# ---------------------------------------------------------------------------

def analyze_signatures(params):
    db = get_db()
    try:
        physics = db.execute(
            "SELECT symbol, direction, explosion_class, compression_days, "
            "compression_depth, ignition_speed FROM market_physics"
        ).fetchall()

        archetypes = db.execute(
            "SELECT archetype_id, archetype_name, n_members, avg_return_1d, "
            "avg_return_5d, false_breakout_rate, signature_features FROM explosion_archetypes"
        ).fetchall()

        explosive = db.execute(
            "SELECT symbol, explosion_date, direction, return_1d, return_5d, "
            "explosion_class, sector, pre1_bb_width FROM explosive_moves"
        ).fetchall()

        # Build sector distribution from explosive moves
        sector_dist = {}
        for em in explosive:
            s = em['sector'] or 'Unknown'
            sector_dist[s] = sector_dist.get(s, 0) + 1

        # Group physics by class
        class_stats = {}
        for p in physics:
            cls = p['explosion_class'] or 'UNKNOWN'
            if cls not in class_stats:
                class_stats[cls] = {
                    'compression_days': [],
                    'compression_depth': [],
                    'ignition_speed': [],
                    'count': 0
                }
            class_stats[cls]['count'] += 1
            if p['compression_days'] is not None:
                class_stats[cls]['compression_days'].append(p['compression_days'])
            if p['compression_depth'] is not None:
                class_stats[cls]['compression_depth'].append(p['compression_depth'])
            if p['ignition_speed'] is not None:
                class_stats[cls]['ignition_speed'].append(p['ignition_speed'])

        archetype_signatures = []
        now_str = datetime.datetime.utcnow().isoformat()

        for arch in archetypes:
            arch_id = arch['archetype_id']
            arch_name = arch['archetype_name']

            try:
                sig_feats = json.loads(arch['signature_features']) if arch['signature_features'] else {}
            except Exception:
                sig_feats = {}

            # Match physics rows to this archetype by explosion_class pattern
            matching_class = None
            if arch_name:
                for cls in class_stats:
                    if any(word.lower() in arch_name.lower() for word in cls.split('_')):
                        matching_class = cls
                        break

            stats = class_stats.get(matching_class or '', {})
            cd_list = stats.get('compression_days', [])
            ig_list = stats.get('ignition_speed', [])

            avg_comp = sum(cd_list) / len(cd_list) if cd_list else sig_feats.get('avg_compression_days', 0)
            avg_ignition = sum(ig_list) / len(ig_list) if ig_list else 0
            n_support = stats.get('count', arch['n_members'] or 0)

            feature_vector = json.dumps({
                'avg_compression_days': round(float(avg_comp), 2),
                'avg_ignition_speed': round(float(avg_ignition), 4),
                'avg_return_1d': float(arch['avg_return_1d'] or 0),
                'avg_return_5d': float(arch['avg_return_5d'] or 0),
                'false_breakout_rate': float(arch['false_breakout_rate'] or 0),
                'sector_distribution': sector_dist,
                'signature_source_features': sig_feats
            })

            precision = max(0.0, 1.0 - (arch['false_breakout_rate'] or 0) / 100.0)
            fpr = (arch['false_breakout_rate'] or 0) / 100.0

            db.execute("""
                INSERT INTO explosion_signatures
                (archetype_id, archetype_name, signature_type, sector, regime,
                 feature_vector, precision, support_count, false_positive_rate, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                arch_id, arch_name, 'ARCHETYPE_SIGNATURE',
                'ALL', 'ALL',
                feature_vector, round(precision, 4),
                n_support, round(fpr, 4), now_str
            ))

            archetype_signatures.append({
                'archetype_id': arch_id,
                'archetype_name': arch_name,
                'avg_compression_days': round(float(avg_comp), 1),
                'avg_ignition_speed': round(float(avg_ignition), 4),
                'n_support': n_support,
                'precision': round(precision, 3),
                'false_positive_rate': round(fpr, 3)
            })

        db.commit()

        return {
            'n_signatures': len(archetype_signatures),
            'n_physics_rows': len(physics),
            'n_explosive_moves': len(explosive),
            'sector_distribution': sector_dist,
            'archetype_signatures': archetype_signatures
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: false_explosion_anatomy
# ---------------------------------------------------------------------------

def false_explosion_anatomy(params):
    db = get_db()
    try:
        moves = db.execute(
            "SELECT symbol, explosion_date, direction, return_1d, return_3d, "
            "return_5d, explosion_class, sector, pre1_bb_width FROM explosive_moves"
        ).fetchall()

        true_explosions = []
        false_explosions = []

        for m in moves:
            r1 = float(m['return_1d'] or 0)
            r5 = float(m['return_5d'] or 0)
            r3 = float(m['return_3d'] or 0)

            abs_r1 = abs(r1)

            if abs_r1 < 3.0:
                continue  # Not a meaningful move

            # Failed: large 1d return but return_5d < return_1d/2
            threshold = r1 / 2 if r1 > 0 else r1 / 2
            if r1 > 5 and r5 < r1 / 2:
                false_explosions.append(dict(m))
            elif r1 < -5 and r5 > r1 / 2:
                false_explosions.append(dict(m))
            else:
                true_explosions.append(dict(m))

        def avg_feature(lst, key):
            vals = [float(x[key]) for x in lst if x.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None

        def sector_breakdown(lst):
            dist = {}
            for x in lst:
                s = x.get('sector') or 'Unknown'
                dist[s] = dist.get(s, 0) + 1
            return dict(sorted(dist.items(), key=lambda kv: kv[1], reverse=True)[:5])

        def class_breakdown(lst):
            dist = {}
            for x in lst:
                c = x.get('explosion_class') or 'Unknown'
                dist[c] = dist.get(c, 0) + 1
            return dist

        differentiating = {
            'avg_1d_return': {
                'true': avg_feature(true_explosions, 'return_1d'),
                'false': avg_feature(false_explosions, 'return_1d')
            },
            'avg_3d_return': {
                'true': avg_feature(true_explosions, 'return_3d'),
                'false': avg_feature(false_explosions, 'return_3d')
            },
            'avg_5d_return': {
                'true': avg_feature(true_explosions, 'return_5d'),
                'false': avg_feature(false_explosions, 'return_5d')
            },
            'avg_pre_bb_width': {
                'true': avg_feature(true_explosions, 'pre1_bb_width'),
                'false': avg_feature(false_explosions, 'pre1_bb_width')
            },
            'sector_distribution': {
                'true': sector_breakdown(true_explosions),
                'false': sector_breakdown(false_explosions)
            },
            'explosion_class': {
                'true': class_breakdown(true_explosions),
                'false': class_breakdown(false_explosions)
            }
        }

        # Key insight: how much tighter is BB before false explosions?
        true_bbw = avg_feature(true_explosions, 'pre1_bb_width')
        false_bbw = avg_feature(false_explosions, 'pre1_bb_width')
        if true_bbw and false_bbw and true_bbw != 0:
            bbw_ratio = round((false_bbw - true_bbw) / true_bbw * 100, 1)
            differentiating['bbw_difference_pct'] = bbw_ratio

        return {
            'n_false': len(false_explosions),
            'n_true': len(true_explosions),
            'false_rate_pct': round(len(false_explosions) / max(1, len(moves)) * 100, 1),
            'differentiating_features': differentiating,
            'sample_false': [
                {'symbol': x['symbol'], 'date': x['explosion_date'],
                 'return_1d': x['return_1d'], 'return_5d': x['return_5d']}
                for x in false_explosions[:5]
            ]
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: sector_physics
# ---------------------------------------------------------------------------

def sector_physics(params):
    db = get_db()
    try:
        sector = params.get('sector', 'Finance')

        all_moves = db.execute(
            "SELECT symbol, explosion_date, direction, return_1d, return_3d, "
            "return_5d, explosion_class, sector FROM explosive_moves"
        ).fetchall()

        all_physics = db.execute(
            "SELECT symbol, direction, explosion_class, compression_days, "
            "compression_depth, ignition_speed FROM market_physics"
        ).fetchall()

        # Build symbol->sector map
        sym_sector = {}
        for m in all_moves:
            if m['symbol']:
                sym_sector[m['symbol']] = m['sector'] or 'Unknown'

        def safe_float(val):
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        def avg_vals(lst):
            lst = [x for x in lst if x is not None]
            return round(sum(lst) / len(lst), 4) if lst else None

        # Sector-specific moves
        sector_moves = [m for m in all_moves if (m['sector'] or '').lower() == sector.lower()]
        market_moves = all_moves

        # Sector physics (join by symbol)
        sector_syms = {m['symbol'] for m in sector_moves}
        sector_phys = [p for p in all_physics if p['symbol'] in sector_syms]
        market_phys = all_physics

        # Compute averages
        def compute_averages(moves, physics):
            r1 = avg_vals([safe_float(m['return_1d']) for m in moves])
            r5 = avg_vals([safe_float(m['return_5d']) for m in moves])
            comp = avg_vals([safe_float(p['compression_days']) for p in physics])
            ignition = avg_vals([safe_float(p['ignition_speed']) for p in physics])
            # Sustain = r5 / r1 where both positive
            sustains = []
            for m in moves:
                r1v = safe_float(m['return_1d'])
                r5v = safe_float(m['return_5d'])
                if r1v and r5v and r1v != 0 and r1v > 0:
                    sustains.append(r5v / r1v)
            sustain_r = avg_vals(sustains)
            return r1, r5, comp, ignition, sustain_r

        s_r1, s_r5, s_comp, s_ignition, s_sustain = compute_averages(sector_moves, sector_phys)
        m_r1, m_r5, m_comp, m_ignition, m_sustain = compute_averages(market_moves, market_phys)

        def vs(sector_val, market_val, label):
            if sector_val is None or market_val is None or market_val == 0:
                return None
            return round((sector_val - market_val) / abs(market_val) * 100, 1)

        sector_signature = {
            'n_explosions': len(sector_moves),
            'avg_return_1d': s_r1,
            'avg_return_5d': s_r5,
            'avg_compression_days': s_comp,
            'avg_ignition_speed': s_ignition,
            'avg_sustain_ratio': s_sustain
        }

        return {
            'sector': sector,
            'n_sector_explosions': len(sector_moves),
            'n_market_explosions': len(market_moves),
            'vs_market_compression': vs(s_comp, m_comp, 'compression'),
            'vs_market_return_1d': vs(s_r1, m_r1, 'return_1d'),
            'vs_market_return_5d': vs(s_r5, m_r5, 'return_5d'),
            'vs_market_ignition': vs(s_ignition, m_ignition, 'ignition'),
            'vs_market_sustain': vs(s_sustain, m_sustain, 'sustain'),
            'sector_signature': sector_signature,
            'market_averages': {
                'avg_return_1d': m_r1,
                'avg_return_5d': m_r5,
                'avg_compression_days': m_comp,
                'avg_ignition_speed': m_ignition,
                'avg_sustain_ratio': m_sustain
            }
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: daily_watchlist
# ---------------------------------------------------------------------------

def daily_watchlist(params):
    db = get_db()
    try:
        # Find latest readiness date
        latest = db.execute(
            "SELECT MAX(date) as max_date FROM explosion_readiness"
        ).fetchone()
        target_date = latest['max_date'] if latest and latest['max_date'] else None

        if not target_date:
            return {'error': 'No readiness data found. Run compute_readiness first.'}

        rows = db.execute(
            "SELECT er.*, su.sector "
            "FROM explosion_readiness er "
            "LEFT JOIN stock_universe su ON er.symbol = su.symbol "
            "WHERE er.date = ? "
            "ORDER BY er.readiness_score DESC LIMIT 20",
            (target_date,)
        ).fetchall()

        # Get current regime
        regime = get_current_regime(db, target_date)

        # Get stock DNA context
        dna_map = {}
        try:
            dna_rows = db.execute(
                "SELECT symbol, archetype, explosion_rate_pct, false_breakout_rate_pct, "
                "avg_return_5d FROM stock_dna"
            ).fetchall()
            for d in dna_rows:
                dna_map[d['symbol']] = dict(d)
        except Exception:
            pass

        watchlist = []
        for row in rows:
            symbol = row['symbol']
            dna = dna_map.get(symbol, {})

            drivers = []
            if row['compression_index'] > 0.7:
                drivers.append('HIGH_COMPRESSION')
            if row['structural_energy'] > 0.5:
                drivers.append('STRUCTURAL_ENERGY')
            if row['contagion_alignment'] > 0.7:
                drivers.append('SECTOR_CONTAGION')
            if row['regime_alignment'] > 0.9:
                drivers.append('REGIME_TAILWIND')
            if not drivers:
                drivers.append('MODERATE_SETUP')

            risk_factors = []
            if row['expected_failure_mode'] == 'FALSE_BREAKOUT':
                risk_factors.append('HIGH_FALSE_BREAKOUT_RISK')
            fbr = dna.get('false_breakout_rate_pct', 0)
            if fbr and float(fbr) > 35:
                risk_factors.append(f'HISTORICAL_FBR_{int(fbr)}%')
            if row['liquidity_absorption'] < 0.1:
                risk_factors.append('LOW_LIQUIDITY_ABSORPTION')
            if not risk_factors:
                risk_factors.append('STANDARD_RISK')

            watchlist.append({
                'symbol': symbol,
                'score': round(row['readiness_score'], 2),
                'archetype': row['matching_archetype'],
                'sector': row['sector'],
                'compression': round(row['compression_index'], 3),
                'structural_energy': round(row['structural_energy'], 3),
                'drivers': drivers,
                'risk': risk_factors,
                'expected_move_5d': dna.get('avg_return_5d'),
                'failure_mode': row['expected_failure_mode']
            })

        return {
            'date': target_date,
            'regime': regime,
            'watchlist': watchlist,
            'n_total_candidates': len(watchlist)
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Command: build_full
# ---------------------------------------------------------------------------

def build_full(params):
    db = get_db()
    db.close()

    results = {}

    # Step 1: analyze signatures
    try:
        results['signatures'] = analyze_signatures({})
    except Exception as e:
        results['signatures'] = {'error': str(e)}

    # Step 2: compute readiness
    try:
        results['readiness'] = compute_readiness({'date': 'today'})
    except Exception as e:
        results['readiness'] = {'error': str(e)}

    # Step 3: sector physics for all sectors
    db2 = get_db()
    try:
        sectors = db2.execute(
            "SELECT DISTINCT sector FROM stock_universe WHERE sector IS NOT NULL"
        ).fetchall()
        sector_list = [s['sector'] for s in sectors if s['sector']]
    finally:
        db2.close()

    sector_results = {}
    for sector in sector_list[:8]:  # Cap at 8 sectors
        try:
            sector_results[sector] = sector_physics({'sector': sector})
        except Exception as e:
            sector_results[sector] = {'error': str(e)}
    results['sector_physics'] = sector_results

    # Step 4: false anatomy
    try:
        results['false_anatomy'] = false_explosion_anatomy({})
    except Exception as e:
        results['false_anatomy'] = {'error': str(e)}

    # Step 5: watchlist
    try:
        results['watchlist'] = daily_watchlist({})
    except Exception as e:
        results['watchlist'] = {'error': str(e)}

    return {
        'status': 'build_full_complete',
        'components_run': list(results.keys()),
        'summary': {
            'n_signatures': results.get('signatures', {}).get('n_signatures', 0),
            'n_readiness_computed': results.get('readiness', {}).get('n_computed', 0),
            'n_sectors_analyzed': len(sector_results),
            'watchlist_count': len(results.get('watchlist', {}).get('watchlist', [])),
            'false_explosion_rate': results.get('false_anatomy', {}).get('false_rate_pct', 0)
        },
        'details': results
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    'compute_readiness': compute_readiness,
    'analyze_signatures': analyze_signatures,
    'false_explosion_anatomy': false_explosion_anatomy,
    'sector_physics': sector_physics,
    'daily_watchlist': daily_watchlist,
    'build_full': build_full,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: python explosion_physics_engine.py <command> [json_params]'}))
        sys.exit(1)

    command = sys.argv[1]
    params = {}
    if len(sys.argv) >= 3:
        try:
            params = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(json.dumps({'error': f'Invalid JSON params: {e}'}))
            sys.exit(1)

    handler = COMMANDS.get(command)
    if not handler:
        print(json.dumps({
            'error': f'Unknown command: {command}',
            'available': list(COMMANDS.keys())
        }))
        sys.exit(1)

    try:
        result = handler(params)
        print(json.dumps(result, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({
            'error': str(e),
            'traceback': traceback.format_exc()
        }, default=str))
        sys.exit(1)


if __name__ == '__main__':
    main()
