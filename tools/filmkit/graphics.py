"""Overlays, full-screen graphics, placeholders and end card.

Everything is drawn in the channel palette and scaled from a 1080p design
grid (s = height / 1080), so the same code renders the 720p animatic, the
1080p master and the vertical Shorts.
"""
import math
import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, features

from . import geo, style
from .style import font, wrap

FADE = 0.35
LNUM = ["lnum"] if features.check("raqm") else None


# ----------------------------------------------------------------- helpers
def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def envelope(t, t_in, t_out, fade=FADE):
    """0→1 fade in at t_in, 1→0 fade out ending at t_out."""
    if t < t_in or t > t_out:
        return 0.0
    return min(1.0, (t - t_in) / fade, (t_out - t) / fade)


def parse_detail(detail):
    """'map region=sweden pins=A,B route=A>B' → {'_': 'map', 'region': 'sweden', ...}"""
    out = {}
    detail = (detail or "").strip()
    head = re.match(r"^([^\s=]+)(?=\s|$)(?!\s*=)", detail)
    if head and "=" not in head.group(1):
        out["_"] = head.group(1)
        detail = detail[head.end():]
    for k, v in re.findall(r"(\w+)=(.*?)(?=\s+\w+=|$)", detail):
        out[k] = v.strip()
    return out


@lru_cache(maxsize=8)
def grain(w, h, seed=7, amount=10.0):
    """Static paper grain as a float32 (h, w, 1) additive layer."""
    rng = np.random.default_rng(seed)
    g = rng.normal(0, 1, (h // 2 + 1, w // 2 + 1)).astype(np.float32)
    img = Image.fromarray(((g * 40) + 128).clip(0, 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    return ((np.asarray(img, np.float32) - 128) / 40 * amount)[..., None]


@lru_cache(maxsize=8)
def vignette(w, h, strength=0.28):
    y, x = np.ogrid[-1:1:h * 1j, -1:1:w * 1j]
    r = np.sqrt((x * 0.9) ** 2 + (y * 1.1) ** 2)
    return (1 - strength * np.clip(r - 0.35, 0, None) ** 1.6)[..., None].astype(np.float32)


def backdrop(w, h):
    """Navy card background with a soft centre glow and paper grain (uint8 RGB)."""
    y, x = np.ogrid[-1:1:h * 1j, -1:1:w * 1j]
    r = np.sqrt(x ** 2 + (y * 1.2) ** 2)
    t = np.clip(1 - r / 1.4, 0, 1)[..., None]
    deep = np.array(style.NAVY_DEEP, np.float32)
    mid = np.array(style.NAVY, np.float32)
    img = deep + (mid - deep) * t + grain(w, h)
    return img.clip(0, 255).astype(np.uint8)


class Layer:
    """Premultiplied RGBA sprite placed at (x, y) on the frame."""

    def __init__(self, img, x=0, y=0):
        a = np.asarray(img.convert("RGBA"), np.float32) / 255.0
        self.rgb = a[..., :3] * a[..., 3:4]
        self.a = a[..., 3:4]
        self.x, self.y = int(x), int(y)

    @classmethod
    def from_canvas(cls, canvas):
        """Crop a full-frame RGBA canvas to its content bbox."""
        box = canvas.getbbox()
        if not box:
            return None
        return cls(canvas.crop(box), box[0], box[1])

    def over(self, frame, alpha=1.0, dx=0, dy=0):
        if alpha <= 0.003:
            return frame
        h, w = frame.shape[:2]
        x0, y0 = self.x + int(dx), self.y + int(dy)
        x1, y1 = x0 + self.a.shape[1], y0 + self.a.shape[0]
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if cx0 >= cx1 or cy0 >= cy1:
            return frame
        sl = (slice(cy0 - y0, cy1 - y0), slice(cx0 - x0, cx1 - x0))
        region = frame[cy0:cy1, cx0:cx1]
        frame[cy0:cy1, cx0:cx1] = region * (1 - self.a[sl] * alpha) + self.rgb[sl] * 255.0 * alpha
        return frame


def shadowed_text(canvas, xy, text, fnt, fill, shadow=0.55, radius=None, spacing=0, anchor="la"):
    """Draw legible text over an illustration: blurred dark halo + text."""
    s = fnt.size
    radius = radius or max(2, s // 9)
    mask = Image.new("L", canvas.size, 0)
    md = ImageDraw.Draw(mask)
    if spacing:
        style.draw_spaced(md, xy, text, fnt, 255, spacing)
    else:
        md.text(xy, text, font=fnt, fill=255, anchor=anchor)
    halo = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(radius))
    dark = Image.new("RGBA", canvas.size, style.NAVY_DEEP + (0,))
    dark.putalpha(halo.point(lambda v: int(v * shadow)))
    canvas.alpha_composite(dark)
    d = ImageDraw.Draw(canvas)
    if spacing:
        style.draw_spaced(d, xy, text, fnt, fill, spacing)
    else:
        d.text(xy, text, font=fnt, fill=fill, anchor=anchor)


# --------------------------------------------------------------- overlays
class Overlay:
    """One timed element on top of a shot. frame(t) composites in place."""

    hold = None  # seconds visible; None = whole shot

    def __init__(self, ov, W, H, dur, slot=0, ctx=None):
        self.ov, self.W, self.H, self.dur = ov, W, H, dur
        self.s = H / 1080
        self.slot = slot
        self.ctx = ctx or {}
        self.d = parse_detail(ov.get("detail", ""))
        self.t_in = 0.35 + 0.45 * slot
        self.t_out = dur - 0.25 if self.hold is None else min(dur - 0.25, self.t_in + self.hold)
        self.build()

    def build(self):
        pass

    def alpha(self, t):
        return envelope(t, self.t_in, self.t_out)

    def apply(self, frame, t):
        a = self.alpha(t)
        if a > 0 and self.layer is not None:
            rise = (1 - ease_out((t - self.t_in) / 0.6)) * 18 * self.s
            self.layer.over(frame, a, dy=rise)
        return frame


class DateOverlay(Overlay):
    hold = 3.6

    def build(self):
        s, c = self.s, Image.new("RGBA", (self.W, self.H))
        f = font("serif-black", 132 * s)
        x, y = 110 * s, self.H - 130 * s - f.size
        shadowed_text(c, (x, y), self.ov["text"], f, style.CREAM)
        ImageDraw.Draw(c).rectangle([x + 4 * s, y - 22 * s, x + 130 * s, y - 14 * s], fill=style.MUSTARD)
        self.layer = Layer.from_canvas(c)


class PlaceOverlay(Overlay):
    hold = 3.4

    def build(self):
        s, c = self.s, Image.new("RGBA", (self.W, self.H))
        f = font("sans-semibold", 30 * s)
        stacked = self.ctx.get("has_date")
        x = 112 * s
        y = self.H - (330 if stacked else 150) * s
        d = ImageDraw.Draw(c)
        r = 9 * s
        d.ellipse([x, y + 8 * s, x + 2 * r, y + 8 * s + 2 * r], fill=style.MUSTARD)
        shadowed_text(c, (x + 2 * r + 14 * s, y), self.ov["text"].upper(), f, style.CREAM, spacing=4 * s)
        self.layer = Layer.from_canvas(c)


class LowerThird(Overlay):
    hold = 4.2

    def build(self):
        s, c = self.s, Image.new("RGBA", (self.W, self.H))
        d = ImageDraw.Draw(c)
        name_f, role_f = font("serif-bold", 56 * s), font("sans-medium", 28 * s)
        raw = self.ov.get("detail", "") or ""
        role = self.d.get("role", "" if "=" in raw else raw)
        x, y = 110 * s, self.H - 230 * s
        w = max(d.textlength(self.ov["text"], font=name_f), d.textlength(role, font=role_f)) + 70 * s
        panel = Image.new("RGBA", (int(w), int(150 * s)), style.NAVY_DEEP + (200,))
        c.alpha_composite(panel, (int(x - 30 * s), int(y - 20 * s)))
        d.rectangle([x - 30 * s, y - 20 * s, x - 22 * s, y + 130 * s], fill=style.TERRACOTTA)
        d.text((x, y), self.ov["text"], font=name_f, fill=style.CREAM)
        if role:
            d.text((x, y + 74 * s), role, font=role_f, fill=style.MUSTARD)
        self.layer = Layer.from_canvas(c)


def split_number(value):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if not m:
        return None
    num = m.group(0)
    decimals = len(num.split(".")[1]) if "." in num else 0
    return value[:m.start()], float(num.replace(",", "")), decimals, value[m.end():], "," in num


def counted(value, p):
    parts = split_number(value)
    if not parts or p >= 1:
        return value
    pre, n, dec, post, commas = parts
    v = n * ease_out(p)
    txt = f"{v:,.{dec}f}" if commas else f"{v:.{dec}f}"
    return f"{pre}{txt}{post}"


class NumberOverlay(Overlay):
    hold = 4.6

    def build(self):
        self.value = self.d.get("value") or self.ov["text"]
        self.label = self.d.get("label") or (self.ov["text"] if self.d.get("value") else "")
        s = self.s
        self.num_f, self.lab_f = font("serif-black", 112 * s), font("sans-medium", 30 * s)
        tmp = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        self.lab_lines = wrap(tmp, self.label, self.lab_f, 560 * s)
        w = max([tmp.textlength(self.value, font=self.num_f)] + [tmp.textlength(l, font=self.lab_f) for l in self.lab_lines]) + 100 * s
        h = 170 * s + 42 * s * len(self.lab_lines)
        self.box = (int(self.W - w - 110 * s), int(self.H / 2 - h / 2), int(w), int(h))
        self.layer = None

    def apply(self, frame, t):
        a = self.alpha(t)
        if a <= 0:
            return frame
        s, (x, y, w, h) = self.s, self.box
        c = Image.new("RGBA", (w, h), style.NAVY_DEEP + (215,))
        d = ImageDraw.Draw(c)
        d.rectangle([0, 0, 8 * s, h], fill=style.MUSTARD)
        d.text((44 * s, 22 * s), counted(self.value, (t - self.t_in) / 1.3), font=self.num_f, fill=style.MUSTARD, features=LNUM)
        for i, line in enumerate(self.lab_lines):
            d.text((46 * s, 150 * s + i * 42 * s), line, font=self.lab_f, fill=style.CREAM)
        Layer(c, x, y).over(frame, a, dy=(1 - ease_out((t - self.t_in) / 0.6)) * 18 * s)
        return frame


class QuoteOverlay(Overlay):
    def build(self):
        s, c = self.s, Image.new("RGBA", (self.W, self.H), style.NAVY_DEEP + (150,))
        d = ImageDraw.Draw(c)
        f = font("serif-italic", 60 * s)
        lines = wrap(d, f"“{self.ov['text'].strip('“”\" ')}”", f, 1300 * s)
        by = self.d.get("by", "")
        total = len(lines) * 84 * s + (70 * s if by else 0)
        y = self.H / 2 - total / 2
        for line in lines:
            d.text((self.W / 2, y), line, font=f, fill=style.CREAM, anchor="ma")
            y += 84 * s
        if by:
            bf = font("sans-semibold", 28 * s)
            label = "— " + by.upper()
            w = style.text_size(d, label, bf, 4 * s)[0]
            style.draw_spaced(d, (self.W / 2 - w / 2, y + 30 * s), label, bf, style.MUSTARD, 4 * s)
        self.layer = Layer(c)


class ChapterOverlay(Overlay):
    hold = 3.4

    def build(self):
        s, c = self.s, Image.new("RGBA", (self.W, self.H), style.NAVY_DEEP + (165,))
        d = ImageDraw.Draw(c)
        n = self.ctx.get("chapter_no")
        small = font("sans-semibold", 28 * s)
        big = font("serif-black", 92 * s)
        label = f"CHAPTER {n}" if n else "CHAPTER"
        w = style.text_size(d, label, small, 6 * s)[0]
        style.draw_spaced(d, (self.W / 2 - w / 2, self.H / 2 - 120 * s), label, small, style.MUSTARD, 6 * s)
        lines = wrap(d, self.ov["text"], big, 1500 * s)
        y = self.H / 2 - 60 * s
        for line in lines:
            d.text((self.W / 2, y), line, font=big, fill=style.CREAM, anchor="ma")
            y += 110 * s
        d.rectangle([self.W / 2 - 60 * s, y + 24 * s, self.W / 2 + 60 * s, y + 30 * s], fill=style.MUSTARD)
        self.layer = Layer(c)

    def apply(self, frame, t):
        a = self.alpha(t)
        if a > 0:
            self.layer.over(frame, a)
        return frame


class TitleOverlay(ChapterOverlay):
    hold = 3.8

    def build(self):
        s, c = self.s, Image.new("RGBA", (self.W, self.H), style.NAVY_DEEP + (140,))
        d = ImageDraw.Draw(c)
        big = font("serif-black", 110 * s)
        small = font("sans-semibold", 30 * s)
        d.text((self.W / 2, self.H / 2 - 90 * s), self.ov["text"].upper(), font=big, fill=style.CREAM, anchor="ma")
        d.rectangle([self.W / 2 - 70 * s, self.H / 2 + 50 * s, self.W / 2 + 70 * s, self.H / 2 + 56 * s], fill=style.MUSTARD)
        raw = self.ov.get("detail", "") or ""
        sub = self.d.get("sub", "" if "=" in raw else raw).upper()
        if sub:
            w = style.text_size(d, sub, small, 6 * s)[0]
            style.draw_spaced(d, (self.W / 2 - w / 2, self.H / 2 + 80 * s), sub, small, style.MUSTARD, 6 * s)
        self.layer = Layer(c)


class MapLayer:
    """Animated map: base layer with slow zoom, pins popping in, route drawing."""

    def __init__(self, detail, W, H, dur, box=None, caption=None):
        self.W, self.H, self.dur = W, H, dur
        self.s = H / 1080
        self.d = detail
        self.region = detail.get("region", "sweden")
        self.pins = [p.strip() for p in detail.get("pins", "").split(",") if p.strip() in geo.PLACES]
        self.route = [p.strip() for p in detail.get("route", "").split(">") if p.strip() in geo.PLACES]
        hl = detail.get("highlight")
        self.highlight = [h.strip() for h in hl.split(",")] if hl else None
        self.box = box or (0, 0, W, H)
        self.caption = caption
        bx, by, bw, bh = self.box
        self.zoom = 1.12
        self.base, self.proj = geo.base_map(self.region, int(bw * self.zoom), int(bh * self.zoom), self.highlight)
        self.focus = self._focus()

    def _focus(self):
        pts = [geo.PLACES[p] for p in (self.route or self.pins)]
        if not pts:
            return 0.5, 0.5
        xs, ys = zip(*(self.proj(*p) for p in pts))
        return (sum(xs) / len(xs)) / self.base.width, (sum(ys) / len(ys)) / self.base.height

    def render(self, t):
        bx, by, bw, bh = self.box
        p = min(1.0, t / max(self.dur, 0.1))
        z = 1.0 + (self.zoom - 1.0) * (0.15 + 0.85 * p)  # relative zoom on the oversized base
        vw, vh = self.base.width / z, self.base.height / z
        fx, fy = self.focus
        cx = min(max(fx * self.base.width, vw / 2), self.base.width - vw / 2)
        cy = min(max(fy * self.base.height, vh / 2), self.base.height - vh / 2)
        cx = self.base.width / 2 + (cx - self.base.width / 2) * p
        cy = self.base.height / 2 + (cy - self.base.height / 2) * p
        left, top = cx - vw / 2, cy - vh / 2
        k = bw / vw
        from .motion import warp
        if not hasattr(self, "base_np"):
            self.base_np = np.asarray(self.base)
        img = Image.fromarray(warp(self.base_np, 1 / k, left, top, bw, bh).clip(0, 255).astype(np.uint8)).convert("RGBA")
        d = ImageDraw.Draw(img)
        s = self.s
        to_px = lambda name: ((self.proj(*geo.PLACES[name])[0] - left) * k, (self.proj(*geo.PLACES[name])[1] - top) * k)
        if len(self.route) >= 2:
            prog = ease_out((t - 0.8) / 1.8)
            segs = list(zip(self.route[:-1], self.route[1:]))
            for i, (a, b) in enumerate(segs):
                lp = min(1, max(0, prog * len(segs) - i))
                if lp <= 0:
                    continue
                (x0, y0), (x1, y1) = to_px(a), to_px(b)
                n = max(2, int(math.hypot(x1 - x0, y1 - y0) / (14 * s)))
                for j in range(int(n * lp)):
                    if j % 2 == 0:
                        u0, u1 = j / n, min((j + 1) / n, lp)
                        d.line([(x0 + (x1 - x0) * u0, y0 + (y1 - y0) * u0), (x0 + (x1 - x0) * u1, y0 + (y1 - y0) * u1)],
                               fill=style.MUSTARD, width=max(2, int(5 * s)))
        names = list(dict.fromkeys(self.pins + self.route))
        lf = font("sans-semibold", 30 * s)
        for i, name in enumerate(names):
            pt = 0.4 + 0.5 * i
            a = ease_out((t - pt) / 0.5)
            if a <= 0:
                continue
            x, y = to_px(name)
            r = 11 * s * (0.6 + 0.4 * a)
            pulse = (t - pt) % 2.0 / 2.0
            pr = r + 28 * s * pulse
            d.ellipse([x - pr, y - pr, x + pr, y + pr], outline=style.MUSTARD + (int(160 * (1 - pulse) * a),), width=max(1, int(2 * s)))
            d.ellipse([x - r, y - r, x + r, y + r], fill=style.MUSTARD + (int(255 * a),), outline=style.NAVY_DEEP, width=max(1, int(2 * s)))
            label = geo.LABELS.get(name, name)
            d.text((x + 20 * s, y - 20 * s), label, font=lf, fill=style.CREAM + (int(255 * a),),
                   stroke_width=max(1, int(3 * s)), stroke_fill=style.NAVY_DEEP + (int(220 * a),))
        if self.caption:
            cf = font("serif-bold", 54 * s)
            d.text((90 * s, bh - 150 * s), self.caption, font=cf, fill=style.CREAM,
                   stroke_width=max(1, int(4 * s)), stroke_fill=style.NAVY_DEEP)
        return img


class MapInset(Overlay):
    def build(self):
        s = self.s
        w, h = int(620 * s), int(400 * s)
        self.map = MapLayer(self.d, w, h, self.dur, box=(0, 0, w, h))
        self.pos = (int(self.W - w - 70 * s), int(70 * s))
        self.layer = None

    def apply(self, frame, t):
        a = self.alpha(t)
        if a <= 0:
            return frame
        s = self.s
        img = self.map.render(t)
        border = Image.new("RGBA", (img.width + int(8 * s), img.height + int(8 * s)), style.CREAM + (255,))
        border.alpha_composite(img, (int(4 * s), int(4 * s)))
        Layer(border, *self.pos).over(frame, a, dy=(1 - ease_out((t - self.t_in) / 0.6)) * 18 * s)
        return frame


OVERLAYS = {
    "date": DateOverlay, "place": PlaceOverlay, "lower-third": LowerThird, "number": NumberOverlay,
    "quote": QuoteOverlay, "chapter": ChapterOverlay, "title": TitleOverlay, "map": MapInset,
}


def build_overlays(overlays, W, H, dur, ctx):
    types = [o.get("type") for o in overlays]
    ctx = dict(ctx, has_date="date" in types)
    out = []
    for i, ov in enumerate(overlays):
        cls = OVERLAYS.get(ov.get("type"))
        if cls:
            out.append(cls(ov, W, H, dur, slot=i if ov.get("type") not in ("chapter", "title", "quote") else 0, ctx=ctx))
    return out


# --------------------------------------------------------- graphic shots
class GraphicCard:
    """Full-screen editor graphic: map, big number, date, quote or chapter."""

    PRIORITY = ["map", "number", "date", "quote", "chapter", "title"]

    def __init__(self, shot, W, H, dur, ctx):
        self.W, self.H, self.dur, self.s = W, H, dur, H / 1080
        ovs = list(shot.get("overlays") or [])
        ovs.sort(key=lambda o: self.PRIORITY.index(o["type"]) if o.get("type") in self.PRIORITY else 99)
        self.main = ovs[0] if ovs else {"type": "title", "text": shot.get("id", "")}
        self.extra = build_overlays(ovs[1:], W, H, dur, ctx) if ovs[1:] else []
        self.bg = backdrop(W, H)
        self.d = parse_detail(self.main.get("detail", ""))
        kind = self.main["type"]
        if kind == "map":
            self.map = MapLayer(self.d, W, H, dur, caption=self.main.get("text"))
        elif kind in ("quote", "chapter", "title"):
            self.ov = OVERLAYS[kind](self.main, W, H, dur, ctx=ctx)
            self.ov.t_in, self.ov.t_out = 0.15, dur + 1
        self.kind = kind

    def frame(self, t):
        s = self.s
        if self.kind == "map":
            img = np.asarray(self.map.render(t).convert("RGB"), np.float32)
            img = img * vignette(self.W, self.H) + grain(self.W, self.H) * 0.6
        else:
            img = self.bg.astype(np.float32)
            if self.kind in ("number", "date"):
                img = self._big(img, t)
            else:
                img = self.ov.apply(img, t)
        for ov in self.extra:
            img = ov.apply(img, t)
        return img

    def _big(self, img, t):
        s = self.s
        c = Image.new("RGBA", (self.W, self.H))
        d = ImageDraw.Draw(c)
        if self.kind == "number":
            value = self.d.get("value") or self.main["text"]
            label = self.d.get("label") or (self.main["text"] if self.d.get("value") else "")
            big = font("serif-black", 230 * s)
            d.text((self.W / 2, self.H / 2 - 190 * s), counted(value, (t - 0.3) / 1.6), font=big, fill=style.MUSTARD, anchor="ma", features=LNUM)
        else:
            value = self.main["text"]
            label = self.d.get("label", "")
            big = font("serif-black", 260 * s)
            d.text((self.W / 2, self.H / 2 - 210 * s), value, font=big, fill=style.CREAM, anchor="ma", features=LNUM)
        d.rectangle([self.W / 2 - 70 * s, self.H / 2 + 95 * s, self.W / 2 + 70 * s, self.H / 2 + 101 * s], fill=style.MUSTARD)
        lf = font("sans-medium", 42 * s)
        y = self.H / 2 + 135 * s
        for line in wrap(d, label, lf, 1300 * s):
            d.text((self.W / 2, y), line, font=lf, fill=style.CREAM, anchor="ma")
            y += 58 * s
        src = self.d.get("source")
        if src:
            d.text((self.W / 2, self.H - 90 * s), src, font=font("sans", 24 * s), fill=style.SLATE, anchor="ma")
        a = ease_out(t / 0.5)
        return Layer(c).over(img, a, dy=(1 - a) * 20 * s)


def end_card(W, H, channel="How They Built It", line="Thanks for watching"):
    s = H / 1080
    c = Image.fromarray(backdrop(W, H)).convert("RGBA")
    d = ImageDraw.Draw(c)
    d.text((W / 2, 120 * s), channel.upper(), font=font("serif-black", 72 * s), fill=style.CREAM, anchor="ma")
    d.text((W / 2, 215 * s), line, font=font("serif-italic", 38 * s), fill=style.MUSTARD, anchor="ma")
    # Reserved areas for YouTube end-screen elements: video (left) and subscribe (right).
    lw = max(2, int(3 * s))
    d.rounded_rectangle([200 * s, 380 * s, 200 * s + 880 * s, 380 * s + 495 * s], radius=18 * s, outline=style.NAVY_LIGHT, width=lw)
    cx, cy, r = 1460 * s, 627 * s, 170 * s
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=style.NAVY_LIGHT, width=lw)
    return np.asarray(c.convert("RGB"), np.uint8)


def placeholder(shot, W, H):
    """Stand-in frame for an image that has not been generated yet."""
    s = H / 1080
    c = Image.fromarray(backdrop(W, H)).convert("RGBA")
    d = ImageDraw.Draw(c)
    sid = str(shot.get("id", "?"))
    d.text((120 * s, H / 2), sid, font=font("serif-black", 300 * s), fill=style.MUSTARD + (255,), anchor="lm")
    prompt = shot.get("prompt", "")
    prompt = prompt.split(", flat 2D editorial")[0].split(", flat 2D")[0]
    pf = font("serif-italic", 40 * s)
    lines = wrap(d, prompt, pf, 1080 * s)[:7]
    y = H / 2 - len(lines) * 27 * s
    for line in lines:
        d.text((720 * s, y), line, font=pf, fill=style.CREAM)
        y += 54 * s
    lab = font("sans-semibold", 24 * s)
    style.draw_spaced(d, (124 * s, H - 100 * s), f"PLACEHOLDER · frames/{sid}.png", lab, style.SLATE, 3 * s)
    return c.convert("RGB")
