#!/usr/bin/env python3
"""
MDE Behavioral Memory — Phase 2.6 symbol-level learning (optional engine hook).

Loaded from mde_symbol_behavior_profiles.json when EGX_MDE_BEHAVIOR_MEMORY=1.
Additive only: adjusts confidence, never vetoes or blocks legacy signals.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PROFILES_PATH = DATA / "mde_symbol_behavior_profiles.json"
RULES_PATH = DATA / "mde_behavior_rules.json"

NOISY_FAMILIES = {"G", "G_noisy", "F", "F_false_weak"}


@lru_cache(maxsize=1)
def load_profiles() -> Dict[str, dict]:
    if not PROFILES_PATH.exists():
        return {}
    try:
        doc = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        return doc.get("profiles") or doc.get("symbols") or {}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def load_rules() -> List[dict]:
    if not RULES_PATH.exists():
        return []
    try:
        doc = json.loads(RULES_PATH.read_text(encoding="utf-8"))
        return doc.get("rules") or []
    except Exception:
        return []


def behavior_memory_enabled() -> bool:
    return os.environ.get("EGX_MDE_BEHAVIOR_MEMORY", "0") == "1"


def apply_confidence_adjustment(
    symbol: str,
    setups: List[str],
    confidence: float,
    hidden_repricing: bool = False,
) -> tuple[float, List[str]]:
    """
    Returns (adjusted_confidence, notes).
    Default passthrough when memory disabled or profile missing.
    """
    if not behavior_memory_enabled():
        return confidence, []

    prof = load_profiles().get(symbol)
    if not prof:
        return confidence, []

    notes: List[str] = []
    adj = confidence
    family = (prof.get("sector_behavior_family") or prof.get("behavior_family") or "").upper()

    if family.startswith("G") or family in NOISY_FAMILIES:
        adj -= 8
        notes.append("behavior_family_noisy:-8")

    best = prof.get("best_historical_setup") or prof.get("best_setup_for_symbol")
    worst = prof.get("worst_historical_setup") or prof.get("worst_setup_for_symbol")
    reliability = float(prof.get("setup_reliability") or prof.get("hit_rate_5d") or 0)

    if best and best in setups:
        boost = min(12, 4 + reliability * 20)
        adj += boost
        notes.append(f"historical_best_setup_{best}:+{boost:.0f}")

    if worst and worst in setups:
        adj -= 6
        notes.append(f"historical_worst_setup_{worst}:-6")

    mem_adj = float(prof.get("confidence_adjustment") or 0)
    if mem_adj:
        adj += mem_adj
        notes.append(f"profile_confidence_adj:{mem_adj:+.1f}")

    if hidden_repricing and int(prof.get("hidden_repricing_events") or 0) >= 5:
        hr_hit = float(prof.get("hit_rate_5d") or 0)
        if hr_hit >= 0.30:
            adj += 5
            notes.append("hr_history_strong:+5")
        elif hr_hit < 0.15:
            adj -= 5
            notes.append("hr_history_weak:-5")

    adj = max(20.0, min(100.0, adj))
    return round(adj, 2), notes


def clear_cache() -> None:
    load_profiles.cache_clear()
    load_rules.cache_clear()
