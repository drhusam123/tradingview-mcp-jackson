"""
telegram_send_cards.py — Send EGX visual cards to Telegram.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
Sends cards as a media group (album) when possible, falls back to
individual photos, then falls back to text via the existing notify.js.

Usage:
  python3 telegram_send_cards.py [YYYY-MM-DD]
"""

import os
import sys
import json
import subprocess
import tempfile
import time
from typing import Optional, List, Dict

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


# ─── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
NOTIFY_JS    = os.path.join(PROJECT_ROOT, "src", "egx", "notify.js")


# ─── Telegram API helpers ──────────────────────────────────────────────────────

def _tg_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def send_photo(chat_id: str, photo_bytes: bytes, caption: str = "") -> bool:
    """Send a single photo via Telegram Bot API."""
    if not REQUESTS_OK or not BOT_TOKEN:
        return False
    try:
        resp = requests.post(
            _tg_url("sendPhoto"),
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("card.png", photo_bytes, "image/png")},
            timeout=30,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[send_photo] Error: {e}")
        return False


def send_media_group(chat_id: str, photos: List[bytes],
                     captions: List[str] = None) -> bool:
    """Send up to 10 photos as a Telegram media group (album)."""
    if not REQUESTS_OK or not BOT_TOKEN or not photos:
        return False

    captions = captions or [""] * len(photos)
    media = []
    files = {}

    for i, (photo, cap) in enumerate(zip(photos, captions)):
        field = f"photo{i}"
        files[field] = (f"card{i}.png", photo, "image/png")
        item = {"type": "photo", "media": f"attach://{field}"}
        if cap:
            item["caption"] = cap[:1024]  # Telegram caption limit
            item["parse_mode"] = "HTML"
        media.append(item)

    try:
        resp = requests.post(
            _tg_url("sendMediaGroup"),
            data={"chat_id": chat_id, "media": json.dumps(media)},
            files=files,
            timeout=60,
        )
        if resp.status_code != 200:
            print(f"[send_media_group] Error {resp.status_code}: {resp.text[:300]}")
            return False
        return True
    except Exception as e:
        print(f"[send_media_group] Error: {e}")
        return False


def send_text_via_notify(message: str) -> bool:
    """Fallback: send text via existing notify.js infrastructure."""
    try:
        result = subprocess.run(
            ["node", "-e", f"""
const {{ sendTelegram }} = require('{NOTIFY_JS}');
sendTelegram({json.dumps(message)}).then(() => process.exit(0)).catch(e => {{ console.error(e); process.exit(1); }});
"""],
            capture_output=True, text=True, timeout=30,
            cwd=PROJECT_ROOT
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[send_text_via_notify] Error: {e}")
        return False


# ─── Caption builders ──────────────────────────────────────────────────────────

def _market_caption(summary: Dict) -> str:
    regime = summary.get("regime", "?")
    ret20  = summary.get("market_return_20d", 0)
    gp     = summary.get("gate_passed", 0)
    regime_emoji = {"BULL": "🟢", "BEAR": "🔴", "CHOPPY": "🟡"}.get(regime, "⚪")
    if gp > 0:
        return f"{regime_emoji} السوق <b>{regime}</b> | عائد 20 يوم: <b>{ret20:+.1f}%</b>\n✅ <b>{gp} فرص</b> مؤهلة اليوم"
    else:
        return f"{regime_emoji} السوق <b>{regime}</b> | عائد 20 يوم: <b>{ret20:+.1f}%</b>\n⏸ لا توصيات اليوم"


def _stock_caption(sc: Dict, summary: Dict) -> str:
    sym      = sc.get("symbol", "?")
    sig_type = sc.get("type", "SWING")
    emoji = {"SCALP": "⚡", "SWING": "🔄", "INVESTMENT": "💎", "BEAR_EXCEPTION": "🎯"}.get(sig_type, "📊")
    label = {"SCALP": "اسكالبينج", "SWING": "سوينج", "INVESTMENT": "استثمار",
             "BEAR_EXCEPTION": "فرصة استثنائية"}.get(sig_type, sig_type)
    return f"{emoji} <b>{sym}</b> — {label}"


def _watchlist_caption(summary: Dict) -> str:
    return "👀 <b>قائمة المراقبة</b> — انتظر إشارة واضحة قبل الدخول"


# ─── Main send function ────────────────────────────────────────────────────────

def send_daily_cards(report_date: str = None, dry_run: bool = False) -> bool:
    """
    Generate and send all cards for the day.
    Returns True if at least the market card was sent.
    """
    # Import here to avoid circular imports
    sys.path.insert(0, os.path.dirname(__file__))
    from telegram_card_generator import generate_daily_cards, save_card

    print(f"[send_cards] Generating cards{' (DRY RUN)' if dry_run else ''}...")
    cards = generate_daily_cards(report_date)

    if cards["fallback_mode"]:
        print("[send_cards] Pillow not available — falling back to text mode")
        summary = cards.get("summary", {})
        msg = _build_text_fallback(summary)
        if not dry_run:
            return send_text_via_notify(msg)
        else:
            print(f"[DRY RUN] Text message:\n{msg}")
            return True

    summary = cards.get("summary", {})
    print(f"[send_cards] Regime={summary.get('regime')} GP={summary.get('gate_passed')}")

    # Collect all photos with captions
    all_photos = []
    all_captions = []

    if cards["market_card"]:
        all_photos.append(cards["market_card"])
        all_captions.append(_market_caption(summary))

    for sc in cards["stock_cards"]:
        all_photos.append(sc["bytes"])
        all_captions.append(_stock_caption(sc, summary))

    if cards["watchlist_card"] and not cards["stock_cards"]:
        all_photos.append(cards["watchlist_card"])
        all_captions.append(_watchlist_caption(summary))

    if not all_photos:
        print("[send_cards] No cards generated — skipping send")
        return False

    # Save cards locally for audit
    os.makedirs(os.path.join(PROJECT_ROOT, "data", "cards"), exist_ok=True)
    if cards["market_card"]:
        save_card(cards["market_card"], f"market_{report_date or 'today'}")
    for sc in cards["stock_cards"]:
        save_card(sc["bytes"], f"stock_{sc['symbol']}_{report_date or 'today'}")
    if cards["watchlist_card"]:
        save_card(cards["watchlist_card"], f"watchlist_{report_date or 'today'}")

    print(f"[send_cards] Sending {len(all_photos)} card(s)...")

    if dry_run:
        print(f"[DRY RUN] Would send {len(all_photos)} cards to chat {CHAT_ID}")
        for i, (photo, cap) in enumerate(zip(all_photos, all_captions)):
            print(f"  Card {i+1}: {len(photo):,} bytes — {cap[:60]}")
        return True

    if not BOT_TOKEN or not CHAT_ID:
        print("[send_cards] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set!")
        return False

    # Try media group first (up to 10 photos)
    if len(all_photos) > 1:
        success = send_media_group(CHAT_ID, all_photos[:10], all_captions[:10])
        if success:
            print(f"[send_cards] ✅ Sent album of {len(all_photos)} cards")
            return True

    # Fallback: send individually
    sent = 0
    for photo, cap in zip(all_photos, all_captions):
        ok = send_photo(CHAT_ID, photo, cap)
        if ok:
            sent += 1
        time.sleep(0.5)  # Rate limit

    print(f"[send_cards] Sent {sent}/{len(all_photos)} cards individually")
    return sent > 0


def _build_text_fallback(summary: Dict) -> str:
    """Minimal text message when image generation fails."""
    regime = summary.get("regime", "UNKNOWN")
    ret20  = summary.get("market_return_20d", 0)
    gp     = summary.get("gate_passed", 0)
    rdate  = summary.get("report_date", "")
    emoji  = {"BULL": "🟢", "BEAR": "🔴", "CHOPPY": "🟡"}.get(regime, "⚪")

    lines = [
        f"📊 <b>EGX AI — تقرير {rdate}</b>",
        f"{emoji} السوق: <b>{regime}</b>",
        f"📈 عائد 20 يوم: <b>{ret20:+.1f}%</b>",
    ]
    if gp > 0:
        lines.append(f"✅ <b>{gp} فرص مؤهلة</b>")
    else:
        lines.append("⏸ <b>لا توصيات اليوم</b>")
    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    date_arg = None
    dry_run  = False

    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg.startswith("20"):
            date_arg = arg

    success = send_daily_cards(date_arg, dry_run=dry_run)
    sys.exit(0 if success else 1)
