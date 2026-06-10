"""
telegram_card_styles.py — Color palettes, fonts, and layout constants
for EGX Telegram visual trading cards.
"""

from dataclasses import dataclass, field
from typing import Tuple

# ─── Type alias ────────────────────────────────────────────────────────────────
Color = Tuple[int, int, int]
RGBA  = Tuple[int, int, int, int]

# ─── Card dimensions ───────────────────────────────────────────────────────────
CARD_W        = 900  # px
CARD_H        = 560  # px  (stock card)
MARKET_CARD_H = 420  # px  (market status card)
WATCH_CARD_H  = 520  # px  (watchlist card)
RADAR_CARD_H  = 500  # px  (explosion radar card — top ML picks by ensemble_prob)

# ─── Corner radius ─────────────────────────────────────────────────────────────
RADIUS = 22

# ─── Font paths ────────────────────────────────────────────────────────────────
# Primary: SF Arabic (macOS system font — properly renders Arabic ligatures)
# Fallback chain: SFArabic → Geeza Pro → system default
import os
_BASE = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "fonts")

def _find_font(candidates):
    """Return first existing font from list of candidate paths."""
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None  # Pillow will use default

# ─── Bilingual font strategy ───────────────────────────────────────────────────
# Arial Unicode covers Arabic + Latin + numbers in one font — no per-character
# font-switching needed. Arial Bold is used for headings (clear weight contrast).
#
# Font hierarchy:
#   FONT_BOLD    → Arial Bold  (headings, symbols, prices — heavy weight)
#   FONT_SEMI    → Arial Bold  (semi-bold labels)
#   FONT_REGULAR → Arial Unicode (body, notes — includes all Unicode blocks)
#   FONT_ARABIC  → Arial Unicode (same font; alias kept for code clarity)

FONT_BOLD    = _find_font([
    os.path.join(_BASE, "Arial-Bold.ttf"),
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
])
FONT_SEMI    = _find_font([
    os.path.join(_BASE, "Arial-Bold.ttf"),
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
])
FONT_REGULAR = _find_font([
    os.path.join(_BASE, "Arial-Unicode.ttf"),
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
])
# Arabic alias — same as FONT_REGULAR (Arial Unicode has full Arabic block)
FONT_ARABIC  = FONT_REGULAR

# ─── Palette: Dark premium theme ───────────────────────────────────────────────
@dataclass(frozen=True)
class Palette:
    # Backgrounds
    bg_dark:      Color = (14, 17, 23)        # deep navy-black
    bg_card:      Color = (20, 24, 33)        # card surface
    bg_panel:     Color = (28, 33, 45)        # inner panels
    bg_pill:      Color = (38, 44, 60)        # small badges

    # Text
    text_primary:  Color = (240, 242, 248)    # near-white
    text_secondary:Color = (160, 168, 190)    # muted
    text_dim:      Color = (100, 108, 130)    # very muted

    # Accent — signal type
    bull_green:   Color = (34, 197, 94)       # #22c55e
    bear_red:     Color = (239, 68, 68)       # #ef4444
    swing_gold:   Color = (251, 191, 36)      # #fbbf24
    invest_blue:  Color = (99, 179, 237)      # #63b3ed
    scalp_pink:   Color = (236, 72, 153)      # #ec4899
    neutral_gray: Color = (107, 114, 128)     # #6b7280

    # Targets
    t1_color:     Color = (52, 211, 153)      # teal
    t2_color:     Color = (251, 191, 36)      # gold
    t3_color:     Color = (167, 139, 250)     # purple
    sl_color:     Color = (248, 113, 113)     # soft red
    entry_color:  Color = (96, 165, 250)      # soft blue

    # Regime backgrounds (gradient-like via solid)
    regime_bear:  Color = (60, 20, 20)
    regime_bull:  Color = (20, 50, 30)
    regime_chop:  Color = (40, 40, 20)

    # Sparkline
    spark_up:     Color = (34, 197, 94)
    spark_dn:     Color = (239, 68, 68)
    spark_grid:   Color = (40, 46, 60)

PALETTE = Palette()

# ─── Signal-type configs ───────────────────────────────────────────────────────
SIGNAL_CONFIGS = {
    "SCALP": {
        "label_ar":  "اسكالبينج",
        "label_en":  "SCALP",
        "emoji":     "⚡",
        "accent":    PALETTE.scalp_pink,
        "horizon":   "ساعات - يومين",
        "risk_note": "مخاطرة عالية / حجم صغير",
    },
    "SWING": {
        "label_ar":  "سوينج",
        "label_en":  "SWING",
        "emoji":     "🔄",
        "accent":    PALETTE.swing_gold,
        "horizon":   "أسابيع",
        "risk_note": "وقف خسارة إلزامي",
    },
    "INVESTMENT": {
        "label_ar":  "استثمار",
        "label_en":  "INVEST",
        "emoji":     "💎",
        "accent":    PALETTE.invest_blue,
        "horizon":   "أشهر",
        "risk_note": "تراكم تدريجي",
    },
    "BEAR_EXCEPTION": {
        "label_ar":  "فرصة استثنائية",
        "label_en":  "BEAR-OPP",
        "emoji":     "🎯",
        "accent":    PALETTE.bull_green,
        "horizon":   "أسابيع",
        "risk_note": "تحوط / حجم محدود",
    },
}

# ─── Regime display ────────────────────────────────────────────────────────────
REGIME_DISPLAY = {
    "BULL":    {"ar": "ثور 🟢",  "color": PALETTE.bull_green,  "bg": PALETTE.regime_bull},
    "BEAR":    {"ar": "دب 🔴",   "color": PALETTE.bear_red,    "bg": PALETTE.regime_bear},
    "CHOPPY":  {"ar": "جانبي 🟡", "color": PALETTE.swing_gold, "bg": PALETTE.regime_chop},
    "UNKNOWN": {"ar": "غير محدد", "color": PALETTE.neutral_gray,"bg": PALETTE.bg_panel},
}

# ─── Behavioral badge ─────────────────────────────────────────────────────────
BEHAVIOR_BADGE = {
    "STEADY":    {"ar": "مستقر",   "color": PALETTE.invest_blue},
    "EXPLOSIVE": {"ar": "انفجاري", "color": PALETTE.scalp_pink},
    "VOLATILE":  {"ar": "متذبذب",  "color": PALETTE.swing_gold},
}
