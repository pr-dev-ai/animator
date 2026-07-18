#!/usr/bin/env python3
"""puppet_video.py — orchestrate a full multi-scene 2D-cutout puppet music video.

Runs all seven proven stages together across a whole song:
  storyboard scenes -> Claude scene direction -> character library (auto-built)
  + per-scene background plates -> auto-placement -> deterministic Blender script
  -> resumable per-scene render -> concat + mux.

`build_music_video`-style generator: yields progress log lines, final line "DONE"
or "ERROR: ...".  Reuses animation_api's proven resumable-render + concat/mux
helpers so a crash in scene 9 never costs scenes 1-8 (each scene is cached on a
hash of its choreography + plate + rig + placement + song window).
"""
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Generator

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
OUTPUTS_DIR = REPO_ROOT / "outputs"
PROJECTS_DIR = REPO_ROOT / "projects"
sys.path.insert(0, str(SCRIPTS_DIR))
# web_ui dir too, so bare imports of sibling modules (animation_api, claude_api)
# resolve whether we run as a CLI script or imported as a package by the server.
sys.path.insert(0, str(Path(__file__).resolve().parent))

FPS = 24
CANVAS = (1152, 768)
GROUND_FRAC = 0.86        # character feet sit here (fraction of canvas height)
CHAR_HEIGHT_FRAC = 0.52   # character height as a fraction of canvas height
CHAR_X_FRAC = 0.40        # horizontal placement of the character


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _character_bbox(rig_dir: Path, rig: dict):
    """Opaque bbox of the character body, for auto-placement scaling."""
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(rig_dir / rig["parts"]["body"]["image"]).convert("RGBA"))[:, :, 3]
    ys, xs = np.where(a > 30)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _auto_place(rig_dir: Path, rig: dict):
    """Feet-on-ground placement + bbox->frame-fraction scale (crude but general)."""
    x0, y0, x1, y1 = _character_bbox(rig_dir, rig)
    W, H = CANVAS
    scale = round((CHAR_HEIGHT_FRAC * H) / max(1, (y1 - y0)), 3)
    ground_y = GROUND_FRAC * H
    anchor_y = rig["body_anchor"][1]
    pos_x = int(CHAR_X_FRAC * W)
    pos_y = int(ground_y - (y1 - anchor_y) * scale)   # bottom of body lands on ground line
    return (pos_x, pos_y), scale


def _read_scenes(project: str):
    shots = []
    sl = PROJECTS_DIR / project / "shotlist.csv"
    if sl.is_file():
        with open(sl, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("shot_id") or "").strip():
                    shots.append({"shot_id": r["shot_id"].strip(),
                                  "description": (r.get("description") or "").strip(),
                                  "camera": (r.get("camera") or "Medium").strip(),
                                  "duration": r.get("duration") or "4.0",
                                  "notes": (r.get("notes") or "").strip()})
    return shots


# Language code -> human-character culture. Indian characters are used only for
# Hindi projects; every other language stays neutral (no ethnicity forced).
_LANG_CULTURE = {"hi": "Indian", "hindi": "Indian"}


def _project_culture(project: str):
    """Human-character culture for a project, from its language.txt (Hindi -> Indian)."""
    lang_file = PROJECTS_DIR / project / "language.txt"
    try:
        code = lang_file.read_text(encoding="utf-8").strip().lower()
    except Exception:  # noqa: BLE001
        return None
    return _LANG_CULTURE.get(code)


def _resolve_rig(character: str, culture=None):
    """Return a ready (rig_dir, rig_dict) for a character, building it if needed."""
    import build_character_rig as bcr
    name = (character or "").strip().lower() or "duck"
    # the duck de-risk lives outside the library; everything else is auto-built.
    if name == "duck" and (OUTPUTS_DIR / "duck_rig" / "rig.json").is_file():
        d = OUTPUTS_DIR / "duck_rig"
    else:
        d = bcr.ensure_rig(name, culture=culture)
    return d, json.loads((d / "rig.json").read_text(encoding="utf-8"))


def prepare_assets(project: str) -> Generator[str, None, None]:
    """Storyboard stage for the puppet pipeline: build the exact assets the video
    will use, and a composed preview of each scene.

    For each scene: Claude directs it, the cast rig is auto-built (reused across
    scenes for consistency), a scenery-only plate is generated, and a static
    PREVIEW (character auto-placed on its plate) is written to
    outputs/<project>_storyboards/<shot>.png — so the storyboard gallery shows
    exactly what the animation will animate.  Idempotent: existing rigs/plates are
    reused.  Yields progress; final line "DONE" or "ERROR: ...".
    """
    from PIL import Image
    scenes = _read_scenes(project)
    if not scenes:
        yield f"ERROR: no scenes for '{project}' — plan the storyboard first"
        return
    yield f"Preparing puppet assets for {len(scenes)} scenes..."
    culture = _project_culture(project)
    if culture:
        yield f"  human characters styled as {culture} (project language)"
    from claude_api import direct_scenes
    choreo = direct_scenes(scenes)

    sb_dir = OUTPUTS_DIR / f"{project}_storyboards"
    sb_dir.mkdir(parents=True, exist_ok=True)
    plate_dir = OUTPUTS_DIR / "bg_plates"
    plate_dir.mkdir(parents=True, exist_ok=True)
    rig_cache = {}

    for idx, s in enumerate(scenes, 1):
        sid = s["shot_id"]
        ch = choreo.get(sid, {})
        character = (ch.get("character") or "").strip()
        setting = ch.get("setting") or "sunny park meadow"

        plate = plate_dir / f"plate_{_sha(setting)}.png"
        if not plate.is_file():
            yield f"[{idx}/{len(scenes)}] {sid}: generating background ({setting[:30]})..."
            try:
                _gen_plate(setting, plate)
            except Exception as exc:  # noqa: BLE001
                yield f"  background gen failed ({exc})"
                continue

        comp = Image.open(plate).convert("RGBA").resize(CANVAS)
        if character:
            if character not in rig_cache:
                yield f"[{idx}/{len(scenes)}] {sid}: building '{character}' character..."
                try:
                    rig_cache[character] = _resolve_rig(character, culture)
                except Exception as exc:  # noqa: BLE001
                    yield f"  character build failed ({exc}); skipping puppet"
                    rig_cache[character] = None
            if rig_cache[character]:
                rig_dir, rig = rig_cache[character]
                pos, scale = _auto_place(rig_dir, rig)
                body = Image.open(rig_dir / rig["parts"]["body"]["image"]).convert("RGBA")
                bw, bh = int(body.width * scale), int(body.height * scale)
                body = body.resize((bw, bh))
                ax, ay = rig["body_anchor"]
                topleft = (int(pos[0] - ax * scale), int(pos[1] - ay * scale))
                comp.alpha_composite(body, topleft)
        preview = sb_dir / f"{sid}.png"
        comp.convert("RGB").save(preview)
        label = character or "(scenery)"
        yield f"[{idx}/{len(scenes)}] {sid} preview ready ({label})"

    yield f"Assets ready — {len(rig_cache)} characters, previews in the gallery."
    yield "DONE"


def build_music_video(project: str, captions: bool = False) -> Generator[str, None, None]:
    """Build outputs/<project>_animated.mp4 from the puppet pipeline.

    captions: burn lyric captions in. Off by default — with the scene durations
    stretched to fill the song (and Hindi word-timing unreliable), the lines drift
    out of sync with the vocals, so they hurt more than help. Re-enable per project
    once reliable per-line vocal timing is available.
    """
    import animation_api as A       # proven resumable render + concat/mux helpers
    import blender_render
    from scene_director import author_scene

    song = OUTPUTS_DIR / f"{project}_song.wav"
    mm_path = OUTPUTS_DIR / f"{project}_musicmap.json"
    if not song.is_file():
        yield f"ERROR: no song at {song} — generate the song first"
        return
    scenes = _read_scenes(project)
    if not scenes:
        yield f"ERROR: no scenes for '{project}' — generate the storyboard first"
        return
    if not mm_path.is_file():
        yield "No music-map yet — analysing the song for beats + lyric timing (a few min)..."
        try:
            import musicmap as MM
            for line in MM.build_musicmap(project):
                yield f"  {line}"
        except Exception as exc:  # noqa: BLE001
            yield f"  (music-map analysis failed: {exc}; motion will play but not beat-locked)"
    musicmap = json.loads(mm_path.read_text(encoding="utf-8")) if mm_path.is_file() else {"beats": [], "downbeats": [], "words": []}

    blender = blender_render.resolve_blender()
    ffmpeg = blender_render.resolve_ffmpeg()
    if not blender:
        yield "ERROR: Blender not found"
        return

    # --- scene timing: stretch shot durations to fill the song ---
    import make_dailies
    total_song = make_dailies.probe_duration(song) or None
    durs = []
    for s in scenes:
        try:
            durs.append(max(1.0, float(s["duration"])))
        except ValueError:
            durs.append(4.0)
    if total_song and sum(durs) > 0:
        k = total_song / sum(durs)
        durs = [d * k for d in durs]
    starts, t = [], 0.0
    for d in durs:
        starts.append(t); t += d
    yield f"Puppet video: {len(scenes)} scenes over {t:.0f}s @ {FPS}fps (CPU render)"

    # --- Claude directs every scene in one call ---
    yield "Directing scenes with Claude..."
    culture = _project_culture(project)   # Hindi -> Indian human characters
    from claude_api import direct_scenes
    choreo = direct_scenes(scenes)

    build_dir = OUTPUTS_DIR / "_puppet_build" / project
    build_dir.mkdir(parents=True, exist_ok=True)
    plate_dir = OUTPUTS_DIR / "bg_plates"
    plate_dir.mkdir(parents=True, exist_ok=True)

    rig_cache = {}
    section_mp4s = []
    for idx, (s, start, dur) in enumerate(zip(scenes, starts, durs), 1):
        sid = s["shot_id"]
        ch = choreo.get(sid, {})
        character = (ch.get("character") or "").strip()   # empty => background-only scene

        # background plate from Claude's scenery-ONLY setting (never the character
        # action — a scene "animals dancing" must not bake animals into the plate).
        setting = ch.get("setting") or "sunny park meadow"
        plate = plate_dir / f"plate_{_sha(setting)}.png"
        if not plate.is_file():
            yield f"[{idx}/{len(scenes)}] {sid}: generating background plate..."
            try:
                _gen_plate(setting, plate)
            except Exception as exc:  # noqa: BLE001
                yield f"  plate gen failed ({exc}); reusing a park plate"
                fallbacks = list(plate_dir.glob("plate_*.png")) or list((OUTPUTS_DIR / "test_win_storyboards").glob("SH010.png"))
                if not fallbacks:
                    yield f"ERROR: no background available for {sid}"; return
                plate = fallbacks[0]

        if not character:
            # title card / no-character scene: background + gentle camera + living-
            # world overlays (drifting clouds / sparkles / stars), so even a
            # character-less shot isn't a static painting.
            import scene_director
            bg_layer = {"name": "bg", "image": str(Path(plate).resolve()), "z": 0,
                        "anchor": [576, 384],
                        "keyframes": [{"t": 0.0, "pos": [576, 384], "scale": 1.0, "easing": "ease_in_out"},
                                      {"t": round(dur, 3), "pos": [576, 384], "scale": 1.06}]}
            spec = {"fps": FPS, "duration": round(dur, 3), "resolution": list(CANVAS),
                    "layers": [bg_layer] + scene_director.fx_layers(ch, dur),
                    "camera": {"keyframes": [{"t": 0.0, "pos": [576, 384], "zoom": 1.02, "easing": "ease_in_out"},
                                             {"t": round(dur, 3), "pos": [576, 384], "zoom": 1.10}]}}
            character = "(none)"
        else:
            # character rig (auto-built + cached)
            if character not in rig_cache:
                yield f"[{idx}/{len(scenes)}] {sid}: preparing '{character}' rig..."
                try:
                    rig_cache[character] = _resolve_rig(character, culture)
                except Exception as exc:  # noqa: BLE001
                    yield f"  rig build failed for '{character}' ({exc}); using duck"
                    rig_cache[character] = _resolve_rig("duck")
            rig_dir, rig = rig_cache[character]
            pos, scale = _auto_place(rig_dir, rig)
            spec = author_scene(ch, rig, rig_dir, musicmap, start, dur, str(plate),
                                duck_pos=pos, duck_scale=scale)
        spec_json = json.dumps(spec, indent=2)
        want = _sha(spec_json + f"|{start:.3f}|{dur:.3f}")
        mp4 = build_dir / f"{sid}.mp4"
        hsh = build_dir / f"{sid}.hash"
        nframes = round(dur * FPS)

        if mp4.is_file() and hsh.is_file() and hsh.read_text(encoding="utf-8").strip() == want:
            yield f"[{idx}/{len(scenes)}] {sid} ({character}, {dur:.1f}s) — cached, skip"
            section_mp4s.append(mp4); continue

        (build_dir / f"{sid}.json").write_text(spec_json, encoding="utf-8")
        yield f"[{idx}/{len(scenes)}] {sid} ({character}, {dur:.1f}s) — rendering {nframes} frames"
        frames_dir = build_dir / f"frames_{sid}"
        rc = yield from A._render_frames(blender, build_dir / f"{sid}.json", frames_dir)
        if rc != 0:
            shutil.rmtree(frames_dir, ignore_errors=True)
            yield f"ERROR: Blender exited {rc} on {sid}"; return
        try:
            A._encode_section(ffmpeg, frames_dir, nframes, mp4)
        except Exception as exc:  # noqa: BLE001
            yield f"ERROR: {exc}"; return
        finally:
            shutil.rmtree(frames_dir, ignore_errors=True)
        hsh.write_text(want, encoding="utf-8")
        section_mp4s.append(mp4)
        yield f"    {sid} encoded ({nframes} frames)"

    out = OUTPUTS_DIR / f"{project}_animated.mp4"
    subs = build_dir / "_captions.ass"
    has_caps = captions and _write_captions_ass(scenes, starts, durs, subs)
    yield (f"Concatenating {len(section_mp4s)} scenes, "
           + ("burning lyric captions, " if has_caps else "")
           + "muxing the song...")
    try:
        _concat_and_mux(ffmpeg, section_mp4s, song, out, build_dir, subs if has_caps else None)
    except Exception as exc:  # noqa: BLE001
        yield f"ERROR: {exc}"; return
    mb = out.stat().st_size / (1024 * 1024)
    yield f"Wrote {out} ({mb:.1f} MB, {t:.0f}s, video+audio)"
    yield "DONE"


def _ass_time(t: float) -> str:
    """Seconds -> ASS timestamp H:MM:SS.cc."""
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_escape(text: str) -> str:
    """Make a lyric line safe as ASS event text (one visual line, wrap-friendly)."""
    text = (text or "").replace("\\", "\\\\").replace("{", "(").replace("}", ")")
    return " ".join(text.split())          # collapse newlines/whitespace to spaces


def _write_captions_ass(scenes, starts, durs, path: Path) -> bool:
    """Write a 'big centered kids' ASS subtitle track from each scene's lyric line
    (the shotlist `notes`), timed to that scene's window. Returns False if there is
    nothing to caption (so the caller skips the burn and its re-encode)."""
    W, H = CANVAS
    events = []
    for s, start, dur in zip(scenes, starts, durs):
        line = _ass_escape(s.get("notes", ""))
        if not line:
            continue
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(start + dur)},Kids,,0,0,0,,{line}")
    if not events:
        return False
    # bright yellow fill, thick dark-navy outline + shadow, bold, bottom-centre.
    # colours are ASS &HAABBGGRR: fill yellow, outline near-black navy.
    style = ("Style: Kids,Comic Sans MS,58,&H0000F0FF,&H000000FF,&H00201005,&H64000000,"
             "-1,0,0,0,100,100,0,0,1,5,3,2,80,80,64,1")
    doc = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"{style}\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        + "\n".join(events) + "\n"
    )
    path.write_text(doc, encoding="utf-8")
    return True


def _concat_and_mux(ffmpeg, section_mp4s, song, out_path, work_dir, subs_path=None):
    """Lossless-concat the scene MP4s (same encoder params) and mux the song.

    Self-contained (does not depend on animation_api internals): concat demuxer with
    -c copy, then mux the song with -shortest so the video length is authoritative.
    If *subs_path* is given, the lyric captions are burned in (this forces a one-time
    video re-encode; per-scene renders are untouched). Intermediates live in work_dir.
    """
    import subprocess
    listfile = work_dir / "_concat_list.txt"
    concat = work_dir / "_concat_video.mp4"
    try:
        listfile.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in section_mp4s),
                            encoding="utf-8")
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "concat", "-safe", "0", "-i", str(listfile),
                            "-c", "copy", str(concat)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"concat failed: {r.stderr[-600:]}")
        mux = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(concat), "-i", str(song)]
        cwd = None
        if subs_path and Path(subs_path).is_file():
            # The libass filter can't take a Windows path (the drive colon is read as
            # an option separator, even escaped). Run ffmpeg IN the subtitle's folder
            # and reference it by bare filename — no colon to escape. All other args
            # stay absolute, so cwd doesn't affect them.
            cwd = str(Path(subs_path).parent)
            mux += ["-vf", f"ass={Path(subs_path).name}", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        else:
            mux += ["-c:v", "copy"]
        mux += ["-c:a", "aac", "-b:a", "192k", "-map", "0:v:0", "-map", "1:a:0",
                "-shortest", "-movflags", "+faststart", str(out_path)]
        r = subprocess.run(mux, capture_output=True, text=True, cwd=cwd)
        if r.returncode != 0:
            raise RuntimeError(f"mux failed: {r.stderr[-600:]}")
    finally:
        listfile.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)


def _gen_plate(setting: str, out_path: Path):
    """Generate a scenery-only background plate for *setting*."""
    import subprocess
    r = subprocess.run([sys.executable, str(SCRIPTS_DIR / "gen_background.py"), setting, out_path.stem],
                       capture_output=True, text=True, timeout=600)
    produced = OUTPUTS_DIR / "bg_plates" / f"{out_path.stem}.png"
    if produced.is_file() and produced != out_path:
        shutil.move(str(produced), str(out_path))
    if not out_path.is_file():
        raise RuntimeError(r.stderr[-300:] or "plate not produced")


if __name__ == "__main__":
    for line in build_music_video(sys.argv[1] if len(sys.argv) > 1 else "test_win"):
        print(line, flush=True)
