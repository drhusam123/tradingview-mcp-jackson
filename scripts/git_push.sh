#!/usr/bin/env bash
# Push to drhusam123/tradingview-mcp-jackson (your repo)
set -euo pipefail
cd "$(dirname "$0")/.."

REMOTE="${EGX_GIT_REMOTE:-drhusam}"
BRANCH="${EGX_GIT_BRANCH:-main}"

echo "═══ Git Push → ${REMOTE}/${BRANCH} ═══"
git status -sb | head -1

if ! git remote get-url "$REMOTE" &>/dev/null; then
  echo "❌ Remote '$REMOTE' not found. Run: git remote -v"
  exit 1
fi

if ! gh auth status -h github.com &>/dev/null; then
  echo ""
  echo "❌ Not logged into GitHub CLI."
  echo "   Run:  gh auth login   (account: drhusam123)"
  echo "   Then: git push ${REMOTE} ${BRANCH}"
  echo ""
  echo "   See: docs/GIT_PUSH.md"
  exit 1
fi

echo "GitHub: $(gh auth status -h github.com 2>&1 | head -1)"
echo "Remote: $(git remote get-url "$REMOTE")"
git push "$REMOTE" "$BRANCH"
echo "✅ Push complete"
