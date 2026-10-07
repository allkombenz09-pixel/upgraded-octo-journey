"""Procedural motion-graphics sound design: whooshes, hits, pin pops, ticks.

Diegetic effects from the shot list (bicycle bell, match strike…) are left to
the editor; these synthetic accents only follow the graphics, so they always
line up with what is drawn on screen.
"""
import numpy as np

from .graphics import FADE
from .voice import SR

rng = np.random.default_rng(11)


def _env(n, attack, decay):
    t = np.arange(n) / SR
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / decay)


def _lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):  # short sounds only, so a Python loop is fine
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def whoosh(d=0.45):
    n = int(d * SR)
    noise = rng.normal(0, 1, n)
    sweep = np.concatenate([_lowpass(noise[: n // 2], 900), _lowpass(noise[n // 2:], 2500)])
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return sweep * env * 0.5


def hit(d=1.6):
    n = int(d * SR)
    t = np.arange(n) / SR
    f = 55 + 70 * np.exp(-t * 18)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.003, 0.45)
    click = _lowpass(rng.normal(0, 1, n), 1800) * _env(n, 0.001, 0.03)
    return body * 0.8 + click * 0.5


def pop(f=880, d=0.18):
    n = int(d * SR)
    t = np.arange(n) / SR
    return np.sin(2 * np.pi * f * t * (1 + 0.6 * np.exp(-t * 40))) * _env(n, 0.002, 0.05) * 0.5


def tick(d=0.06):
    n = int(d * SR)
    return _lowpass(rng.normal(0, 1, n), 4000) * _env(n, 0.0005, 0.008) * 0.6


def events(shots):
    """(time, sound, gain) for every graphic accent in the timed shot list."""
    ev = []
    for s in shots:
        t0 = s["start"]
        if s.get("transition_in") == "whip":
            ev.append((t0 - 0.25, whoosh(), 0.55))
        ovs = s.get("overlays") or []
        if s.get("kind") == "graphic":
            main = ovs[0]["type"] if ovs else ""
            if main == "map":
                pins = [p for o in ovs if o["type"] == "map" for p in (o.get("detail", "").split("pins=")[-1].split()[0].split(",") if "pins=" in o.get("detail", "") else [])]
                for i, _ in enumerate(pins):
                    ev.append((t0 + 0.4 + 0.5 * i, pop(660 + 110 * i), 0.35))
            elif main in ("number", "date"):
                ev.append((t0 + 0.2, hit(), 0.5))
                ev.extend((t0 + 0.3 + k * 0.08, tick(), 0.25) for k in range(18) if main == "number")
            elif main in ("chapter", "title", "quote"):
                ev.append((t0 + 0.1, hit(), 0.45))
            continue
        for slot, o in enumerate(ovs):
            t_in = t0 + 0.35 + 0.45 * (slot if o["type"] not in ("chapter", "title", "quote") else 0)
            if o["type"] in ("chapter", "title"):
                ev.append((t_in, hit(), 0.45))
            elif o["type"] == "date":
                ev.append((t_in, tick(), 0.35))
            elif o["type"] in ("number", "map"):
                ev.append((t_in, pop(740), 0.3))
            elif o["type"] == "lower-third":
                ev.append((t_in - 0.1, whoosh(0.35), 0.25))
    return ev


def track(shots, total):
    out = np.zeros(int(total * SR) + SR, np.float32)
    for t, snd, g in events(shots):
        i = int(max(0.0, t) * SR)
        n = min(len(snd), len(out) - i)
        if n > 0:
            out[i:i + n] += snd[:n].astype(np.float32) * g
    return out[: int(total * SR)]
