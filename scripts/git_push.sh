#!/usr/bin/env bash
# Push to LewisWJackson/tradingview-mcp-jackson (requires gh auth as LewisWJackson)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "═══ Git Push ═══"
git status -sb | head -1

if ! gh auth status -h github.com &>/dev/null; then
  echo ""
  echo "❌ Not logged into GitHub CLI."
  echo "   Run:  gh auth login"
  echo "   Then: git push origin main"
  echo ""
  echo "   See: docs/GIT_PUSH.md"
  exit 1
fi

echo "GitHub: $(gh auth status -h github.com 2>&1 | head -1)"
git push origin main
echo "✅ Push complete"
