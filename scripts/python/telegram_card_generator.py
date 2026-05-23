"""
telegram_card_generator.py — Pillow-based visual trading card generator
for EGX algorithmic system Telegram output.

Cards generated:
  1. MarketStatusCard  — daily regime + market summary (always sent first)
  2. StockSignalCard   — per qualifying stock with entry/T1/T2/T3/SL
  3. WatchlistCard     — top watchlist stocks when no gate-passed signals

Arabic RTL rendering uses arabic_reshaper + bidi algorithm.
Falls back to plain text labels if libraries not available.
"""

import os
import io
import sqlite3
import math
from datetime import datetime, date
from typing import Optional, List, Dict, Tuple, Any

# ── Pillow ──────────────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

# ── Arabic text ─────────────────────────────────────────────────────────────────
try:
    import arabic_reshaper
    from bidi.algorithm import get_display as bidi_get_display
    ARABIC_OK = True
except ImportError:
    ARABIC_OK = False

from telegram_card_styles import (
    CARD_W, CARD_H, MARKET_CARD_H, WATCH_CARD_H, RADIUS,
    FONT_BOLD, FONT_SEMI, FONT_REGULAR, FONT_ARABIC,
    PALETTE, SIGNAL_CONFIGS, REGIME_DISPLAY, BEHAVIOR_BADGE,
    Color, RGBA
)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "egx_trading.db")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cards")


# ═══════════════════════════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _strip_emoji(text: str) -> str:
    """Remove emoji/symbol chars that render as boxes in bilingual fonts.
    Keeps: Arabic (0600-06FF, FB50-FDFF), Latin, digits, punctuation, spaces.
    Strips: emoji (2600+), variation selectors (FE00-FE0F), combining Cf chars.
    """
    import unicodedata
    result = []
    for ch in text:
        cp = ord(ch)
        cat = unicodedata.category(ch)
        # Always skip: variation selectors, zero-width joiners, direction marks
        if 0xFE00 <= cp <= 0xFE0F:  # variation selectors (️ after emoji)
            continue
        if cp in (0x200B, 0x200C, 0x200D, 0x200E, 0x200F,  # zero-width/dir marks
                  0xFEFF):  # BOM
            continue
        # Keep Arabic presentation forms
        if 0xFB50 <= cp <= 0xFDFF or 0x0600 <= cp <= 0x06FF:
            result.append(ch)
            continue
        # Keep basic ASCII + Latin extended
        if cp < 0x2000:
            result.append(ch)
            continue
        # Skip emoji and symbols (U+2000+) unless it's a letter/number/punct
        if cat.startswith(('L', 'N', 'P', 'Z')):
            result.append(ch)
        # else: skip symbols, emoji, etc.
    return ''.join(result).strip()


def _ar(text: str) -> str:
    """Reshape + bidi Arabic string for left-to-right Pillow rendering.
    Strips emojis since SF Arabic font renders them as boxes."""
    if not text:
        return text
    clean = _strip_emoji(text)
    if ARABIC_OK:
        try:
            reshaped = arabic_reshaper.reshape(clean)
            return bidi_get_display(reshaped)
        except Exception:
            pass
    return clean  # fallback: raw string


def _font(path: str, size: int) -> Any:
    """Load Latin/numeric font with fallback to default."""
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        try:
            return ImageFont.truetype(FONT_REGULAR, size)
        except Exception:
            return ImageFont.load_default()


def _font_ar(size: int) -> Any:
    """Load Arabic font — renders Arabic ligatures correctly."""
    try:
        if FONT_ARABIC:
            return ImageFont.truetype(FONT_ARABIC, size)
    except Exception:
        pass
    return _font(FONT_REGULAR, size)


def _rgba(color: Color, alpha: int = 255) -> RGBA:
    return (*color, alpha)


def _lerp_color(c1: Color, c2: Color, t: float) -> Color:
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _rounded_rect(draw: ImageDraw.Draw, xy: Tuple, radius: int, fill: Color,
                  outline: Optional[Color] = None, outline_width: int = 1):
    """Draw a filled rounded rectangle."""
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill,
                           outline=outline, width=outline_width)


def _text_center(draw: ImageDraw.Draw, text: str, font, y: int, width: int,
                 fill: Color, x_offset: int = 0):
    """Draw text centered horizontally."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (width - tw) // 2 + x_offset
    draw.text((x, y), text, font=font, fill=fill)


def _text_right(draw: ImageDraw.Draw, text: str, font, xy: Tuple[int, int],
                fill: Color):
    """Draw text right-aligned ending at xy[0]."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((xy[0] - tw, xy[1]), text, font=font, fill=fill)


# ═══════════════════════════════════════════════════════════════════════════════
# Sparkline renderer
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_sparkline(draw: ImageDraw.Draw, prices: List[float],
                    x: int, y: int, w: int, h: int,
                    color_up: Color = None, color_dn: Color = None):
    """Draw a mini price sparkline in bounding box (x,y,x+w,y+h)."""
    if color_up is None:
        color_up = PALETTE.spark_up
    if color_dn is None:
        color_dn = PALETTE.spark_dn

    if not prices or len(prices) < 2:
        return

    lo = min(prices)
    hi = max(prices)
    span = hi - lo if hi != lo else 1.0

    def _px(p):
        return x + int((prices.index(p) / (len(prices) - 1)) * w) if prices else x

    def _py(p):
        return y + h - int(((p - lo) / span) * h)

    points = []
    for i, p in enumerate(prices):
        px = x + int((i / (len(prices) - 1)) * w)
        py = y + h - int(((p - lo) / span) * (h - 2)) - 1
        points.append((px, py))

    # Background grid
    for step in [0.25, 0.5, 0.75]:
        gy = y + int((1 - step) * h)
        draw.line([(x, gy), (x + w, gy)], fill=PALETTE.spark_grid, width=1)

    # Determine overall trend
    trend_color = color_up if prices[-1] >= prices[0] else color_dn

    # Draw lines
    for i in range(len(points) - 1):
        draw.line([points[i], points[i + 1]], fill=trend_color, width=2)

    # Current price dot
    last = points[-1]
    draw.ellipse([last[0]-3, last[1]-3, last[0]+3, last[1]+3],
                 fill=trend_color, outline=PALETTE.text_primary)


def _fetch_spark_prices(symbol: str, n: int = 20) -> List[float]:
    """Fetch last n closing prices from OHLCV for sparkline."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT close FROM ohlcv WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol, n)
        ).fetchall()
        conn.close()
        prices = [r[0] for r in reversed(rows) if r[0] and r[0] > 0]
        return prices
    except Exception:
        return []


def _list_index_fake(lst, val):
    """Safe index for sparkline (avoids list.index() for floats)."""
    for i, v in enumerate(lst):
        if v == val:
            return i
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# Price-level bar renderer
# ═══════════════════════════════════════════════════════════════════════════════

def _draw_price_bar(draw: ImageDraw.Draw, label_ar: str, price: float,
                    x: int, y: int, bar_w: int, color: Color,
                    font_label, font_price, current: float = None):
    """Draw a labeled price level pill."""
    pill_h = 36
    _rounded_rect(draw, (x, y, x + bar_w, y + pill_h), radius=8,
                  fill=(*color, 40) if len(color) == 3 else color)
    draw.line([(x, y + pill_h // 2), (x + 4, y + pill_h // 2)],
              fill=color, width=3)

    label_text = _ar(label_ar)
    draw.text((x + 10, y + 6), label_text, font=font_label, fill=color)

    price_text = f"{price:.2f}"
    _text_right(draw, price_text, font_price, (x + bar_w - 8, y + 7), PALETTE.text_primary)

    # Pct from current
    if current and current > 0:
        pct = (price - current) / current * 100
        sign = "+" if pct >= 0 else ""
        pct_text = f"{sign}{pct:.1f}%"
        pct_color = PALETTE.bull_green if pct >= 0 else PALETTE.bear_red
        _text_right(draw, pct_text, font_label,
                    (x + bar_w - 8, y + pill_h - 18), pct_color)


# ═══════════════════════════════════════════════════════════════════════════════
# Card base
# ═══════════════════════════════════════════════════════════════════════════════

def _new_card(width: int, height: int) -> Tuple[Image.Image, ImageDraw.Draw]:
    img = Image.new("RGB", (width, height), PALETTE.bg_dark)
    draw = ImageDraw.Draw(img, "RGBA")
    # Main card surface
    _rounded_rect(draw, (8, 8, width - 8, height - 8), radius=RADIUS,
                  fill=PALETTE.bg_card)
    return img, draw


def _draw_logo_strip(draw: ImageDraw.Draw, width: int, y: int = 14):
    """Draw 'EGX AI • نظام التداول الذكي' — uses Arabic font for the Arabic part."""
    # Draw Arabic part with Arabic font
    ar_text = _ar("نظام التداول الذكي")
    en_text = "EGX AI  •  "
    font_ar = _font_ar(13)
    font_en = _font(FONT_SEMI, 13)
    # Measure both
    bbox_en = draw.textbbox((0, 0), en_text, font=font_en)
    bbox_ar = draw.textbbox((0, 0), ar_text, font=font_ar)
    total_w = (bbox_en[2] - bbox_en[0]) + (bbox_ar[2] - bbox_ar[0])
    start_x = (width - total_w) // 2
    draw.text((start_x, y), en_text, font=font_en, fill=PALETTE.text_dim)
    draw.text((start_x + bbox_en[2] - bbox_en[0], y), ar_text, font=font_ar, fill=PALETTE.text_dim)


def _draw_date_badge(draw: ImageDraw.Draw, width: int, y: int, report_date: str):
    date_text = report_date  # keep as Latin (numbers/hyphens)
    font = _font(FONT_SEMI, 14)
    bbox = draw.textbbox((0, 0), date_text, font=font)
    bw = bbox[2] - bbox[0] + 24
    bx = (width - bw) // 2
    _rounded_rect(draw, (bx, y, bx + bw, y + 26), radius=13,
                  fill=PALETTE.bg_pill)
    _text_center(draw, date_text, font, y + 4, width, PALETTE.text_secondary)


# ═══════════════════════════════════════════════════════════════════════════════
# Card 1: Market Status
# ═══════════════════════════════════════════════════════════════════════════════

def build_market_card(market_data: Dict) -> Optional[bytes]:
    """
    market_data keys:
      report_date, regime, market_return_20d, market_return_5d,
      breadth_pct (% stocks above MA), total_signals, gate_passed,
      posture (AGGRESSIVE/CAUTIOUS/DEFENSIVE), note (Arabic summary)
    """
    if not PILLOW_OK:
        return None

    W, H = CARD_W, MARKET_CARD_H
    img, draw = _new_card(W, H)

    regime = market_data.get("regime", "UNKNOWN")
    rd = REGIME_DISPLAY.get(regime, REGIME_DISPLAY["UNKNOWN"])

    # ── Header gradient strip ────────────────────────────────────────────────
    _rounded_rect(draw, (16, 16, W - 16, 90), radius=16, fill=rd["bg"])
    draw.line([(16, 90), (W - 16, 90)], fill=rd["color"], width=2)

    # Logo
    _draw_logo_strip(draw, W, y=22)

    # Regime label
    font_regime = _font_ar(28)
    regime_text = _ar(f"سوق  {rd['ar']}")
    _text_center(draw, regime_text, font_regime, 52, W, rd["color"])

    # Date
    _draw_date_badge(draw, W, 100, market_data.get("report_date", ""))

    # ── Stats row ────────────────────────────────────────────────────────────
    stats_y = 140
    font_val  = _font(FONT_BOLD, 26)
    font_lbl  = _font(FONT_SEMI, 13)

    stats = [
        ("عائد 20 يوم", f"{market_data.get('market_return_20d', 0):+.1f}%",
         PALETTE.bull_green if market_data.get("market_return_20d", 0) >= 0 else PALETTE.bear_red),
        ("عائد 5 أيام", f"{market_data.get('market_return_5d', 0):+.1f}%",
         PALETTE.bull_green if market_data.get("market_return_5d", 0) >= 0 else PALETTE.bear_red),
        ("اتساع السوق", f"{market_data.get('breadth_pct', 0):.0f}%",
         PALETTE.text_primary),
        ("فرص اليوم", str(market_data.get("gate_passed", 0)),
         PALETTE.bull_green if market_data.get("gate_passed", 0) > 0 else PALETTE.neutral_gray),
    ]

    font_lbl_ar = _font_ar(13)
    col_w = (W - 32) // len(stats)
    for i, (lbl, val, color) in enumerate(stats):
        cx = 16 + i * col_w
        _rounded_rect(draw, (cx + 4, stats_y, cx + col_w - 4, stats_y + 90),
                      radius=14, fill=PALETTE.bg_panel)
        _text_center(draw, val, font_val, stats_y + 14, col_w, color, x_offset=cx)
        _text_center(draw, _ar(lbl), font_lbl_ar, stats_y + 55, col_w, PALETTE.text_secondary,
                     x_offset=cx)

    # ── Posture badge ────────────────────────────────────────────────────────
    posture_map = {
        "AGGRESSIVE": ("عدواني", PALETTE.bull_green),
        "CAUTIOUS":   ("حذر",   PALETTE.swing_gold),
        "DEFENSIVE":  ("دفاعي", PALETTE.bear_red),
    }
    posture = market_data.get("posture", "CAUTIOUS")
    pos_label, pos_color = posture_map.get(posture, ("حذر", PALETTE.swing_gold))
    posture_y = stats_y + 104
    posture_text = _ar(f"الموقف: {pos_label}")
    font_pos = _font_ar(16)
    bbox = draw.textbbox((0, 0), posture_text, font=font_pos)
    bw = bbox[2] - bbox[0] + 24
    bx = (W - bw) // 2
    _rounded_rect(draw, (bx, posture_y, bx + bw, posture_y + 28), radius=14,
                  fill=(*pos_color, 30))
    draw.rounded_rectangle([bx, posture_y, bx + bw, posture_y + 28],
                           radius=14, outline=pos_color, width=1)
    _text_center(draw, posture_text, font_pos, posture_y + 4, W, pos_color)

    # ── Note ────────────────────────────────────────────────────────────────
    note = market_data.get("note", "")
    if note:
        note_y = posture_y + 44
        font_note = _font_ar(16)
        note_r = _ar(note)
        # Word-wrap at ~65 chars
        lines = _wrap_text(note_r, font_note, W - 60, draw)
        for li, line in enumerate(lines[:3]):
            _text_center(draw, line, font_note, note_y + li * 24,
                         W, PALETTE.text_secondary)

    # ── Footer ──────────────────────────────────────────────────────────────
    font_footer = _font_ar(11)
    footer = _ar("النظام للمعلومات فقط • لا يُعدّ توصية استثمارية")
    _text_center(draw, footer, font_footer, H - 22, W, PALETTE.text_dim)

    return _img_to_bytes(img)


# ═══════════════════════════════════════════════════════════════════════════════
# Card 2: Stock Signal Card
# ═══════════════════════════════════════════════════════════════════════════════

def build_stock_card(signal: Dict) -> Optional[bytes]:
    """
    signal keys (from unified_signals + enrichment):
      symbol, signal_type (SCALP/SWING/INVESTMENT/BEAR_EXCEPTION),
      entry_low, entry_high, current_price,
      t1, t2, t3, stop_loss,
      ml_score, rsi, behavior_class (STEADY/EXPLOSIVE/VOLATILE),
      sector, report_date
    """
    if not PILLOW_OK:
        return None

    W, H = CARD_W, CARD_H
    img, draw = _new_card(W, H)

    sig_type = signal.get("signal_type", "SWING")
    cfg = SIGNAL_CONFIGS.get(sig_type, SIGNAL_CONFIGS["SWING"])
    accent = cfg["accent"]
    symbol = signal.get("symbol", "???")
    current = signal.get("current_price", 0.0)

    # ── Top accent strip ────────────────────────────────────────────────────
    _rounded_rect(draw, (16, 16, W - 16, 95), radius=16,
                  fill=(*accent, 25))
    draw.line([(16, 95), (W - 16, 95)], fill=accent, width=2)

    # Signal-type badge (top-left)
    badge_text = f"{cfg['emoji']} {cfg['label_ar']}"
    badge_r = _ar(badge_text)
    font_badge = _font(FONT_BOLD, 16)
    bbox = draw.textbbox((0, 0), badge_r, font=font_badge)
    bw = bbox[2] - bbox[0] + 16
    _rounded_rect(draw, (24, 22, 24 + bw, 48), radius=10, fill=accent)
    draw.text((32, 26), badge_r, font=font_badge, fill=PALETTE.bg_dark)

    # Logo (centered)
    _draw_logo_strip(draw, W, y=26)

    # Date (top-right)
    font_date = _font(FONT_REGULAR, 12)
    draw.text((W - 120, 30), signal.get("report_date", ""), font=font_date,
              fill=PALETTE.text_dim)

    # ── Symbol + sector ──────────────────────────────────────────────────────
    font_sym  = _font(FONT_BOLD, 38)
    font_sect = _font_ar(14)
    draw.text((30, 54), symbol, font=font_sym, fill=PALETTE.text_primary)

    sector = signal.get("sector", "")
    if sector:
        draw.text((30, 96), _ar(sector), font=font_sect, fill=PALETTE.text_dim)

    # Horizon pill
    hor_text = _ar(cfg["horizon"])
    font_hor = _font_ar(13)
    bbox = draw.textbbox((0, 0), hor_text, font=font_hor)
    hw = bbox[2] - bbox[0] + 16
    hx = W - 24 - hw
    _rounded_rect(draw, (hx, 56, hx + hw, 80), radius=10, fill=PALETTE.bg_pill)
    draw.text((hx + 8, 60), hor_text, font=font_hor, fill=PALETTE.text_secondary)

    # ── Sparkline ────────────────────────────────────────────────────────────
    prices = _fetch_spark_prices(symbol, 20)
    if prices:
        _draw_sparkline(draw, prices, W - 200, 22, 160, 64)

    # ── Behavior badge + ML score ────────────────────────────────────────────
    bclass = signal.get("behavior_class", "")
    bc_y = 100
    if bclass and bclass in BEHAVIOR_BADGE:
        bcfg = BEHAVIOR_BADGE[bclass]
        bc_text = _ar(f"• {bcfg['ar']}")
        font_bc = _font_ar(13)
        bbox = draw.textbbox((0, 0), bc_text, font=font_bc)
        bw = bbox[2] - bbox[0] + 14
        _rounded_rect(draw, (W - 24 - bw, bc_y, W - 24, bc_y + 24), radius=12,
                      fill=(*bcfg["color"], 30))
        draw.text((W - 20 - bw, bc_y + 4), bc_text, font=font_bc, fill=bcfg["color"])

    # ML confidence bar
    ml = signal.get("ml_score", 0)
    font_ml = _font_ar(13)
    ml_text = _ar(f"ثقة النموذج: {ml:.0f}%")
    draw.text((30, bc_y + 3), ml_text, font=font_ml, fill=PALETTE.text_secondary)
    # progress bar
    bar_x, bar_y, bar_w, bar_h = 30, bc_y + 22, 200, 6
    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                           radius=3, fill=PALETTE.bg_pill)
    fill_w = int(bar_w * min(ml / 100, 1.0))
    if fill_w > 0:
        fill_color = accent if ml >= 70 else PALETTE.swing_gold if ml >= 50 else PALETTE.neutral_gray
        draw.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h],
                               radius=3, fill=fill_color)

    # ── Divider ──────────────────────────────────────────────────────────────
    div_y = 140
    draw.line([(24, div_y), (W - 24, div_y)], fill=PALETTE.bg_pill, width=1)

    # ── Left panel: Entry zone ───────────────────────────────────────────────
    panel_y = div_y + 12
    panel_x = 24
    panel_w = (W - 48) // 2 - 8

    font_panel_title = _font_ar(15)
    font_price_big   = _font(FONT_BOLD, 32)
    font_price_small = _font(FONT_SEMI, 18)
    font_label_sm    = _font_ar(13)
    font_price_lbl   = _font(FONT_BOLD, 16)

    # Entry zone panel
    _rounded_rect(draw, (panel_x, panel_y, panel_x + panel_w, panel_y + 150),
                  radius=14, fill=PALETTE.bg_panel)

    entry_title = _ar("منطقة الدخول")
    _text_center(draw, entry_title, font_panel_title, panel_y + 10,
                 panel_w, PALETTE.entry_color, x_offset=panel_x)

    entry_low  = signal.get("entry_low", current * 0.98)
    entry_high = signal.get("entry_high", current * 1.00)

    ent_text = f"{entry_low:.2f} — {entry_high:.2f}"
    _text_center(draw, ent_text, font_price_small, panel_y + 38,
                 panel_w, PALETTE.text_primary, x_offset=panel_x)

    cur_text = _ar(f"السعر الحالي: {current:.2f}")
    _text_center(draw, cur_text, font_label_sm, panel_y + 72,
                 panel_w, PALETTE.text_dim, x_offset=panel_x)

    rsi = signal.get("rsi", 0)
    rsi_color = PALETTE.bull_green if rsi < 40 else PALETTE.bear_red if rsi > 60 else PALETTE.text_secondary
    rsi_text = _ar(f"RSI: {rsi:.1f}")
    _text_center(draw, rsi_text, font_label_sm, panel_y + 94,
                 panel_w, rsi_color, x_offset=panel_x)

    # Stop Loss inside entry panel
    sl = signal.get("stop_loss", 0)
    if sl:
        sl_pct = (sl - current) / current * 100 if current else 0
        sl_text = f"SL: {sl:.2f}  ({sl_pct:+.1f}%)"
        _rounded_rect(draw, (panel_x + 8, panel_y + 114, panel_x + panel_w - 8, panel_y + 144),
                      radius=8, fill=(*PALETTE.sl_color, 20))
        _text_center(draw, _ar(f"وقف الخسارة:  {sl:.2f}"), font_label_sm,
                     panel_y + 120, panel_w, PALETTE.sl_color, x_offset=panel_x)

    # ── Right panel: Targets ─────────────────────────────────────────────────
    rpanel_x = panel_x + panel_w + 16
    rpanel_w = W - 48 - panel_w - 16

    _rounded_rect(draw, (rpanel_x, panel_y, rpanel_x + rpanel_w, panel_y + 150),
                  radius=14, fill=PALETTE.bg_panel)

    tgt_title = _ar("مستهدفات جني الأرباح")
    _text_center(draw, tgt_title, font_panel_title, panel_y + 10,
                 rpanel_w, PALETTE.t1_color, x_offset=rpanel_x)

    targets = [
        (_ar("هدف ١"), signal.get("t1", 0), PALETTE.t1_color),
        (_ar("هدف ٢"), signal.get("t2", 0), PALETTE.t2_color),
        (_ar("هدف ٣"), signal.get("t3", 0), PALETTE.t3_color),
    ]
    tgt_y_start = panel_y + 40
    tgt_spacing = 34

    for i, (tlbl, tprice, tcolor) in enumerate(targets):
        ty = tgt_y_start + i * tgt_spacing
        if tprice and tprice > 0:
            pct = (tprice - current) / current * 100 if current else 0
            sign = "+" if pct >= 0 else ""

            # Label
            draw.text((rpanel_x + 12, ty), tlbl, font=font_label_sm, fill=tcolor)
            # Price
            price_str = f"{tprice:.2f}"
            _text_right(draw, price_str, font_price_lbl,
                        (rpanel_x + rpanel_w - 8, ty - 1), PALETTE.text_primary)
            # Pct
            pct_str = f"{sign}{pct:.1f}%"
            _text_right(draw, pct_str, font_label_sm,
                        (rpanel_x + rpanel_w - 8, ty + 16), tcolor)
            # Mini progress line
            if current and tprice > current:
                bar_len = min(int((pct / 30) * (rpanel_w - 80)), rpanel_w - 80)
                bar_len = max(bar_len, 4)
                bby = ty + 28
                draw.line([(rpanel_x + 12, bby), (rpanel_x + 12 + bar_len, bby)],
                          fill=(*tcolor, 120), width=2)

    # ── Risk note ────────────────────────────────────────────────────────────
    risk_y = panel_y + 162
    font_risk = _font_ar(13)
    risk_note = _ar(cfg['risk_note'])  # no emoji prefix — causes box rendering
    _text_center(draw, risk_note, font_risk, risk_y, W, PALETTE.neutral_gray)

    # ── Divider ──────────────────────────────────────────────────────────────
    draw.line([(24, risk_y + 22), (W - 24, risk_y + 22)], fill=PALETTE.bg_pill, width=1)

    # ── Bottom stats row ─────────────────────────────────────────────────────
    stats_y2 = risk_y + 32
    font_stat_v = _font(FONT_BOLD, 18)
    font_stat_l = _font_ar(12)

    bottom_stats = []
    if signal.get("momentum5d") is not None:
        bottom_stats.append((_ar("زخم 5 أيام"), f"{signal['momentum5d']:+.1f}%",
                             PALETTE.bull_green if signal["momentum5d"] > 0 else PALETTE.bear_red))
    if signal.get("volume_ratio") is not None:
        bottom_stats.append((_ar("حجم/متوسط"), f"{signal['volume_ratio']:.1f}×",
                             PALETTE.invest_blue))
    if signal.get("atr_pct") is not None:
        bottom_stats.append((_ar("تذبذب ATR"), f"{signal['atr_pct']:.1f}%",
                             PALETTE.text_secondary))
    if signal.get("cycle_btm") is not None:
        bottom_stats.append((_ar("دورة السعر"), f"{signal['cycle_btm']:.0f}%",
                             PALETTE.swing_gold))

    if bottom_stats:
        bscol_w = (W - 48) // max(len(bottom_stats), 1)
        for i, (lbl, val, color) in enumerate(bottom_stats[:4]):
            bsx = 24 + i * bscol_w
            _text_center(draw, val, font_stat_v, stats_y2, bscol_w, color, x_offset=bsx)
            _text_center(draw, lbl, font_stat_l, stats_y2 + 22, bscol_w,
                         PALETTE.text_dim, x_offset=bsx)

    # ── Footer ──────────────────────────────────────────────────────────────
    font_footer = _font_ar(11)
    footer = _ar("للمعلومات فقط • المخاطرة مسؤولية المتداول")
    _text_center(draw, footer, font_footer, H - 20, W, PALETTE.text_dim)

    return _img_to_bytes(img)


# ═══════════════════════════════════════════════════════════════════════════════
# Card 3: Watchlist Card (BEAR / no signals day)
# ═══════════════════════════════════════════════════════════════════════════════

def build_watchlist_card(watchlist: List[Dict], market_data: Dict) -> Optional[bytes]:
    """
    watchlist: list of dicts with symbol, rsi, ml_score, note_ar, current_price
    """
    if not PILLOW_OK:
        return None

    W, H = CARD_W, WATCH_CARD_H
    img, draw = _new_card(W, H)

    regime = market_data.get("regime", "UNKNOWN")
    rd = REGIME_DISPLAY.get(regime, REGIME_DISPLAY["UNKNOWN"])

    # Header
    _rounded_rect(draw, (16, 16, W - 16, 80), radius=16, fill=rd["bg"])
    draw.line([(16, 80), (W - 16, 80)], fill=rd["color"], width=2)

    _draw_logo_strip(draw, W, y=20)

    font_title = _font(FONT_BOLD, 24)
    title = _ar("قائمة المراقبة — لا توصيات اليوم")
    _text_center(draw, title, font_title, 44, W, rd["color"])

    _draw_date_badge(draw, W, 88, market_data.get("report_date", ""))

    # Reason
    reason = market_data.get("note", "")
    if reason:
        font_reason = _font_ar(15)
        reason_r = _ar(reason)
        lines = _wrap_text(reason_r, font_reason, W - 60, draw)
        for li, line in enumerate(lines[:2]):
            _text_center(draw, line, font_reason, 118 + li * 22,
                         W, PALETTE.text_secondary)

    # Watchlist rows
    row_y = 170
    font_sym   = _font(FONT_BOLD, 18)
    font_info  = _font(FONT_SEMI, 13)
    font_note  = _font_ar(13)

    for i, stock in enumerate(watchlist[:5]):
        ry = row_y + i * 58
        _rounded_rect(draw, (24, ry, W - 24, ry + 50), radius=12, fill=PALETTE.bg_panel)

        sym = stock.get("symbol", "")
        price = stock.get("current_price", 0)
        rsi = stock.get("rsi", 0)
        ml  = stock.get("ml_score", 0)
        mom5 = stock.get("momentum_5d", 0)
        expl = stock.get("explosion_score", 0)
        note_ar = stock.get("note_ar", "")

        # Symbol
        draw.text((36, ry + 8), sym, font=font_sym, fill=PALETTE.text_primary)

        # Price + momentum arrow
        mom_arrow = "+" if mom5 >= 0 else ""
        mom_color = PALETTE.bull_green if mom5 > 0 else PALETTE.bear_red if mom5 < 0 else PALETTE.text_dim
        price_text = f"{price:.2f}"
        draw.text((36, ry + 30), price_text, font=font_info, fill=PALETTE.text_secondary)
        mom_text = f"5D: {mom_arrow}{mom5:.1f}%"
        draw.text((108, ry + 30), mom_text, font=font_info, fill=mom_color)

        # RSI
        rsi_color = PALETTE.bull_green if rsi < 40 else PALETTE.bear_red if rsi > 60 else PALETTE.text_dim
        rsi_text = f"RSI {rsi:.1f}"
        draw.text((210, ry + 8), rsi_text, font=font_info, fill=rsi_color)

        # ML explosion score
        expl_color = PALETTE.swing_gold if expl >= 70 else PALETTE.text_dim
        ml_text = f"ML {expl:.0f}%"
        draw.text((210, ry + 28), ml_text, font=font_info, fill=expl_color)

        # Note (apply _ar() here, not in _enrich_watchlist)
        if note_ar:
            note_r = _ar(str(note_ar))
            _text_right(draw, note_r, font_note, (W - 36, ry + 16), PALETTE.text_secondary)

        # Sparkline mini
        prices = _fetch_spark_prices(sym, 10)
        if prices:
            _draw_sparkline(draw, prices, W - 230, ry + 6, 120, 38)

    # Footer
    font_footer = _font_ar(11)
    footer = _ar("يُحدَّث يومياً • انتظر إشارة واضحة قبل الدخول")
    _text_center(draw, footer, font_footer, H - 18, W, PALETTE.text_dim)

    return _img_to_bytes(img)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _img_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, optimize=True)
    return buf.getvalue()


def _wrap_text(text: str, font, max_w: int, draw: ImageDraw.Draw) -> List[str]:
    """Simple word-wrap for Pillow text."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_w:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def save_card(card_bytes: bytes, name: str) -> str:
    """Save card PNG to data/cards/ and return path."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.png")
    with open(path, "wb") as f:
        f.write(card_bytes)
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# Data enrichment: compute entry/targets/SL from DB signals
# ═══════════════════════════════════════════════════════════════════════════════

def _enrich_signal(row: sqlite3.Row, conn: sqlite3.Connection) -> Dict:
    """Convert a unified_signals row into a card-ready dict."""
    sym = row["symbol"]
    sig = dict(row)

    # Current price — prefer ohlcv, fallback to entry_price in signal
    price_row = conn.execute(
        "SELECT close FROM ohlcv WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)
    ).fetchone()
    current = price_row[0] if price_row and price_row[0] else (sig.get("entry_price") or 0.0)
    sig["current_price"] = current

    # ATR for target calculation
    atr_row = conn.execute(
        "SELECT atr14 FROM indicators_cache WHERE symbol=? ORDER BY bar_date DESC LIMIT 1", (sym,)
    ).fetchone()
    atr = (atr_row["atr14"] if atr_row else None) or (current * 0.02)

    # RSI & momentum from indicators_cache
    ind_row = conn.execute(
        "SELECT rsi14, momentum_5d, vol_ratio_20 FROM indicators_cache "
        "WHERE symbol=? ORDER BY bar_date DESC LIMIT 1",
        (sym,)
    ).fetchone()
    if ind_row:
        sig["rsi"]          = ind_row["rsi14"] or 50
        sig["momentum5d"]   = ind_row["momentum_5d"] or 0
        sig["volume_ratio"] = ind_row["vol_ratio_20"] or 1.0
    else:
        sig["rsi"]          = 50
        sig["momentum5d"]   = 0
        sig["volume_ratio"] = 1.0

    sig["atr_pct"] = (atr / current * 100) if current else 0

    # Signal type: based on conviction_tier + behavioral_class
    bclass = sig.get("behavioral_class") or ""
    tier   = sig.get("conviction_tier") or ""
    active_regime = sig.get("active_regime") or ""

    sig_type = "SWING"
    if active_regime == "BEAR":
        sig_type = "BEAR_EXCEPTION"
    elif bclass == "EXPLOSIVE" or "EXPLOSIVE" in tier:
        sig_type = "SCALP"
    elif bclass == "STEADY" and sig.get("cycle_score", 0) and sig["cycle_score"] > 60:
        sig_type = "INVESTMENT"
    sig["signal_type"] = sig_type

    sig["behavior_class"] = bclass

    # Sector
    sector_row = conn.execute(
        "SELECT sector FROM stock_universe WHERE symbol=? LIMIT 1", (sym,)
    ).fetchone()
    sig["sector"] = sector_row["sector"] if sector_row else ""

    # Entry zone: use stored values if available, else derive from ATR
    stored_entry_high = sig.get("entry_high")
    stored_stop       = sig.get("stop_loss")
    stored_t1         = sig.get("t1_target")
    stored_t2         = sig.get("t2_target")

    sig["entry_low"]  = round((stored_entry_high or current) - atr * 0.3, 2)
    sig["entry_high"] = round(stored_entry_high or (current + atr * 0.1), 2)
    sig["stop_loss"]  = round(stored_stop or (current - atr), 2)

    # Targets: use stored T1/T2, extend T3 as 2× T2 gain
    t1 = stored_t1 or round(current + atr * 1.5, 2)
    t2 = stored_t2 or round(current + atr * 3.0, 2)
    t3 = round(current + (t2 - current) * 2.0, 2)  # T3 = 2× T2 gain

    sig["t1"] = round(t1, 2)
    sig["t2"] = round(t2, 2)
    sig["t3"] = round(t3, 2)

    # cycle_btm: map cycle_score 0-100 → %, 100 = confirmed bottom
    sig["cycle_btm"] = sig.get("cycle_score") or 0

    # ML score
    sig["ml_score"] = sig.get("unified_score") or 0

    return sig


def _enrich_watchlist(symbols: List[str], conn: sqlite3.Connection) -> List[Dict]:
    """Build watchlist entries for BEAR/no-signal days.
    Note: store raw Arabic text in note_ar — _ar() applied in drawing function.
    """
    result = []
    for sym in symbols[:5]:
        # Price from ohlcv_history (correct table name)
        price_row = conn.execute(
            "SELECT close FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 1", (sym,)
        ).fetchone()
        current = price_row[0] if price_row else 0.0

        ind_row = conn.execute(
            "SELECT rsi14, momentum_5d, adx14, vol_ratio_20 "
            "FROM indicators_cache WHERE symbol=? ORDER BY bar_date DESC LIMIT 1", (sym,)
        ).fetchone()
        rsi = float(ind_row["rsi14"]) if ind_row and ind_row["rsi14"] else 50.0
        mom5 = float(ind_row["momentum_5d"]) if ind_row and ind_row["momentum_5d"] else 0.0
        adx = float(ind_row["adx14"]) if ind_row and ind_row["adx14"] else 0.0
        vol_ratio = float(ind_row["vol_ratio_20"]) if ind_row and ind_row["vol_ratio_20"] else 1.0

        ml_row = conn.execute(
            "SELECT unified_score, explosion_score FROM unified_signals "
            "WHERE symbol=? ORDER BY signal_date DESC LIMIT 1",
            (sym,)
        ).fetchone()
        ml = float(ml_row["unified_score"]) if ml_row and ml_row["unified_score"] else 0.0
        expl = float(ml_row["explosion_score"]) if ml_row and ml_row["explosion_score"] else 0.0

        # Generate contextual Arabic note (raw text — NOT bidi-shaped here)
        note = ""
        if rsi < 35 and mom5 < 0:
            note = "تراكم محتمل — مفرط في البيع"
        elif rsi < 40:
            note = "منطقة تراكم محتملة"
        elif expl >= 75 and adx < 25:
            note = "ضغط — انتظر كسر المقاومة"
        elif vol_ratio < 0.5:
            note = "حجم ضعيف — جانبي"
        elif ml >= 70:
            note = "زخم قوي — انتظر تأكيداً"
        elif mom5 > 5:
            note = "زخم صاعد — مراقبة"
        else:
            note = "انتظر إشارة واضحة"

        result.append({
            "symbol": sym,
            "current_price": current,
            "rsi": rsi,
            "ml_score": ml,
            "explosion_score": expl,
            "momentum_5d": mom5,
            "note_ar": note,   # raw Arabic — _ar() applied in build_watchlist_card
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

def generate_daily_cards(report_date: str = None) -> Dict:
    """
    Main function called by night_lab.py / telegram_report.py.

    Returns:
      {
        "market_card": bytes | None,
        "stock_cards": [{"symbol": str, "type": str, "bytes": bytes}],
        "watchlist_card": bytes | None,
        "fallback_mode": bool,
        "summary": {...}
      }
    """
    if not PILLOW_OK:
        return {"fallback_mode": True, "market_card": None, "stock_cards": [],
                "watchlist_card": None, "summary": {}}

    if report_date is None:
        report_date = date.today().isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    result = {
        "market_card": None,
        "stock_cards": [],
        "watchlist_card": None,
        "fallback_mode": False,
        "summary": {}
    }

    try:
        # ── Market data ──────────────────────────────────────────────────────
        regime_row = conn.execute(
            "SELECT regime, market_return_20d, breadth_pct "
            "FROM regime_history ORDER BY date DESC LIMIT 1"
        ).fetchone()

        regime = regime_row["regime"] if regime_row else "UNKNOWN"
        ret20  = regime_row["market_return_20d"] if regime_row else 0
        breadth_pct = regime_row["breadth_pct"] * 100 if regime_row and regime_row["breadth_pct"] else 0

        # 5-day market return: approximate from EGX30 proxy (average of top stocks)
        try:
            ret5_row = conn.execute(
                "SELECT AVG(o2.close/o1.close - 1) as ret5 "
                "FROM ohlcv o1 JOIN ohlcv o2 ON o1.symbol=o2.symbol "
                "WHERE o1.date=(SELECT date FROM ohlcv ORDER BY date DESC LIMIT 1 OFFSET 5) "
                "AND o2.date=(SELECT MAX(date) FROM ohlcv) "
                "AND o1.close > 0"
            ).fetchone()
            ret5 = (ret5_row["ret5"] or 0) * 100
        except Exception:
            ret5 = ret20 * 0.25  # rough estimate

        # Gate-passed signals for today (or latest) — quality_gate_passed=1
        gp_rows = conn.execute(
            "SELECT us.* FROM unified_signals us "
            "LEFT JOIN data_quality_flags dq "
            "  ON us.symbol=dq.symbol AND dq.issue_type='UNIT_ERROR' "
            "WHERE us.quality_gate_passed=1 AND us.signal_date=? "
            "AND dq.id IS NULL "
            "ORDER BY us.unified_score DESC LIMIT 5",
            (report_date,)
        ).fetchall()

        # Fallback: latest date with gate-passed signals
        if not gp_rows:
            latest = conn.execute(
                "SELECT MAX(signal_date) FROM unified_signals WHERE quality_gate_passed=1"
            ).fetchone()[0]
            if latest:
                gp_rows = conn.execute(
                    "SELECT us.* FROM unified_signals us "
                    "LEFT JOIN data_quality_flags dq "
                    "  ON us.symbol=dq.symbol AND dq.issue_type='UNIT_ERROR' "
                    "WHERE us.quality_gate_passed=1 AND us.signal_date=? "
                    "AND dq.id IS NULL "
                    "ORDER BY us.unified_score DESC LIMIT 5",
                    (latest,)
                ).fetchall()
                report_date = latest or report_date

        gate_passed = len(gp_rows)

        # Posture
        if regime == "BEAR":
            posture = "DEFENSIVE"
        elif regime == "BULL" and ret20 > 5:
            posture = "AGGRESSIVE"
        else:
            posture = "CAUTIOUS"

        # Note
        if regime == "BEAR":
            note = _ar(f"السوق في مرحلة هبوط — عائد 20 يوم {ret20:+.1f}%. تجنب الدخول الجديد.")
        elif gate_passed > 0:
            note = _ar(f"اكتُشفت {gate_passed} فرصة اليوم. راجع بطاقات الأسهم أدناه.")
        else:
            note = _ar("لا توصيات اليوم. انتظر اتساعاً إيجابياً في السوق.")

        market_data = {
            "report_date": report_date,
            "regime": regime,
            "market_return_20d": ret20,
            "market_return_5d": ret5,
            "breadth_pct": breadth_pct,
            "gate_passed": gate_passed,
            "posture": posture,
            "note": note,
        }

        result["summary"] = market_data

        # ── Market card ──────────────────────────────────────────────────────
        market_bytes = build_market_card(market_data)
        result["market_card"] = market_bytes

        # ── Stock cards ──────────────────────────────────────────────────────
        if gp_rows:
            for row in gp_rows:
                try:
                    enriched = _enrich_signal(row, conn)
                    enriched["report_date"] = report_date
                    card_bytes = build_stock_card(enriched)
                    if card_bytes:
                        result["stock_cards"].append({
                            "symbol": enriched["symbol"],
                            "type": enriched["signal_type"],
                            "bytes": card_bytes,
                        })
                except Exception as e:
                    print(f"[card] Error building card for {row['symbol']}: {e}")
        else:
            # ── Watchlist card ───────────────────────────────────────────────
            # Best watchlist: highest unified_score, not gate-passed
            watch_rows = conn.execute(
                "SELECT symbol FROM unified_signals "
                "WHERE quality_gate_passed=0 AND signal_date=? "
                "AND conviction_tier != 'REJECT' "
                "ORDER BY unified_score DESC LIMIT 5",
                (report_date,)
            ).fetchall()
            if not watch_rows:
                watch_rows = conn.execute(
                    "SELECT symbol FROM unified_signals "
                    "WHERE quality_gate_passed=0 "
                    "AND conviction_tier != 'REJECT' "
                    "ORDER BY unified_score DESC LIMIT 5"
                ).fetchall()

            watch_syms = [r["symbol"] for r in watch_rows]
            watchlist = _enrich_watchlist(watch_syms, conn)
            if watchlist:
                wl_bytes = build_watchlist_card(watchlist, market_data)
                result["watchlist_card"] = wl_bytes

    except Exception as e:
        print(f"[card_generator] Error: {e}")
        import traceback
        traceback.print_exc()
        result["fallback_mode"] = True
    finally:
        conn.close()

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# CLI test
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    rdate = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"[card_generator] Generating cards for {rdate or 'today'}...")

    cards = generate_daily_cards(rdate)
    print(f"[card_generator] Regime: {cards['summary'].get('regime')}")
    print(f"[card_generator] Gate-passed: {cards['summary'].get('gate_passed')}")
    print(f"[card_generator] Fallback: {cards['fallback_mode']}")

    if cards["market_card"]:
        p = save_card(cards["market_card"], f"market_{rdate or 'today'}")
        print(f"[card_generator] Market card: {p}")

    for sc in cards["stock_cards"]:
        p = save_card(sc["bytes"], f"stock_{sc['symbol']}_{rdate or 'today'}")
        print(f"[card_generator] Stock card: {p}")

    if cards["watchlist_card"]:
        p = save_card(cards["watchlist_card"], f"watchlist_{rdate or 'today'}")
        print(f"[card_generator] Watchlist card: {p}")
