"""
telegram_card_generator.py — Pillow-based visual trading card generator
for EGX algorithmic system Telegram output.

Cards generated:
  1. MarketStatusCard  — daily regime + market summary (always sent first)
  2. StockSignalCard   — per final_signals actionable stock with entry/T1/T2/T3/SL
  3. WatchlistCard     — suppressed when no same-date actionable final signal exists

Arabic RTL rendering uses arabic_reshaper + bidi algorithm.
Falls back to plain text labels if libraries not available.
"""
from __future__ import annotations

import os
import io
import json
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
    CARD_W, CARD_H, MARKET_CARD_H, WATCH_CARD_H, RADAR_CARD_H, RADIUS,
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
    """Fetch last n closing prices from ohlcv_history for sparkline.
    Uses ohlcv_history (correct table — 1.3M rows) instead of ohlcv (may be empty).
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT close FROM ohlcv_history WHERE symbol=? "
            "ORDER BY bar_time DESC LIMIT ?",
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
    signal keys (from final_signals + enrichment):
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
    # Show explosion probability when ≥30% (supplemental ML signal)
    if signal.get("explosion_prob_ml") is not None:
        expl = signal["explosion_prob_ml"]
        expl_color = (PALETTE.bull_green if expl >= 70
                      else PALETTE.swing_gold if expl >= 50
                      else PALETTE.neutral_gray)
        bottom_stats.append((_ar("انفجار ML"), f"{expl:.0f}%", expl_color))
    elif signal.get("cycle_btm") is not None:
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
# Card 4: Explosion Radar Card — top ML predictions by ensemble_prob
# ═══════════════════════════════════════════════════════════════════════════════

def build_explosion_radar_card(radar_data: Dict) -> Optional[bytes]:
    """
    radar_data keys:
      report_date     — date of predictions
      regime          — current market regime
      top_picks       — list of dicts: {symbol, prob, tier, price, mom5d, rsi}
      model_version   — model checkpoint used (e.g. 'lgbm_v3_weighted_ensemble')
      reliability     — 'OK' / 'ACCEPTABLE' / etc.
      total_universe  — how many symbols were scored

    Renders a dark-themed card showing top 5 explosion probability picks
    with horizontal probability bars, price, and key indicators.
    """
    if not PILLOW_OK:
        return None

    W, H = CARD_W, RADAR_CARD_H
    img, draw = _new_card(W, H)

    regime = radar_data.get("regime", "UNKNOWN")
    rd = REGIME_DISPLAY.get(regime, REGIME_DISPLAY["UNKNOWN"])
    top_picks = radar_data.get("top_picks", [])

    # ── Header strip ─────────────────────────────────────────────────────────
    accent_color = PALETTE.scalp_pink  # hot-pink = high-energy signal
    _rounded_rect(draw, (16, 16, W - 16, 92), radius=16,
                  fill=(*accent_color, 18))
    draw.line([(16, 92), (W - 16, 92)], fill=accent_color, width=2)

    _draw_logo_strip(draw, W, y=22)

    # Title — Arabic + Latin
    font_title_ar = _font_ar(26)
    font_title_en = _font(FONT_BOLD, 18)
    title_ar = _ar("رادار الانفجار")
    title_en = "Explosion Radar"
    bbox_ar = draw.textbbox((0, 0), title_ar, font=font_title_ar)
    bbox_en = draw.textbbox((0, 0), title_en, font=font_title_en)
    total_title_w = (bbox_ar[2] - bbox_ar[0]) + 20 + (bbox_en[2] - bbox_en[0])
    tx = (W - total_title_w) // 2
    draw.text((tx, 52), title_ar, font=font_title_ar, fill=accent_color)
    draw.text((tx + (bbox_ar[2] - bbox_ar[0]) + 12, 58), title_en,
              font=font_title_en, fill=PALETTE.text_dim)

    # Date + universe info
    _draw_date_badge(draw, W, 102, radar_data.get("report_date", ""))

    universe_n = radar_data.get("total_universe", 0)
    reliability = radar_data.get("reliability", "OK")
    rel_color = PALETTE.bull_green if reliability == "OK" else (
        PALETTE.swing_gold if reliability == "ACCEPTABLE" else PALETTE.bear_red)
    rel_text = f"Ensemble: {universe_n} symbols  •  Model: {reliability}"
    font_rel = _font(FONT_REGULAR, 12)
    _text_center(draw, rel_text, font_rel, 136, W, rel_color)

    # ── Regime badge (right side) ─────────────────────────────────────────────
    font_reg = _font_ar(14)
    reg_text = _ar(f"سوق {rd['ar']}")
    bbox = draw.textbbox((0, 0), reg_text, font=font_reg)
    bw = bbox[2] - bbox[0] + 18
    _rounded_rect(draw, (W - 28 - bw, 54, W - 28, 78), radius=10,
                  fill=(*rd["color"], 25))
    draw.rounded_rectangle([W - 28 - bw, 54, W - 28, 78],
                           radius=10, outline=rd["color"], width=1)
    draw.text((W - 24 - bw, 57), reg_text, font=font_reg, fill=rd["color"])

    # ── Column headers ────────────────────────────────────────────────────────
    hdr_y = 160
    font_hdr = _font(FONT_SEMI, 13)
    draw.text((44, hdr_y), "#", font=font_hdr, fill=PALETTE.text_dim)
    draw.text((72, hdr_y), "Symbol", font=font_hdr, fill=PALETTE.text_dim)
    draw.text((196, hdr_y), "Prob%", font=font_hdr, fill=PALETTE.text_dim)
    draw.text((560, hdr_y), "Price", font=font_hdr, fill=PALETTE.text_dim)
    draw.text((680, hdr_y), "RSI", font=font_hdr, fill=PALETTE.text_dim)
    draw.text((760, hdr_y), "5D%", font=font_hdr, fill=PALETTE.text_dim)
    draw.line([(32, hdr_y + 18), (W - 32, hdr_y + 18)],
              fill=PALETTE.bg_pill, width=1)

    # ── Top picks rows ────────────────────────────────────────────────────────
    row_y_start = hdr_y + 26
    row_h       = 54
    bar_max_w   = 340   # max width of probability bar (px)
    bar_x_start = 196   # bar left edge

    font_rank  = _font(FONT_BOLD, 15)
    font_sym   = _font(FONT_BOLD, 20)
    font_prob  = _font(FONT_BOLD, 22)
    font_tier  = _font(FONT_SEMI, 12)
    font_stats = _font(FONT_SEMI, 13)

    for i, pick in enumerate(top_picks[:5]):
        ry = row_y_start + i * row_h

        sym    = pick.get("symbol", "???")
        prob   = pick.get("prob", 0.0)     # 0.0-1.0
        tier   = pick.get("tier", "LOW")
        price  = pick.get("price", 0.0)
        rsi    = pick.get("rsi", 50.0)
        mom5d  = pick.get("mom5d", 0.0)

        # Row background (alternating)
        if i % 2 == 0:
            _rounded_rect(draw, (32, ry, W - 32, ry + row_h - 4),
                          radius=10, fill=PALETTE.bg_panel)

        # Rank
        draw.text((44, ry + 14), str(i + 1), font=font_rank, fill=PALETTE.text_dim)

        # Symbol
        draw.text((72, ry + 10), sym, font=font_sym, fill=PALETTE.text_primary)

        # Tier badge below symbol
        tier_colors = {
            "HIGH":   PALETTE.bull_green,
            "MEDIUM": PALETTE.swing_gold,
            "LOW":    PALETTE.neutral_gray,
        }
        tier_color = tier_colors.get(tier, PALETTE.neutral_gray)
        tier_text  = tier
        bbox_t = draw.textbbox((0, 0), tier_text, font=font_tier)
        tw = bbox_t[2] - bbox_t[0] + 10
        _rounded_rect(draw, (72, ry + 32, 72 + tw, ry + 46),
                      radius=5, fill=(*tier_color, 30))
        draw.text((77, ry + 33), tier_text, font=font_tier, fill=tier_color)

        # Probability bar
        prob_pct  = min(prob, 1.0)
        fill_w    = int(bar_max_w * prob_pct)
        bar_y     = ry + 16
        bar_h_px  = 18
        # Background
        draw.rounded_rectangle(
            [bar_x_start, bar_y, bar_x_start + bar_max_w, bar_y + bar_h_px],
            radius=4, fill=PALETTE.bg_pill)
        # Fill — gradient-like by tier
        if fill_w > 0:
            bar_color = (
                PALETTE.bull_green  if tier == "HIGH"   else
                PALETTE.swing_gold  if tier == "MEDIUM" else
                PALETTE.neutral_gray
            )
            draw.rounded_rectangle(
                [bar_x_start, bar_y, bar_x_start + fill_w, bar_y + bar_h_px],
                radius=4, fill=bar_color)

        # Probability % label (on bar)
        prob_text = f"{prob*100:.0f}%"
        bbox_p = draw.textbbox((0, 0), prob_text, font=font_prob)
        px_text = bar_x_start + fill_w + 8
        if px_text + (bbox_p[2] - bbox_p[0]) > bar_x_start + bar_max_w + 60:
            px_text = bar_x_start + fill_w - (bbox_p[2] - bbox_p[0]) - 6
        draw.text((px_text, bar_y - 2), prob_text, font=font_prob,
                  fill=tier_color)

        # Price
        price_text = f"{price:.2f}" if price else "—"
        draw.text((560, ry + 12), price_text, font=font_stats,
                  fill=PALETTE.text_primary)

        # RSI
        rsi_color = (PALETTE.bull_green if rsi < 40
                     else PALETTE.bear_red if rsi > 60
                     else PALETTE.text_secondary)
        draw.text((680, ry + 12), f"{rsi:.1f}", font=font_stats, fill=rsi_color)

        # 5D momentum
        mom_color = (PALETTE.bull_green if mom5d > 0
                     else PALETTE.bear_red if mom5d < 0
                     else PALETTE.text_dim)
        mom_sign  = "+" if mom5d >= 0 else ""
        draw.text((760, ry + 12), f"{mom_sign}{mom5d:.1f}%",
                  font=font_stats, fill=mom_color)

        # Mini sparkline (far right)
        spark = _fetch_spark_prices(sym, 10)
        if spark:
            _draw_sparkline(draw, spark, W - 100, ry + 8, 60, 34)

    # ── Footer ────────────────────────────────────────────────────────────────
    font_footer = _font_ar(11)
    footer = _ar("رادار تجريبي — تُحدَّث البيانات يومياً • للمعلومات فقط")
    _text_center(draw, footer, font_footer, H - 22, W, PALETTE.text_dim)

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
    """Convert a final_signals row into a card-ready dict.

    FIX 2026-05-27: use ohlcv_history (1.3M rows) instead of ohlcv (may be empty).
    FIX 2026-05-27: pull explosion_prob from explosion_predictions for accurate ML score.
    """
    sym = row["symbol"]
    sig = dict(row)
    try:
        breakdown = json.loads(sig.get("source_breakdown") or "{}")
        if isinstance(breakdown, dict):
            sig["behavioral_class"] = breakdown.get("behavioral_class") or sig.get("behavioral_class")
            sig["conviction_tier"] = breakdown.get("conviction_tier") or sig.get("conviction_tier")
            sig["cycle_score"] = breakdown.get("cycle") or sig.get("cycle_score")
    except Exception:
        pass

    # Current price — ohlcv_history is the authoritative price table
    price_row = conn.execute(
        "SELECT close FROM ohlcv_history WHERE symbol=? ORDER BY bar_time DESC LIMIT 1",
        (sym,)
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

    # Signal type: based on final signal context + source breakdown.
    bclass = sig.get("behavioral_class") or ""
    tier   = sig.get("conviction_tier") or ""
    active_regime = sig.get("active_regime") or sig.get("regime") or ""
    setup_type = sig.get("setup_type") or ""

    sig_type = "SWING"
    if active_regime == "BEAR":
        sig_type = "BEAR_EXCEPTION"
    elif bclass == "EXPLOSIVE" or "EXPLOSIVE" in tier or "Breakout" in setup_type:
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
    stored_entry      = sig.get("entry_price")
    stored_stop       = sig.get("stop_loss")
    stored_t1         = sig.get("t1_target")
    stored_t2         = sig.get("t2_target")

    sig["entry_low"]  = round((stored_entry or stored_entry_high or current) - atr * 0.3, 2)
    sig["entry_high"] = round(stored_entry_high or stored_entry or (current + atr * 0.1), 2)
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

    # Score — final_signals.score drives the client-facing actionable selection.
    # If explosion_predictions has a strong signal
    # (≥30%) for this stock from a reliable model, also store it as explosion_prob_ml
    # for display in the bottom stats row.
    sig["ml_score"]       = round(float(sig.get("score") or 0), 1)
    sig["ml_tier"]        = "HIGH" if sig["ml_score"] >= 70 else ("MEDIUM" if sig["ml_score"] >= 50 else "LOW")
    sig["ml_reliability"] = "final_signals.score"

    # Supplemental: explosion probability from the fixed weighted-average ensemble
    explosion_row = conn.execute(
        """
        SELECT explosion_prob, confidence_tier, reliability_flag
        FROM   explosion_predictions
        WHERE  symbol=?
          AND  pred_date=?
          AND  reliability_flag IN ('OK','ACCEPTABLE')
        LIMIT  1
        """,
        (sym, sig.get("trade_date") or sig.get("report_date"))
    ).fetchone()
    if explosion_row and explosion_row[0] and float(explosion_row[0]) >= 0.30:
        # Only surface when meaningful (≥30% explosion probability)
        sig["explosion_prob_ml"]  = round(float(explosion_row[0]) * 100, 1)
        sig["explosion_tier_ml"]  = explosion_row[1] or "LOW"
    else:
        sig["explosion_prob_ml"]  = None
        sig["explosion_tier_ml"]  = None

    return sig


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

    # Preserve original requested date. Client opportunity cards must only use
    # final_signals.actionable=1 for this exact date; no stale signal fallback.
    requested_date = report_date

    # ── EGX Market Calendar: detect holiday / non-trading day ─────────────
    # Import lazily to avoid circular dependency at module level.
    try:
        import sys as _sys, os as _os
        _cal_dir = _os.path.dirname(__file__)
        if _cal_dir not in _sys.path:
            _sys.path.insert(0, _cal_dir)
        from event_calendar import is_trading_day as _is_td, holiday_name as _hname
        _today_is_trading = _is_td(requested_date)
        _today_holiday    = _hname(requested_date)  # str or None
    except Exception:
        _today_is_trading = True
        _today_holiday    = None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    result = {
        "market_card": None,
        "stock_cards": [],
        "watchlist_card": None,
        "explosion_radar_card": None,    # NEW: top ML picks by ensemble_prob
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
        # market_return_20d is stored as a fraction (e.g. -0.207 = -20.7%).
        # Multiply by 100 to convert to percentage for display.
        _raw_ret20 = regime_row["market_return_20d"] if regime_row else 0
        ret20 = (_raw_ret20 * 100) if _raw_ret20 is not None else 0
        breadth_pct = regime_row["breadth_pct"] * 100 if regime_row and regime_row["breadth_pct"] else 0

        # 5-day market return: sample from regime_history if available,
        # else approximate from EGX30 proxy using ohlcv_history for a few liquid symbols.
        # SQLite does not support window functions in older builds — use a correlated subquery.
        try:
            # Use the 6th-most-recent bar_time for each symbol via correlated subquery
            ret5_row = conn.execute(
                """
                SELECT AVG(h2.close / h1.close - 1) AS ret5
                FROM   ohlcv_history h2
                JOIN   ohlcv_history h1
                    ON h1.symbol = h2.symbol
                WHERE  h2.bar_time = (
                           SELECT MAX(bar_time) FROM ohlcv_history
                           WHERE symbol = h2.symbol AND close > 0
                       )
                  AND  h1.bar_time = (
                           SELECT bar_time FROM ohlcv_history
                           WHERE symbol = h1.symbol AND close > 0
                           ORDER BY bar_time DESC
                           LIMIT 1 OFFSET 5
                       )
                  AND  h1.close > 0
                  AND  h2.symbol IN (
                           SELECT symbol FROM ohlcv_history
                           GROUP BY symbol HAVING COUNT(*) >= 10
                           LIMIT 50
                       )
                """
            ).fetchone()
            raw5 = ret5_row[0] if ret5_row and ret5_row[0] is not None else None
            # Sanity check: 5-day market return should be between -20% and +20%
            ret5 = raw5 * 100 if (raw5 is not None and abs(raw5) < 0.20) else ret20 * 0.25
        except Exception:
            ret5 = ret20 * 0.25  # rough estimate

        # Client-facing opportunities: final_signals is the product gate.
        # Never fall back to legacy research signals or an older signal date.
        gp_rows = conn.execute(
            "SELECT f.* FROM final_signals f "
            "LEFT JOIN data_quality_flags dq "
            "  ON f.symbol=dq.symbol AND dq.issue_type='UNIT_ERROR' "
            "WHERE f.actionable=1 "
            "AND f.veto_reason IS NULL "
            "AND f.trade_date=? "
            "AND dq.id IS NULL "
            "ORDER BY f.score DESC LIMIT 5",
            (requested_date,)
        ).fetchall()

        if gp_rows:
            symbols = [r["symbol"] for r in gp_rows]
            placeholders = ",".join("?" for _ in symbols)
            forecast_date_row = conn.execute(
                "SELECT MAX(forecast_date) FROM stock_tomorrow_forecast"
            ).fetchone()
            forecast_date = forecast_date_row[0] if forecast_date_row else None
            if forecast_date and symbols:
                bearish_rows = conn.execute(
                    f"""
                    SELECT symbol
                    FROM stock_tomorrow_forecast
                    WHERE forecast_date=?
                      AND direction='DOWN'
                      AND symbol IN ({placeholders})
                    """,
                    [forecast_date, *symbols],
                ).fetchall()
                bearish = {r[0] for r in bearish_rows}
                if bearish:
                    gp_rows = [r for r in gp_rows if r["symbol"] not in bearish]

        gate_passed = len(gp_rows)
        no_actionable_guard = gate_passed == 0

        # Posture
        if regime == "BEAR":
            posture = "DEFENSIVE"
        elif regime == "BULL" and ret20 > 5:
            posture = "AGGRESSIVE"
        else:
            posture = "CAUTIOUS"

        # Keep ML radar internal. Client cards must match the final text
        # opportunities exactly, so supplemental radar cards are suppressed.
        top_ml = []
        if False and gate_passed > 0:
            top_ml = conn.execute(
                """
                SELECT symbol, explosion_prob
                FROM   explosion_predictions
                WHERE  pred_date = ?
                  AND  reliability_flag IN ('OK', 'ACCEPTABLE')
                ORDER  BY explosion_prob DESC
                LIMIT  3
                """,
                (requested_date,)
            ).fetchall()

        top_names = " | ".join(f"{r[0]} {r[1]*100:.0f}%" for r in top_ml) if top_ml else ""

        # Note: context-aware, shows top ML picks and reliability.
        # Store RAW Arabic (not pre-shaped) — build_market_card calls _ar() internally.
        # ── Holiday / non-trading day note ───────────────────────────────────
        if _today_holiday:
            # Market is closed today — be explicit and reassuring
            note = (f"🎉 {_today_holiday} — السوق مغلق اليوم. "
                    "لا توجد فرص تنفيذية جديدة لهذا التاريخ.")
        elif not _today_is_trading:
            # Weekend (Fri/Sat) — normal
            dow_ar = {4: "الجمعة", 5: "السبت"}.get(date.fromisoformat(requested_date).weekday(), "إجازة")
            note = (f"يوم {dow_ar} — السوق مغلق. "
                    "لا توجد فرص تنفيذية جديدة لهذا التاريخ.")
        elif regime == "BEAR":
            if gate_passed == 0:
                # Explicitly explain the BEAR_REGIME_FILTER so clients understand
                if top_names:
                    note = (f"السوق في مرحلة دب ({ret20:+.1f}%). الإشارات محجوبة — "
                            f"فلتر السوق الهابط فعّال. رادار الانفجار: {top_names}.")
                else:
                    note = (f"السوق في مرحلة دب ({ret20:+.1f}%). "
                            "جميع الإشارات محجوبة بواسطة فلتر السوق الهابط. تجنب الدخول الجديد.")
            else:
                # Rare: BEAR exception signals passed gate (ml≥85 AND rsi≤35)
                if top_names:
                    note = f"سوق دب — {gate_passed} فرصة استثنائية. رادار: {top_names}. حجم محدود."
                else:
                    note = f"سوق دب — {gate_passed} فرصة استثنائية. احرص على وقف الخسارة."
        elif gate_passed > 0:
            note = f"اكتُشفت {gate_passed} فرصة تنفيذية مؤكدة اليوم."
        else:
            note = ("لا توجد فرص تنفيذية مؤكدة لنفس تاريخ التقرير. "
                    "لن تُنشأ بطاقات أسهم أو مراقبة أو رادار.")

        market_data = {
            "report_date":    requested_date,   # Always today — client sees correct date
            "signal_date":    requested_date,    # Same-date only; no stale fallback
            "signal_source":  "final_signals",
            "regime":         regime,
            "market_return_20d": ret20,
            "market_return_5d":  ret5,
            "breadth_pct":    breadth_pct,
            "gate_passed":    gate_passed,
            "posture":        posture,
            "note":           note,
            "is_trading_day": _today_is_trading,
            "holiday_name":   _today_holiday,   # None when market open
            "no_actionable_guard": no_actionable_guard,
            "client_opportunity_guard": (
                "PASS: final_signals actionable=1 for report_date"
                if gate_passed > 0 else
                "BLOCK: no same-date final_signals actionable=1; stock/watchlist/radar suppressed"
            ),
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
                    enriched["report_date"] = requested_date
                    card_bytes = build_stock_card(enriched)
                    if card_bytes:
                        result["stock_cards"].append({
                            "symbol":           enriched["symbol"],
                            "type":             enriched["signal_type"],
                            "ml_score":         enriched.get("ml_score", 0),
                            "ml_tier":          enriched.get("ml_tier", "LOW"),
                            "explosion_prob_ml": enriched.get("explosion_prob_ml"),
                            "explosion_tier_ml": enriched.get("explosion_tier_ml"),
                            "bytes":            card_bytes,
                        })
                except Exception as e:
                    print(f"[card] Error building card for {row['symbol']}: {e}")
        else:
            print("[card_guard] No same-date final_signals actionable=1 — suppressing stock/watchlist/radar cards")

        # ── Explosion Radar card (same-date actionable days only) ────────────
        # Uses explosion_predictions with reliability IN ('OK','ACCEPTABLE') only.
        # Suppressed entirely when no same-date actionable final signal exists.
        radar_pred_date = requested_date
        radar_rows = []
        if False and gate_passed > 0:
            radar_rows = conn.execute(
                """
                SELECT ep.symbol, ep.explosion_prob, ep.confidence_tier,
                       ep.model_version, ep.reliability_flag
                FROM   explosion_predictions ep
                WHERE  ep.pred_date = ?
                  AND  ep.reliability_flag IN ('OK', 'ACCEPTABLE')
                ORDER  BY ep.explosion_prob DESC
                LIMIT  5
                """,
                (radar_pred_date,)
            ).fetchall()

        if radar_rows:
            # Enrich with price, RSI, momentum
            top_picks = []
            for rr in radar_rows:
                sym = rr[0]
                price_row = conn.execute(
                    "SELECT close FROM ohlcv_history WHERE symbol=? "
                    "ORDER BY bar_time DESC LIMIT 1",
                    (sym,)
                ).fetchone()
                ind_row = conn.execute(
                    "SELECT rsi14, momentum_5d FROM indicators_cache "
                    "WHERE symbol=? ORDER BY bar_date DESC LIMIT 1",
                    (sym,)
                ).fetchone()
                top_picks.append({
                    "symbol":  sym,
                    "prob":    float(rr[1]) if rr[1] else 0.0,
                    "tier":    rr[2] or "LOW",
                    "price":   float(price_row[0]) if price_row and price_row[0] else 0.0,
                    "rsi":     float(ind_row["rsi14"])       if ind_row and ind_row["rsi14"]       else 50.0,
                    "mom5d":   float(ind_row["momentum_5d"]) if ind_row and ind_row["momentum_5d"] else 0.0,
                })

            # Determine reliability from first row (all from same date, same flag)
            reliability = radar_rows[0][4] if radar_rows[0][4] else "UNKNOWN"
            model_ver   = radar_rows[0][3] if radar_rows[0][3] else "unknown"
            universe_n  = conn.execute(
                "SELECT COUNT(*) FROM explosion_predictions WHERE pred_date=?",
                (radar_pred_date,)
            ).fetchone()[0]

            radar_data = {
                "report_date":    radar_pred_date,
                "regime":         regime,
                "top_picks":      top_picks,
                "model_version":  model_ver,
                "reliability":    reliability,
                "total_universe": universe_n,
            }

            try:
                radar_bytes = build_explosion_radar_card(radar_data)
                result["explosion_radar_card"] = radar_bytes
                result["summary"]["radar_top"] = [p["symbol"] for p in top_picks]
                result["summary"]["radar_date"] = radar_pred_date
            except Exception as e_radar:
                print(f"[card] Error building explosion radar card: {e_radar}")

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
