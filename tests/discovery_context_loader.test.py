#!/usr/bin/env python3
"""Smoke tests for discovery_context_loader.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "python"))
from discovery_context_loader import load_discovery_params, read_pending_directives


def test_load_discovery_params_shape():
    p = load_discovery_params()
    assert isinstance(p, dict)
    assert "feedback_queue" in p
    assert "p6_directives" in p
    assert "strict_quality" in p
    assert isinstance(p["feedback_queue"], list)


def test_pending_directives_list():
    rows = read_pending_directives(3)
    assert isinstance(rows, list)


if __name__ == "__main__":
    test_load_discovery_params_shape()
    test_pending_directives_list()
    print("discovery_context_loader_ok")
