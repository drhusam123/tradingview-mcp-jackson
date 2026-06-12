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
DB_PATH      = os.path.join(PROJECT_ROOT, "data", "egx_trading.db")


def _latest_ohlcv_date() -> Optional[str]:
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT MAX(date(bar_time, 'unixepoch')) AS latest FROM ohlcv_history_execution"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _cards_safe_for_client(summary: Dict, requested_report_date: str) -> List[str]:
    """Return blocking reasons. Empty list means visual cards may be sent."""
    reasons = []
    if not summary:
        reasons.append("missing card summary")
    latest = _latest_ohlcv_date()
    signal_date = summary.get("signal_date")
    signal_source = summary.get("signal_source")
    radar_date = None
    try:
        radar = summary.get("radar_top") or []
        # radar_top may be symbols only; generator date is stored on the radar card itself.
        radar_date = summary.get("radar_date")
    except Exception:
        radar_date = None

    if latest and latest < requested_report_date:
        reasons.append(f"trusted OHLCV is stale: {latest} < {requested_report_date}")
    if signal_source and signal_source != "final_signals":
        reasons.append(f"client opportunities must come from final_signals, got {signal_source}")
    if signal_date and signal_date != requested_report_date:
        reasons.append(f"signal cards are stale: signal_date={signal_date}, report_date={requested_report_date}")
    if radar_date and radar_date != requested_report_date:
        reasons.append(f"radar card is stale: radar_date={radar_date}, report_date={requested_report_date}")
    if summary.get("no_actionable_guard"):
        reasons.append(
            "visual client cards require same-date final_signals actionable=1; "
            "no market/stock/watchlist/radar cards may be sent"
        )
    if str(summary).find("undefined") >= 0 or str(summary).find("None @") >= 0:
        reasons.append("summary contains debug/undefined content")
    return reasons


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


def send_text_via_notify(message: str, report_date: str = "") -> bool:
    """Fallback: send text via existing notify.js infrastructure."""
    try:
        result = subprocess.run(
            ["node", "--input-type=module", "-e", f"""
import {{ sendTelegram }} from {json.dumps(NOTIFY_JS)};
sendTelegram({json.dumps(message)}, {{ parseMode: 'HTML', clientDelivery: true, reportDate: {json.dumps(report_date)} }})
  .then((r) => process.exit(r?.ok ? 0 : 1))
  .catch(e => {{ console.error(e); process.exit(1); }});
"""],
            capture_output=True, text=True, timeout=30,
            cwd=PROJECT_ROOT
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[send_text_via_notify] Error: {e}")
        return False


# ─── Arabic label helpers ──────────────────────────────────────────────────────

_REGIME_AR = {
    "BULL":    "ثور (صعودي)",
    "BEAR":    "دب (هابط)",
    "CHOPPY":  "جانبي",
    "UNKNOWN": "غير محدد",
}
_POSTURE_AR = {
    "AGGRESSIVE": ("⚔️", "عدواني"),
    "CAUTIOUS":   ("🛡",  "حذر"),
    "DEFENSIVE":  ("🔒", "دفاعي"),
}
_REGIME_EMOJI = {"BULL": "🟢", "BEAR": "🔴", "CHOPPY": "🟡"}


# ─── Caption builders ──────────────────────────────────────────────────────────

def _market_caption(summary: Dict) -> str:
    """
    Market status card caption.
    Always uses report_date (today) as display date.
    Client opportunity cards are same-date final_signals only.
    Explains BEAR_REGIME_FILTER when gate_passed == 0 and BEAR regime.
    Shows holiday greeting when EGX is closed.
    """
    regime      = summary.get("regime", "?")
    ret20       = summary.get("market_return_20d", 0) or 0
    ret5        = summary.get("market_return_5d", 0) or 0
    gp          = summary.get("gate_passed", 0) or 0
    breadth     = summary.get("breadth_pct", 0) or 0
    posture     = summary.get("posture", "CAUTIOUS")
    radar       = summary.get("radar_top", [])
    report_date = summary.get("report_date", "")
    signal_date = summary.get("signal_date", report_date)
    is_trading  = summary.get("is_trading_day", True)
    holiday     = summary.get("holiday_name")         # None when market open

    regime_emoji              = _REGIME_EMOJI.get(regime, "⚪")
    posture_emoji, posture_ar = _POSTURE_AR.get(posture, ("🛡", "حذر"))
    regime_ar                 = _REGIME_AR.get(regime, regime)

    lines = [f"📊 <b>نظام EGX الذكي — {report_date}</b>"]

    # ── Holiday / weekend banner ──────────────────────────────────────────
    if holiday:
        lines.append(f"🎉 <b>{holiday}</b> — السوق مغلق اليوم")
        lines.append(f"{regime_emoji} آخر جلسة: {signal_date}  |  {regime_ar}")
        lines.append(f"📈 عائد 20 يوم: <b>{ret20:+.1f}%</b>  |  5 أيام: <b>{ret5:+.1f}%</b>")
        if radar:
            radar_str = "  |  ".join(radar[:3])
            lines.append(f"🎯 رادار الانفجار: <code>{radar_str}</code>")
        lines.append("⏸ <b>لا تداول اليوم</b> — نتابعكم عند فتح السوق")
        lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
        return "\n".join(lines)

    if not is_trading:
        lines.append("📅 <b>إجازة أسبوعية</b> — السوق مغلق (جمعة / سبت)")
        lines.append(f"{regime_emoji} آخر جلسة: {signal_date}  |  {regime_ar}")
        if radar:
            radar_str = "  |  ".join(radar[:3])
            lines.append(f"🎯 رادار الانفجار: <code>{radar_str}</code>")
        lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
        return "\n".join(lines)

    # ── Normal trading day ────────────────────────────────────────────────
    lines += [
        f"{regime_emoji} السوق: <b>{regime_ar}</b>  {posture_emoji} <b>{posture_ar}</b>",
        f"📈 عائد 20 يوم: <b>{ret20:+.1f}%</b>  |  5 أيام: <b>{ret5:+.1f}%</b>",
        f"📡 اتساع السوق: <b>{breadth:.0f}%</b>",
    ]

    if gp > 0:
        if signal_date and signal_date != report_date:
            lines.append(f"✅ <b>{gp} فرص مؤهلة</b> — من تاريخ {signal_date}")
        else:
            lines.append(f"✅ <b>{gp} فرص مؤهلة</b> — راجع بطاقات الأسهم")
    else:
        if regime == "BEAR":
            lines.append("🔒 <b>لا توصيات</b> — جميع الإشارات محجوبة بسبب السوق الهابط")
        else:
            lines.append("⏸ <b>لا فرص تنفيذية اليوم</b> — لا توجد إشارة نهائية مؤكدة لنفس التاريخ")

    if radar:
        radar_str = "  |  ".join(radar[:3])
        lines.append(f"🎯 رادار الانفجار: <code>{radar_str}</code>")

    lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
    return "\n".join(lines)


def _stock_caption(sc: Dict, summary: Dict) -> str:
    """
    Stock signal card caption.
    Shows: symbol, signal type (Arabic), ML score with tier, explosion probability (≥30%).
    Uses signal_date from summary to show when the signal was generated.
    """
    sym      = sc.get("symbol", "?")
    sig_type = sc.get("type", "SWING")
    ml_score = sc.get("ml_score", 0) or 0
    ml_tier  = sc.get("ml_tier", "") or ""

    type_map = {
        "SCALP":          ("⚡", "اسكالبينج"),
        "SWING":          ("🔄", "تداول متأرجح"),
        "INVESTMENT":     ("💎", "استثمار متوسط المدى"),
        "BEAR_EXCEPTION": ("🎯", "فرصة استثنائية في الهبوط"),
    }
    emoji, label = type_map.get(sig_type, ("📊", sig_type))

    regime       = summary.get("regime", "?")
    regime_ar    = _REGIME_AR.get(regime, regime)
    regime_emoji = _REGIME_EMOJI.get(regime, "⚪")
    signal_date  = summary.get("signal_date", "") or summary.get("report_date", "")

    lines = [
        f"{emoji} <b>{sym}</b> — {label}",
        f"{regime_emoji} السوق: {regime_ar}",
    ]

    if signal_date:
        lines.append(f"📅 تاريخ الإشارة: <code>{signal_date}</code>")

    # ML unified score (primary gate-passed metric)
    if ml_score > 0:
        tier_ar = {"HIGH": "عالي", "MEDIUM": "متوسط", "LOW": "منخفض"}.get(ml_tier, ml_tier)
        tier_str = f" — {tier_ar}" if tier_ar else ""
        lines.append(f"🤖 نقاط الثقة: <b>{ml_score:.0f}%{tier_str}</b>")

    # Explosion probability from fixed ensemble (supplemental, shown only when ≥30%)
    expl_prob = sc.get("explosion_prob_ml")
    expl_tier = sc.get("explosion_tier_ml", "") or ""
    if expl_prob and expl_prob >= 30:
        expl_emoji = "🔥" if expl_prob >= 70 else "⚡" if expl_prob >= 50 else "📡"
        expl_tier_ar = {"HIGH": "عالي", "MEDIUM": "متوسط", "LOW": "منخفض"}.get(expl_tier, "")
        tier_note = f" ({expl_tier_ar})" if expl_tier_ar else ""
        lines.append(f"{expl_emoji} احتمال انفجار السعر: <b>{expl_prob:.0f}%{tier_note}</b>")

    lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
    return "\n".join(lines)


def _watchlist_caption(summary: Dict) -> str:
    """
    Watchlist card caption. The generator suppresses this for client output
    when no same-date actionable final signal exists.
    """
    regime       = summary.get("regime", "?")
    regime_ar    = _REGIME_AR.get(regime, regime)
    regime_emoji = _REGIME_EMOJI.get(regime, "⚪")
    report_date  = summary.get("report_date", "")

    lines = [
        f"👀 <b>قائمة المراقبة — {report_date}</b>",
        f"{regime_emoji} السوق {regime_ar}",
        "لم تُستوفَ شروط بوابة الجودة اليوم.",
        "هذه الأسهم لديها أعلى نقاط نظام لكنها لم تتجاوز الفلاتر بعد.",
        "راقبها للدخول عند تحقق إشارة واضحة.",
        "<i>للمعلومات فقط • ليس توصية استثمارية</i>",
    ]
    return "\n".join(lines)


def _radar_caption(summary: Dict, radar_picks: List = None) -> str:
    """
    Explosion radar card caption — top 5 ML picks with ensemble probabilities.
    These are supplemental ML predictions shown only when same-date actionable
    final signals passed the client guard.
    """
    radar        = radar_picks or summary.get("radar_top", [])
    regime       = summary.get("regime", "?")
    regime_ar    = _REGIME_AR.get(regime, regime)
    regime_emoji = _REGIME_EMOJI.get(regime, "⚪")
    report_date  = summary.get("report_date", "")

    lines = [
        f"🎯 <b>رادار الانفجار — {report_date}</b>",
        f"{regime_emoji} السوق: {regime_ar}",
        "أعلى 5 أسهم حسب احتمال حركة سعرية حادة (نموذج المجموعة المُعايَر):",
    ]
    if radar:
        for i, sym in enumerate(radar[:5], 1):
            lines.append(f"  <b>{i}.</b> <code>{sym}</code>")
    else:
        lines.append("  — لا بيانات متاحة لهذا اليوم —")

    lines.append("")
    lines.append("⚠️ <b>تنبيه:</b> هذه أسهم تحت المراقبة، لا توصيات تنفيذية.")
    lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
    return "\n".join(lines)


# ─── Deduplication guard ───────────────────────────────────────────────────────

_CARDS_LOG_DB = os.path.join(PROJECT_ROOT, "data", "egx_trading.db")

def _mark_cards_sent(report_date: str) -> None:
    """Record that visual cards were sent for report_date."""
    try:
        import sqlite3
        conn = sqlite3.connect(_CARDS_LOG_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_cards_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                sent_at TEXT DEFAULT (datetime('now')),
                n_cards INTEGER,
                source TEXT
            )
        """)
        conn.execute(
            "INSERT INTO telegram_cards_log (report_date, source) VALUES (?, ?)",
            (report_date, "send_daily_cards")
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _cards_sent_today(report_date: str) -> bool:
    """Return True if visual cards were already sent for report_date in the last 8 hours."""
    try:
        import sqlite3
        conn = sqlite3.connect(_CARDS_LOG_DB)
        row = conn.execute(
            """SELECT sent_at FROM telegram_cards_log
               WHERE report_date=? AND sent_at >= datetime('now', '-8 hours')
               ORDER BY id DESC LIMIT 1""",
            (report_date,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False  # If check fails, allow send


# ─── Main send function ────────────────────────────────────────────────────────

def send_daily_cards(report_date: str = None, dry_run: bool = False,
                     force: bool = False) -> bool:
    """
    Generate and send all cards for the day.
    Returns True if at least the market card was sent.

    force=True  → resend only after client QA passes.
    force=False → skip if already sent in the last 8 hours (dedup guard — BUG-04 fix)
    """
    if report_date is None:
        report_date = __import__('datetime').date.today().isoformat()

    # BUG-04 FIX: Prevent duplicate sends — egx_telegram_daily.mjs runs at 15:00 UTC
    # and calls telegram_send_cards.py; night_lab.py runs at 19:00+ UTC and also calls
    # send_daily_cards(). Without this guard, clients receive the same cards twice daily.
    if not dry_run and not force and _cards_sent_today(report_date):
        print(f"[send_cards] Cards already sent for {report_date} in last 8h — skipping "
              "(pass force=True to override)", flush=True)
        return True  # Report success so night_lab doesn't log an error

    # Import here to avoid circular imports
    sys.path.insert(0, os.path.dirname(__file__))
    from telegram_card_generator import generate_daily_cards, save_card

    print(f"[send_cards] Generating cards{' (DRY RUN)' if dry_run else ''}...")
    cards = generate_daily_cards(report_date)

    if cards["fallback_mode"]:
        print("[send_cards] Pillow not available — falling back to text mode")
        summary = cards.get("summary", {})
        blockers = _cards_safe_for_client(summary, report_date)
        if blockers and not dry_run:
            print("[send_cards] CLIENT QA BLOCKED fallback text:")
            for reason in blockers:
                print(f"  - {reason}")
            return False
        msg = _build_text_fallback(summary)
        if not dry_run:
            return send_text_via_notify(msg, report_date)
        else:
            print(f"[DRY RUN] Text message:\n{msg}")
            return True

    summary = cards.get("summary", {})
    print(f"[send_cards] Regime={summary.get('regime')} GP={summary.get('gate_passed')} "
          f"RadarTop={summary.get('radar_top', [])[:3]}")
    if summary.get("client_opportunity_guard"):
        print(f"[send_cards] Guard={summary.get('client_opportunity_guard')}")

    blockers = _cards_safe_for_client(summary, report_date)
    if blockers and not dry_run:
        print("[send_cards] CLIENT QA BLOCKED visual cards:")
        for reason in blockers:
            print(f"  - {reason}")
        return False
    if summary.get("no_actionable_guard"):
        print("[send_cards] No same-date final_signals actionable=1 — no client images generated")
        return True if dry_run else False

    # Collect all photos with captions
    all_photos = []
    all_captions = []
    no_actionable_guard = bool(summary.get("no_actionable_guard"))
    if no_actionable_guard:
        cards["stock_cards"] = []
        cards["watchlist_card"] = None
        cards["explosion_radar_card"] = None

    # 1. Market status card (always first)
    if cards["market_card"]:
        all_photos.append(cards["market_card"])
        all_captions.append(_market_caption(summary))

    # 2. Stock signal cards — skip on holidays/weekends (signals are stale)
    _is_trading = summary.get("is_trading_day", True)
    if _is_trading:
        for sc in cards["stock_cards"]:
            all_photos.append(sc["bytes"])
            all_captions.append(_stock_caption(sc, summary))

        # 3. Watchlist card (only when no stock signals — quiet market)
        if cards["watchlist_card"] and not cards["stock_cards"]:
            all_photos.append(cards["watchlist_card"])
            all_captions.append(_watchlist_caption(summary))
    else:
        print(f"[send_cards] 📅 Non-trading day ({summary.get('holiday_name','weekend')}) — "
              "skipping stock/watchlist cards")

    # 4. Explosion radar card (same-date actionable days only)
    if cards.get("explosion_radar_card"):
        all_photos.append(cards["explosion_radar_card"])
        radar_picks = summary.get("radar_top", [])
        all_captions.append(_radar_caption(summary, radar_picks))

    # 5. Portfolio card (added when there are open positions to track)
    try:
        import importlib.util as _ptilu, os as _ptos
        _pt_path = _ptos.path.join(_ptos.path.dirname(__file__), 'portfolio_tracker.py')
        _pt_spec = _ptilu.spec_from_file_location('portfolio_tracker', _pt_path)
        _pt_mod  = _ptilu.module_from_spec(_pt_spec)
        _pt_spec.loader.exec_module(_pt_mod)

        _pt_conn    = _pt_mod.get_db()
        _pt_summary = _pt_mod.get_portfolio_summary(_pt_conn)
        _pt_conn.close()

        if _pt_summary.get('n_open', 0) > 0:
            _pt_card_bytes = _pt_mod.build_portfolio_card(_pt_summary)
            if _pt_card_bytes:
                all_photos.append(_pt_card_bytes)
                all_captions.append(_pt_mod.portfolio_telegram_caption(_pt_summary))
                print(f"[send_cards] 💼 Portfolio card added: {_pt_summary['n_open']} open positions")
    except Exception as _pt_e:
        print(f"[send_cards] Portfolio card skipped: {_pt_e}")

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
    if cards.get("explosion_radar_card"):
        save_card(cards["explosion_radar_card"], f"radar_{report_date or 'today'}")
    if cards.get("portfolio_card"):
        save_card(cards["portfolio_card"], f"portfolio_{report_date or 'today'}")

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
            _mark_cards_sent(report_date)   # Record send for dedup guard
            return True

    # Fallback: send individually
    sent = 0
    for photo, cap in zip(all_photos, all_captions):
        ok = send_photo(CHAT_ID, photo, cap)
        if ok:
            sent += 1
        time.sleep(0.5)  # Rate limit

    if sent > 0:
        _mark_cards_sent(report_date)   # Record send for dedup guard
    print(f"[send_cards] Sent {sent}/{len(all_photos)} cards individually")
    return sent > 0


def _build_text_fallback(summary: Dict) -> str:
    """Text fallback when Pillow image generation is unavailable."""
    regime      = summary.get("regime", "UNKNOWN")
    regime_ar   = _REGIME_AR.get(regime, regime)
    ret20       = summary.get("market_return_20d", 0) or 0
    ret5        = summary.get("market_return_5d", 0) or 0
    gp          = summary.get("gate_passed", 0) or 0
    breadth     = summary.get("breadth_pct", 0) or 0
    posture     = summary.get("posture", "CAUTIOUS")
    report_date = summary.get("report_date", "")
    signal_date = summary.get("signal_date", report_date)
    radar       = summary.get("radar_top", [])

    emoji                     = _REGIME_EMOJI.get(regime, "⚪")
    posture_emoji, posture_ar = _POSTURE_AR.get(posture, ("🛡", "حذر"))

    lines = [
        f"📊 <b>نظام EGX الذكي — {report_date}</b>",
        f"{emoji} السوق: <b>{regime_ar}</b>  {posture_emoji} <b>{posture_ar}</b>",
        f"📈 عائد 20 يوم: <b>{ret20:+.1f}%</b>  |  5 أيام: <b>{ret5:+.1f}%</b>",
        f"📡 اتساع السوق: <b>{breadth:.0f}%</b>",
    ]

    if gp > 0:
        if signal_date and signal_date != report_date:
            lines.append(f"✅ <b>{gp} فرص مؤهلة</b> — من تاريخ {signal_date}")
        else:
            lines.append(f"✅ <b>{gp} فرص مؤهلة</b> — راجع التوصيات")
    else:
        if regime == "BEAR":
            lines.append("🔒 <b>لا توصيات</b> — الإشارات محجوبة بسبب السوق الهابط")
        else:
            lines.append("⏸ <b>لا فرص تنفيذية اليوم</b> — لا توجد إشارة نهائية مؤكدة لنفس التاريخ")

    if radar:
        radar_str = "  |  ".join(radar[:3])
        lines.append(f"🎯 رادار الانفجار: <code>{radar_str}</code>")

    lines.append("<i>للمعلومات فقط • ليس توصية استثمارية</i>")
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
