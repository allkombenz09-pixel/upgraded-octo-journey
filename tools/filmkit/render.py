"""Render the film: shots + transitions -> video chunks in parallel -> mux with audio."""
import math
import multiprocessing as mp
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import geo, motion
from .voice import SR

TRANSITION = {"crossfade": 0.8, "dip-to-black": 1.2, "whip": 0.32, "cut": 0.0}
OPEN_FADE = 0.7


def chapter_numbers(shots):
    n, out = 0, {}
    for s in shots:
        for ov in s.get("overlays") or []:
            if ov.get("type") == "chapter":
                n += 1
                out[s["id"]] = n
    return out


class Timeline:
    def __init__(self, shots, W, H, fps, frames_dir):
        self.shots, self.W, self.H, self.fps, self.frames_dir = shots, W, H, fps, frames_dir
        self.total = shots[-1]["end"]
        self.cache = {}
        self.chapters = chapter_numbers(shots)
        self.trans = []
        for i, s in enumerate(shots):
            if i == 0:
                self.trans.append(0.0)
                continue
            d = TRANSITION.get(s.get("transition_in", "cut"), 0.0)
            prev = shots[i - 1]
            d = min(d, 0.8 * (prev["end"] - prev["start"]), 0.8 * (s["end"] - s["start"]))
            self.trans.append(max(0.0, d))

    def renderer(self, i):
        if i not in self.cache:
            if len(self.cache) > 3:
                self.cache.pop(min(self.cache))
            s = self.shots[i]
            dur = s["end"] - s["start"]
            ctx = {"chapter_no": self.chapters.get(s["id"])}
            self.cache[i] = motion.make_shot(s, self.W, self.H, dur, self.fps, self.frames_dir, ctx)
        return self.cache[i]

    def shot_frame(self, i, t):
        return self.renderer(i).frame(t - self.shots[i]["start"])

    def index(self, t, hint=0):
        i = hint
        while i + 1 < len(self.shots) and t >= self.shots[i + 1]["start"]:
            i += 1
        while i > 0 and t < self.shots[i]["start"]:
            i -= 1
        return i

    def frame(self, t, i):
        f = self.shot_frame(i, t)
        # incoming transition of shot i
        d = self.trans[i]
        c = self.shots[i]["start"]
        if d and t < c + d / 2:
            f = self.blend(i, self.shot_frame(i - 1, t), f, t, c, d)
        # outgoing transition into shot i+1
        if i + 1 < len(self.shots):
            d2 = self.trans[i + 1]
            c2 = self.shots[i + 1]["start"]
            if d2 and t >= c2 - d2 / 2:
                f = self.blend(i + 1, f, self.shot_frame(i + 1, t), t, c2, d2)
        if t < OPEN_FADE:
            f = f * (t / OPEN_FADE)
        return f

    def blend(self, i, a, b, t, c, d):
        kind = self.shots[i].get("transition_in", "cut")
        x = min(1.0, max(0.0, (t - (c - d / 2)) / d))
        if kind == "dip-to-black":
            return a * max(0.0, 1 - 2 * x) if x < 0.5 else b * min(1.0, 2 * x - 1)
        if kind == "whip":
            e = 0.5 - 0.5 * math.cos(math.pi * x)
            shift = int(e * self.W)
            strip = np.concatenate([a, b], axis=1)[:, shift:shift + self.W]
            k = 9
            blur = sum(np.roll(strip, j * int(self.W * 0.012 * math.sin(math.pi * x)), axis=1) for j in range(-k // 2, k // 2 + 1)) / k
            return blur
        return a * (1 - x) + b * x


def _encode_chunk(args):
    shots, W, H, fps, frames_dir, f0, f1, path, preset, crf = args
    tl = Timeline(shots, W, H, fps, frames_dir)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-threads", "2", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    hint = 0
    for fi in range(f0, f1):
        t = fi / fps
        hint = tl.index(t, hint)
        f = tl.frame(t, hint)
        p.stdin.write(np.clip(f, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close()
    if p.wait():
        raise RuntimeError(f"ffmpeg failed on {path}")
    return path


def render_video(shots, W, H, fps, frames_dir, out, workers=4, preset="medium", crf=18, log=print):
    total = shots[-1]["end"]
    n = int(math.ceil(total * fps))
    geo.countries()  # parse once before forking
    bounds = np.linspace(0, n, workers + 1).astype(int)
    tmp = Path(tempfile.mkdtemp(prefix="filmkit-"))
    jobs = [(shots, W, H, fps, frames_dir, int(bounds[k]), int(bounds[k + 1]), tmp / f"chunk{k:02d}.mp4", preset, crf)
            for k in range(workers) if bounds[k + 1] > bounds[k]]
    log(f"rendering {n} frames at {W}x{H}/{fps} in {len(jobs)} chunks")
    with mp.get_context("fork").Pool(len(jobs)) as pool:
        parts = pool.map(_encode_chunk, jobs)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    return out


def mux(video, audio, out, loudnorm=True):
    """audio: stereo float32 at SR."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-", wav],
                   input=np.asarray(audio, np.float32).tobytes(), check=True)
    af = ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"] if loudnorm else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    *af, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    Path(wav).unlink(missing_ok=True)
    return out
