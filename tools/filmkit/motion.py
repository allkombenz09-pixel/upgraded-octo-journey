"""Shot renderers: stills with documentary camera moves, Kling clips, graphics."""
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # PIL fallback is ~20x slower but works
    cv2 = None

from . import graphics

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".webm")

ZOOM = 1.16  # max zoom for push/pull; pans run at PAN_ZOOM
PAN_ZOOM = 1.14


def find_asset(frames_dir, shot_id, exts):
    if not frames_dir:
        return None
    for ext in exts:
        for name in (shot_id, shot_id.lstrip("0") or shot_id):
            p = Path(frames_dir) / f"{name}{ext}"
            if p.exists():
                return p
    return None


def ease(p):
    p = min(1.0, max(0.0, p))
    return 0.8 * p + 0.2 * (0.5 - 0.5 * math.cos(math.pi * p))


def camera_path(move, fx, fy):
    """Return f(p) -> (zoom, u, v).

    u, v in [0, 1] place the view inside the room the zoom leaves free:
    0 = left/top edge, 0.5 = centred, 1 = right/bottom edge.
    """
    fx = 0.5 if fx is None else min(max(fx, 0.0), 1.0)
    fy = 0.5 if fy is None else min(max(fy, 0.0), 1.0)
    lerp = lambda a, b, p: a + (b - a) * p
    if move == "push-in":
        return lambda p: (lerp(1.0, ZOOM, p), fx, fy)
    if move == "pull-out":
        return lambda p: (lerp(ZOOM, 1.0, p), fx, fy)
    if move == "pan-left":
        return lambda p: (PAN_ZOOM, lerp(1.0, 0.0, p), fy)
    if move == "pan-right":
        return lambda p: (PAN_ZOOM, lerp(0.0, 1.0, p), fy)
    if move == "tilt-up":
        return lambda p: (PAN_ZOOM, fx, lerp(1.0, 0.0, p))
    if move == "tilt-down":
        return lambda p: (PAN_ZOOM, fx, lerp(0.0, 1.0, p))
    if move == "drift":
        return lambda p: (lerp(1.06, 1.11, p), lerp(1 - fx, fx, p), lerp(1 - fy, fy, p))
    return lambda p: (lerp(1.03, 1.045, p), 0.5, 0.5)  # static: barely-there breathing


def warp(src, a, left, top, W, H):
    """Sample a W×H view: output pixel (x, y) <- source (a·x + left, a·y + top)."""
    if cv2 is not None:
        m = np.array([[a, 0, left], [0, a, top]], np.float64)
        out = cv2.warpAffine(src, m, (W, H), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
        return out.astype(np.float32)
    img = src.transform((W, H), Image.AFFINE, (a, 0, left, 0, a, top), Image.BICUBIC)
    return np.asarray(img, np.float32)


class StillShot:
    def __init__(self, img, W, H, dur, move, fx=None, fy=None, overlays=(), lead=0.0):
        """lead: seconds of motion before t=0 (used when a crossfade starts early)."""
        self.W, self.H, self.dur, self.lead = W, H, dur, lead
        iw, ih = img.size
        cover = max(W / iw, H / ih)
        self.base = cover * ZOOM * 1.02
        self.src = img.convert("RGB").resize((max(1, round(iw * self.base)), max(1, round(ih * self.base))), Image.LANCZOS)
        self.src_np = np.asarray(self.src) if cv2 is not None else None
        self.cover = cover
        self.iw, self.ih = iw, ih
        self.path = camera_path(move, fx, fy)
        self.overlays = list(overlays)

    def frame(self, t):
        p = ease((t + self.lead) / max(self.dur + self.lead, 1e-3))
        z, u, v = self.path(p)
        sw, sh = self.src.size
        vw, vh = self.W / (self.cover * z) * self.base, self.H / (self.cover * z) * self.base
        left = max(0.0, sw - vw) * u
        top = max(0.0, sh - vh) * v
        a = vw / self.W
        frame = warp(self.src_np if cv2 is not None else self.src, a, left, top, self.W, self.H)
        for ov in self.overlays:
            frame = ov.apply(frame, t)
        return frame


class ClipShot:
    """A Kling (or any) video clip, scaled to frame; holds the last frame if short."""

    def __init__(self, path, W, H, dur, fps, overlays=()):
        self.W, self.H, self.fps = W, H, fps
        n = int(math.ceil(dur * fps)) + 2
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},"
              f"tpad=stop_mode=clone:stop_duration={dur + 1:.2f}")
        cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf", vf, "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        self.frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
        self.overlays = list(overlays)

    def frame(self, t):
        i = min(len(self.frames) - 1, max(0, int(round(t * self.fps))))
        frame = self.frames[i].astype(np.float32)
        for ov in self.overlays:
            frame = ov.apply(frame, t)
        return frame


class EndCard:
    def __init__(self, W, H):
        self.img = graphics.end_card(W, H).astype(np.float32)

    def frame(self, t):
        return self.img.copy()


def make_shot(shot, W, H, dur, fps, frames_dir, ctx, lead=0.0):
    if shot.get("kind") == "endcard":
        return EndCard(W, H)
    if shot.get("kind") == "graphic":
        return graphics.GraphicCard(shot, W, H, dur, ctx)
    overlays = graphics.build_overlays(shot.get("overlays") or [], W, H, dur, ctx)
    clip = find_asset(frames_dir, shot["id"], VIDEO_EXTS)
    if clip:
        return ClipShot(clip, W, H, dur, fps, overlays)
    still = find_asset(frames_dir, shot["id"], IMAGE_EXTS)
    img = Image.open(still) if still else graphics.placeholder(shot, W, H)
    return StillShot(img, W, H, dur, shot.get("camera", "push-in"), shot.get("focus_x"), shot.get("focus_y"), overlays, lead)
