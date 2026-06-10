#!/bin/bash
# Launch TradingView with CDP on macOS
# Usage: ./scripts/launch_tv_debug_mac.sh [port]
#
# NOTE: Newer TradingView Desktop rejects --remote-debugging-port on the binary
# directly ("bad option"). Use open -a --args, or Chrome fallback via TV_CDP_BROWSER=chrome.

PORT="${1:-9222}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "═══ TradingView CDP Launch (macOS) ═══"

# Method 1: open -a --args (recommended for Desktop on macOS)
if [ -d "/Applications/TradingView.app" ]; then
  echo "Trying Desktop via open -a --args (port $PORT)..."
  pkill -f "TradingView.app/Contents/MacOS/TradingView$" 2>/dev/null
  sleep 1
  open -a "/Applications/TradingView.app" --args "--remote-debugging-port=$PORT"
  for i in $(seq 1 20); do
    if curl -sf "http://localhost:$PORT/json/version" > /dev/null 2>&1; then
      echo "✅ Desktop CDP ready at http://localhost:$PORT"
      curl -s "http://localhost:$PORT/json/version" | python3 -m json.tool 2>/dev/null
      exit 0
    fi
    sleep 1
  done
  echo "⚠️  Desktop CDP not responding (TV may reject debug port on this version)"
fi

# Method 2: Chrome fallback (works on all macOS versions)
echo ""
echo "Falling back to Chrome CDP (TV_CDP_BROWSER=chrome)..."
export TV_CDP_BROWSER=chrome
export TV_CDP_PORT="$PORT"
node -e "
import { launch, healthCheck } from './src/core/health.js';
const r = await launch({ port: $PORT, browser: 'chrome', kill_existing: false });
console.log(JSON.stringify(r, null, 2));
if (r.cdp_ready !== false) {
  const h = await healthCheck();
  console.log('Chart:', h.chart_symbol, '@', h.chart_resolution);
}
" 2>&1

if curl -sf "http://localhost:$PORT/json/version" > /dev/null 2>&1; then
  echo "✅ Chrome CDP ready at http://localhost:$PORT"
  exit 0
fi

echo "❌ CDP failed. Install Google Chrome or launch TradingView manually."
echo "   npm run tv:launch"
exit 1
