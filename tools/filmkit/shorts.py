"""Vertical 9:16 Shorts cut from the film's images, with burned-in word captions."""
import math
import re

import numpy as np
from PIL import Image, ImageDraw

from . import graphics, motion, music, style, timeline
from .style import font
from .voice import SR


def vertical_source(img, crop_x, W, H):
    """Crop the 16:9 still to a 9:16 window around crop_x (0..1)."""
    iw, ih = img.size
    cw = min(iw, int(round(ih * W / H)))
    cx = (0.5 if crop_x is None else crop_x) * iw
    left = int(min(max(cx - cw / 2, 0), iw - cw))
    return img.crop((left, 0, left + cw, ih))


class FitShot:
    """16:9 frame centred on a blurred, darkened copy of itself (for graphics)."""

    def __init__(self, inner, W, H):
        self.inner, self.W, self.H = inner, W, H

    def frame(self, t):
        f = self.inner.frame(t)
        img = Image.fromarray(np.clip(f, 0, 255).astype(np.uint8))
        bg = img.resize((int(self.H * img.width / img.height), self.H)).crop((0, 0, self.W, self.H))
        from PIL import ImageFilter
        bg = Image.eval(bg.filter(ImageFilter.GaussianBlur(30)), lambda v: int(v * 0.45))
        fg = img.resize((self.W, int(self.W * img.height / img.width)), Image.LANCZOS)
        bg.paste(fg, (0, (self.H - fg.height) // 2))
        return np.asarray(bg, np.float32).copy()


def words_timed(beats):
    """Spread each beat's words over its spoken span, weighted by length."""
    out = []
    for b in beats:
        words = b["text"].replace("...", "… ").split()
        weights = [len(re.sub(r"\W", "", w)) + 2 + (4 if re.search(r"[.!?…,]$", w) else 0) for w in words]
        tot = sum(weights) or 1
        t = b["start"]
        for w, k in zip(words, weights):
            d = (b["end"] - b["start"]) * k / tot
            out.append((t, t + d, w))
            t += d
    return out


def caption_groups(words, max_chars=16):
    groups, cur = [], []
    for w in words:
        text = " ".join(x[2] for x in cur + [w])
        if cur and (len(text) > max_chars or re.search(r"[.!?…,]$", cur[-1][2])):
            groups.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        groups.append(cur)
    return groups


class Captions:
    def __init__(self, beats, W, H):
        self.W, self.H, self.s = W, H, W / 1080
        self.groups = caption_groups(words_timed(beats))
        self.font = font("sans-black", 84 * self.s)

    def apply(self, frame, t):
        g = next((g for g in self.groups if g[0][0] - 0.05 <= t < g[-1][1] + 0.05), None)
        if g is None:
            return frame
        s = self.s
        c = Image.new("RGBA", (self.W, int(260 * s)))
        d = ImageDraw.Draw(c)
        words = [w[2].strip("…").upper() if w[2] != "…" else "…" for w in g]
        widths = [d.textlength(w + " ", font=self.font) for w in words]
        total = sum(widths) - d.textlength(" ", font=self.font)
        lines = [(words, widths)]
        if total > self.W * 0.86 and len(words) > 1:
            k = math.ceil(len(words) / 2)
            lines = [(words[:k], widths[:k]), (words[k:], widths[k:])]
        y = 20 * s
        idx = 0
        for lw, lwid in lines:
            x = (self.W - (sum(lwid) - d.textlength(" ", font=self.font))) / 2
            for w, wd in zip(lw, lwid):
                active = g[idx][0] <= t < g[idx][1] + 0.08
                d.text((x, y), w, font=self.font, fill=style.MUSTARD if active else (255, 255, 255),
                       stroke_width=int(9 * s), stroke_fill=(10, 14, 24))
                x += wd
                idx += 1
            y += 100 * s
        pop = min(1.0, (t - g[0][0]) / 0.12)
        graphics.Layer(c, 0, int(self.H * 0.60)).over(frame, 1.0, dy=(1 - pop) * 14 * s)
        return frame


def build_short(short, ep_shots, voice_audio, spans, W, H, fps, frames_dir):
    """Returns (timed cuts with renderers, beats on the short's clock)."""
    beats = [dict(id=b["id"], text=b["text"], chapter="", start=s + 0.25, end=e + 0.25)
             for b, (s, e) in zip(short["beats"], spans)]
    by_id = {s["id"]: s for s in ep_shots}
    cuts = []
    for c in short.get("cuts") or []:
        base = dict(by_id.get(c["shot"], {"id": c["shot"], "kind": "image", "prompt": ""}))
        base.update(id=c["shot"], beats=c.get("beats") or [], crop_x=c.get("crop_x"), overlays=[])
        cuts.append(base)
    timed = timeline.shot_times(cuts, beats, log=lambda *_: None)
    timed = [s for s in timed if s.get("kind") != "endcard"]
    if timed:
        timed[-1]["end"] = beats[-1]["end"] + 0.5
    return timed, beats


def make_renderer(shot, W, H, fps, frames_dir):
    dur = shot["end"] - shot["start"]
    if shot.get("kind") == "graphic":
        return FitShot(graphics.GraphicCard(shot, 1920, 1080, dur, {}), W, H)
    clip = motion.find_asset(frames_dir, shot["id"], motion.VIDEO_EXTS)
    if clip:
        return FitShot(motion.ClipShot(clip, 1920, 1080, dur, fps), W, H)
    still = motion.find_asset(frames_dir, shot["id"], motion.IMAGE_EXTS)
    img = Image.open(still).convert("RGB") if still else graphics.placeholder(shot, 1920, 1080)
    src = vertical_source(img, shot.get("crop_x"), W, H)
    move = "push-in" if shot.get("camera") not in ("pull-out",) else "pull-out"
    return motion.StillShot(src, W, H, dur, move, 0.5, 0.42)
