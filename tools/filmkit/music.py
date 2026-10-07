"""Temp score for the animatic + voice/music mix with ducking.

The final cut uses a licensed track (YouTube Audio Library) passed with
--music; the synthesised score only exists so the animatic plays like a film
and follows the director's mood cues.
"""
import numpy as np

from .voice import SR

# Chord progressions as MIDI notes; one chord per bar (4 s at 60 BPM).
PRESETS = {
    "calm":     dict(chords=[[50, 57, 62, 66, 69], [47, 54, 59, 62, 66], [43, 50, 59, 62, 66], [45, 52, 57, 61, 64]], arp=1, bright=0.6, pulse=0),
    "curious":  dict(chords=[[50, 57, 60, 65, 69], [46, 53, 58, 62, 65], [41, 53, 57, 60, 65], [48, 55, 60, 64, 67]], arp=2, bright=0.7, pulse=0),
    "hopeful":  dict(chords=[[43, 55, 59, 62, 67], [42, 54, 57, 62, 66], [40, 52, 55, 59, 64], [48, 55, 60, 62, 67]], arp=2, bright=0.9, pulse=0),
    "tense":    dict(chords=[[45, 52, 57, 60, 64], [45, 53, 57, 60, 65], [41, 53, 57, 60, 65], [40, 52, 56, 59, 64]], arp=0, bright=0.4, pulse=2),
    "dark":     dict(chords=[[38, 50, 53, 57, 62], [34, 46, 50, 53, 58], [43, 50, 55, 58, 62], [45, 49, 52, 57, 61]], arp=0, bright=0.3, pulse=1),
    "triumph":  dict(chords=[[43, 55, 59, 62, 67], [48, 55, 60, 64, 67], [40, 55, 59, 64, 67], [50, 57, 62, 66, 69]], arp=3, bright=1.0, pulse=2),
}
KEYWORDS = [
    ("dark", ["dark", "grim", "somber", "sombre", "shadow", "serious", "heavy", "sad", "melanch"]),
    ("tense", ["tense", "tension", "conflict", "suspense", "threat", "boycott", "danger", "urgent", "pressure"]),
    ("triumph", ["triumph", "epic", "grand", "global", "soaring", "climax", "big"]),
    ("hopeful", ["hope", "uplift", "inspir", "optimis", "warm", "bright", "rise", "growth", "momentum"]),
    ("curious", ["curio", "mystery", "playful", "intrig", "question", "light", "whims"]),
]


def mood_preset(mood):
    m = (mood or "").lower()
    for name, words in KEYWORDS:
        if any(w in m for w in words):
            return name
    return "calm"


def hz(n):
    return 440.0 * 2 ** ((n - 69) / 12)


def tone(f, dur, harmonics=6, tilt=1.6, detune=0.0):
    t = np.arange(int(dur * SR)) / SR
    out = np.zeros_like(t)
    for h in range(1, harmonics + 1):
        if f * h > 9000:
            break
        out += np.sin(2 * np.pi * f * h * (1 + detune) * t + h) / h ** tilt
    return out


def adsr(n, a, r):
    env = np.ones(n)
    na, nr = int(a * SR), int(r * SR)
    if na:
        env[:na] = np.linspace(0, 1, na) ** 1.5
    if nr:
        env[-nr:] *= np.linspace(1, 0, nr) ** 1.5
    return env


def section(preset, dur, bar0=0, seed=0):
    p = PRESETS[preset]
    rng = np.random.default_rng(seed)
    bar = 4.0
    n = int(dur * SR)
    L, R = np.zeros(n + SR * 12), np.zeros(n + SR * 12)
    nb = int(np.ceil(dur / bar))
    for b in range(nb):
        chord = p["chords"][(bar0 + b) % len(p["chords"])]
        start = int(b * bar * SR)
        # pad: overlapping bars for legato
        for i, note in enumerate(chord[1:]):
            for side, det in ((L, -0.0025), (R, 0.0025)):
                x = tone(hz(note), bar + 1.6, harmonics=4 + int(3 * p["bright"]), detune=det * (1 + i * 0.3))
                x *= adsr(len(x), 1.3, 1.6) * 0.045
                side[start:start + len(x)] += x
        # bass
        x = tone(hz(chord[0]), bar + 1.0, harmonics=2, tilt=2.2) * adsr(int((bar + 1.0) * SR), 0.4, 1.0) * 0.10
        L[start:start + len(x)] += x
        R[start:start + len(x)] += x
        # piano-like arpeggio
        steps = p["arp"] * 2
        for k in range(steps):
            note = chord[1:][(k * 2 + b) % (len(chord) - 1)] + 12
            if rng.random() < 0.18:
                continue
            ts = start + int(k * bar / steps * SR)
            d = 2.2
            tt = np.arange(int(d * SR)) / SR
            x = (np.sin(2 * np.pi * hz(note) * tt) + 0.25 * np.sin(4 * np.pi * hz(note) * tt)) * np.exp(-tt * 2.4)
            x *= adsr(len(x), 0.004, 0.2) * 0.05 * (0.8 + 0.4 * rng.random())
            pan = 0.35 + 0.3 * rng.random()
            L[ts:ts + len(x)] += x * (1 - pan)
            R[ts:ts + len(x)] += x * pan
        # low pulse for tension
        for k in range(p["pulse"] * 2):
            ts = start + int(k * bar / (p["pulse"] * 2) * SR)
            tt = np.arange(int(0.6 * SR)) / SR
            x = np.sin(2 * np.pi * hz(chord[0] - 12) * tt) * np.exp(-tt * 7) * 0.12
            L[ts:ts + len(x)] += x
            R[ts:ts + len(x)] += x
    return np.stack([L[:n], R[:n]], 1)


def reverb(x, seconds=2.4, wet=0.32, seed=3):
    """Cheap stereo reverb: FFT convolution with decaying noise, block by block."""
    rng = np.random.default_rng(seed)
    m = int(seconds * SR)
    t = np.arange(m) / SR
    out = x.copy()
    for ch in range(2):
        ir = rng.normal(0, 1, m) * np.exp(-t * 3.2 / seconds)
        ir /= np.sqrt(np.sum(ir ** 2))
        block = 1 << 19
        nfft = 1 << int(np.ceil(np.log2(block + m)))
        IR = np.fft.rfft(ir, nfft)
        acc = np.zeros(len(x) + m)
        for s in range(0, len(x), block):
            seg = x[s:s + block, ch]
            y = np.fft.irfft(np.fft.rfft(seg, nfft) * IR, nfft)[: len(seg) + m]
            acc[s:s + len(y)] += y
        out[:, ch] = x[:, ch] * (1 - wet) + acc[: len(x)] * wet
    return out


def score(total, cues, log=print):
    """cues: [(time, mood)] sorted. Returns stereo float32 of `total` seconds."""
    cues = sorted(cues) or [(0.0, "calm")]
    if cues[0][0] > 0:
        cues.insert(0, (0.0, cues[0][1]))
    xfade = 3.0
    out = np.zeros((int(total * SR), 2))
    bar0 = 0
    for i, (t0, mood) in enumerate(cues):
        t1 = cues[i + 1][0] if i + 1 < len(cues) else total
        if t1 - t0 < 1:
            continue
        preset = mood_preset(mood)
        log(f"  music {t0:6.1f}s–{t1:6.1f}s  {preset:8s} ({mood})")
        seg = section(preset, t1 - t0 + xfade, bar0=bar0, seed=i)
        bar0 += int((t1 - t0) / 4)
        s = int(t0 * SR)
        n = min(len(seg), len(out) - s)
        env = np.ones(n)
        k = int(xfade * SR)
        if i > 0:
            env[:k] = np.linspace(0, 1, k)
        if i + 1 < len(cues) and n > k:
            env[-k:] = np.linspace(1, 0, k)
        out[s:s + n] += seg[:n] * env[:, None]
    out = reverb(out)
    fade = int(3 * SR)
    out[:int(1.5 * SR)] *= np.linspace(0, 1, int(1.5 * SR))[:, None]
    out[-fade:] *= np.linspace(1, 0, fade)[:, None]
    peak = np.max(np.abs(out)) or 1
    return (out / peak * 0.5).astype(np.float32)


def fit(music, total):
    """Loop or trim a licensed track to the film length with fades."""
    n = int(total * SR)
    reps = int(np.ceil(n / len(music)))
    out = np.concatenate([music] * reps)[:n]
    out[:int(1.0 * SR)] *= np.linspace(0, 1, int(1.0 * SR))[:, None]
    out[-int(4 * SR):] *= np.linspace(1, 0, int(4 * SR))[:, None]
    return out


def mix(voice, music, music_db=-17.0, duck_db=-9.0):
    """Voice (mono) over music (stereo) with sidechain-style ducking."""
    n = max(len(voice), len(music))
    v = np.zeros(n, np.float32)
    v[:len(voice)] = voice
    m = np.zeros((n, 2), np.float32)
    m[:len(music)] = music
    hop = int(0.02 * SR)
    frames = n // hop + 1
    pad = np.zeros(frames * hop, np.float32)
    pad[:n] = np.abs(v)
    level = pad.reshape(frames, hop).max(1)
    active = (level > 0.02).astype(np.float32)
    # attack fast, release slow
    env = np.zeros(frames, np.float32)
    for i in range(frames):
        prev = env[i - 1] if i else 0.0
        env[i] = prev + (active[i] - prev) * (0.5 if active[i] > prev else 0.06)
    gain = 10 ** ((music_db + duck_db * np.repeat(env, hop)[:n]) / 20)
    vpk = np.max(np.abs(v)) or 1.0
    out = m * gain[:, None] / (np.max(np.abs(m)) or 1) + (v / vpk * 0.8)[:, None]
    return out.astype(np.float32)
