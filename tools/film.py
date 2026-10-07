#!/usr/bin/env python3
"""Render an episode of "How They Built It".

  python tools/film.py episodes/01-ikea status            # what is still missing
  python tools/film.py episodes/01-ikea animatic          # 720p preview, placeholders + scratch voice
  python tools/film.py episodes/01-ikea final             # 1080p master from frames/ + audio/ + music/
  python tools/film.py episodes/01-ikea shorts            # 4 vertical Shorts
  python tools/film.py episodes/01-ikea extras            # captions.srt, description, thumbnail, timing

Folder layout inside the episode:
  episode.json      script beats, shots, Shorts, packaging (source of truth)
  frames/           01.png … 52.png, 25a.png, 25b.png, thumbnail.png, optional 10.mp4 (Kling)
  audio/            part1.mp3 part2.mp3 part3.mp3 short1.mp3 … short4.mp3
  music/            one licensed track (YouTube Audio Library) for the final mix
  out/              everything rendered
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from filmkit import music, motion, render, sfx, shorts as S, thumb, timeline, voice as V  # noqa: E402

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")


def load(ep_dir):
    return json.loads((ep_dir / "episode.json").read_text())


def first_audio(folder, stem):
    for ext in AUDIO_EXTS:
        p = folder / f"{stem}{ext}"
        if p.exists():
            return p
    return None


def status(ep_dir, ep):
    frames, audio = ep_dir / "frames", ep_dir / "audio"
    imgs = [s["id"] for s in ep["shots"] if s.get("kind") == "image"]
    missing = [i for i in imgs if not motion.find_asset(frames, i, motion.IMAGE_EXTS + motion.VIDEO_EXTS)]
    print(f"images: {len(imgs) - len(missing)}/{len(imgs)} ready")
    if missing:
        print("  missing:", " ".join(missing))
    kling = [s["id"] for s in ep["shots"] if s.get("kling")]
    print("kling clips:", " ".join(f"{k}{'✓' if motion.find_asset(frames, k, motion.VIDEO_EXTS) else '·'}" for k in kling))
    print("thumbnail bg:", "✓" if motion.find_asset(frames, "thumbnail", motion.IMAGE_EXTS) else "missing")
    for stem in [p["id"] for p in ep["parts"]] + [s["id"] for s in ep["shorts"]]:
        print(f"audio {stem}:", "✓" if first_audio(audio, stem) else "missing")
    tracks = sorted((ep_dir / "music").glob("*")) if (ep_dir / "music").exists() else []
    print("music:", tracks[0].name if tracks else "missing (temp score will be used)")


def music_cue_times(ep, beats):
    at = {b["id"]: b["start"] for b in beats}
    cues = [(max(0.0, at[c["at_beat"]] - 1.0), c.get("mood", "calm")) for c in ep.get("music_cues", []) if c.get("at_beat") in at]
    return cues or [(0.0, "calm")]


def music_bed(ep_dir, ep, beats, total, licensed=None, log=print):
    track = licensed
    if track is None and (ep_dir / "music").exists():
        found = sorted(p for p in (ep_dir / "music").iterdir() if p.suffix.lower() in AUDIO_EXTS)
        track = found[0] if found else None
    if track:
        log(f"music: {track.name}")
        raw = V.decode(track)
        return music.fit(np.stack([raw, raw], 1), total)
    log("music: temp score")
    return music.score(total, music_cue_times(ep, beats), log=log)


def write_extras(ep_dir, ep, beats, shots_t, out):
    (out / "captions.srt").write_text(timeline.srt(beats))
    chap = timeline.chapters(beats, shots_t)
    (out / "chapters.txt").write_text(chap + "\n")
    pk = ep.get("packaging", {})
    body = pk.get("description_body", "")
    if "Chapters:" in body:
        desc = body
    else:
        split = body.find("Sources:")
        desc = (body[:split].rstrip() + "\n\nChapters:\n" + chap + "\n\n" + body[split:]) if split >= 0 else body + "\n\nChapters:\n" + chap
    (out / "description.txt").write_text(desc.strip() + "\n")
    (out / "timing.json").write_text(json.dumps({
        "beats": beats,
        "shots": [{k: s.get(k) for k in ("id", "kind", "start", "end", "camera", "transition_in")} for s in shots_t],
    }, indent=1, ensure_ascii=False))
    rows = ["id;start;end;seconds;kind;camera;transition;overlays;sfx;beats"]
    for s in shots_t:
        ovs = " | ".join(f"{o['type']}: {o.get('text', '')}" for o in s.get("overlays") or [])
        rows.append(";".join([s["id"], timeline.fmt_ts(s["start"], False), timeline.fmt_ts(s["end"], False),
                              f"{s['end'] - s['start']:.1f}", s.get("kind", ""), s.get("camera", ""),
                              s.get("transition_in", ""), ovs, s.get("sfx", "") or "", ",".join(s.get("beats") or [])]))
    (out / "edit_decision_list.csv").write_text("\n".join(rows) + "\n")
    img = thumb.thumbnail(pk.get("thumbnail_text", ""), ep_dir / "frames", pk.get("thumbnail_prompt", ""))
    img.save(out / "thumbnail.png")


def scratch_voice(args, ep):
    if args.no_voice:
        return None
    v = ep.get("scratch_voice", {})
    return V.ScratchVoice(voice=v.get("voice", "af_heart"), speed=v.get("speed", 0.95))


def cmd_film(ep_dir, ep, args, final):
    out = ep_dir / "out"
    out.mkdir(exist_ok=True)
    W, H = (1920, 1080) if (args.res or (1080 if final else 720)) == 1080 else (1280, 720)
    audio_dir = ep_dir / "audio"
    vtrack, beats = timeline.build_voice(ep, audio_dir if final or args.use_audio else None,
                                         None if final else scratch_voice(args, ep))
    shots_t = timeline.shot_times(ep["shots"], beats)
    total = shots_t[-1]["end"]
    print(f"film length {timeline.fmt_ts(total, False)} — {len(beats)} beats, {len(shots_t)} shots")
    write_extras(ep_dir, ep, beats, shots_t, out)
    name = "film" if final else "animatic"
    silent = out / f"{name}_video.mp4"
    render.render_video(shots_t, W, H, args.fps, ep_dir / "frames", silent, workers=args.workers,
                        preset="medium" if final else "veryfast", crf=18 if final else 23)
    bed = music_bed(ep_dir, ep, beats, total, Path(args.music) if args.music else None)
    voice = np.zeros(int(total * V.SR), np.float32)
    voice[:min(len(vtrack), len(voice))] = vtrack[:len(voice)]
    mixed = music.mix(voice, bed)
    if not args.no_sfx:
        fx = sfx.track(shots_t, total) * 0.5
        mixed[: len(fx)] += fx[:, None]
    render.mux(silent, mixed, out / f"{name}.mp4")
    silent.unlink()
    print("wrote", out / f"{name}.mp4")


def cmd_shorts(ep_dir, ep, args, final):
    import subprocess
    out = ep_dir / "out"
    out.mkdir(exist_ok=True)
    W, H = (1080, 1920) if (args.res or (1080 if final else 720)) == 1080 else (720, 1280)
    sv = None if final else scratch_voice(args, ep)
    for short in ep["shorts"]:
        if args.only and short["id"] not in args.only.split(","):
            continue
        src = first_audio(ep_dir / "audio", short["id"])
        if src:
            audio = V.decode(src)
            spans = V.align(audio, short["beats"])
        elif sv:
            audio, spans = sv.part(short["beats"])
        else:
            print(f"{short['id']}: no audio, skipped")
            continue
        voice = np.concatenate([np.zeros(int(0.25 * V.SR), np.float32), audio])
        cuts, beats = S.build_short(short, ep["shots"], voice, spans, W, H, args.fps, ep_dir / "frames")
        total = cuts[-1]["end"]
        caps = S.Captions(beats, W, H)
        silent = out / f"{short['id']}_video.mp4"
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(args.fps),
               "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", str(silent)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        rend, idx = {}, 0
        for fi in range(int(total * args.fps)):
            t = fi / args.fps
            while idx + 1 < len(cuts) and t >= cuts[idx + 1]["start"]:
                idx += 1
            if idx not in rend:
                rend = {idx: S.make_renderer(cuts[idx], W, H, args.fps, ep_dir / "frames")}
            f = rend[idx].frame(t - cuts[idx]["start"])
            f = caps.apply(f, t)
            p.stdin.write(np.clip(f, 0, 255).astype(np.uint8).tobytes())
        p.stdin.close()
        p.wait()
        bed = music.score(total + 1, [(0.0, "curious")], log=lambda *_: None)
        mixed = music.mix(np.concatenate([voice, np.zeros(V.SR, np.float32)])[: int(total * V.SR)], bed[: int(total * V.SR)], music_db=-19)
        render.mux(silent, mixed, out / f"{short['id']}.mp4")
        silent.unlink()
        print(f"wrote {out / short['id']}.mp4 ({total:.1f}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", type=Path)
    ap.add_argument("command", choices=["status", "animatic", "final", "shorts", "shorts-final", "extras"])
    ap.add_argument("--res", type=int, choices=[720, 1080])
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--music", help="licensed music file for the mix")
    ap.add_argument("--use-audio", action="store_true", help="animatic with the real voice files from audio/")
    ap.add_argument("--no-voice", action="store_true", help="animatic without scratch TTS (timed by word count)")
    ap.add_argument("--only", help="comma-separated short ids")
    ap.add_argument("--no-sfx", action="store_true", help="skip the synthetic graphic accents")
    args = ap.parse_args()
    ep = load(args.episode)
    if args.command == "status":
        status(args.episode, ep)
    elif args.command in ("animatic", "final"):
        cmd_film(args.episode, ep, args, final=args.command == "final")
    elif args.command in ("shorts", "shorts-final"):
        cmd_shorts(args.episode, ep, args, final=args.command == "shorts-final")
    elif args.command == "extras":
        out = args.episode / "out"
        out.mkdir(exist_ok=True)
        vtrack, beats = timeline.build_voice(ep, args.episode / "audio" if args.use_audio else None, None)
        write_extras(args.episode, ep, beats, timeline.shot_times(ep["shots"], beats), out)
        print("wrote extras to", out)


if __name__ == "__main__":
    main()
