"""
animation_api.py — build the full-length animated music video for a project.

`build_music_video(project)` is the single entry point a server route calls.  It
is GENERAL: any project that has its own storyboard images animates THOSE via
depth-based 2.5D parallax (scripts/depth_parallax.py + scripts/blender_render.py),
shot by shot on the CPU, then crossfades the shots and muxes the project's song,
writing outputs/<project>_animated.mp4.  A project with no storyboard images gets
a clear "generate storyboards first" error — there is no hardcoded scene.

Per-scene motion is Claude-authored: each shot's storyboard prompt is read and
Claude chooses the camera move (pan direction / intensity / push) that fits that
scene (web_ui/claude_api.author_scene_motions), in a single batched call per
build.  When Claude is unavailable, or a shot has no prompt, the renderer falls
back to its tuned default rotating drift.

Why shot by shot with a per-shot cache:
  * Rendering in discrete, cached shots keeps any single render step short and
    lets a re-invocation resume instead of restarting from zero — a shot whose
    inputs are unchanged and whose MP4 already exists is skipped.
  * Each shot renders exactly (slot + crossfade) long so the shots crossfade and
    mux to precisely the song length, giving tight A/V sync.

Yields plain progress log lines (server.py wraps these in SSE, exactly like
music_ace.generate_song and the pipeline_api generators); the final line is
"DONE" or starts with "ERROR:".
"""

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Generator

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
OUTPUTS_DIR = REPO_ROOT / "outputs"
PROJECTS_DIR = REPO_ROOT / "projects"

# scripts/ holds the spec author, the renderer, and the beat/tool resolvers.
sys.path.insert(0, str(SCRIPTS_DIR))

_VALID_PROJECT_NAME = re.compile(r"[A-Za-z0-9_-]+")

FPS = 24


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _render_frames(blender, spec_path, frames_dir) -> Generator[str, None, int]:
    """Run Blender headless to render a spec's frames; yield sparse progress.

    Reuses blender_render's executor (build + render + silent-failure guard) but
    parses its per-frame chatter down to a heartbeat instead of streaming every
    line.  Returns Blender's exit code (0 = ok, including the guard passing).
    """
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("frame_*.png"):
        old.unlink()

    cmd = [
        blender, "--background", "--factory-startup",
        "--python", str((SCRIPTS_DIR / "blender_render.py").resolve()), "--",
        "--render", "--spec", str(spec_path), "--frames", str(frames_dir),
        "--engine", "cpu",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    saved = 0
    last_beat = 0
    guard_line = None
    for line in proc.stdout:
        line = line.rstrip()
        if "Saved:" in line and "frame_" in line:
            saved += 1
            if saved - last_beat >= 48:      # ~2s of footage between heartbeats
                last_beat = saved
                yield f"    rendered {saved} frames..."
        elif "guard:" in line or "FATAL" in line:
            guard_line = line
    proc.wait()
    if guard_line:
        yield f"    {guard_line.split('] ')[-1]}"
    return proc.returncode


def _encode_section(ffmpeg, frames_dir, nframes, out_path) -> None:
    """Encode exactly `nframes` PNGs to a video-only MP4 (no audio).

    Blender renders one extra boundary frame (round(dur*fps)+1); capping the
    encode at `nframes` = span*fps makes every section exactly its span long, so
    the concatenation lands frame-accurate against the song.
    """
    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"no frames rendered in {frames_dir}")
    first = frames[0].stem.split("_")[-1]
    pattern = str(Path(frames_dir) / f"frame_%0{len(first)}d.png")
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=neighbor"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(FPS), "-start_number", first, "-i", pattern,
        "-frames:v", str(nframes), "-vf", even,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"section encode failed: {result.stderr[-800:]}")


def _list_own_images(project):
    """The project's own storyboard PNGs, in filename order (SH010, SH020, ...)."""
    return sorted((OUTPUTS_DIR / f"{project}_storyboards").glob("*.png"))


def _load_shot_prompts(project):
    """Return {shot_id: (prompt, camera)} from the project's planned storyboard.

    The storyboard prompt (projects/<p>/prompts/storyboards.json, keyed by
    shot_id) is what Claude reads to author each scene's motion; the camera
    (projects/<p>/shotlist.csv) gives it the framing.  Missing/unreadable files
    just yield an empty map — the caller then falls back to default motion.
    """
    prompts = {}
    sb = PROJECTS_DIR / project / "prompts" / "storyboards.json"
    if sb.is_file():
        try:
            for item in json.loads(sb.read_text(encoding="utf-8")):
                sid = str(item.get("shot_id", "")).strip()
                if sid:
                    prompts[sid] = str(item.get("prompt", "")).strip()
        except (json.JSONDecodeError, OSError, AttributeError, TypeError):
            prompts = {}

    cameras = {}
    shotlist = PROJECTS_DIR / project / "shotlist.csv"
    if shotlist.is_file():
        try:
            import make_dailies as md
            for s in md.read_shotlist(shotlist):
                cameras[s["shot_id"]] = s.get("camera", "")
        except Exception:                              # noqa: BLE001
            cameras = {}

    return {sid: (p, cameras.get(sid, "")) for sid, p in prompts.items()}


# Claude returns intensity/push as 0..1; map them onto the depth-parallax
# renderer's knobs.  near_pan_frac spans 0.06 (barely moving) .. 0.16 (lively),
# centred on the tuned 0.11 at intensity 0.5.  push spans 0.02 .. 0.14, landing
# on the tuned 0.05 at push 0.25.  An "out" drift pulls back (negative push).
_PAN_MIN, _PAN_MAX = 0.06, 0.16
_PUSH_MIN, _PUSH_MAX = 0.02, 0.14


def _motion_to_render_kwargs(motion):
    """Translate a Claude motion dict into depth_parallax.build_shot kwargs.

    Returns {"drift", "near_pan_frac", "push"}.  drift=None keeps the renderer's
    default rotating-palette behaviour (the tuned fallback).
    """
    intensity = motion.get("intensity", 0.5)
    push_unit = motion.get("push", 0.25)
    drift = motion.get("drift")
    near_pan_frac = _PAN_MIN + (_PAN_MAX - _PAN_MIN) * intensity
    push = _PUSH_MIN + (_PUSH_MAX - _PUSH_MIN) * push_unit
    if drift == "out":                                 # pull-back = negative zoom
        push = -push
    return {"drift": drift, "near_pan_frac": round(near_pan_frac, 4),
            "push": round(push, 4)}


def _plan_own_shots(project, song_duration):
    """Order the project's images into shots that span the whole song.

    Returns [(shot_id, image_path, slot_seconds)] whose slots sum to exactly
    `song_duration` (quantised to the frame grid), so the finished video matches
    the audio length.

    Timing priority (mirrors make_dailies):
      1. Lyric-synced windows, when enough shots quote a lyric (authoritative).
      2. Otherwise stretch the shotlist durations to fill the song and snap the
         cuts onto musical bar boundaries; with no shotlist, split evenly.
    """
    import beat_timing
    from make_dailies import read_shotlist, snap_durations_to_bars

    images = _list_own_images(project)
    img_by_id = {p.stem: p for p in images}

    # Base order + weights: the shotlist, restricted to shots that have an image.
    shotlist = PROJECTS_DIR / project / "shotlist.csv"
    ordered = []
    if shotlist.is_file():
        ordered = [(s["shot_id"], s["duration"])
                   for s in read_shotlist(shotlist) if s["shot_id"] in img_by_id]
    if not ordered:                               # no shotlist / no id match
        ordered = [(p.stem, 1.0) for p in images]
    ids = [i for i, _ in ordered]
    base = [max(0.1, d) for _, d in ordered]

    # 1) Lyric sync, only if it placed (nearly) every shot.
    windows = {k: v for k, v in beat_timing.shot_windows(project).items()
               if k in img_by_id}
    if windows and len(windows) >= max(2, int(0.8 * len(ids))):
        ids = sorted(windows, key=lambda k: windows[k][0])
        slots = [windows[k][1] for k in ids]
    else:
        # 2) Stretch to fill the song, then snap cuts onto bar boundaries.
        total_base = sum(base)
        slots = [b * song_duration / total_base for b in base]
        bar_times, _tempo, bar_seconds = beat_timing.bar_grid(project, song_duration)
        if bar_times and bar_seconds:
            slots, _moved = snap_durations_to_bars(slots, bar_times, bar_seconds)

    # Quantise to the frame grid and pin the total to the song length exactly, so
    # sum(slots) == song_duration and audio/video end together.
    slots = [max(1, round(s * FPS)) / FPS for s in slots]
    target_frames = round(song_duration * FPS)
    drift = target_frames - round(sum(slots) * FPS)
    slots[-1] = max(1, round(slots[-1] * FPS) + drift) / FPS   # absorb rounding

    return [(sid, img_by_id[sid], slot) for sid, slot in zip(ids, slots)]


def _build_from_own_images(project, song, n_images) -> Generator[str, None, None]:
    """General animation path: DEPTH-PARALLAX-animate the project's own images.

    For each storyboard still we estimate a monocular depth map, slice it into
    near/mid/far paper-cutout layers (scripts/depth_parallax.py), and render a
    Blender spec in which each layer pans by a different amount -> genuine 2.5D
    parallax rather than a flat Ken Burns pan/zoom.  Shots are cut together with a
    short crossfade and the project's song is muxed on, writing
    outputs/<project>_animated.mp4.

    Rendered shot-by-shot with a per-shot cache, so a re-invocation resumes
    instead of restarting, and no single render step runs long.  Yields progress
    lines; the final line is "DONE" or starts with "ERROR:".
    """
    import blender_render
    import depth_parallax
    import make_dailies as md

    yield (f"Animating '{project}' from its own {n_images} storyboard images "
           f"via depth-based 2.5D parallax.")

    blender = blender_render.resolve_blender()
    if not blender:
        yield "ERROR: Blender not found (install BlenderFoundation.Blender)"
        return
    ffmpeg = blender_render.resolve_ffmpeg()
    try:
        subprocess.run([ffmpeg, "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        yield "ERROR: ffmpeg not found (install Gyan.FFmpeg)"
        return

    song_duration = md.probe_duration(song)
    if song_duration <= 0:
        yield f"ERROR: could not read song duration from {song}"
        return

    try:
        plan = _plan_own_shots(project, song_duration)
    except Exception as exc:                       # noqa: BLE001
        yield f"ERROR: could not plan shots: {exc}"
        return
    if not plan:
        yield "ERROR: no storyboard images to animate"
        return
    yield (f"Planned {len(plan)} shots over {song_duration:.0f}s "
           f"(depth parallax, CPU render).")

    build_dir = OUTPUTS_DIR / "_parallax_build" / project
    assets_dir = build_dir / "assets"
    build_dir.mkdir(parents=True, exist_ok=True)

    # Claude authors each scene's motion from its storyboard prompt — ONE batched
    # call for the whole video (never a per-shot loop, to keep the cost a single
    # request).  Shots with a prompt get a Claude-chosen drift/intensity/push;
    # shots with none (or if Claude is unavailable) keep the tuned default
    # rotating drift.  Failure here never blocks the render.
    shot_prompts = _load_shot_prompts(project)
    motions = {}
    described = [
        {"shot_id": sid, "prompt": shot_prompts[sid][0],
         "camera": shot_prompts[sid][1], "slot": slot}
        for sid, _img, slot in plan
        if sid in shot_prompts and shot_prompts[sid][0]
    ]
    if described:
        try:
            try:
                from web_ui import claude_api
            except ImportError:
                import claude_api            # when web_ui is itself on sys.path
            motions = claude_api.author_scene_motions(
                [{"shot_id": d["shot_id"], "prompt": d["prompt"],
                  "camera": d["camera"], "duration": d["slot"]} for d in described]
            )
            yield (f"Claude authored per-scene motion for {len(described)} shots "
                   f"(1 API call).")
            for d in described:
                m = motions.get(d["shot_id"], {})
                yield (f"    {d['shot_id']}: drift={m.get('drift') or 'default'} "
                       f"intensity={m.get('intensity', 0.5):.2f} "
                       f"push={m.get('push', 0.25):.2f}"
                       + (f" — {m.get('reason')}" if m.get('reason') else ""))
        except Exception as exc:                       # noqa: BLE001
            yield (f"    (per-scene motion authoring unavailable: {exc}; "
                   f"using default drift)")
            motions = {}
    else:
        yield "    (no storyboard prompts found — using default rotating drift)"

    slots = [s for _, _, s in plan]
    # Crossfade must stay shorter than the shortest shot (else xfade offsets
    # overlap and ffmpeg produces garbage); cap at 0.4x the shortest slot.
    xfade = min(0.5, 0.4 * min(slots)) if len(plan) > 1 else 0.0
    xfade = round(xfade * FPS) / FPS

    clips = []
    for idx, (sid, img, slot) in enumerate(plan, 1):
        # Render each clip xfade longer than its slot so the crossfade overlap is
        # consumed without stealing screen time (see make_dailies.assemble math).
        render_dur = slot + xfade
        nframes = round(render_dur * FPS)
        drift_idx = idx - 1                         # default rotating drift index
        # Claude-authored motion for this shot, if any; else tuned defaults.
        kw = _motion_to_render_kwargs(motions[sid]) if sid in motions else {}
        mp4_path = build_dir / f"{sid}.mp4"
        hash_path = build_dir / f"{sid}.hash"
        motion_sig = (f"{kw.get('drift')}|{kw.get('near_pan_frac')}|"
                      f"{kw.get('push')}")
        want_hash = _sha(f"{img.name}|{img.stat().st_mtime_ns}|{render_dur:.4f}|"
                         f"{drift_idx}|{motion_sig}|{FPS}|v2")

        if (mp4_path.is_file() and hash_path.is_file()
                and hash_path.read_text().strip() == want_hash):
            yield f"[{idx}/{len(plan)}] {sid} ({slot:.1f}s) — cached, skipping"
            clips.append(mp4_path)
            continue

        yield (f"[{idx}/{len(plan)}] {sid} ({slot:.1f}s) — depth + parallax "
               f"(drift={kw.get('drift') or 'default'}), {nframes} frames")
        try:
            spec_path = depth_parallax.build_shot(
                img, assets_dir, render_dur, drift_index=drift_idx, fps=FPS, **kw)
        except Exception as exc:                   # noqa: BLE001
            yield f"ERROR: depth/layer build failed for {sid}: {exc}"
            return

        frames_dir = build_dir / f"frames_{sid}"
        rc = yield from _render_frames(blender, spec_path, frames_dir)
        if rc != 0:
            shutil.rmtree(frames_dir, ignore_errors=True)
            yield (f"ERROR: Blender exited {rc} on shot '{sid}' — render failed or "
                   f"the silent-failure guard tripped. No video written.")
            return
        try:
            _encode_section(ffmpeg, frames_dir, nframes, mp4_path)
        except RuntimeError as exc:
            yield f"ERROR: {exc}"
            return
        finally:
            shutil.rmtree(frames_dir, ignore_errors=True)
        hash_path.write_text(want_hash, encoding="utf-8")
        clips.append(mp4_path)
        yield f"    shot '{sid}' encoded ({nframes} frames = {render_dur:.1f}s)"

    out_path = OUTPUTS_DIR / f"{project}_animated.mp4"
    total = sum(slots)
    yield (f"Crossfading {len(clips)} shots ({xfade:.2f}s transitions) and "
           f"muxing the song ({total:.0f}s)...")
    audio_track = build_dir / "_audio.wav"
    try:
        md.build_audio_track(total, song, [], audio_track)
        md.assemble(clips, slots, xfade, audio_track, out_path)
    except SystemExit:
        yield "ERROR: crossfade assembly / mux failed (see log above)"
        return
    finally:
        audio_track.unlink(missing_ok=True)

    if not out_path.is_file():
        yield "ERROR: assembly produced no output file"
        return
    size_mb = out_path.stat().st_size / (1024 * 1024)
    yield (f"Wrote {out_path} ({size_mb:.1f} MB, {total:.0f}s, {FPS}fps, "
           f"depth-parallax video+audio)")
    yield "DONE"


def build_music_video(project: str) -> Generator[str, None, None]:
    """Build outputs/<project>_animated.mp4 — the project's animated music video.

    General, depth-parallax path: reads the project's song
    (outputs/<project>_song.wav) and its own storyboard images
    (outputs/<project>_storyboards/*.png), authors Claude-driven per-scene motion,
    renders each shot with depth-based 2.5D parallax on the CPU (cached), then
    crossfades the shots and muxes the song.  A project with no storyboard images
    gets a clear "generate storyboards first" error — there is no hardcoded scene.
    Yields progress lines; the final line is "DONE" or starts with "ERROR:".
    """
    if not _VALID_PROJECT_NAME.fullmatch(project or ""):
        yield f"ERROR: invalid project name {project!r}"
        return

    song = OUTPUTS_DIR / f"{project}_song.wav"
    if not song.is_file():
        yield f"ERROR: no song at {song} — generate the song first"
        return

    own_images = _list_own_images(project)
    if not own_images:
        yield (f"ERROR: project '{project}' has no storyboard images to animate — "
               f"generate storyboards first "
               f"(expected outputs/{project}_storyboards/*.png).")
        return

    yield from _build_from_own_images(project, song, len(own_images))


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Build a project's animated music video (depth parallax).")
    p.add_argument("--project", required=True)
    args = p.parse_args()
    for line in build_music_video(args.project):
        print(line, flush=True)
