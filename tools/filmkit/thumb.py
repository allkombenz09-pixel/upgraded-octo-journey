"""YouTube thumbnail: background still + big caps text on the left third."""
import re

import numpy as np
from PIL import Image, ImageDraw

from . import graphics, motion, style
from .style import font


def thumbnail(text, frames_dir, prompt, W=1280, H=720):
    src = motion.find_asset(frames_dir, "thumbnail", motion.IMAGE_EXTS)
    img = Image.open(src).convert("RGB") if src else graphics.placeholder({"id": "thumbnail", "prompt": prompt}, W, H)
    iw, ih = img.size
    k = max(W / iw, H / ih)
    img = img.resize((round(iw * k), round(ih * k)), Image.LANCZOS)
    img = img.crop(((img.width - W) // 2, (img.height - H) // 2, (img.width - W) // 2 + W, (img.height - H) // 2 + H))
    # darken the left side so the words pop
    x = np.linspace(1, 0, W)[None, :, None]
    shade = np.clip(x * 1.5 - 0.2, 0, 1) * 0.65
    arr = np.asarray(img, np.float32) * (1 - shade) + np.array(style.NAVY_DEEP, np.float32) * shade
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(img)
    words = text.upper().split()
    lines, cur = [], []
    for w in words:  # at most ~2 words per line
        if len(cur) == 2 or (cur and len(" ".join(cur + [w])) > 9):
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    size = 150 if len(lines) <= 2 else 120
    f = font("sans-black", size)
    while max(d.textlength(l, font=f) for l in lines) > W * 0.55 and size > 60:
        size -= 6
        f = font("sans-black", size)
    y = (H - len(lines) * size * 1.02) / 2
    for line in lines:
        hot = bool(re.search(r"[\d$€]", line))
        d.text((64, y), line, font=f, fill=style.MUSTARD if hot else style.CREAM, stroke_width=10, stroke_fill=style.NAVY_DEEP)
        y += size * 1.02
    return img.convert("RGB")
