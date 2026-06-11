#!/usr/bin/env bash
# EGX — paste-ready manual steps (only what cannot run unattended).
# Usage: bash scripts/manual_finish.sh [push|live|telegram|all]
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

section() { echo ""; echo "════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════"; }

case "${1:-all}" in
  push)
    section "1) GitHub push (drhusam123 — اختياري، نسخة احتياطية)"
    echo "Run these in order:"
    cat <<'EOF'
gh auth status
cd /Users/dr.husam/tradingview-mcp-jackson
git push drhusam main
# أو: npm run egx:git:push
EOF
    ;;
  ml)
    section "2) ML deps (one-time macOS)"
    cat <<'EOF'
brew install libomp
pip3 install scikit-learn lifelines lightgbm pandas
EOF
    ;;
  live)
    section "3) Live E2E (TradingView CDP on :9222)"
    cat <<'EOF'
cd /Users/dr.husam/tradingview-mcp-jackson
# Start TV with CDP first, then:
npm run tv:smoke
npm run test:live
EOF
    ;;
  telegram)
    section "4) Client Telegram (live — your decision)"
    cat <<'EOF'
cd /Users/dr.husam/tradingview-mcp-jackson
npm run egx:prod:prepare-send
npm run egx:telegram:cron
npm run egx:post:session
EOF
    ;;
  all|*)
    bash "$0" push
    bash "$0" ml
    bash "$0" live
    bash "$0" telegram
    section "5) Full stack verify (after push)"
    cat <<'EOF'
cd /Users/dr.husam/tradingview-mcp-jackson
npm run egx:e2e:complete
npm run egx:go:live
EOF
    ;;
esac
