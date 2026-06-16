"""
Visual design tokens: the dark "media browser" palette, fonts, and a small
hex-to-rgb helper. Pure data, imported by the UI and widgets.
"""

LOGO_SIZE = (56, 56)

# Palette -------------------------------------------------------------------- #
BG          = "#0E0F16"   # app background
BG_SUNKEN   = "#0A0B11"   # scroll trough / behind content
SURFACE     = "#181B25"   # category cards
SURFACE_2   = "#14161E"   # list rows
HOVER       = "#222634"   # hovered card
ROW_HOVER   = "#1E2230"   # hovered row
LOGO_BG     = "#20242F"   # logo tile background
BORDER      = "#262A38"   # subtle borders
BORDER_SOFT = "#1E2230"
ACCENT      = "#2DD4BF"   # teal, primary accent / progress / highlights
ACCENT_HOV  = "#5EEAD4"   # lighter teal (button hover)
ACCENT_INK  = "#04211E"   # dark text on accent buttons
ACCENT_DIM  = "#1C3B38"   # accent-tinted surface (pills)
INDIGO      = "#A5B4FC"   # secondary accent text
LIVE        = "#FB7185"   # live / on-air dot
TEXT        = "#F2F4F8"   # primary text
TEXT_MUTED  = "#A7AEC0"   # secondary text
TEXT_FAINT  = "#6B7180"   # captions / tertiary

# Typography ----------------------------------------------------------------- #
FONT_TITLE   = ("Segoe UI Semibold", 21)
FONT_SUB     = ("Segoe UI", 11)
FONT_H2      = ("Segoe UI Semibold", 16)
FONT_CARD    = ("Segoe UI Semibold", 13)
FONT_ROW     = ("Segoe UI Semibold", 12)
FONT_BODY    = ("Segoe UI", 11)
FONT_SMALL   = ("Segoe UI", 10)
FONT_CAPTION = ("Segoe UI", 9)
FONT_PILL    = ("Segoe UI Semibold", 8)
FONT_BTN     = ("Segoe UI Semibold", 10)
FONT_BTN_LG  = ("Segoe UI Semibold", 11)


def rgb(hex_color: str):
    """'#RRGGBB' to an (r, g, b) tuple."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
