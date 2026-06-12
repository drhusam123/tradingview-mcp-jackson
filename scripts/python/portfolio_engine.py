"""
Portfolio Construction Engine — EGX Navigator  Ph80
=====================================================
يحوّل قائمة الإشارات اليومية إلى محفظة مؤسسية مُحكمة.

المبادئ:
  1. Risk-first: size by risk budget, not by conviction alone
  2. Correlation control: max 3 stocks per correlation group
  3. Sector concentration: max 25% per sector
  4. Liquidity: max 10% of ADV20 per position (capacity model)
  5. Portfolio heat: max 15% at-risk simultaneously
  6. Drawdown protection: multiplier from risk_engine

CLI:
  python3 portfolio_engine.py build                        # محفظة اليوم بـ 1M EGP
  python3 portfolio_engine.py build --capital 5000000      # 5M EGP
  python3 portfolio_engine.py build --json                 # JSON output
  python3 portfolio_engine.py capacity --symbol OCPH       # capacity analysis per symbol
"""

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DB_PATH: Path = Path(__file__).resolve().parent.parent.parent / "data" / "egx_trading.db"

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
EXPOSURE_MAP: dict[str, float] = {
    "BULL_HIGH_CONFIDENCE": 0.75,
    "BULL_NEUTRAL_BREADTH": 0.52,
    "BULL_WEAK_BREADTH": 0.35,
    "NEUTRAL": 0.30,
    "BEAR": 0.10,
    "CRISIS": 0.00,
}

SECTOR_LIMITS: dict[str, float] = {
    "Banks": 0.25,
    "Real Estate": 0.20,
    "Technology": 0.15,
    "Consumer": 0.20,
    "Industrials": 0.20,
    "Construction": 0.18,
    "Healthcare": 0.15,
    "Health Technology": 0.15,
    "Health Services": 0.15,
    "Telecoms": 0.15,
    "Communications": 0.15,
    "Food & Bev": 0.18,
    "Chemicals": 0.15,
    "Utilities": 0.12,
    "Finance": 0.25,
    "Process Industries": 0.20,
    "Producer Manufacturing": 0.20,
    "Non-Energy Minerals": 0.18,
    "default": 0.15,
}

MAX_PORTFOLIO_HEAT: float = 0.15       # 15 % max at-risk simultaneously
MAX_SIGNALS_PER_DAY: int = 7           # behavioral guardrail
MIN_HOLDING_DAYS: dict[str, int] = {
    "SHORT_SWING": 2,
    "LONG_SWING": 5,
    "default": 1,
}
MAX_CONSEC_LOSSES_BEFORE_REVIEW: int = 3
DEFAULT_CAPITAL: int = 1_000_000       # 1 M EGP

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _sector_limit(sector: str) -> float:
    return SECTOR_LIMITS.get(sector, SECTOR_LIMITS["default"])


# ---------------------------------------------------------------------------
# Main engine class
# ---------------------------------------------------------------------------

class PortfolioEngine:
    """Risk-first portfolio constructor for EGX Navigator daily signals."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def construct_portfolio(
        self,
        signals: List[Dict[str, Any]],
        regime_state: str,
        portfolio_capital: float,
        drawdown_multiplier: float = 1.0,
        existing_positions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Build a risk-budgeted, capacity-constrained portfolio from today's signals.

        Parameters
        ----------
        signals : list of dicts from unified_signals
        regime_state : one of EXPOSURE_MAP keys
        portfolio_capital : total capital in EGP
        drawdown_multiplier : float in (0, 1] from risk_engine; reduces exposure after losses
        existing_positions : (optional) open positions already carrying heat

        Returns
        -------
        dict with keys: allocations, summary, rejected, warnings
        """
        warnings: List[str] = []
        rejected: List[Dict[str, Any]] = []

        # ── Behavioral guardrail ──────────────────────────────────────────
        if len(signals) > MAX_SIGNALS_PER_DAY:
            warnings.append(
                f"Too many signals ({len(signals)}) → capped at {MAX_SIGNALS_PER_DAY}"
            )
            signals = sorted(signals, key=lambda s: s.get("ues_score", 0), reverse=True)[
                :MAX_SIGNALS_PER_DAY
            ]

        # ── Exposure budget ───────────────────────────────────────────────
        base_exposure = EXPOSURE_MAP.get(regime_state, EXPOSURE_MAP["NEUTRAL"])
        target_exposure = _clamp(base_exposure * drawdown_multiplier, 0.0, 1.0)
        if drawdown_multiplier < 1.0:
            warnings.append(
                f"Drawdown multiplier {drawdown_multiplier:.2f} → exposure reduced "
                f"from {base_exposure:.0%} to {target_exposure:.0%}"
            )

        if regime_state == "CRISIS":
            return {
                "allocations": [],
                "summary": {
                    "regime_state": regime_state,
                    "target_exposure": 0.0,
                    "total_deployed_pct": 0.0,
                    "total_deployed_egp": 0.0,
                    "portfolio_heat": 0.0,
                    "cash_pct": 1.0,
                    "n_positions": 0,
                    "capital": portfolio_capital,
                    "date": date.today().isoformat(),
                },
                "rejected": [
                    {"symbol": s.get("symbol", "?"), "reason": "CRISIS_MODE_NO_TRADES"}
                    for s in signals
                ],
                "warnings": ["CRISIS regime: zero deployment."],
            }

        # ── Portfolio gates ───────────────────────────────────────────────
        filtered, gate_rejected = self._apply_portfolio_gates(signals, regime_state)
        rejected.extend(gate_rejected)

        # ── Rank by UES score ────────────────────────────────────────────
        filtered = sorted(filtered, key=lambda s: s.get("ues_score", 0), reverse=True)

        # ── Correlation de-dup (sector proxy) ────────────────────────────
        filtered, corr_rejected = self._correlation_dedup(filtered)
        rejected.extend(corr_rejected)

        # ── Existing heat ─────────────────────────────────────────────────
        existing_heat = 0.0
        if existing_positions:
            existing_heat = sum(p.get("at_risk_pct", 0.0) for p in existing_positions)

        # ── Sector running totals ─────────────────────────────────────────
        sector_deployed: Dict[str, float] = {}
        total_deployed = 0.0
        total_heat = existing_heat
        allocations: List[Dict[str, Any]] = []

        for sig in filtered:
            symbol = sig.get("symbol", "UNKNOWN")
            entry = float(sig.get("entry", 0) or sig.get("entry_price", 0) or 0)
            sl = float(sig.get("stop_loss", 0) or 0)
            t1 = float(sig.get("target1", 0) or sig.get("t1_target", 0) or 0)
            ues_score = float(sig.get("ues_score", 50) or 50)
            sector = str(sig.get("sector", "Unknown") or "Unknown")
            adv20 = float(sig.get("adv20_value", 0) or sig.get("avg_daily_volume", 0) or 0)
            rsi14 = sig.get("rsi14")
            adx14 = sig.get("adx14")
            signal_type = str(sig.get("signal_type", sig.get("behavioral_class", "SWING")) or "SWING")

            # ── Validate entry / stop ─────────────────────────────────────
            if entry <= 0 or sl <= 0 or sl >= entry:
                rejected.append({"symbol": symbol, "reason": "INVALID_ENTRY_OR_SL"})
                continue

            # ── Trade risk percent ────────────────────────────────────────
            trade_risk_pct = abs(entry - sl) / entry
            trade_risk_pct = _clamp(trade_risk_pct, 0.03, 0.25)

            # ── Conviction multiplier ─────────────────────────────────────
            conviction_mult = 1.0 + 0.5 * ((ues_score - 60.0) / 40.0)
            conviction_mult = _clamp(conviction_mult, 0.7, 1.5)

            # ── Target risk per position ──────────────────────────────────
            remaining_heat = MAX_PORTFOLIO_HEAT - total_heat
            if remaining_heat <= 0:
                rejected.append({"symbol": symbol, "reason": "PORTFOLIO_HEAT_FULL"})
                warnings.append("Portfolio heat limit reached — remaining signals rejected.")
                break

            target_risk = min(0.02 * conviction_mult, remaining_heat)

            # ── Position size (as fraction of capital) ───────────────────
            position_size = target_risk / trade_risk_pct

            # ── Liquidity cap: max 10 % of ADV20 ─────────────────────────
            if adv20 > 0:
                max_by_liquidity = (adv20 * 0.10) / portfolio_capital
            else:
                max_by_liquidity = 0.05  # conservative default

            # ── Hard caps ────────────────────────────────────────────────
            position_size = min(position_size, max_by_liquidity, 0.10)

            # ── Sector cap ───────────────────────────────────────────────
            sec_limit = _sector_limit(sector)
            sec_used = sector_deployed.get(sector, 0.0)
            position_size = min(position_size, sec_limit - sec_used)

            # ── Exposure ceiling ─────────────────────────────────────────
            remaining_exposure = target_exposure - total_deployed
            position_size = min(position_size, remaining_exposure)

            # ── Minimum viable size check ─────────────────────────────────
            if position_size < 0.005:
                rejected.append({"symbol": symbol, "reason": "SIZE_BELOW_MINIMUM"})
                continue

            # ── Accept the position ───────────────────────────────────────
            size_egp = position_size * portfolio_capital
            at_risk_pct = position_size * trade_risk_pct
            adv20_egp = adv20 if adv20 > 0 else None

            rationale = (
                f"UES={ues_score:.1f} | regime={regime_state} | "
                f"risk={trade_risk_pct:.1%} | cvx={conviction_mult:.2f} | "
                f"sec_used={sec_used:.1%}+{position_size:.1%}"
            )

            allocations.append(
                {
                    "symbol": symbol,
                    "signal_type": signal_type,
                    "size_pct": round(position_size, 4),
                    "size_egp": round(size_egp, 2),
                    "at_risk_pct": round(at_risk_pct, 4),
                    "sector": sector,
                    "ues_score": round(ues_score, 2),
                    "entry": entry,
                    "stop_loss": sl,
                    "target1": t1,
                    "adv20_egp": adv20_egp,
                    "rsi14": rsi14,
                    "adx14": adx14,
                    "rationale": rationale,
                }
            )

            # ── Update running totals ─────────────────────────────────────
            sector_deployed[sector] = sec_used + position_size
            total_deployed += position_size
            total_heat += at_risk_pct

            # ── Stop once exposure target is hit ─────────────────────────
            if total_deployed >= target_exposure - 0.001:
                break

        summary = {
            "date": date.today().isoformat(),
            "regime_state": regime_state,
            "target_exposure": round(target_exposure, 4),
            "total_deployed_pct": round(total_deployed, 4),
            "total_deployed_egp": round(total_deployed * portfolio_capital, 2),
            "portfolio_heat": round(total_heat, 4),
            "cash_pct": round(1.0 - total_deployed, 4),
            "n_positions": len(allocations),
            "capital": portfolio_capital,
            "sector_breakdown": self._sector_summary(allocations),
            "drawdown_multiplier": drawdown_multiplier,
        }

        return {
            "allocations": allocations,
            "summary": summary,
            "rejected": rejected,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Gate filters
    # ------------------------------------------------------------------

    def _apply_portfolio_gates(
        self, signals: List[Dict[str, Any]], regime: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Hard-gate filters:
          - Deduplicate same symbol (keep highest UES)
          - Remove LOW_LIQUIDITY if ADV20 < 1 M EGP
          - In BEAR: only INVESTMENT and UNDERVALUED signal_types pass
        """
        rejected: List[Dict[str, Any]] = []
        seen_symbols: Dict[str, Dict[str, Any]] = {}

        # Dedup by symbol — keep highest UES
        for sig in signals:
            sym = sig.get("symbol", "")
            score = float(sig.get("ues_score", 0) or 0)
            if sym not in seen_symbols or score > float(
                seen_symbols[sym].get("ues_score", 0) or 0
            ):
                seen_symbols[sym] = sig

        deduped = list(seen_symbols.values())

        filtered: List[Dict[str, Any]] = []
        for sig in deduped:
            sym = sig.get("symbol", "?")
            adv20 = float(
                sig.get("adv20_value", 0)
                or sig.get("avg_daily_volume", 0)
                or 0
            )
            liquidity_tier = str(sig.get("liquidity_tier", "") or "")
            signal_type = str(
                sig.get("signal_type", sig.get("behavioral_class", "")) or ""
            )

            # Liquidity gate
            if adv20 < 1_000_000 and liquidity_tier.upper() in ("LOW_LIQUIDITY", "TIER3", "TIER4"):
                rejected.append({"symbol": sym, "reason": f"LOW_LIQUIDITY:ADV20={adv20/1e6:.2f}M"})
                continue

            # BEAR regime signal-type filter
            if regime == "BEAR" and signal_type.upper() not in (
                "INVESTMENT",
                "UNDERVALUED",
            ):
                rejected.append(
                    {"symbol": sym, "reason": f"BEAR_REGIME_FILTER:{signal_type}"}
                )
                continue

            filtered.append(sig)

        return filtered, rejected

    # ------------------------------------------------------------------
    # Correlation dedup (sector proxy)
    # ------------------------------------------------------------------

    def _correlation_dedup(
        self,
        signals: List[Dict[str, Any]],
        max_per_group: int = 3,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Group by sector (proxy for correlation group).
        Keep top `max_per_group` per sector by UES score.

        Note: A real implementation would use a rolling correlation
        matrix from ohlcv_history_execution; here sector membership is the
        simplified proxy.
        """
        from collections import defaultdict

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for sig in signals:
            sector = str(sig.get("sector", "Unknown") or "Unknown")
            groups[sector].append(sig)

        kept: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        for sector, group in groups.items():
            sorted_group = sorted(
                group, key=lambda s: float(s.get("ues_score", 0) or 0), reverse=True
            )
            kept.extend(sorted_group[:max_per_group])
            for sig in sorted_group[max_per_group:]:
                rejected.append(
                    {
                        "symbol": sig.get("symbol", "?"),
                        "reason": f"CORR_GROUP_LIMIT:{sector}",
                    }
                )

        return kept, rejected

    # ------------------------------------------------------------------
    # Sector limits (enforcement in sizing loop; this returns unchanged)
    # ------------------------------------------------------------------

    def _enforce_sector_limits(
        self, signals: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Returns signals unchanged — actual sector-limit enforcement
        happens inside the position-sizing loop in construct_portfolio(),
        where running sector totals are tracked per position added.
        """
        return signals

    # ------------------------------------------------------------------
    # Sector summary
    # ------------------------------------------------------------------

    def _sector_summary(
        self, allocations: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """Returns {sector: total_pct} sorted descending."""
        totals: Dict[str, float] = {}
        for alloc in allocations:
            sec = alloc.get("sector", "Unknown")
            totals[sec] = totals.get(sec, 0.0) + alloc.get("size_pct", 0.0)
        return dict(sorted(totals.items(), key=lambda x: x[1], reverse=True))

    # ------------------------------------------------------------------
    # Regime detection
    # ------------------------------------------------------------------

    def _determine_regime_state(self, conn: sqlite3.Connection) -> str:
        """
        Derive granular regime state from market_breadth_enhanced +
        markov_signal_daily.

        Mapping:
          BULL + ad_ratio > 0.55  → BULL_HIGH_CONFIDENCE
          BULL + 0.40-0.55        → BULL_NEUTRAL_BREADTH
          BULL + < 0.40           → BULL_WEAK_BREADTH
          SIDEWAYS / NEUTRAL      → NEUTRAL
          BEAR                    → BEAR
          fallback                → NEUTRAL
        """
        cur = conn.cursor()

        # --- market breadth ---
        ad_ratio: Optional[float] = None
        breadth_signal: str = ""
        try:
            cur.execute(
                """SELECT ad_ratio, signal
                   FROM market_breadth_enhanced
                   ORDER BY date DESC LIMIT 1"""
            )
            row = cur.fetchone()
            if row:
                ad_ratio = float(row[0]) if row[0] is not None else None
                breadth_signal = str(row[1] or "")
        except sqlite3.OperationalError:
            pass

        # --- markov regime ---
        markov_state: str = ""
        markov_confidence: float = 0.0
        try:
            cur.execute(
                """SELECT current_state, continuation_confidence
                   FROM markov_signal_daily
                   ORDER BY date DESC LIMIT 1"""
            )
            row = cur.fetchone()
            if row:
                markov_state = str(row[0] or "").upper()
                markov_confidence = float(row[1] or 0.0)
        except sqlite3.OperationalError:
            pass

        # Breadth-to-regime heuristic
        def _from_breadth(signal: str) -> str:
            s = signal.upper()
            if "LEAN_BULL" in s or "BULL" in s:
                return "BULL"
            if "LEAN_BEAR" in s or "BEAR" in s:
                return "BEAR"
            return "NEUTRAL"

        base_regime = markov_state or _from_breadth(breadth_signal)

        if base_regime == "BULL":
            if ad_ratio is not None:
                if ad_ratio > 0.55:
                    return "BULL_HIGH_CONFIDENCE"
                elif ad_ratio >= 0.40:
                    return "BULL_NEUTRAL_BREADTH"
                else:
                    return "BULL_WEAK_BREADTH"
            # No ad_ratio available — use breadth signal
            if "HIGH" in breadth_signal.upper() or "LEAN_BULL" in breadth_signal.upper():
                return "BULL_HIGH_CONFIDENCE"
            return "BULL_NEUTRAL_BREADTH"

        if base_regime == "BEAR":
            return "BEAR"

        if base_regime in ("CRISIS",):
            return "CRISIS"

        return "NEUTRAL"


# ---------------------------------------------------------------------------
# Standalone data-loading helpers
# ---------------------------------------------------------------------------

def load_today_signals(
    db_path: "Path | str" = DB_PATH, date_str: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Load unified_signals for today (or `date_str`) from the DB.
    Joins with symbol_liquidity_profile for ADV20 and sector data.
    Only returns quality_gate_passed = 1 rows.

    Returns list of dicts suitable for PortfolioEngine.construct_portfolio().
    """
    target_date = date_str or date.today().isoformat()
    db_path = Path(db_path)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # unified_signals columns available
        query = """
            SELECT
                us.symbol,
                us.signal_date,
                us.unified_score          AS ues_score,
                us.conviction_tier,
                us.active_regime          AS regime_at_signal,
                us.entry_price            AS entry,
                us.entry_high,
                us.stop_loss,
                us.t1_target              AS target1,
                us.t2_target              AS target2,
                us.r_ratio,
                us.behavioral_class       AS signal_type,
                us.liquidity_tier,
                us.max_position_egp,
                us.quality_gate_passed,
                us.gate_reason,
                us.dna_score,
                us.cycle_score,
                us.pine_rs_percentile,
                -- from symbol_liquidity_profile
                COALESCE(slp.avg_daily_volume,  lp.advt_30d, 0) AS adv20_value,
                COALESCE(slp.sector, su.sector, 'Unknown')       AS sector,
                -- technical indicators (latest available)
                tic.rsi_14                AS rsi14,
                NULL                      AS atr14,
                NULL                      AS adx14
            FROM unified_signals us
            LEFT JOIN symbol_liquidity_profile slp ON us.symbol = slp.symbol
            LEFT JOIN liquidity_profile lp
                   ON us.symbol = lp.symbol
            LEFT JOIN stock_universe su  ON us.symbol = su.symbol
            LEFT JOIN (
                SELECT symbol, rsi_14, fetch_date
                FROM technical_indicators_cache t1
                WHERE fetch_date = (
                    SELECT MAX(t2.fetch_date)
                    FROM technical_indicators_cache t2
                    WHERE t2.symbol = t1.symbol
                )
            ) tic ON us.symbol = tic.symbol
            WHERE us.signal_date = ?
              AND us.quality_gate_passed = 1
            ORDER BY us.unified_score DESC
        """
        cur = conn.execute(query, (target_date,))
        rows = cur.fetchall()
    except sqlite3.OperationalError as exc:
        conn.close()
        raise RuntimeError(f"DB query failed: {exc}") from exc

    conn.close()

    if not rows:
        return []

    signals = [dict(row) for row in rows]
    return signals


def build_portfolio_from_db(
    db_path: "Path | str" = DB_PATH,
    capital: float = DEFAULT_CAPITAL,
    date_str: Optional[str] = None,
) -> Dict[str, Any]:
    """
    End-to-end pipeline:
      1. Load today's signals from DB
      2. Determine regime state
      3. Construct portfolio
      4. Persist allocations to portfolio_allocations table
      5. Return portfolio dict

    Raises SystemExit with an informative message when no signals are found.
    """
    db_path = Path(db_path)
    target_date = date_str or date.today().isoformat()

    # ── Load signals ──────────────────────────────────────────────────────
    signals = load_today_signals(db_path, target_date)
    if not signals:
        print(
            f"[EGX Navigator] لا توجد إشارات بتاريخ {target_date} "
            f"في unified_signals (quality_gate_passed=1)."
        )
        sys.exit(0)

    # ── Determine regime ──────────────────────────────────────────────────
    conn = sqlite3.connect(db_path)
    engine = PortfolioEngine()
    regime_state = engine._determine_regime_state(conn)
    conn.close()

    # ── Construct portfolio ───────────────────────────────────────────────
    portfolio = engine.construct_portfolio(
        signals=signals,
        regime_state=regime_state,
        portfolio_capital=capital,
    )

    # ── Persist to DB ─────────────────────────────────────────────────────
    _save_portfolio_to_db(db_path, portfolio, target_date, regime_state, capital)

    return portfolio


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def _save_portfolio_to_db(
    db_path: Path,
    portfolio: dict[str, Any],
    target_date: str,
    regime_state: str,
    capital: float,
) -> None:
    """
    Upsert portfolio allocations into a dedicated table.
    Creates the table if it does not exist (graceful schema migration).
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Use a dedicated table with our schema to avoid conflicts
    # with the pre-existing portfolio_allocations table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nav_portfolio_allocations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            allocation_date TEXT,
            symbol          TEXT,
            signal_type     TEXT,
            size_pct        REAL,
            size_egp        REAL,
            at_risk_pct     REAL,
            sector          TEXT,
            ues_score       REAL,
            entry           REAL,
            stop_loss       REAL,
            target1         REAL,
            rationale       TEXT,
            regime_state    TEXT,
            capital_aum     REAL,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Remove any previous run for the same date to allow re-runs
    cur.execute(
        "DELETE FROM nav_portfolio_allocations WHERE allocation_date = ?",
        (target_date,),
    )

    now_str = datetime.utcnow().isoformat(timespec="seconds")
    rows = [
        (
            target_date,
            alloc["symbol"],
            alloc.get("signal_type", ""),
            alloc["size_pct"],
            alloc["size_egp"],
            alloc["at_risk_pct"],
            alloc.get("sector", ""),
            alloc.get("ues_score"),
            alloc.get("entry"),
            alloc.get("stop_loss"),
            alloc.get("target1"),
            alloc.get("rationale", ""),
            regime_state,
            capital,
            now_str,
        )
        for alloc in portfolio.get("allocations", [])
    ]

    cur.executemany(
        """
        INSERT INTO nav_portfolio_allocations
          (allocation_date, symbol, signal_type, size_pct, size_egp, at_risk_pct,
           sector, ues_score, entry, stop_loss, target1, rationale,
           regime_state, capital_aum, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_portfolio_report(portfolio: dict[str, Any], capital: float) -> None:
    """
    Human-readable Arabic/English mixed console report.
    """
    summary = portfolio.get("summary", {})
    allocations = portfolio.get("allocations", [])
    rejected = portfolio.get("rejected", [])
    warnings = portfolio.get("warnings", [])

    heat = summary.get("portfolio_heat", 0.0)
    heat_icon = "✅" if heat <= MAX_PORTFOLIO_HEAT else "⚠️"
    max_heat_pct = MAX_PORTFOLIO_HEAT * 100

    sep = "━" * 60

    print(f"\n🏗️  توصية المحفظة — {summary.get('date', date.today().isoformat())}")
    print(sep)
    print(
        f"  رأس المال: {capital:>12,.0f} EGP   "
        f"الـ Regime: {summary.get('regime_state', 'UNKNOWN')}"
    )
    print(
        f"  التعرض المقترح: {summary.get('target_exposure', 0)*100:.0f}%   "
        f"النقدية: {summary.get('cash_pct', 1)*100:.1f}%"
    )
    print(
        f"  الـ Heat (At-Risk): "
        f"{heat*100:.1f}% / {max_heat_pct:.0f}% max {heat_icon}"
    )

    if warnings:
        for w in warnings:
            print(f"  ⚠️  {w}")

    print(sep)
    if not allocations:
        print("  لا توجد مراكز مقترحة بناءً على القيود الحالية.")
    else:
        print(
            f"  {'#':<3} {'SYMBOL':<8} {'النوع':<15} "
            f"{'الحجم%':>7} {'EGP':>10} {'Risk%':>6} "
            f"{'UES':>5} {'ADV(M)':>7}"
        )
        print("  " + "-" * 58)
        for i, a in enumerate(allocations, 1):
            adv_m = (
                f"{a['adv20_egp']/1_000_000:.1f}"
                if a.get("adv20_egp")
                else "  N/A"
            )
            print(
                f"  {i:<3} {a['symbol']:<8} {a.get('signal_type',''):<15} "
                f"{a['size_pct']*100:>6.1f}% "
                f"{a['size_egp']:>10,.0f} "
                f"{a['at_risk_pct']*100:>5.1f}% "
                f"{a.get('ues_score',0):>5.1f} "
                f"{adv_m:>7}"
            )

    # Sector breakdown
    sector_bd = summary.get("sector_breakdown", {})
    if sector_bd:
        print(sep)
        print("  توزيع القطاعات:")
        parts = [f"{sec}: {pct*100:.1f}%" for sec, pct in sector_bd.items()]
        # wrap in rows of 4
        for chunk_start in range(0, len(parts), 4):
            print("    " + "   ".join(parts[chunk_start : chunk_start + 4]))

    # Rejected
    if rejected:
        print(sep)
        print("  ⛔ مرفوض (Hard Gates):")
        for r in rejected:
            print(f"    {r.get('symbol','?')}: {r.get('reason','?')}")

    print(sep + "\n")


# ---------------------------------------------------------------------------
# Capacity analysis helper
# ---------------------------------------------------------------------------

def _capacity_analysis(db_path: Path, symbol: str) -> None:
    """Print capacity/liquidity details for a single symbol."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print(f"\n📊 Capacity Analysis — {symbol}")
    print("━" * 50)

    # From symbol_liquidity_profile
    cur.execute(
        "SELECT * FROM symbol_liquidity_profile WHERE symbol = ?", (symbol,)
    )
    row = cur.fetchone()
    if row:
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
        print(f"  Sector:          {d.get('sector','N/A')}")
        adv = d.get("avg_daily_volume", 0) or 0
        print(f"  ADV (30d):       {adv:>14,.0f} EGP")
        print(f"  10% ADV cap:     {adv*0.10:>14,.0f} EGP")
        print(f"  Liquidity tier:  {d.get('liquidity_tier','N/A')}")
        print(f"  Max position:    {d.get('max_position_egp',0):>14,.0f} EGP")
    else:
        # Fallback to liquidity_profile
        cur.execute(
            "SELECT advt_30d, liquidity_tier FROM liquidity_profile WHERE symbol = ? ORDER BY computed_date DESC LIMIT 1",
            (symbol,),
        )
        row2 = cur.fetchone()
        if row2:
            adv = float(row2[0] or 0)
            print(f"  ADV (30d):       {adv:>14,.0f} EGP  (from liquidity_profile)")
            print(f"  10% ADV cap:     {adv*0.10:>14,.0f} EGP")
            print(f"  Liquidity tier:  {row2[1]}")
        else:
            print(f"  No liquidity data found for {symbol}")

    # Latest signal
    cur.execute(
        """SELECT signal_date, unified_score, conviction_tier, entry_price,
                  stop_loss, t1_target, behavioral_class
           FROM unified_signals WHERE symbol = ?
           ORDER BY signal_date DESC LIMIT 1""",
        (symbol,),
    )
    sig = cur.fetchone()
    if sig:
        print(f"\n  Latest signal ({sig[0]}):")
        print(f"    UES={sig[1]:.2f}  tier={sig[2]}  type={sig[6]}")
        if sig[3] and sig[4]:
            risk_pct = abs(sig[3] - sig[4]) / sig[3]
            print(
                f"    Entry={sig[3]}  SL={sig[4]}  T1={sig[5]}  "
                f"TradeRisk={risk_pct:.1%}"
            )

    conn.close()
    print("━" * 50 + "\n")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EGX Navigator — Portfolio Construction Engine"
    )
    sub = parser.add_subparsers(dest="command")

    # build subcommand
    build_p = sub.add_parser("build", help="Build today's portfolio")
    build_p.add_argument(
        "--capital",
        type=float,
        default=DEFAULT_CAPITAL,
        help=f"Portfolio capital in EGP (default {DEFAULT_CAPITAL:,})",
    )
    build_p.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output raw JSON instead of formatted report",
    )
    build_p.add_argument(
        "--date",
        type=str,
        default=None,
        help="Signal date override (YYYY-MM-DD, default: today)",
    )
    build_p.add_argument(
        "--db",
        type=str,
        default=str(DB_PATH),
        help=f"Path to SQLite DB (default: {DB_PATH})",
    )
    build_p.add_argument(
        "--drawdown-mult",
        type=float,
        default=1.0,
        help="Drawdown multiplier [0-1] from risk engine (default 1.0)",
    )

    # capacity subcommand
    cap_p = sub.add_parser("capacity", help="Capacity analysis for a symbol")
    cap_p.add_argument("--symbol", type=str, required=True, help="Ticker symbol")
    cap_p.add_argument(
        "--db",
        type=str,
        default=str(DB_PATH),
        help=f"Path to SQLite DB (default: {DB_PATH})",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.command is None:
        print("Usage:  python3 portfolio_engine.py build [--capital N] [--json] [--date YYYY-MM-DD]")
        print("        python3 portfolio_engine.py capacity --symbol OCPH")
        sys.exit(0)

    if args.command == "capacity":
        _capacity_analysis(Path(args.db), args.symbol.upper())
        return

    if args.command == "build":
        db = Path(args.db)
        capital = float(args.capital)
        date_str = args.date
        drawdown_mult = float(args.drawdown_mult)

        # Load signals
        signals = load_today_signals(db, date_str)
        target_date = date_str or date.today().isoformat()

        if not signals:
            print(
                f"[EGX Navigator] لا توجد إشارات بتاريخ {target_date} "
                f"(quality_gate_passed=1). لا يمكن بناء المحفظة."
            )
            sys.exit(0)

        # Determine regime
        conn = sqlite3.connect(db)
        engine = PortfolioEngine()
        regime_state = engine._determine_regime_state(conn)
        conn.close()

        # Construct
        portfolio = engine.construct_portfolio(
            signals=signals,
            regime_state=regime_state,
            portfolio_capital=capital,
            drawdown_multiplier=drawdown_mult,
        )

        # Persist
        _save_portfolio_to_db(db, portfolio, target_date, regime_state, capital)

        # Output
        if args.output_json:
            print(json.dumps(portfolio, ensure_ascii=False, indent=2))
        else:
            print_portfolio_report(portfolio, capital)


if __name__ == "__main__":
    main()
