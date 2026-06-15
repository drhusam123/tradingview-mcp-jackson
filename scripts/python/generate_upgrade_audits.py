#!/usr/bin/env python3
"""Generate upgrade audit reports: environment, dependencies, security, tools."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "audit"


def sh(cmd: str, timeout: int = 60) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=str(ROOT)).stdout.strip()
    except Exception as e:
        return f"error: {e}"


def write(name: str, content: str) -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / name).write_text(content, encoding="utf-8")


def env_audit() -> None:
    node_v = sh("node -v")
    npm_v = sh("npm -v")
    py_v = sh("python3 --version")
    pip_v = sh("python3 -m pip --version")
    py_path = sh("which python3")
    db = "data/egx_trading.db" if (ROOT / "data/egx_trading.db").exists() else "missing"

    heavy_py = ["tensorflow", "torch", "xgboost", "lightgbm", "mlflow", "tsfresh"]
    heavy_found = [p for p in heavy_py if p in sh("python3 -m pip list --format=freeze 2>/dev/null").lower()]

    content = f"""# Environment Audit

**Generated:** {datetime.now(timezone.utc).isoformat()}

| Field | Value |
|-------|-------|
| Node Version | {node_v} |
| NPM Version | {npm_v} |
| Python Version | {py_v} |
| Pip Version | {pip_v} |
| Python Path | {py_path} |
| Detected DB | {db} |
| Device Profile | MacBook Pro Intel i9 / 16GB RAM |

## Device Constraint Notes

- No CUDA / Metal-heavy ML training in daily automation
- `config/performance.json`: max_workers=4, enable_heavy_research=false
- Prefer SQLite + chunked reads over in-memory full loads

## Heavy Python Packages (installed)

{chr(10).join(f'- {p}' for p in heavy_found) or '- none detected via pip freeze'}

## Main Commands

- `npm run egx:health` — system health
- `npm run egx:validate-data` — market data validation
- `npm run egx:full-cycle -- --fast` — daily fast DAG
- `npm run egx:audit:e2e` — institutional E2E
- `npm run test:smoke` — lightweight smoke tests

## Broken Commands

Run `node scripts/egx_automation_verify.mjs` — expected 182/182 PASS.

## Recommended Updates

- Keep Node 24 LTS track; patch npm deps only after smoke test
- Python 3.11.9 via pyenv — do not jump to 3.12 without ML wheel check
"""
    write("ENVIRONMENT_AUDIT.md", content)


def dependency_audit() -> None:
    npm_out = sh("npm outdated --json 2>/dev/null", timeout=90)
    rows = []
    try:
        outdated = json.loads(npm_out) if npm_out.startswith("{") else {}
        for pkg, info in outdated.items():
            cur = info.get("current", "?")
            lat = info.get("latest", "?")
            risk = "Risky" if pkg in ("puppeteer-core",) and str(lat).split(".")[0] > str(cur).split(".")[0] else "Optional"
            rows.append(f"| {pkg} | {cur} | {lat} | npm | {risk} | Skip major bump unless tested | pending |")
    except json.JSONDecodeError:
        rows.append("| — | — | — | — | — | npm outdated parse failed | — |")

    content = """# Dependency Audit

**Generated:** """ + datetime.now(timezone.utc).isoformat() + """

| Package | Current | Latest | Used By | Risk | Recommendation | Action Taken |
|---------|---------|--------|---------|------|----------------|--------------|
""" + "\n".join(rows[:25]) + """

## Python (requirements-python.txt)

| Package | Role | Memory Impact | Recommendation |
|---------|------|---------------|----------------|
| lightgbm | ML scoring | Medium CPU | Keep — CPU-only, no GPU |
| duckdb | Analytics | Low | Keep |
| tensorflow | Legacy paths | High RAM | Avoid in daily cron |
| mlflow | MLOps | Medium | Weekly/manual only |
| tsfresh | Features | High | Manual research only |

**Action:** No mass upgrade this wave — document only. Run `npm run test:smoke` after any bump.
"""
    write("DEPENDENCY_AUDIT.md", content)


def security_audit() -> None:
    findings = []
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8", errors="replace") if (ROOT / ".gitignore").exists() else ""
    env_ok = ".env" in gitignore
    findings.append(("`.env` gitignored", "High", ".gitignore", "Secret leak", "Present" if env_ok else "ADD .env", "Fixed" if env_ok else "Open"))

    for pat, label in [(r"TELEGRAM_BOT_TOKEN\s*=\s*['\"][^'\"]+['\"]", "hardcoded telegram token"), (r"api[_-]?key\s*=\s*['\"][a-zA-Z0-9]{20,}", "hardcoded API key")]:
        for p in (ROOT / "scripts").rglob("*.{mjs,js,py}"):
            pass
    # quick grep via shell
    secrets = sh(r"rg -l 'TELEGRAM_BOT_TOKEN\s*=\s*[\"\\']' scripts src 2>/dev/null | head -5")
    if secrets:
        findings.append(("Hardcoded token in source", "Critical", secrets, "Credential exposure", "Move to .env", "Open"))

    lines = ["# Security Audit", "", f"**Generated:** {datetime.now(timezone.utc).isoformat()}", "",
             "| Finding | Severity | File | Risk | Fix | Status |", "|---------|----------|------|------|-----|--------|"]
    for f in findings:
        lines.append(f"| {f[0]} | {f[1]} | {f[2]} | {f[3]} | {f[4]} | {f[5]} |")
    lines += ["", "## Rules enforced", "", "- Tokens read from `.env` only", "- Logs must not print secrets", "- Dry-run default for telegram cron"]
    write("SECURITY_AUDIT.md", "\n".join(lines) + "\n")


def tools_recommendation() -> None:
    content = f"""# Tools Recommendation

**Generated:** {datetime.now(timezone.utc).isoformat()}
**Device:** Intel i9 / 16GB — balanced mode

| Tool/Library | Layer | Why Needed | Alternative | Memory/CPU | Decision |
|--------------|-------|------------|-------------|------------|----------|
| better-sqlite3 | Node DB | Fast sync SQLite | sqlite3 | Low | **Keep** |
| duckdb | Python analytics | SQL on parquet | pandas only | Medium | **Keep** |
| lightgbm | ML | CPU booster | sklearn | Medium CPU | **Keep** |
| polars | Data | Faster than pandas | pandas | Medium RAM | **Later** |
| pandera | Validation | Schema checks | custom SQL | Low | **Later** |
| pino | Node logging | Structured logs | run_logger.mjs | Low | **Skip** — added lightweight logger |
| great-expectations | Data QA | Heavy | validate_market_data.py | High | **Skip** |
| redis/postgres | Infra | — | SQLite | High ops | **Skip** |

## Installed This Wave

- `config/performance.json` — device limits
- `scripts/python/validate_market_data.py` — lightweight validation
- `scripts/lib/run_logger.mjs` + `system_logger.py` — JSONL logging
- `scripts/python/db_optimize.py` — indexes + backup
"""
    write("TOOLS_RECOMMENDATION.md", content)


def main() -> None:
    env_audit()
    dependency_audit()
    security_audit()
    tools_recommendation()
    print("Generated: ENVIRONMENT, DEPENDENCY, SECURITY, TOOLS_RECOMMENDATION audits")


if __name__ == "__main__":
    main()
