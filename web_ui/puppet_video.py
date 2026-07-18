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


def _resolve_rig(character: str):
    """Return a ready (rig_dir, rig_dict) for a character, building it if needed."""
    import build_character_rig as bcr
    name = (character or "").strip().lower() or "duck"
    # the duck de-risk lives outside the library; everything else is auto-built.
    if name == "duck" and (OUTPUTS_DIR / "duck_rig" / "rig.json").is_file():
        d = OUTPUTS_DIR / "duck_rig"
    else:
        d = bcr.ensure_rig(name)
    return d, json.loads((d / "rig.json").read_text())


def build_music_video(project: str) -> Generator[str, None, None]:
    """Build outputs/<project>_animated.mp4 from the puppet pipeline."""
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
    musicmap = json.loads(mm_path.read_text()) if mm_path.is_file() else {"beats": [], "downbeats": [], "words": []}
    if not mm_path.is_file():
        yield "  (no musicmap — motion will play but not beat-locked; run musicmap.py first)"

    blender = blender_render.resolve_blender()
    ffmpeg = blender_render.resolve_ffmpeg()
    if not blender:
        yield "ERROR: Blender not found"
        return

    # --- scene timing: stretch shot durations to fill the song ---
    total_song = A._audio_duration(song) if hasattr(A, "_audio_duration") else None
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
        character = ch.get("character", "") or "duck"

        # background plate per scene setting (cached by setting text)
        setting = s["description"] or "a sunny park meadow"
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

        # character rig (auto-built + cached)
        if character not in rig_cache:
            yield f"[{idx}/{len(scenes)}] {sid}: preparing '{character}' rig..."
            try:
                rig_cache[character] = _resolve_rig(character)
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

        if mp4.is_file() and hsh.is_file() and hsh.read_text().strip() == want:
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
    yield f"Concatenating {len(section_mp4s)} scenes and muxing the song..."
    try:
        A._concat_and_mux(ffmpeg, section_mp4s, song, out, build_dir)
    except Exception as exc:  # noqa: BLE001
        yield f"ERROR: {exc}"; return
    mb = out.stat().st_size / (1024 * 1024)
    yield f"Wrote {out} ({mb:.1f} MB, {t:.0f}s, video+audio)"
    yield "DONE"


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
