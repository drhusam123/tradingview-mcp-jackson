#!/usr/bin/env python3
"""
Phase 76 — Genetic Strategy Evolution (DEAP)
"التطور الجيني للاستراتيجيات — اكتشاف قوانين الدخول تلقائياً"

يستخدم Genetic Programming لتطوير استراتيجيات دخول مبنية على:
  - RSI thresholds
  - Bollinger Band squeeze conditions
  - Volume ratios
  - Momentum conditions
  - Market breadth filters

كل "جين" هو (feature, direction, threshold).
كل "فرد" هو مجموعة من 2-5 شروط (AND logic).
الـ fitness هو: OOS expectancy * stability_score

Commands:
  evolve        — Run genetic evolution (30-50 generations)
  top_strategies — Show top evolved strategies
  validate      — Walk-forward validate top strategies
  report        — Full evolution report
"""
import sys, json, sqlite3, datetime, random, math
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).parent.parent.parent / 'data' / 'egx_trading.db'
EVOLVED_PATH = Path(__file__).parent.parent.parent / 'data' / 'evolved_strategies.json'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _safe(v, default=0.0):
    if v is None: return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except: return default


# ─────────────────────────────────────────────────────────────────────────────
# Gene & Individual Definitions
# ─────────────────────────────────────────────────────────────────────────────

# Available features and their realistic ranges
GENE_SPACE = {
    'pre1_rsi':             ('<',  [20, 25, 30, 35, 40, 45]),
    'pre3_rsi':             ('<',  [20, 25, 30, 35, 40, 45]),
    'pre5_rsi':             ('<',  [20, 25, 30, 35, 40, 45]),
    'pre1_bb_width':        ('<',  [0.03, 0.05, 0.07, 0.10, 0.12, 0.15]),
    'pre3_bb_width':        ('<',  [0.03, 0.05, 0.07, 0.10, 0.12, 0.15]),
    'pre1_vol_ratio':       ('>',  [1.2, 1.5, 2.0, 2.5, 3.0]),
    'pre3_vol_ratio':       ('>',  [1.2, 1.5, 2.0, 2.5, 3.0]),
    'pre5_momentum_5d':     ('>',  [-5.0, -2.0, 0.0, 2.0, 5.0]),
    'pre5_bb_position':     ('<',  [0.2, 0.3, 0.4]),
    'pre5_compression_days': ('>',  [3, 5, 7, 10]),
}


def _random_gene():
    feature = random.choice(list(GENE_SPACE.keys()))
    direction, values = GENE_SPACE[feature]
    threshold = random.choice(values)
    return (feature, direction, threshold)


def _random_individual(min_genes=2, max_genes=5):
    n = random.randint(min_genes, max_genes)
    genes = [_random_gene() for _ in range(n)]
    # Deduplicate by feature (keep last)
    seen = {}
    for g in genes:
        seen[g[0]] = g
    return list(seen.values())


def _individual_matches(individual, row):
    """Returns True if a data row satisfies all conditions in the individual."""
    for feat, direction, threshold in individual:
        val = _safe(row.get(feat))
        if direction == '<' and not (val < threshold):
            return False
        if direction == '>' and not (val > threshold):
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Data Loading
# ─────────────────────────────────────────────────────────────────────────────

def _load_explosion_data(conn):
    """Load explosive_moves with pre-event feature values."""
    rows = conn.execute("""
        SELECT symbol, explosion_date,
               pre1_rsi, pre3_rsi, pre5_rsi,
               pre1_bb_width, pre3_bb_width,
               pre1_vol_ratio, pre3_vol_ratio,
               pre5_momentum_5d, pre5_bb_position, pre5_compression_days,
               return_3d AS close_pct_change_3d
        FROM explosive_moves
        WHERE explosion_date IS NOT NULL
          AND pre1_rsi IS NOT NULL
        ORDER BY explosion_date
    """).fetchall()
    return [dict(r) for r in rows]


def _load_negative_sample(conn, n=2000):
    """Sample non-explosion dates with feature values (from ohlcv_history_execution)."""
    # We approximate negatives by sampling feature distributions from
    # explosive_moves with very low change (< 1%) as "near misses"
    rows = conn.execute("""
        SELECT symbol, explosion_date AS date,
               pre1_rsi, pre3_rsi, pre5_rsi,
               pre1_bb_width, pre3_bb_width,
               pre1_vol_ratio, pre3_vol_ratio,
               pre5_momentum_5d, pre5_bb_position, pre5_compression_days
        FROM explosive_moves
        WHERE ABS(return_3d) < 1.0
        ORDER BY RANDOM()
        LIMIT ?
    """, (n,)).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Fitness Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate(individual, positives, negatives, split_date='2025-01-01'):
    """Evaluate an individual strategy on IS and OOS data.

    Returns fitness score = OOS precision * sqrt(OOS activations) * stability
    """
    if not individual:
        return -999.0, {}

    # Split IS / OOS
    is_pos  = [r for r in positives if r['explosion_date'] < split_date]
    oos_pos = [r for r in positives if r['explosion_date'] >= split_date]
    is_neg  = negatives[:len(negatives)//2]
    oos_neg = negatives[len(negatives)//2:]

    # IS precision
    is_hits  = sum(1 for r in is_pos if _individual_matches(individual, r))
    is_false = sum(1 for r in is_neg if _individual_matches(individual, r))
    is_total = is_hits + is_false
    is_prec  = is_hits / is_total if is_total > 0 else 0.0

    # OOS precision
    oos_hits  = sum(1 for r in oos_pos if _individual_matches(individual, r))
    oos_false = sum(1 for r in oos_neg if _individual_matches(individual, r))
    oos_total = oos_hits + oos_false
    oos_prec  = oos_hits / oos_total if oos_total > 0 else 0.0

    # Stability: OOS should not be much worse than IS
    stability = oos_prec / is_prec if is_prec > 0 else 0.0
    stability = min(stability, 1.0)  # cap at 1 (can't be better than IS)

    # Fitness: reward high OOS precision + enough signals + stability
    n_signals = oos_hits
    fitness   = oos_prec * math.sqrt(max(n_signals, 0)) * stability

    return fitness, {
        'is_precision':  round(is_prec * 100, 1),
        'oos_precision': round(oos_prec * 100, 1),
        'is_hits':  is_hits,
        'oos_hits': oos_hits,
        'stability': round(stability, 3),
        'fitness':   round(fitness, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Genetic Operators
# ─────────────────────────────────────────────────────────────────────────────

def _crossover(ind1, ind2):
    """Single-point crossover between two individuals."""
    if len(ind1) < 2 or len(ind2) < 2:
        return ind1[:], ind2[:]
    pt1 = random.randint(1, len(ind1) - 1)
    pt2 = random.randint(1, len(ind2) - 1)
    child1 = ind1[:pt1] + ind2[pt2:]
    child2 = ind2[:pt2] + ind1[pt1:]
    # Deduplicate
    def dedup(ind):
        seen = {}
        for g in ind:
            seen[g[0]] = g
        return list(seen.values()) or [_random_gene()]
    return dedup(child1), dedup(child2)


def _mutate(individual, p_mut=0.3):
    """Mutate an individual by replacing random genes."""
    result = [g for g in individual]
    for i in range(len(result)):
        if random.random() < p_mut:
            result[i] = _random_gene()
    # Occasionally add or remove a gene
    if random.random() < 0.2 and len(result) < 5:
        result.append(_random_gene())
    elif random.random() < 0.2 and len(result) > 2:
        result.pop(random.randint(0, len(result) - 1))
    seen = {}
    for g in result:
        seen[g[0]] = g
    return list(seen.values()) or [_random_gene()]


def _tournament_select(population, fitnesses, k=3):
    """Tournament selection."""
    contestants = random.sample(range(len(population)), min(k, len(population)))
    winner = max(contestants, key=lambda i: fitnesses[i])
    return population[winner][:]


# ─────────────────────────────────────────────────────────────────────────────
# Main Evolution Loop
# ─────────────────────────────────────────────────────────────────────────────

def cmd_evolve(params):
    """Run genetic evolution to discover novel entry strategies.

    params:
      pop_size      : int (default 80)   — population size
      n_generations : int (default 40)   — number of generations
      split_date    : str (default '2025-01-01') — IS/OOS split
      elite_n       : int (default 5)    — elites carried forward
    """
    pop_size      = int(params.get('pop_size', 80))
    n_gen         = int(params.get('n_generations', 40))
    split_date    = params.get('split_date', '2025-01-01')
    elite_n       = int(params.get('elite_n', 5))

    conn = get_db()
    positives = _load_explosion_data(conn)
    negatives = _load_negative_sample(conn, n=3000)
    conn.close()

    if len(positives) < 50:
        return {'success': False, 'error': f'Only {len(positives)} positives — need ≥50'}

    print(f"[GA] {len(positives)} positives, {len(negatives)} negatives, {n_gen} generations", flush=True)

    # Initialize population
    population = [_random_individual() for _ in range(pop_size)]
    best_ever  = None
    best_fit   = -999.0
    history    = []

    for gen in range(n_gen):
        # Evaluate fitness
        fitnesses = []
        for ind in population:
            fit, _ = _evaluate(ind, positives, negatives, split_date)
            fitnesses.append(fit)

        # Track best
        gen_best_idx = max(range(len(fitnesses)), key=lambda i: fitnesses[i])
        gen_best_fit = fitnesses[gen_best_idx]
        if gen_best_fit > best_fit:
            best_fit  = gen_best_fit
            best_ever = population[gen_best_idx][:]

        avg_fit = sum(f for f in fitnesses if f > -100) / max(1, sum(1 for f in fitnesses if f > -100))
        history.append({'gen': gen, 'best': round(gen_best_fit, 4), 'avg': round(avg_fit, 4)})

        if gen % 10 == 0:
            print(f"[GA] Gen {gen:3d} best={gen_best_fit:.4f} avg={avg_fit:.4f}", flush=True)

        # Elitism: keep top elite_n
        elite_idxs = sorted(range(len(fitnesses)), key=lambda i: -fitnesses[i])[:elite_n]
        new_pop = [population[i][:] for i in elite_idxs]

        # Breed new individuals
        while len(new_pop) < pop_size:
            p1 = _tournament_select(population, fitnesses)
            p2 = _tournament_select(population, fitnesses)
            c1, c2 = _crossover(p1, p2)
            c1 = _mutate(c1)
            c2 = _mutate(c2)
            new_pop.extend([c1, c2])

        population = new_pop[:pop_size]

    # Final evaluation of top 20
    final_fitnesses = []
    for ind in population:
        fit, stats = _evaluate(ind, positives, negatives, split_date)
        final_fitnesses.append((fit, stats, ind))

    final_fitnesses.sort(key=lambda x: -x[0])
    top = final_fitnesses[:20]

    # Format top strategies
    top_strategies = []
    for rank, (fit, stats, ind) in enumerate(top):
        conditions = [f"{feat} {op} {thresh}" for feat, op, thresh in ind]
        top_strategies.append({
            'rank':          rank + 1,
            'fitness':       round(fit, 4),
            'is_precision':  stats['is_precision'],
            'oos_precision': stats['oos_precision'],
            'oos_hits':      stats['oos_hits'],
            'stability':     stats['stability'],
            'conditions':    conditions,
            'n_conditions':  len(ind),
            'genes':         [(f, d, t) for f, d, t in ind],
        })

    # Save evolved strategies
    EVOLVED_PATH.parent.mkdir(parents=True, exist_ok=True)
    evolved_data = {
        'evolved_at':     datetime.datetime.now().isoformat(),
        'n_generations':  n_gen,
        'pop_size':       pop_size,
        'split_date':     split_date,
        'best_fitness':   round(best_fit, 4),
        'strategies':     top_strategies,
        'evolution_history': history,
    }
    with open(EVOLVED_PATH, 'w') as fh:
        json.dump(evolved_data, fh, indent=2)

    return {
        'success':         True,
        'n_generations':   n_gen,
        'best_fitness':    round(best_fit, 4),
        'top_strategies':  top_strategies[:5],
        'saved_to':        str(EVOLVED_PATH),
    }


def cmd_top_strategies(params):
    """Show top evolved strategies from last evolution run."""
    limit = int(params.get('limit', 10))
    if not EVOLVED_PATH.exists():
        return {'success': False, 'error': 'No evolved strategies — run evolve first'}

    with open(EVOLVED_PATH) as fh:
        data = json.load(fh)

    return {
        'success':       True,
        'evolved_at':    data.get('evolved_at'),
        'n_generations': data.get('n_generations'),
        'best_fitness':  data.get('best_fitness'),
        'strategies':    data.get('strategies', [])[:limit],
    }


def cmd_validate(params):
    """Walk-forward validate top evolved strategies."""
    if not EVOLVED_PATH.exists():
        return {'success': False, 'error': 'No evolved strategies — run evolve first'}

    with open(EVOLVED_PATH) as fh:
        data = json.load(fh)

    top_n = int(params.get('top_n', 5))
    strategies = data.get('strategies', [])[:top_n]

    conn = get_db()
    positives = _load_explosion_data(conn)
    negatives = _load_negative_sample(conn, n=3000)
    conn.close()

    results = []
    for strat in strategies:
        ind = [(g[0], g[1], g[2]) for g in strat.get('genes', [])]

        # Multiple OOS splits
        wf_windows = [
            ('2023-01-01', '2024-01-01'),
            ('2024-01-01', '2025-01-01'),
            ('2025-01-01', '2026-01-01'),
        ]
        window_precs = []
        for split_date, _ in wf_windows:
            fit, stats = _evaluate(ind, positives, negatives, split_date)
            window_precs.append(stats.get('oos_precision', 0))

        avg_prec   = sum(window_precs) / len(window_precs) if window_precs else 0
        stability  = sum(1 for p in window_precs if p > 40) / len(window_precs) if window_precs else 0

        results.append({
            'rank':           strat['rank'],
            'conditions':     strat['conditions'],
            'wf_precisions':  [round(p, 1) for p in window_precs],
            'avg_precision':  round(avg_prec, 1),
            'wf_stability':   round(stability * 100, 0),
            'verdict':        '✅ Stable' if stability >= 0.67 else '⚠️ Marginal' if stability >= 0.33 else '❌ Unstable',
        })

    return {
        'success':  True,
        'n_tested': len(results),
        'results':  results,
    }


def cmd_report(params):
    evo  = cmd_evolve({'pop_size': 60, 'n_generations': 30})
    val  = cmd_validate({'top_n': 5})
    return {
        'success':   True,
        'evolution': evo,
        'validation': val,
    }


COMMANDS = {
    'evolve':          cmd_evolve,
    'top_strategies':  cmd_top_strategies,
    'validate':        cmd_validate,
    'report':          cmd_report,
}

if __name__ == '__main__':
    cmd    = sys.argv[1] if len(sys.argv) > 1 else 'top_strategies'
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    handler = COMMANDS.get(cmd)
    if not handler:
        print(json.dumps({'error': f'Unknown: {cmd}', 'available': list(COMMANDS.keys())}))
        sys.exit(1)
    print(json.dumps(handler(params), default=str))
