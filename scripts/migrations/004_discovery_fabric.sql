-- L11 Discovery Fabric — unified atom registry SSOT
CREATE TABLE IF NOT EXISTS discovery_atom_registry (
    atom_id           TEXT NOT NULL,
    source_layer      TEXT NOT NULL,
    source_table      TEXT,
    source_miner      TEXT,
    condition_json    TEXT,
    regime_filter     TEXT NOT NULL DEFAULT '',
    status            TEXT DEFAULT 'proposed',
    backtest_wr       REAL,
    backtest_n        INTEGER,
    backtest_lift     REAL,
    backtest_pf       REAL,
    boost_weight      REAL DEFAULT 1.0,
    penalize_weight   REAL DEFAULT 1.0,
    ml_feature_col    TEXT,
    hard_negative     INTEGER DEFAULT 0,
    validated_at      TEXT,
    proposed_at       TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (atom_id, regime_filter)
);

CREATE INDEX IF NOT EXISTS idx_discovery_atoms_status
    ON discovery_atom_registry(status, backtest_lift DESC);

CREATE INDEX IF NOT EXISTS idx_discovery_atoms_miner
    ON discovery_atom_registry(source_miner);

CREATE TABLE IF NOT EXISTS discovery_fabric_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at        TEXT DEFAULT (datetime('now')),
    stage         TEXT NOT NULL,
    n_proposed    INTEGER,
    n_validated   INTEGER,
    n_rejected    INTEGER,
    detail_json   TEXT
);
