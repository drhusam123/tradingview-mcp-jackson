#!/usr/bin/env python3
"""
Unified Market Cognition Graph (UMCG) — Phase 21
==================================================
The top-level knowledge graph that integrates every market intelligence layer
produced by Phases 1-20 into a single directed multi-relational graph.

Nodes : STOCK, SECTOR, LAW, PRECURSOR, ARCHETYPE, REGIME, MACRO, FAILURE
Edges : CAUSES, CONTAGION, CO_ACTIVATES, SUPPRESSES, PRECEDES, BELONGS_TO,
        MANIFESTS_IN, REGIME_DEPENDENT, MACRO_DRIVES, MUTATES_FROM,
        FAILS_UNDER, AMPLIFIES

Commands (sys.argv[1]):
  build_full          — ingest all source tables, populate umcg_nodes + umcg_edges
  compute_metrics     — PageRank, betweenness, eigenvector centrality
  detect_communities  — greedy modularity communities
  find_fragility      — fragility hubs + structural bridges
  weekly_snapshot     — snapshot + evolution delta vs previous week
  query_paths         — shortest causal path (params: source, target)
  get_snapshot        — return latest snapshot + top nodes

DB tables created: umcg_nodes, umcg_edges, umcg_snapshots
"""

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

# ─── NetworkX optional import ────────────────────────────────────────────────

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

# ─── DB setup ─────────────────────────────────────────────────────────────────

_HERE   = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_HERE, '..', '..', 'data', 'egx_trading.db')


def get_db():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    _create_tables(db)
    return db


def _create_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS umcg_nodes (
            node_id             TEXT PRIMARY KEY,
            node_type           TEXT NOT NULL,
            name                TEXT,
            attributes          TEXT,
            pagerank            REAL DEFAULT 0,
            betweenness         REAL DEFAULT 0,
            eigenvector         REAL DEFAULT 0,
            community_id        INTEGER DEFAULT -1,
            is_fragility_hub    INTEGER DEFAULT 0,
            is_structural_bridge INTEGER DEFAULT 0,
            created_at          TEXT,
            updated_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS umcg_edges (
            edge_id         TEXT PRIMARY KEY,
            source_id       TEXT,
            target_id       TEXT,
            edge_type       TEXT,
            weight          REAL DEFAULT 1.0,
            lag_days        INTEGER DEFAULT 0,
            regime          TEXT,
            evidence_count  INTEGER DEFAULT 1,
            confidence      REAL DEFAULT 0.5,
            p_value         REAL,
            is_validated    INTEGER DEFAULT 0,
            created_at      TEXT
        );

        CREATE TABLE IF NOT EXISTS umcg_snapshots (
            snapshot_date   TEXT PRIMARY KEY,
            n_nodes         INTEGER,
            n_edges         INTEGER,
            avg_clustering  REAL,
            n_communities   INTEGER,
            graph_density   REAL,
            top_pagerank    TEXT,
            top_betweenness TEXT,
            fragility_hubs  TEXT,
            evolution_delta TEXT,
            computed_at     TEXT
        );
    """)
    db.commit()


# ─── Node / edge ID helpers ───────────────────────────────────────────────────

def _node_id(node_type: str, name: str) -> str:
    safe = str(name).strip().replace(' ', '_').replace('/', '_').lower()
    return f"{node_type.lower()}:{safe}"


def _edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    return f"{source_id}|{edge_type}|{target_id}"


# ─── Upsert helpers ───────────────────────────────────────────────────────────

def _upsert_node(db, node_id, node_type, name, attributes=None):
    now = datetime.utcnow().isoformat()
    attr_json = json.dumps(attributes or {}, default=str)
    db.execute("""
        INSERT INTO umcg_nodes (node_id, node_type, name, attributes, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            name       = excluded.name,
            attributes = excluded.attributes,
            updated_at = excluded.updated_at
    """, (node_id, node_type, name, attr_json, now, now))


def _upsert_edge(db, source_id, target_id, edge_type,
                 weight=1.0, lag_days=0, regime=None,
                 evidence_count=1, confidence=0.5, p_value=None,
                 is_validated=0):
    eid  = _edge_id(source_id, target_id, edge_type)
    now  = datetime.utcnow().isoformat()
    db.execute("""
        INSERT INTO umcg_edges
            (edge_id, source_id, target_id, edge_type, weight, lag_days,
             regime, evidence_count, confidence, p_value, is_validated, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(edge_id) DO UPDATE SET
            weight         = excluded.weight,
            lag_days       = excluded.lag_days,
            evidence_count = excluded.evidence_count,
            confidence     = excluded.confidence
    """, (eid, source_id, target_id, edge_type,
          weight, lag_days, regime, evidence_count,
          confidence, p_value, is_validated, now))


# ─── Pure-Python fallback graph ───────────────────────────────────────────────

class _FallbackGraph:
    """Minimal directed graph when networkx is not available."""

    def __init__(self):
        self.nodes = {}   # node_id -> attr dict
        self.edges = []   # list of (src, dst, attr)
        self._adj  = defaultdict(set)  # src -> {dst}

    def add_node(self, nid, **kwargs):
        self.nodes[nid] = kwargs

    def add_edge(self, src, dst, **kwargs):
        self.edges.append((src, dst, kwargs))
        self._adj[src].add(dst)
        if nid not in self.nodes:
            self.nodes.setdefault(src, {})
            self.nodes.setdefault(dst, {})

    def number_of_nodes(self):
        return len(self.nodes)

    def number_of_edges(self):
        return len(self.edges)

    def out_degree(self, n=None):
        if n is not None:
            return len(self._adj.get(n, set()))
        return {k: len(v) for k, v in self._adj.items()}

    def simple_pagerank(self, alpha=0.85, max_iter=100, tol=1e-6):
        """Power-iteration PageRank."""
        nodes  = list(self.nodes.keys())
        n      = len(nodes)
        if n == 0:
            return {}
        idx    = {nd: i for i, nd in enumerate(nodes)}
        pr     = [1.0 / n] * n
        out_d  = [len(self._adj.get(nd, set())) for nd in nodes]

        for _ in range(max_iter):
            new_pr = [(1 - alpha) / n] * n
            for src, dst_set in self._adj.items():
                i  = idx[src]
                od = out_d[i]
                if od == 0:
                    continue
                share = alpha * pr[i] / od
                for dst in dst_set:
                    j = idx.get(dst)
                    if j is not None:
                        new_pr[j] += share
            diff = sum(abs(new_pr[i] - pr[i]) for i in range(n))
            pr   = new_pr
            if diff < tol:
                break
        return {nodes[i]: pr[i] for i in range(n)}

    def simple_betweenness(self):
        """Brandes betweenness centrality (unweighted)."""
        nodes = list(self.nodes.keys())
        n     = len(nodes)
        bc    = {nd: 0.0 for nd in nodes}
        for s in nodes:
            # BFS
            stack = []
            pred  = defaultdict(list)
            sigma = {nd: 0.0 for nd in nodes}
            sigma[s] = 1.0
            dist  = {nd: -1 for nd in nodes}
            dist[s] = 0
            queue  = [s]
            while queue:
                v = queue.pop(0)
                stack.append(v)
                for w in self._adj.get(v, set()):
                    if dist[w] < 0:
                        queue.append(w)
                        dist[w] = dist[v] + 1
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        pred[w].append(v)
            delta = {nd: 0.0 for nd in nodes}
            while stack:
                w = stack.pop()
                for v in pred[w]:
                    if sigma[w] > 0:
                        delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
                if w != s:
                    bc[w] += delta[w]
        # normalize
        if n > 2:
            scale = 1.0 / ((n - 1) * (n - 2))
            bc    = {k: v * scale for k, v in bc.items()}
        return bc


# ─── Build graph from DB ──────────────────────────────────────────────────────

def _load_graph_from_db(db):
    """Reconstruct the graph (nx.DiGraph or _FallbackGraph) from umcg_nodes/umcg_edges."""
    if HAS_NX:
        G = nx.DiGraph()
    else:
        G = _FallbackGraph()

    for row in db.execute("SELECT node_id, node_type, name FROM umcg_nodes"):
        G.add_node(row['node_id'], node_type=row['node_type'], name=row['name'])

    for row in db.execute("SELECT source_id, target_id, edge_type, weight FROM umcg_edges"):
        G.add_edge(row['source_id'], row['target_id'],
                   edge_type=row['edge_type'],
                   weight=float(row['weight'] or 1.0))
    return G


# ─── Table existence helpers ──────────────────────────────────────────────────

def _table_exists(db, table_name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    ).fetchone()
    return row is not None


def _column_exists(db, table_name: str, col_name: str) -> bool:
    try:
        rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(r['name'] == col_name for r in rows)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Command: build_full
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_build_full(db, params):
    """
    Ingest all source tables and populate umcg_nodes + umcg_edges.
    Returns {n_nodes, n_edges, node_type_dist, edge_type_dist}.
    """
    node_type_dist  = defaultdict(int)
    edge_type_dist  = defaultdict(int)

    # ── 1. STOCK nodes ────────────────────────────────────────────────────────
    if _table_exists(db, 'stock_universe'):
        rows = db.execute(
            "SELECT symbol, name, sector, status FROM stock_universe WHERE status='fetched'"
        ).fetchall()
        for r in rows:
            nid  = _node_id('STOCK', r['symbol'])
            attr = {'symbol': r['symbol'], 'sector': r['sector'], 'status': r['status']}
            _upsert_node(db, nid, 'STOCK', r['name'] or r['symbol'], attr)
            node_type_dist['STOCK'] += 1

    # overlay stock_dna attributes
    if _table_exists(db, 'stock_dna'):
        for r in db.execute("SELECT symbol, sector, explosion_count, explosion_rate_pct, "
                            "false_breakout_rate_pct, archetype FROM stock_dna").fetchall():
            nid  = _node_id('STOCK', r['symbol'])
            attr = {
                'explosion_count':        r['explosion_count'],
                'explosion_rate_pct':     r['explosion_rate_pct'],
                'false_breakout_rate_pct': r['false_breakout_rate_pct'],
                'archetype':              r['archetype'],
            }
            attr_json = json.dumps(attr, default=str)
            db.execute(
                "UPDATE umcg_nodes SET attributes=?, updated_at=? WHERE node_id=?",
                (attr_json, datetime.utcnow().isoformat(), nid)
            )

    # ── 2. SECTOR nodes ───────────────────────────────────────────────────────
    if _table_exists(db, 'sector_dna'):
        for r in db.execute("SELECT sector, n_stocks, avg_explosion_rate, "
                            "synchronization_pct, sector_archetype FROM sector_dna").fetchall():
            if not r['sector']:
                continue
            nid  = _node_id('SECTOR', r['sector'])
            attr = {
                'n_stocks':           r['n_stocks'],
                'avg_explosion_rate': r['avg_explosion_rate'],
                'synchronization_pct': r['synchronization_pct'],
                'sector_archetype':   r['sector_archetype'],
            }
            _upsert_node(db, nid, 'SECTOR', r['sector'], attr)
            node_type_dist['SECTOR'] += 1

    # ── 3. LAW nodes ──────────────────────────────────────────────────────────
    if _table_exists(db, 'universal_laws_p16'):
        for r in db.execute("SELECT pattern_id, pattern_name, direction, precision, "
                            "law_status, regime_stability_score FROM universal_laws_p16").fetchall():
            nid  = _node_id('LAW', r['pattern_id'] or r['pattern_name'])
            attr = {
                'direction':             r['direction'],
                'precision':             r['precision'],
                'law_status':            r['law_status'],
                'regime_stability_score': r['regime_stability_score'],
            }
            _upsert_node(db, nid, 'LAW', r['pattern_name'], attr)
            node_type_dist['LAW'] += 1

    # ── 4. PRECURSOR nodes ────────────────────────────────────────────────────
    if _table_exists(db, 'precursor_patterns'):
        for r in db.execute("SELECT id, pattern_name, direction, support_rate, "
                            "effect_size FROM precursor_patterns").fetchall():
            nid  = _node_id('PRECURSOR', r['id'] or r['pattern_name'])
            attr = {
                'direction':    r['direction'],
                'support_rate': r['support_rate'],
                'effect_size':  r['effect_size'],
            }
            _upsert_node(db, nid, 'PRECURSOR', r['pattern_name'], attr)
            node_type_dist['PRECURSOR'] += 1

    # ── 5. ARCHETYPE nodes ────────────────────────────────────────────────────
    if _table_exists(db, 'explosion_archetypes'):
        for r in db.execute("SELECT archetype_id, archetype_name, n_members, "
                            "pct_of_total FROM explosion_archetypes").fetchall():
            nid  = _node_id('ARCHETYPE', r['archetype_id'] or r['archetype_name'])
            attr = {
                'n_members':   r['n_members'],
                'pct_of_total': r['pct_of_total'],
            }
            _upsert_node(db, nid, 'ARCHETYPE', r['archetype_name'], attr)
            node_type_dist['ARCHETYPE'] += 1

    # ── 6. REGIME nodes ───────────────────────────────────────────────────────
    regime_counts = {}
    if _table_exists(db, 'regime_history'):
        try:
            for r in db.execute("SELECT regime, COUNT(*) AS cnt FROM regime_history GROUP BY regime"):
                regime_counts[r['regime']] = r['cnt']
        except Exception:
            pass

    for regime in ['BULL', 'BEAR', 'CHOPPY', 'CRISIS']:
        nid  = _node_id('REGIME', regime)
        attr = {'observation_count': regime_counts.get(regime, 0)}
        _upsert_node(db, nid, 'REGIME', regime, attr)
        node_type_dist['REGIME'] += 1

    db.commit()

    # ── 7. BELONGS_TO edges: stock → sector ───────────────────────────────────
    if _table_exists(db, 'stock_universe'):
        for r in db.execute("SELECT symbol, sector FROM stock_universe WHERE status='fetched'"):
            if not r['sector']:
                continue
            src = _node_id('STOCK', r['symbol'])
            dst = _node_id('SECTOR', r['sector'])
            # only add if both nodes exist
            s_exists = db.execute("SELECT 1 FROM umcg_nodes WHERE node_id=?", (src,)).fetchone()
            d_exists = db.execute("SELECT 1 FROM umcg_nodes WHERE node_id=?", (dst,)).fetchone()
            if s_exists and d_exists:
                _upsert_edge(db, src, dst, 'BELONGS_TO', weight=1.0, confidence=1.0, is_validated=1)
                edge_type_dist['BELONGS_TO'] += 1

    # ── 8. CONTAGION edges: sector → sector ───────────────────────────────────
    if _table_exists(db, 'sector_contagion'):
        # introspect columns
        cols = [r['name'] for r in db.execute("PRAGMA table_info(sector_contagion)")]
        src_col  = 'source_sector' if 'source_sector' in cols else (cols[0] if cols else None)
        dst_col  = 'target_sector' if 'target_sector' in cols else (cols[1] if len(cols) > 1 else None)
        wgt_col  = next((c for c in ['correlation', 'weight', 'co_explosion_rate'] if c in cols), None)

        if src_col and dst_col:
            q = f"SELECT {src_col}, {dst_col}" + (f", {wgt_col}" if wgt_col else "") + " FROM sector_contagion"
            for r in db.execute(q).fetchall():
                src_sec = r[0]
                dst_sec = r[1]
                weight  = float(r[2]) if wgt_col and r[2] is not None else 0.5
                src = _node_id('SECTOR', src_sec)
                dst = _node_id('SECTOR', dst_sec)
                s_e = db.execute("SELECT 1 FROM umcg_nodes WHERE node_id=?", (src,)).fetchone()
                d_e = db.execute("SELECT 1 FROM umcg_nodes WHERE node_id=?", (dst,)).fetchone()
                if s_e and d_e and src != dst:
                    _upsert_edge(db, src, dst, 'CONTAGION',
                                 weight=abs(weight), confidence=min(abs(weight), 1.0))
                    edge_type_dist['CONTAGION'] += 1

    # ── 9. MANIFESTS_IN edges: law → sector ───────────────────────────────────
    #  Heuristic: if a law's pattern_name contains a sector name, link it
    if _table_exists(db, 'universal_laws_p16'):
        sectors = [r['name'] for r in
                   db.execute("SELECT name FROM umcg_nodes WHERE node_type='SECTOR'").fetchall()]
        laws    = db.execute("SELECT node_id, name FROM umcg_nodes WHERE node_type='LAW'").fetchall()
        for law in laws:
            law_name = (law['name'] or '').lower()
            for sec in sectors:
                if sec and sec.lower() in law_name:
                    dst = _node_id('SECTOR', sec)
                    _upsert_edge(db, law['node_id'], dst, 'MANIFESTS_IN',
                                 weight=0.7, confidence=0.6)
                    edge_type_dist['MANIFESTS_IN'] += 1

    # ── 10. PRECEDES edges: precursor → law ───────────────────────────────────
    precursors = db.execute("SELECT node_id, name FROM umcg_nodes WHERE node_type='PRECURSOR'").fetchall()
    laws_all   = db.execute("SELECT node_id, name FROM umcg_nodes WHERE node_type='LAW'").fetchall()
    for p in precursors:
        for l in laws_all:
            pn = (p['name'] or '').lower().split()
            ln = (l['name'] or '').lower().split()
            overlap = len(set(pn) & set(ln))
            if overlap >= 2:
                _upsert_edge(db, p['node_id'], l['node_id'], 'PRECEDES',
                             weight=0.6, confidence=0.55, lag_days=1)
                edge_type_dist['PRECEDES'] += 1

    # ── 11. MANIFESTS_IN: archetype → sector (via stock archetype membership) ─
    if _table_exists(db, 'stock_dna') and _table_exists(db, 'stock_universe'):
        try:
            rows = db.execute("""
                SELECT sd.archetype, su.sector, COUNT(*) AS cnt
                FROM stock_dna sd
                JOIN stock_universe su ON sd.symbol = su.symbol
                WHERE su.status='fetched' AND sd.archetype IS NOT NULL AND su.sector IS NOT NULL
                GROUP BY sd.archetype, su.sector
            """).fetchall()
            for r in rows:
                src = _node_id('ARCHETYPE', r['archetype'])
                dst = _node_id('SECTOR', r['sector'])
                s_e = db.execute("SELECT 1 FROM umcg_nodes WHERE node_id=?", (src,)).fetchone()
                d_e = db.execute("SELECT 1 FROM umcg_nodes WHERE node_id=?", (dst,)).fetchone()
                if s_e and d_e:
                    _upsert_edge(db, src, dst, 'MANIFESTS_IN',
                                 weight=min(r['cnt'] / 10.0, 1.0),
                                 evidence_count=r['cnt'], confidence=0.7)
                    edge_type_dist['MANIFESTS_IN'] += 1
        except Exception:
            pass

    # ── 12. REGIME_DEPENDENT edges: law → regime ─────────────────────────────
    regimes_nodes = db.execute("SELECT node_id, name FROM umcg_nodes WHERE node_type='REGIME'").fetchall()
    for law in laws_all:
        law_name = (law['name'] or '').lower()
        for rn in regimes_nodes:
            if rn['name'] and rn['name'].lower() in law_name:
                _upsert_edge(db, law['node_id'], rn['node_id'], 'REGIME_DEPENDENT',
                             weight=0.8, confidence=0.65)
                edge_type_dist['REGIME_DEPENDENT'] += 1

    db.commit()

    # ── Summary ───────────────────────────────────────────────────────────────
    n_nodes = db.execute("SELECT COUNT(*) FROM umcg_nodes").fetchone()[0]
    n_edges = db.execute("SELECT COUNT(*) FROM umcg_edges").fetchone()[0]

    return {
        'success':        True,
        'n_nodes':        n_nodes,
        'n_edges':        n_edges,
        'node_type_dist': dict(node_type_dist),
        'edge_type_dist': dict(edge_type_dist),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Command: compute_metrics
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_compute_metrics(db, params):
    """
    Compute PageRank, betweenness, and eigenvector centrality.
    Updates umcg_nodes. Returns top-10 lists.
    """
    G = _load_graph_from_db(db)
    now = datetime.utcnow().isoformat()

    if HAS_NX:
        pr  = nx.pagerank(G, alpha=0.85, max_iter=500, tol=1e-6)
        bc  = nx.betweenness_centrality(G, normalized=True)
        try:
            ec = nx.eigenvector_centrality(G, max_iter=1000, tol=1e-6)
        except (nx.PowerIterationFailedConvergence, Exception):
            ec = {n: 0.0 for n in G.nodes()}
    else:
        pr = G.simple_pagerank(alpha=0.85)
        bc = G.simple_betweenness()
        ec = {n: 0.0 for n in G.nodes}

    # persist to DB
    for node_id in pr:
        db.execute("""
            UPDATE umcg_nodes
            SET pagerank=?, betweenness=?, eigenvector=?, updated_at=?
            WHERE node_id=?
        """, (pr.get(node_id, 0), bc.get(node_id, 0), ec.get(node_id, 0), now, node_id))
    db.commit()

    # top-10 lists
    def _top(scores, k=10):
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
        result = []
        for nid, score in ranked:
            row = db.execute("SELECT name, node_type FROM umcg_nodes WHERE node_id=?", (nid,)).fetchone()
            result.append({
                'node_id':   nid,
                'name':      row['name'] if row else nid,
                'node_type': row['node_type'] if row else '',
                'score':     round(score, 6),
            })
        return result

    return {
        'success':        True,
        'top_pagerank':   _top(pr),
        'top_betweenness': _top(bc),
        'top_eigenvector': _top(ec),
        'computed_at':    now,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Command: detect_communities
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_detect_communities(db, params):
    """
    Community detection using greedy modularity (networkx) or
    label propagation fallback. Updates community_id in umcg_nodes.
    """
    G = _load_graph_from_db(db)
    now = datetime.utcnow().isoformat()

    if HAS_NX:
        # convert to undirected for community detection
        UG = G.to_undirected()
        try:
            from networkx.algorithms.community import greedy_modularity_communities
            communities = list(greedy_modularity_communities(UG))
        except Exception:
            # fallback: connected components as communities
            communities = [list(c) for c in nx.connected_components(UG)]
    else:
        # simple union-find / connected components via adjacency
        communities = _fallback_components(G)

    community_sizes = {}
    for cid, members in enumerate(communities):
        community_sizes[cid] = len(members)
        for node_id in members:
            db.execute(
                "UPDATE umcg_nodes SET community_id=?, updated_at=? WHERE node_id=?",
                (cid, now, node_id)
            )
    db.commit()

    largest_cid  = max(community_sizes, key=community_sizes.get) if community_sizes else -1
    largest_size = community_sizes.get(largest_cid, 0)

    # sample members of largest community
    largest_members = []
    if largest_cid >= 0 and largest_cid < len(communities):
        members = list(communities[largest_cid])[:10]
        for nid in members:
            row = db.execute("SELECT name, node_type FROM umcg_nodes WHERE node_id=?", (nid,)).fetchone()
            largest_members.append({'node_id': nid, 'name': row['name'] if row else nid,
                                    'node_type': row['node_type'] if row else ''})

    return {
        'success':          True,
        'n_communities':    len(communities),
        'community_sizes':  community_sizes,
        'largest_community': {
            'community_id': largest_cid,
            'size':         largest_size,
            'sample':       largest_members,
        },
    }


def _fallback_components(G):
    """Union-Find connected components for _FallbackGraph."""
    parent = {n: n for n in G.nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for src, dst, _ in G.edges:
        union(src, dst)

    groups = defaultdict(list)
    for n in G.nodes:
        groups[find(n)].append(n)
    return list(groups.values())


# ═══════════════════════════════════════════════════════════════════════════════
# Command: find_fragility
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_find_fragility(db, params):
    """
    Identify fragility hubs (top 10% PageRank AND betweenness)
    and structural bridges (edges whose removal disconnects components).
    """
    rows = db.execute(
        "SELECT node_id, name, node_type, pagerank, betweenness FROM umcg_nodes ORDER BY pagerank DESC"
    ).fetchall()
    if not rows:
        return {'success': True, 'n_hubs': 0, 'n_bridges': 0, 'hub_list': [], 'bridge_list': []}

    n     = len(rows)
    top10 = max(1, int(n * 0.10))

    pr_sorted = sorted(rows, key=lambda r: r['pagerank'] or 0, reverse=True)
    bc_sorted = sorted(rows, key=lambda r: r['betweenness'] or 0, reverse=True)

    top_pr_ids = {r['node_id'] for r in pr_sorted[:top10]}
    top_bc_ids = {r['node_id'] for r in bc_sorted[:top10]}
    hub_ids    = top_pr_ids & top_bc_ids

    now = datetime.utcnow().isoformat()
    db.execute("UPDATE umcg_nodes SET is_fragility_hub=0")
    for nid in hub_ids:
        db.execute("UPDATE umcg_nodes SET is_fragility_hub=1, updated_at=? WHERE node_id=?", (now, nid))

    hub_list = []
    for nid in hub_ids:
        r = db.execute("SELECT name, node_type, pagerank, betweenness FROM umcg_nodes WHERE node_id=?",
                       (nid,)).fetchone()
        if r:
            hub_list.append({'node_id': nid, 'name': r['name'], 'node_type': r['node_type'],
                             'pagerank': round(r['pagerank'] or 0, 6),
                             'betweenness': round(r['betweenness'] or 0, 6)})

    # Structural bridges: edges whose removal changes component count
    G       = _load_graph_from_db(db)
    bridges = _find_bridge_edges(G)

    db.execute("UPDATE umcg_nodes SET is_structural_bridge=0")
    bridge_list = []
    for (src, dst) in bridges[:50]:  # cap at 50
        db.execute("UPDATE umcg_nodes SET is_structural_bridge=1, updated_at=? WHERE node_id=?", (now, src))
        bridge_list.append({'source': src, 'target': dst})

    db.commit()

    return {
        'success':     True,
        'n_hubs':      len(hub_ids),
        'n_bridges':   len(bridges),
        'hub_list':    sorted(hub_list, key=lambda x: x['pagerank'], reverse=True),
        'bridge_list': bridge_list[:20],
    }


def _find_bridge_edges(G):
    """Return list of (src, dst) bridge edges."""
    if HAS_NX:
        UG = G.to_undirected()
        try:
            return list(nx.bridges(UG))
        except Exception:
            return []
    else:
        # simplified: edges connecting two otherwise separate components
        # detect by checking if removing edge changes component count
        initial_components = len(_fallback_components(G))
        bridges = []
        for src, dst, _ in G.edges:
            # temporarily remove
            G._adj[src].discard(dst)
            comps = len(_fallback_components(G))
            G._adj[src].add(dst)
            if comps > initial_components:
                bridges.append((src, dst))
        return bridges


# ═══════════════════════════════════════════════════════════════════════════════
# Command: weekly_snapshot
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_weekly_snapshot(db, params):
    """
    Compute full metrics, persist to umcg_snapshots, return evolution delta.
    """
    # ensure metrics are up-to-date
    metrics    = cmd_compute_metrics(db, {})
    community  = cmd_detect_communities(db, {})
    fragility  = cmd_find_fragility(db, {})

    n_nodes = db.execute("SELECT COUNT(*) FROM umcg_nodes").fetchone()[0]
    n_edges = db.execute("SELECT COUNT(*) FROM umcg_edges").fetchone()[0]

    # average clustering
    avg_clustering = 0.0
    if HAS_NX:
        G = _load_graph_from_db(db)
        try:
            UG = G.to_undirected()
            avg_clustering = nx.average_clustering(UG)
        except Exception:
            avg_clustering = 0.0

    # graph density
    graph_density = (2 * n_edges / (n_nodes * (n_nodes - 1))) if n_nodes > 1 else 0.0

    # fragility hubs
    hub_ids = [h['node_id'] for h in fragility.get('hub_list', [])]

    snapshot_date = datetime.utcnow().strftime('%Y-%m-%d')
    computed_at   = datetime.utcnow().isoformat()

    # compute evolution delta vs previous snapshot
    prev = db.execute(
        "SELECT * FROM umcg_snapshots WHERE snapshot_date < ? ORDER BY snapshot_date DESC LIMIT 1",
        (snapshot_date,)
    ).fetchone()

    evolution_delta = {}
    if prev:
        evolution_delta = {
            'node_delta':      n_nodes - (prev['n_nodes'] or 0),
            'edge_delta':      n_edges - (prev['n_edges'] or 0),
            'density_delta':   round(graph_density - (prev['graph_density'] or 0), 6),
            'community_delta': community['n_communities'] - (prev['n_communities'] or 0),
            'prev_date':       prev['snapshot_date'],
        }

    db.execute("""
        INSERT OR REPLACE INTO umcg_snapshots
            (snapshot_date, n_nodes, n_edges, avg_clustering, n_communities,
             graph_density, top_pagerank, top_betweenness, fragility_hubs,
             evolution_delta, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        snapshot_date, n_nodes, n_edges,
        round(avg_clustering, 6), community['n_communities'],
        round(graph_density, 8),
        json.dumps(metrics.get('top_pagerank', [])[:5], default=str),
        json.dumps(metrics.get('top_betweenness', [])[:5], default=str),
        json.dumps(hub_ids[:10], default=str),
        json.dumps(evolution_delta, default=str),
        computed_at,
    ))
    db.commit()

    return {
        'success':        True,
        'snapshot_date':  snapshot_date,
        'n_nodes':        n_nodes,
        'n_edges':        n_edges,
        'n_communities':  community['n_communities'],
        'graph_density':  round(graph_density, 6),
        'n_fragility_hubs': fragility['n_hubs'],
        'evolution_delta': evolution_delta,
        'key_changes':    _summarise_delta(evolution_delta),
        'computed_at':    computed_at,
    }


def _summarise_delta(delta):
    if not delta:
        return ['First snapshot — no previous baseline.']
    msgs = []
    if delta.get('node_delta', 0) != 0:
        msgs.append(f"Nodes {'grew' if delta['node_delta']>0 else 'shrank'} by {abs(delta['node_delta'])} since {delta.get('prev_date','?')}.")
    if delta.get('edge_delta', 0) != 0:
        msgs.append(f"Edges {'grew' if delta['edge_delta']>0 else 'shrank'} by {abs(delta['edge_delta'])}.")
    if abs(delta.get('density_delta', 0)) > 1e-6:
        msgs.append(f"Graph density changed by {delta['density_delta']:+.4f}.")
    return msgs or ['No significant structural change.']


# ═══════════════════════════════════════════════════════════════════════════════
# Command: query_paths
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_query_paths(db, params):
    """
    Find shortest causal path(s) between two nodes.
    params: {source: 'sector:finance', target: 'stock:comi', max_paths: 3}
    """
    raw_source = params.get('source', '')
    raw_target = params.get('target', '')
    max_paths  = int(params.get('max_paths', 3))

    # resolve node IDs (allow partial match)
    def _resolve(raw):
        # direct match
        row = db.execute("SELECT node_id FROM umcg_nodes WHERE node_id=?", (raw,)).fetchone()
        if row:
            return row['node_id']
        # lower-case match
        row = db.execute("SELECT node_id FROM umcg_nodes WHERE LOWER(node_id)=LOWER(?)", (raw,)).fetchone()
        if row:
            return row['node_id']
        # LIKE match on name
        row = db.execute("SELECT node_id FROM umcg_nodes WHERE LOWER(name) LIKE LOWER(?)",
                         (f'%{raw}%',)).fetchone()
        if row:
            return row['node_id']
        return None

    src_id = _resolve(raw_source)
    dst_id = _resolve(raw_target)

    if not src_id:
        return {'success': False, 'error': f"Source node not found: {raw_source}"}
    if not dst_id:
        return {'success': False, 'error': f"Target node not found: {raw_target}"}

    if src_id == dst_id:
        return {'success': True, 'paths': [[src_id]], 'path_weights': [0.0]}

    G = _load_graph_from_db(db)

    if HAS_NX:
        if src_id not in G.nodes() or dst_id not in G.nodes():
            return {'success': False, 'error': 'Source or target not in graph.'}
        try:
            shortest = nx.shortest_path(G, src_id, dst_id)
            paths    = [shortest]
            try:
                all_paths = list(nx.all_simple_paths(G, src_id, dst_id, cutoff=6))
                all_paths.sort(key=len)
                paths = all_paths[:max_paths]
            except Exception:
                pass
        except nx.NetworkXNoPath:
            return {'success': False, 'error': f"No path found from {src_id} to {dst_id}"}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    else:
        # BFS fallback
        paths = _bfs_path(G, src_id, dst_id)
        if not paths:
            return {'success': False, 'error': f"No path found from {src_id} to {dst_id}"}

    # compute path weights (sum of edge weights)
    def _path_weight(path):
        total = 0.0
        for i in range(len(path) - 1):
            eid = None
            row = db.execute(
                "SELECT weight FROM umcg_edges WHERE source_id=? AND target_id=? LIMIT 1",
                (path[i], path[i+1])
            ).fetchone()
            total += float(row['weight']) if row else 1.0
        return round(total, 4)

    path_weights = [_path_weight(p) for p in paths]

    # enrich paths with names
    def _enrich(path):
        out = []
        for nid in path:
            row = db.execute("SELECT name, node_type FROM umcg_nodes WHERE node_id=?", (nid,)).fetchone()
            out.append({'node_id': nid,
                        'name': row['name'] if row else nid,
                        'node_type': row['node_type'] if row else ''})
        return out

    return {
        'success':      True,
        'source':       src_id,
        'target':       dst_id,
        'paths':        [_enrich(p) for p in paths],
        'path_weights': path_weights,
        'n_paths_found': len(paths),
    }


def _bfs_path(G, src, dst):
    """BFS for single shortest path in _FallbackGraph."""
    from collections import deque
    q       = deque([[src]])
    visited = {src}
    while q:
        path = q.popleft()
        node = path[-1]
        for nb in G._adj.get(node, set()):
            new_path = path + [nb]
            if nb == dst:
                return [new_path]
            if nb not in visited:
                visited.add(nb)
                q.append(new_path)
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# Command: get_snapshot
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_get_snapshot(db, params):
    """
    Return the latest umcg_snapshot + top nodes + graph summary.
    """
    snap = db.execute(
        "SELECT * FROM umcg_snapshots ORDER BY snapshot_date DESC LIMIT 1"
    ).fetchone()

    n_nodes = db.execute("SELECT COUNT(*) FROM umcg_nodes").fetchone()[0]
    n_edges = db.execute("SELECT COUNT(*) FROM umcg_edges").fetchone()[0]

    node_type_dist = {}
    for r in db.execute("SELECT node_type, COUNT(*) AS cnt FROM umcg_nodes GROUP BY node_type"):
        node_type_dist[r['node_type']] = r['cnt']

    edge_type_dist = {}
    for r in db.execute("SELECT edge_type, COUNT(*) AS cnt FROM umcg_edges GROUP BY edge_type"):
        edge_type_dist[r['edge_type']] = r['cnt']

    # top nodes by PageRank
    top_pr = []
    for r in db.execute(
        "SELECT node_id, name, node_type, pagerank, betweenness, community_id, is_fragility_hub "
        "FROM umcg_nodes ORDER BY pagerank DESC LIMIT 10"
    ).fetchall():
        top_pr.append({
            'node_id':         r['node_id'],
            'name':            r['name'],
            'node_type':       r['node_type'],
            'pagerank':        round(r['pagerank'] or 0, 6),
            'betweenness':     round(r['betweenness'] or 0, 6),
            'community_id':    r['community_id'],
            'is_fragility_hub': bool(r['is_fragility_hub']),
        })

    # fragility hubs
    hubs = []
    for r in db.execute(
        "SELECT node_id, name, node_type, pagerank, betweenness "
        "FROM umcg_nodes WHERE is_fragility_hub=1 ORDER BY pagerank DESC LIMIT 10"
    ).fetchall():
        hubs.append({'node_id': r['node_id'], 'name': r['name'],
                     'node_type': r['node_type'],
                     'pagerank': round(r['pagerank'] or 0, 6),
                     'betweenness': round(r['betweenness'] or 0, 6)})

    result = {
        'success':        True,
        'n_nodes':        n_nodes,
        'n_edges':        n_edges,
        'node_type_dist': node_type_dist,
        'edge_type_dist': edge_type_dist,
        'top_pagerank':   top_pr,
        'fragility_hubs': hubs,
    }

    if snap:
        result['latest_snapshot'] = {
            'snapshot_date':  snap['snapshot_date'],
            'n_nodes':        snap['n_nodes'],
            'n_edges':        snap['n_edges'],
            'avg_clustering': snap['avg_clustering'],
            'n_communities':  snap['n_communities'],
            'graph_density':  snap['graph_density'],
            'computed_at':    snap['computed_at'],
            'evolution_delta': _safe_json_load(snap['evolution_delta']),
        }
    else:
        result['latest_snapshot'] = None

    return result


def _safe_json_load(s):
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    'build_full':          cmd_build_full,
    'compute_metrics':     cmd_compute_metrics,
    'detect_communities':  cmd_detect_communities,
    'find_fragility':      cmd_find_fragility,
    'weekly_snapshot':     cmd_weekly_snapshot,
    'query_paths':         cmd_query_paths,
    'get_snapshot':        cmd_get_snapshot,
}


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'get_snapshot'
    params  = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    if command not in COMMANDS:
        result = {
            'success':           False,
            'error':             f"Unknown command: {command}",
            'available_commands': list(COMMANDS.keys()),
        }
        print(json.dumps(result, default=str))
        return

    try:
        db     = get_db()
        result = COMMANDS[command](db, params)
        db.close()
    except Exception as exc:
        import traceback
        result = {
            'success': False,
            'error':   str(exc),
            'trace':   traceback.format_exc(),
        }

    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
