"""Voice tracks: real part files from Syntx, or a scratch voice for the animatic.

Scratch voice uses Kokoro-82M (Apache-2.0) through kokoro-onnx. Model and
voices are fetched from the npm registry by tools/setup_scratch_voice.sh.
Each beat is synthesised separately and cached, so beat timings are exact.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np

SR = 48000
CACHE = Path.home() / ".cache" / "filmkit"
BEAT_GAP = 0.32      # pause between beats inside a part (scratch voice)
ELLIPSIS_PAUSE = 0.28


def decode(path, sr=SR):
    """Any audio file -> mono float32 at sr."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def write_wav(path, audio, sr=SR):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                    str(path)], input=np.asarray(audio, np.float32).tobytes(), check=True)


def resample(audio, sr_in, sr_out=SR):
    if sr_in == sr_out:
        return audio.astype(np.float32)
    n = int(round(len(audio) * sr_out / sr_in))
    x = np.linspace(0, len(audio) - 1, n)
    return np.interp(x, np.arange(len(audio)), audio).astype(np.float32)


class ScratchVoice:
    def __init__(self, voice="af_heart", speed=0.95, model_dir=None):
        self.voice, self.speed = voice, speed
        self.model_dir = Path(model_dir or CACHE / "kokoro")
        self._k = None

    def _kokoro(self):
        if self._k is None:
            from kokoro_onnx import Kokoro  # optional dependency
            self._k = Kokoro(str(self.model_dir / "kokoro-q8.onnx"), str(self.model_dir / "voices.bin"))
        return self._k

    def say(self, text):
        key = hashlib.sha1(json.dumps([text, self.voice, self.speed, 2]).encode()).hexdigest()[:16]
        f = CACHE / "tts" / f"{key}.npy"
        if f.exists():
            return np.load(f)
        # Kokoro reads "..." too briskly; synthesise the pieces and insert real pauses.
        pieces = [p.strip() for p in re.split(r"\.\.\.|…", text)]
        out = []
        for i, piece in enumerate(pieces):
            if piece:
                if not re.search(r"[.!?,;:]$", piece) and i < len(pieces) - 1:
                    piece += ","
                lang = "en-gb" if self.voice.startswith("b") else "en-us"
                audio, sr = self._kokoro().create(piece, voice=self.voice, speed=self.speed, lang=lang)
                out.append(resample(np.asarray(audio, np.float32), sr))
            if i < len(pieces) - 1:
                out.append(np.zeros(int(ELLIPSIS_PAUSE * SR), np.float32))
        audio = np.concatenate(out) if out else np.zeros(1, np.float32)
        f.parent.mkdir(parents=True, exist_ok=True)
        np.save(f, audio)
        return audio

    def part(self, beats, log=print):
        """Synthesise a part beat by beat. Returns (audio, [(start, end) per beat])."""
        chunks, spans, t = [], [], 0.0
        for i, b in enumerate(beats):
            log(f"  tts {b['id']}: {b['text'][:60]}")
            a = self.say(b["text"])
            spans.append((t, t + len(a) / SR))
            chunks.append(a)
            t += len(a) / SR
            if i < len(beats) - 1:
                gap = np.zeros(int(BEAT_GAP * SR), np.float32)
                chunks.append(gap)
                t += BEAT_GAP
        return np.concatenate(chunks), spans


# ------------------------------------------------------- real voice files
def silences(audio, sr=SR, floor_db=-38.0, min_len=0.16, hop=0.01):
    """Return [(start, end)] of pauses using a short-time RMS gate."""
    n = int(hop * sr)
    frames = len(audio) // n
    rms = np.sqrt(np.mean(audio[: frames * n].reshape(frames, n) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    ref = np.percentile(db, 95)
    quiet = db < max(floor_db, ref - 32)
    out, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * hop >= min_len:
                out.append((start * hop, i * hop))
            start = None
    if start is not None:
        out.append((start * hop, frames * hop))
    return out


def beat_weight(text):
    return len(text) + 14 * text.count("...") + 8 * len(re.findall(r"[.!?](\s|$)", text)) + 4 * text.count(",")


def align(audio, beats, sr=SR):
    """Estimate beat (start, end) inside a recorded part by snapping a
    character-proportional guess to the nearest pause."""
    dur = len(audio) / sr
    sil = silences(audio, sr)
    head = sil[0][1] if sil and sil[0][0] <= 0.05 else 0.0
    tail = sil[-1][0] if sil and sil[-1][1] >= dur - 0.05 else dur
    w = np.array([beat_weight(b["text"]) for b in beats], float)
    cum = np.cumsum(w) / w.sum()
    inner = [s for s in sil if s[0] > head + 0.2 and s[1] < tail - 0.2]
    bounds, prev = [], head
    avg = (tail - head) / len(beats)
    for k in range(len(beats) - 1):
        guess = head + (tail - head) * cum[k]
        window = max(1.2, 0.45 * avg)
        cands = [s for s in inner if abs((s[0] + s[1]) / 2 - guess) <= window and s[0] > prev + 0.4]
        if cands:
            best = min(cands, key=lambda s: abs((s[0] + s[1]) / 2 - guess) - 0.6 * (s[1] - s[0]))
            bounds.append(best)
            prev = best[1]
        else:
            bounds.append((guess, guess))
            prev = guess
    spans, start = [], head
    for s0, s1 in bounds:
        spans.append((start, s0))
        start = s1
    spans.append((start, tail))
    return spans
