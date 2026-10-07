"""Beat and shot timing, captions (SRT) and YouTube chapters."""
import re

import numpy as np

from . import voice as V

LEAD_IN = 0.5      # seconds of picture before the first word
PART_GAP = 1.3     # breath between parts (dip to black lives here)
CUT_EARLY = 0.12   # cut slightly before the word lands
TAIL = 1.6         # after the last word, before the end card
END_CARD = 12.0    # YouTube end-screen elements need 5–20 s


def build_voice(ep, audio_dir=None, scratch=None, log=print):
    """Return (voice_track, beats) where beats = [{id, part, text, start, end, chapter}] on the film clock."""
    track, beats, t = [np.zeros(int(LEAD_IN * V.SR), np.float32)], [], LEAD_IN
    for pi, part in enumerate(ep["parts"]):
        src = None
        if audio_dir:
            for ext in (".mp3", ".wav", ".m4a", ".flac", ".ogg"):
                p = audio_dir / f"{part['id']}{ext}"
                if p.exists():
                    src = p
                    break
        if src:
            log(f"voice {part['id']}: {src.name} (aligning beats to pauses)")
            audio = V.decode(src)
            spans = V.align(audio, part["beats"])
        elif scratch:
            log(f"voice {part['id']}: scratch TTS")
            audio, spans = scratch.part(part["beats"], log=log)
        else:
            # silent animatic: ~2.35 words/s plus ellipsis pauses
            audio, spans, tt = None, [], 0.0
            for b in part["beats"]:
                d = len(b["text"].split()) / 2.35 + 0.3 * b["text"].count("...")
                spans.append((tt, tt + d))
                tt += d + V.BEAT_GAP
            audio = np.zeros(int(tt * V.SR), np.float32)
        for b, (s, e) in zip(part["beats"], spans):
            beats.append(dict(id=b["id"], part=part["id"], text=b["text"], chapter=b.get("chapter") or "",
                              start=t + s, end=t + e))
        track.append(audio)
        t += len(audio) / V.SR
        if pi < len(ep["parts"]) - 1:
            track.append(np.zeros(int(PART_GAP * V.SR), np.float32))
            t += PART_GAP
    return np.concatenate(track), beats


def shot_times(shots, beats, log=print):
    """Give every shot a [start, end) on the film clock from the beats it covers.

    Beat slots run from one beat's first word to the next beat's first word,
    so pauses belong to the picture that is already on screen. A beat listed
    by several consecutive shots is split evenly between them.
    """
    order = {b["id"]: i for i, b in enumerate(beats)}
    slot_start = [max(0.0, b["start"] - CUT_EARLY) for b in beats]
    slot_start[0] = 0.0
    slot_end = slot_start[1:] + [beats[-1]["end"] + TAIL]
    owners = {}
    for si, sh in enumerate(shots):
        for bid in sh.get("beats") or []:
            if bid in order:
                owners.setdefault(bid, []).append(si)
    timed = []
    for si, sh in enumerate(shots):
        ids = [b for b in (sh.get("beats") or []) if b in order]
        if not ids:
            if sh.get("kind") != "endcard":
                log(f"warning: shot {sh.get('id')} covers no known beat, skipped")
            continue
        starts, ends = [], []
        for bid in ids:
            i = order[bid]
            own = owners[bid]
            k, n = own.index(si), len(own)
            s0, s1 = slot_start[i], slot_end[i]
            starts.append(s0 + (s1 - s0) * k / n)
            ends.append(s0 + (s1 - s0) * (k + 1) / n)
        timed.append(dict(sh, start=min(starts), end=max(ends)))
    timed.sort(key=lambda s: (s["start"], s["end"]))
    for a, b in zip(timed, timed[1:]):
        a["end"] = b["start"]
    timed = [s for s in timed if s["end"] - s["start"] > 0.2]
    if timed:
        timed[0]["start"] = 0.0
        last_end = timed[-1]["end"]
        timed.append(dict(id="END", kind="endcard", beats=[], overlays=[], camera="static",
                          transition_in="crossfade", start=last_end, end=last_end + END_CARD))
    return timed


def fmt_ts(t, srt=True):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if srt:
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}"
    return f"{int(h)}:{int(m):02d}:{int(s):02d}" if h else f"{int(m):02d}:{int(s):02d}"


def caption_chunks(text, max_chars=84):
    """Split a beat into caption cues at sentence / clause boundaries."""
    text = re.sub(r"\s+", " ", text.replace("...", "…")).strip()
    parts = re.split(r"(?<=[.!?…])\s+", text)
    out = []
    for p in parts:
        while len(p) > max_chars:
            cut = max(p.rfind(", ", 0, max_chars), p.rfind(" ", 0, max_chars))
            cut = cut if cut > 20 else max_chars
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    merged = []
    for p in out:
        if merged and len(merged[-1]) + 1 + len(p) <= max_chars // 2:
            merged[-1] += " " + p
        else:
            merged.append(p)
    return merged


def two_lines(text, width=42):
    if len(text) <= width:
        return text
    mid = len(text) // 2
    left, right = text.rfind(" ", 0, mid + 6), text.find(" ", mid - 6)
    cut = left if left != -1 and (right == -1 or mid - left <= right - mid) else right
    return text[:cut].strip() + "\n" + text[cut:].strip() if cut > 0 else text


def srt(beats):
    cues = []
    for b in beats:
        chunks = caption_chunks(b["text"])
        total = sum(len(c) for c in chunks) or 1
        t = b["start"]
        for c in chunks:
            d = (b["end"] - b["start"]) * len(c) / total
            cues.append((t, t + d, two_lines(c)))
            t += d
    return "\n".join(f"{i}\n{fmt_ts(s)} --> {fmt_ts(e)}\n{txt}\n" for i, (s, e, txt) in enumerate(cues, 1))


def chapters(beats, shots):
    """YouTube chapters: first at 00:00, each at the cut where its beat starts."""
    cut_at = {}
    for s in shots:
        for bid in s.get("beats") or []:
            cut_at.setdefault(bid, s["start"])
    out = []
    for b in beats:
        if b["chapter"]:
            t = 0.0 if not out else cut_at.get(b["id"], b["start"])
            out.append((t, b["chapter"]))
    clean = []
    for t, title in out:
        if clean and t - clean[-1][0] < 10:  # YouTube needs >= 10 s per chapter
            continue
        clean.append((t, title))
    return "\n".join(f"{fmt_ts(t, srt=False)} {title}" for t, title in clean)
