"""Lightweight structured run logger — JSONL, no heavy deps."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAILY = ROOT / "logs" / "daily"
ERRORS = ROOT / "logs" / "errors"


def _ensure_dirs() -> None:
    for d in (DAILY, ERRORS, ROOT / "logs" / "engines", ROOT / "logs" / "telegram"):
        d.mkdir(parents=True, exist_ok=True)


def log_run(
    command: str,
    *,
    layer: str = "general",
    status: str = "ok",
    ms: int = 0,
    error: str | None = None,
    meta: dict | None = None,
) -> dict:
    _ensure_dirs()
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "layer": layer,
        "status": status,
        "ms": ms,
        "error": (error or "")[:500] or None,
        **(meta or {}),
    }
    day = row["at"][:10]
    path = ERRORS / f"{layer}_{day}.jsonl" if status == "error" else DAILY / f"runs_{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


def log_error(layer: str, err: BaseException, impact: str = "") -> dict:
    return log_run(
        command=layer,
        layer=layer,
        status="error",
        error=str(getattr(err, "__traceback__", err)) if False else f"{type(err).__name__}: {err}",
        meta={"impact": impact, "next_action": "egx:health && logs/errors"},
    )
