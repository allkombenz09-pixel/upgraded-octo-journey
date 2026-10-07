"""Channel look: palette, fonts and text helpers shared by all graphics."""
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

ASSETS = Path(__file__).resolve().parent / "assets"
FONTS = ASSETS / "fonts"

# Muted warm palette from the image style tail: navy, cream, mustard, terracotta.
NAVY = (31, 42, 68)
NAVY_DEEP = (20, 27, 45)
NAVY_LIGHT = (47, 62, 96)
CREAM = (243, 233, 210)
MUSTARD = (217, 164, 65)
TERRACOTTA = (196, 98, 63)
SLATE = (120, 134, 158)

FONT_FILES = {
    "serif-black": "PlayfairDisplay-Black.ttf",
    "serif-bold": "PlayfairDisplay-Bold.ttf",
    "serif-italic": "PlayfairDisplay-Italic.ttf",
    "serif-bold-italic": "PlayfairDisplay-BoldItalic.ttf",
    "sans": "Inter-Regular.otf",
    "sans-medium": "Inter-Medium.otf",
    "sans-semibold": "Inter-SemiBold.otf",
    "sans-bold": "Inter-Bold.otf",
    "sans-black": "Inter-Black.otf",
    "sans-italic": "Inter-Italic.otf",
}


@lru_cache(maxsize=256)
def font(name, size):
    return ImageFont.truetype(str(FONTS / FONT_FILES[name]), max(1, int(round(size))))


def text_size(draw, text, fnt, spacing=0):
    if spacing:
        w = sum(draw.textlength(c, font=fnt) for c in text) + spacing * max(0, len(text) - 1)
    else:
        w = draw.textlength(text, font=fnt)
    asc, desc = fnt.getmetrics()
    return w, asc + desc


def draw_spaced(draw, xy, text, fnt, fill, spacing):
    """Draw text with extra letter spacing (for small-caps labels)."""
    x, y = xy
    for c in text:
        draw.text((x, y), c, font=fnt, fill=fill)
        x += draw.textlength(c, font=fnt) + spacing


def wrap(draw, text, fnt, max_width):
    """Greedy word wrap to a pixel width. Returns a list of lines."""
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and draw.textlength(trial, font=fnt) > max_width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines
