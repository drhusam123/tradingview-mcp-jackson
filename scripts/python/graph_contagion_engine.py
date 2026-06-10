#!/usr/bin/env python3
"""
Graph Contagion Engine — Phase 17
===================================
NetworkX-based market graph analysis: centrality, community detection,
contagion paths, SIR cascade simulation, and momentum spillover.

Commands (sys.argv[1]):
  build_network       — build full market graph from correlations + sector membership
  pagerank            — compute PageRank centrality, find top influencers
  communities         — Louvain/greedy community detection
  contagion_paths     — shortest contagion path between each sector pair
  cascade_simulation  — SIR-like spread: Finance shock → how many stocks affected
  centrality_analysis — betweenness, closeness, degree centrality for all nodes
  momentum_spillover  — does momentum of A predict return of B next day?
  full_analysis       — run all stages

DB Tables created:
  contagion_network   — edges with weights, type, delay, co-explosion rate
  stock_centrality    — per-stock PageRank, betweenness, community, influencer rank
"""

import json, sys, time, sqlite3, math
from pathlib import Path
from collections import defaultdict
from datetime import datetime

DATA    = Path(__file__).parent.parent.parent / 'data'
DB_PATH = str(DATA / 'egx_trading.db')

# ─── DB helpers ──────────────────────────────────────────────────────────────

def get_connection():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con

def ensure_schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS contagion_network (
            source TEXT, target TEXT, edge_weight REAL, edge_type TEXT,
            avg_delay_days REAL, co_explosion_rate REAL, updated_at TEXT,
            PRIMARY KEY (source, target, edge_type)
        );
        CREATE TABLE IF NOT EXISTS stock_centrality (
            symbol TEXT PRIMARY KEY, pagerank REAL, betweenness REAL,
            degree_centrality REAL, community_id INTEGER, bridge_score REAL,
            influencer_rank INTEGER, updated_at TEXT
        );
    """)
    con.commit()

# ─── NetworkX import ─────────────────────────────────────────────────────────

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False

# ─── Data loading ─────────────────────────────────────────────────────────────

def load_returns(con, lookback_days=252):
    """Load daily returns per symbol for the last N trading days."""
    rows = con.execute("""
        SELECT symbol, bar_time,
               (close - LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time))
               / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY bar_time), 0) AS daily_return
        FROM ohlcv_history
        WHERE bar_time > strftime('%s', 'now', '-' || ? || ' days')
        ORDER BY symbol, bar_time
    """, (lookback_days + 10,)).fetchall()

    by_symbol = defaultdict(list)
    for r in rows:
        if r['daily_return'] is not None:
            by_symbol[r['symbol']].append((r['bar_time'], r['daily_return']))
    return by_symbol

def load_sectors(con):
    rows = con.execute(
        "SELECT symbol, sector FROM stock_universe WHERE sector IS NOT NULL AND sector != ''"
    ).fetchall()
    return {r['symbol']: r['sector'] for r in rows}

def compute_correlation_matrix(by_symbol, min_overlap=60, corr_threshold=0.3):
    """
    Compute pairwise Pearson correlation between stocks.
    Returns dict: {(sym_a, sym_b): corr_value} for corr > threshold.
    """
    symbols = [s for s, v in by_symbol.items() if len(v) >= min_overlap]
    # Build aligned time-series dict
    # Use bar_time as key for alignment
    ts = {}
    for sym in symbols:
        ts[sym] = dict(by_symbol[sym])

    # Find common dates
    all_dates = sorted(set().union(*[set(d.keys()) for d in ts.values()]))

    # Build matrix: symbol × dates (fill NaN with None)
    data = {}
    for sym in symbols:
        data[sym] = [ts[sym].get(d) for d in all_dates]

    edges = {}
    sym_list = list(data.keys())
    n = len(sym_list)

    for i in range(n):
        for j in range(i + 1, n):
            sa, sb = sym_list[i], sym_list[j]
            va = data[sa]
            vb = data[sb]
            # get pairs where both have values
            pairs = [(a, b) for a, b in zip(va, vb)
                     if a is not None and b is not None
                     and not (math.isnan(a) or math.isnan(b))]
            if len(pairs) < min_overlap:
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            corr = _pearson(xs, ys)
            if corr is not None and abs(corr) >= corr_threshold:
                edges[(sa, sb)] = corr
    return edges, sym_list

def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx  = sum((x - mx) ** 2 for x in xs)
    dy  = sum((y - my) ** 2 for y in ys)
    denom = math.sqrt(dx * dy)
    if denom == 0:
        return None
    return num / denom

def build_graph(edges_corr, sectors, sym_list):
    """Build NetworkX graph with stock + sector nodes."""
    G = nx.Graph()

    # Stock nodes
    for sym in sym_list:
        sector = sectors.get(sym, 'Unknown')
        G.add_node(sym, node_type='stock', sector=sector)

    # Sector nodes
    sector_set = set(sectors.values())
    for sec in sector_set:
        G.add_node(f'SEC:{sec}', node_type='sector', sector=sec)

    # Correlation edges between stocks
    for (sa, sb), corr in edges_corr.items():
        if G.has_node(sa) and G.has_node(sb):
            G.add_edge(sa, sb, edge_weight=float(abs(corr)),
                       edge_type='correlation', corr=float(corr))

    # Sector membership edges (lighter weight)
    for sym, sec in sectors.items():
        if G.has_node(sym) and G.has_node(f'SEC:{sec}'):
            G.add_edge(sym, f'SEC:{sec}', edge_weight=0.5,
                       edge_type='sector_member', corr=0.5)

    return G

# ─── Command: build_network ──────────────────────────────────────────────────

def cmd_build_network(params):
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Loading OHLCV returns...', flush=True)
    con = get_connection()
    ensure_schema(con)

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)

    print(f'[Phase 17] Computing correlations for {len(by_symbol)} stocks...', flush=True)
    corr_threshold = float(params.get('corr_threshold', 0.3))
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=corr_threshold)

    G = build_graph(edges_corr, sectors, sym_list)

    # Save edges to DB
    now = datetime.utcnow().isoformat()
    rows_to_save = []
    for (sa, sb), corr in edges_corr.items():
        rows_to_save.append((sa, sb, float(abs(corr)), 'correlation', 1.0, 0.0, now))
        rows_to_save.append((sb, sa, float(abs(corr)), 'correlation', 1.0, 0.0, now))

    con.executemany("""
        INSERT OR REPLACE INTO contagion_network
        (source, target, edge_weight, edge_type, avg_delay_days, co_explosion_rate, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows_to_save)
    con.commit()
    con.close()

    result = {
        'n_stocks':        len(sym_list),
        'n_edges':         G.number_of_edges(),
        'n_nodes':         G.number_of_nodes(),
        'n_corr_edges':    len(edges_corr),
        'density':         round(nx.density(G), 4),
        'corr_threshold':  corr_threshold,
        'n_components':    nx.number_connected_components(G),
        'edges_saved_to_db': len(rows_to_save),
        'elapsed':         round(time.time() - t0, 2)
    }
    return result

# ─── Command: pagerank ───────────────────────────────────────────────────────

def cmd_pagerank(params):
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Computing PageRank...', flush=True)
    con = get_connection()
    ensure_schema(con)

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=0.3)
    G = build_graph(edges_corr, sectors, sym_list)

    # Compute PageRank on stock-only subgraph
    stock_nodes = [n for n, d in G.nodes(data=True) if d.get('node_type') == 'stock']
    G_stocks = G.subgraph(stock_nodes).copy()

    pr = nx.pagerank(G_stocks, weight='edge_weight', alpha=0.85, max_iter=200)
    pr_sorted = sorted(pr.items(), key=lambda x: -x[1])

    # Assign influencer rank + save
    now = datetime.utcnow().isoformat()
    for rank, (sym, score) in enumerate(pr_sorted, 1):
        sector = sectors.get(sym, 'Unknown')
        con.execute("""
            INSERT OR REPLACE INTO stock_centrality
            (symbol, pagerank, betweenness, degree_centrality, community_id,
             bridge_score, influencer_rank, updated_at)
            VALUES (?, ?, COALESCE((SELECT betweenness FROM stock_centrality WHERE symbol=?), 0),
                    COALESCE((SELECT degree_centrality FROM stock_centrality WHERE symbol=?), 0),
                    COALESCE((SELECT community_id FROM stock_centrality WHERE symbol=?), -1),
                    COALESCE((SELECT bridge_score FROM stock_centrality WHERE symbol=?), 0),
                    ?, ?)
        """, (sym, float(score), sym, sym, sym, sym, rank, now))
    con.commit()

    top_influencers = [
        {'symbol': sym, 'pagerank': round(float(score), 6),
         'rank': rank, 'sector': sectors.get(sym, 'Unknown')}
        for rank, (sym, score) in enumerate(pr_sorted[:20], 1)
    ]

    # Sector average PageRank
    sector_pr = defaultdict(list)
    for sym, score in pr.items():
        sec = sectors.get(sym, 'Unknown')
        sector_pr[sec].append(score)
    sector_avg = {sec: round(sum(v)/len(v), 6) for sec, v in sector_pr.items()}
    sector_avg_sorted = sorted(sector_avg.items(), key=lambda x: -x[1])

    con.close()
    return {
        'n_stocks': len(pr),
        'top_influencers': top_influencers,
        'sector_pagerank': [{'sector': s, 'avg_pagerank': v} for s, v in sector_avg_sorted[:10]],
        'saved_to_db': len(pr),
        'elapsed': round(time.time() - t0, 2)
    }

# ─── Command: communities ────────────────────────────────────────────────────

def cmd_communities(params):
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Detecting communities...', flush=True)
    con = get_connection()
    ensure_schema(con)

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=0.3)
    G = build_graph(edges_corr, sectors, sym_list)

    stock_nodes = [n for n, d in G.nodes(data=True) if d.get('node_type') == 'stock']
    G_stocks = G.subgraph(stock_nodes).copy()

    # Use greedy modularity communities (Louvain-like)
    communities_gen = nx.algorithms.community.greedy_modularity_communities(
        G_stocks, weight='edge_weight'
    )
    communities_list = [sorted(c) for c in communities_gen]

    # Build community map
    sym_to_community = {}
    for cid, community in enumerate(communities_list):
        for sym in community:
            sym_to_community[sym] = cid

    # Save community_id to stock_centrality
    now = datetime.utcnow().isoformat()
    for sym, cid in sym_to_community.items():
        con.execute("""
            INSERT OR REPLACE INTO stock_centrality
            (symbol, pagerank, betweenness, degree_centrality, community_id,
             bridge_score, influencer_rank, updated_at)
            VALUES (?,
                COALESCE((SELECT pagerank FROM stock_centrality WHERE symbol=?), 0),
                COALESCE((SELECT betweenness FROM stock_centrality WHERE symbol=?), 0),
                COALESCE((SELECT degree_centrality FROM stock_centrality WHERE symbol=?), 0),
                ?,
                COALESCE((SELECT bridge_score FROM stock_centrality WHERE symbol=?), 0),
                COALESCE((SELECT influencer_rank FROM stock_centrality WHERE symbol=?), 999),
                ?)
        """, (sym, sym, sym, sym, cid, sym, sym, now))
    con.commit()
    con.close()

    # Describe communities
    community_descriptions = []
    for cid, community in enumerate(communities_list[:20]):
        # Sector composition
        sector_counts = defaultdict(int)
        for sym in community:
            sec = sectors.get(sym, 'Unknown')
            sector_counts[sec] += 1
        dominant_sector = max(sector_counts, key=sector_counts.get) if sector_counts else 'Unknown'
        community_descriptions.append({
            'community_id':     cid,
            'size':             len(community),
            'dominant_sector':  dominant_sector,
            'sector_mix':       dict(sector_counts),
            'members_sample':   community[:10],
        })

    modularity = nx.algorithms.community.quality.modularity(
        G_stocks, communities_list, weight='edge_weight'
    ) if communities_list else 0.0

    return {
        'n_communities':    len(communities_list),
        'modularity':       round(float(modularity), 4),
        'communities':      community_descriptions,
        'largest_community_size': max(len(c) for c in communities_list) if communities_list else 0,
        'saved_to_db':      len(sym_to_community),
        'elapsed':          round(time.time() - t0, 2)
    }

# ─── Command: contagion_paths ─────────────────────────────────────────────────

def cmd_contagion_paths(params):
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Finding contagion paths...', flush=True)
    con = get_connection()

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=0.3)
    G = build_graph(edges_corr, sectors, sym_list)
    con.close()

    # Build sector representative nodes (highest degree stock per sector)
    sector_reps = {}
    for sym in sym_list:
        sec = sectors.get(sym, 'Unknown')
        if sec not in sector_reps:
            sector_reps[sec] = []
        sector_reps[sec].append(sym)

    # Pick sector rep = stock with highest degree in its sector
    sector_rep_node = {}
    for sec, stocks in sector_reps.items():
        if not stocks:
            continue
        best = max(stocks, key=lambda s: G.degree(s, weight='edge_weight') if G.has_node(s) else 0)
        sector_rep_node[sec] = best

    sector_list = [s for s in sector_rep_node if s != 'Unknown']

    # Find shortest paths between sector pairs
    contagion_paths_result = []
    for i, sec_a in enumerate(sector_list):
        for sec_b in sector_list[i+1:]:
            node_a = sector_rep_node[sec_a]
            node_b = sector_rep_node[sec_b]
            if not (G.has_node(node_a) and G.has_node(node_b)):
                continue
            try:
                path = nx.shortest_path(G, node_a, node_b, weight=None)
                path_len = len(path) - 1
                # Estimate delay: each hop = ~1 trading day
                contagion_paths_result.append({
                    'from_sector':    sec_a,
                    'to_sector':      sec_b,
                    'path':           path,
                    'hops':           path_len,
                    'est_delay_days': path_len,
                    'direct':         path_len == 1,
                })
            except nx.NetworkXNoPath:
                contagion_paths_result.append({
                    'from_sector':    sec_a,
                    'to_sector':      sec_b,
                    'path':           [],
                    'hops':           -1,
                    'est_delay_days': -1,
                    'direct':         False,
                })

    contagion_paths_result.sort(key=lambda x: x['hops'] if x['hops'] >= 0 else 999)

    return {
        'n_sector_pairs': len(contagion_paths_result),
        'direct_connections': sum(1 for p in contagion_paths_result if p['direct']),
        'avg_hops': round(
            sum(p['hops'] for p in contagion_paths_result if p['hops'] > 0)
            / max(1, sum(1 for p in contagion_paths_result if p['hops'] > 0)), 2
        ),
        'paths': contagion_paths_result[:30],
        'elapsed': round(time.time() - t0, 2)
    }

# ─── Command: cascade_simulation ─────────────────────────────────────────────

def cmd_cascade_simulation(params):
    """
    SIR-like cascade: if top-5 Finance stocks drop >5%, how many
    others get affected within 5 days via correlation links (>0.5)?
    """
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Running cascade simulation...', flush=True)
    con = get_connection()

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=0.3)
    G = build_graph(edges_corr, sectors, sym_list)
    con.close()

    # Build strong-edge subgraph (corr > 0.5 only)
    G_strong = nx.Graph()
    for n, d in G.nodes(data=True):
        if d.get('node_type') == 'stock':
            G_strong.add_node(n, **d)
    for u, v, d in G.edges(data=True):
        if d.get('edge_type') == 'correlation' and abs(d.get('corr', 0)) >= 0.5:
            G_strong.add_edge(u, v, **d)

    shock_threshold = float(params.get('shock_threshold', 0.05))
    cascade_days    = int(params.get('cascade_days', 5))
    seed_sector     = params.get('seed_sector', 'Finance')

    # Find seed stocks (top-5 Finance by degree)
    finance_stocks = [sym for sym in sym_list if sectors.get(sym) == seed_sector]
    if not finance_stocks:
        # fall back to Banking
        finance_stocks = [sym for sym in sym_list if sectors.get(sym) == 'Banking']
    finance_stocks_deg = sorted(
        finance_stocks,
        key=lambda s: G_strong.degree(s, weight='edge_weight') if G_strong.has_node(s) else 0,
        reverse=True
    )
    seed_stocks = finance_stocks_deg[:5]

    # BFS cascade over days
    infected   = set(seed_stocks)
    exposed    = set(seed_stocks)
    day_report = {}

    current_frontier = set(seed_stocks)
    for day in range(1, cascade_days + 1):
        new_infected = set()
        for sym in current_frontier:
            if not G_strong.has_node(sym):
                continue
            for neighbor in G_strong.neighbors(sym):
                if neighbor not in infected:
                    edge_data = G_strong.get_edge_data(sym, neighbor)
                    corr_val  = abs(edge_data.get('corr', 0)) if edge_data else 0
                    # Propagate if corr > 0.5 (already filtered) with probability = corr
                    import random
                    if corr_val > 0.5 and random.random() < corr_val:
                        new_infected.add(neighbor)
        infected.update(new_infected)
        current_frontier = new_infected
        day_report[day] = {
            'newly_affected': len(new_infected),
            'total_affected': len(infected),
            'sample':         list(new_infected)[:5]
        }

    # Sector breakdown of casualties
    sector_casualties = defaultdict(int)
    for sym in infected:
        if sym not in seed_stocks:
            sec = sectors.get(sym, 'Unknown')
            sector_casualties[sec] += 1
    sector_casualties_sorted = sorted(sector_casualties.items(), key=lambda x: -x[1])

    return {
        'seed_sector':      seed_sector,
        'seed_stocks':      seed_stocks,
        'shock_threshold':  shock_threshold,
        'cascade_days':     cascade_days,
        'total_infected':   len(infected),
        'total_stocks':     len(sym_list),
        'infection_rate':   round(len(infected) / max(1, len(sym_list)), 3),
        'day_by_day':       day_report,
        'sector_casualties': [{'sector': s, 'n_affected': n}
                               for s, n in sector_casualties_sorted[:10]],
        'most_vulnerable_sectors': [s for s, n in sector_casualties_sorted[:3]],
        'elapsed': round(time.time() - t0, 2)
    }

# ─── Command: centrality_analysis ────────────────────────────────────────────

def cmd_centrality_analysis(params):
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Computing centrality metrics...', flush=True)
    con = get_connection()
    ensure_schema(con)

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=0.3)
    G = build_graph(edges_corr, sectors, sym_list)

    stock_nodes = [n for n, d in G.nodes(data=True) if d.get('node_type') == 'stock']
    G_stocks = G.subgraph(stock_nodes).copy()

    print('[Phase 17] Degree centrality...', flush=True)
    degree_c = nx.degree_centrality(G_stocks)

    print('[Phase 17] Betweenness centrality (sample k=50)...', flush=True)
    # Use k-sample for speed on large graphs
    k = min(50, len(stock_nodes))
    betweenness_c = nx.betweenness_centrality(G_stocks, weight='edge_weight', k=k, normalized=True)

    print('[Phase 17] Closeness centrality...', flush=True)
    closeness_c = nx.closeness_centrality(G_stocks, distance=None)

    # Bridge score = betweenness × (1 / degree) — high = bottleneck
    bridge_scores = {}
    for sym in stock_nodes:
        bet = betweenness_c.get(sym, 0)
        deg = max(degree_c.get(sym, 0.001), 0.001)
        bridge_scores[sym] = bet / deg

    now = datetime.utcnow().isoformat()
    for sym in stock_nodes:
        con.execute("""
            INSERT OR REPLACE INTO stock_centrality
            (symbol, pagerank, betweenness, degree_centrality, community_id,
             bridge_score, influencer_rank, updated_at)
            VALUES (?,
                COALESCE((SELECT pagerank FROM stock_centrality WHERE symbol=?), 0),
                ?, ?,
                COALESCE((SELECT community_id FROM stock_centrality WHERE symbol=?), -1),
                ?,
                COALESCE((SELECT influencer_rank FROM stock_centrality WHERE symbol=?), 999),
                ?)
        """, (sym, sym,
              float(betweenness_c.get(sym, 0)),
              float(degree_c.get(sym, 0)),
              sym, float(bridge_scores.get(sym, 0)), sym, now))
    con.commit()
    con.close()

    # Top stocks by each metric
    top_degree     = sorted(degree_c.items(),     key=lambda x: -x[1])[:15]
    top_betweenness= sorted(betweenness_c.items(),key=lambda x: -x[1])[:15]
    top_bridges    = sorted(bridge_scores.items(),key=lambda x: -x[1])[:15]

    return {
        'n_stocks': len(stock_nodes),
        'top_degree_centrality': [
            {'symbol': s, 'degree_centrality': round(float(v), 4),
             'sector': sectors.get(s, 'Unknown')} for s, v in top_degree
        ],
        'top_betweenness': [
            {'symbol': s, 'betweenness': round(float(v), 6),
             'sector': sectors.get(s, 'Unknown')} for s, v in top_betweenness
        ],
        'top_bridges': [
            {'symbol': s, 'bridge_score': round(float(v), 4),
             'sector': sectors.get(s, 'Unknown')} for s, v in top_bridges
        ],
        'saved_to_db': len(stock_nodes),
        'elapsed': round(time.time() - t0, 2)
    }

# ─── Command: momentum_spillover ─────────────────────────────────────────────

def cmd_momentum_spillover(params):
    """
    For each stock pair with high edge weight (corr > 0.5),
    test whether momentum of A predicts return of B next day.
    """
    if not HAS_NX:
        return {'error': 'networkx not installed'}
    t0 = time.time()
    print('[Phase 17] Computing momentum spillover...', flush=True)
    con = get_connection()

    by_symbol = load_returns(con, lookback_days=252)
    sectors   = load_sectors(con)
    edges_corr, sym_list = compute_correlation_matrix(by_symbol, corr_threshold=0.3)
    con.close()

    # Build time-indexed returns
    ts_by_sym = {}
    for sym in sym_list:
        ts_by_sym[sym] = dict(by_symbol[sym])

    all_dates = sorted(set().union(*[set(v.keys()) for v in ts_by_sym.values()]))
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    spillover_results = []
    # Only test high-corr pairs (>0.5)
    high_corr_pairs = [(sa, sb, corr) for (sa, sb), corr in edges_corr.items() if abs(corr) > 0.5]
    high_corr_pairs.sort(key=lambda x: -abs(x[2]))

    for sa, sb, corr in high_corr_pairs[:100]:  # cap for speed
        ret_a = ts_by_sym.get(sa, {})
        ret_b = ts_by_sym.get(sb, {})

        # Build (momentum_A_today, return_B_tomorrow) pairs
        pairs = []
        for date in all_dates[:-1]:
            idx = date_to_idx[date]
            next_date = all_dates[idx + 1] if idx + 1 < len(all_dates) else None
            if next_date is None:
                continue
            mom_a = ret_a.get(date)
            ret_b_next = ret_b.get(next_date)
            if mom_a is not None and ret_b_next is not None:
                pairs.append((mom_a, ret_b_next))

        if len(pairs) < 30:
            continue

        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        predictive_corr = _pearson(xs, ys)

        if predictive_corr is not None and abs(predictive_corr) > 0.05:
            spillover_results.append({
                'stock_a':          sa,
                'stock_b':          sb,
                'sector_a':         sectors.get(sa, 'Unknown'),
                'sector_b':         sectors.get(sb, 'Unknown'),
                'correlation':      round(float(corr), 4),
                'predictive_corr':  round(float(predictive_corr), 4),
                'n_pairs':          len(pairs),
                'direction':        'positive' if predictive_corr > 0 else 'negative',
            })

    spillover_results.sort(key=lambda x: -abs(x['predictive_corr']))

    return {
        'n_pairs_tested':   len(high_corr_pairs[:100]),
        'n_significant':    len(spillover_results),
        'top_spillovers':   spillover_results[:20],
        'avg_predictive_corr': round(
            sum(abs(r['predictive_corr']) for r in spillover_results)
            / max(1, len(spillover_results)), 4
        ),
        'elapsed': round(time.time() - t0, 2)
    }

# ─── Command: full_analysis ──────────────────────────────────────────────────

def cmd_full_analysis(params):
    t0 = time.time()
    print('[Phase 17] Running full graph analysis...', flush=True)
    results = {}

    stages = [
        ('build_network',       cmd_build_network),
        ('pagerank',            cmd_pagerank),
        ('communities',         cmd_communities),
        ('contagion_paths',     cmd_contagion_paths),
        ('cascade_simulation',  cmd_cascade_simulation),
        ('centrality_analysis', cmd_centrality_analysis),
        ('momentum_spillover',  cmd_momentum_spillover),
    ]
    for key, fn in stages:
        try:
            print(f'[Phase 17] Stage: {key}...', flush=True)
            results[key] = fn({})
        except Exception as ex:
            import traceback
            results[key] = {'error': str(ex), 'traceback': traceback.format_exc()[-500:]}

    results['elapsed_total'] = round(time.time() - t0, 2)
    return results

# ─── Dispatch ────────────────────────────────────────────────────────────────

COMMANDS = {
    'build_network':       cmd_build_network,
    'pagerank':            cmd_pagerank,
    'communities':         cmd_communities,
    'contagion_paths':     cmd_contagion_paths,
    'cascade_simulation':  cmd_cascade_simulation,
    'centrality_analysis': cmd_centrality_analysis,
    'momentum_spillover':  cmd_momentum_spillover,
    'full_analysis':       cmd_full_analysis,
}

if __name__ == '__main__':
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else 'full_analysis'
        params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        fn = COMMANDS.get(command)
        if fn is None:
            out = {'error': f'unknown command: {command}', 'available': list(COMMANDS.keys())}
        else:
            out = fn(params)
        print(json.dumps(out))
    except Exception as ex:
        import traceback
        print(json.dumps({'error': str(ex), 'traceback': traceback.format_exc()[-1000:]}))
