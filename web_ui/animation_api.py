"""
animation_api.py — build the full-length animated music video for a project.

`build_music_video(project)` is the single entry point a server route calls.  It
renders the "Wheels on the Bus" video section by section (verse by verse) on the
CPU via Blender, then concatenates the sections and muxes the project's song,
writing outputs/<project>_animated.mp4.

Why section by section:
  * Each verse is a different action on the SAME reused bus scene kit, so the
    spec author (scripts/build_bus_video.py) only varies the motion per section.
  * Rendering in discrete, cached sections keeps any single render step short
    (~1-3 min) and lets a re-invocation resume instead of restarting a ~25 min
    job from zero — a section whose spec is unchanged and whose MP4 already
    exists is skipped.
  * The section MP4s share one encoder config, so they concatenate losslessly
    (ffmpeg concat demuxer, -c copy) into exactly song-length video, giving tight
    A/V sync at every cut.

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


def _frame_count(section) -> int:
    """Exact frame count for a section: span * fps (no +1 boundary duplicate)."""
    return round((section["end"] - section["start"]) * FPS)


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


def _concat_and_mux(ffmpeg, section_mp4s, song, out_path, work_dir) -> None:
    """Concat the section MP4s (lossless) and mux the song, into out_path.

    Intermediates live in `work_dir` (the per-project build dir), not in the
    shared outputs/ folder, so concurrent builds of different projects cannot
    clobber each other's concat list, and they are always cleaned up.
    """
    listfile = work_dir / "_concat_list.txt"
    concat = work_dir / "_concat_video.mp4"
    try:
        listfile.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in section_mp4s), encoding="utf-8")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-c", "copy", str(concat)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"concat failed: {r.stderr[-800:]}")

        # Video length is authoritative; -shortest bounds the mux to it.  Video
        # is exactly the song length, so nothing is truncated.
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(concat), "-i", str(song),
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-map", "0:v:0", "-map", "1:a:0", "-shortest",
               "-movflags", "+faststart", str(out_path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"mux failed: {r.stderr[-800:]}")
    finally:
        listfile.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)


def _list_own_images(project):
    """The project's own storyboard PNGs, in filename order (SH010, SH020, ...)."""
    return sorted((OUTPUTS_DIR / f"{project}_storyboards").glob("*.png"))


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

    Rendered section-by-section with a per-shot cache (same design as the bus
    showcase path), so a re-invocation resumes instead of restarting, and no
    single render step runs long.  Yields progress lines; the final line is
    "DONE" or starts with "ERROR:".
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
        drift = idx - 1                            # cycle camera drift per shot
        mp4_path = build_dir / f"{sid}.mp4"
        hash_path = build_dir / f"{sid}.hash"
        want_hash = _sha(f"{img.name}|{img.stat().st_mtime_ns}|{render_dur:.4f}|"
                         f"{drift}|{FPS}|v1")

        if (mp4_path.is_file() and hash_path.is_file()
                and hash_path.read_text().strip() == want_hash):
            yield f"[{idx}/{len(plan)}] {sid} ({slot:.1f}s) — cached, skipping"
            clips.append(mp4_path)
            continue

        yield (f"[{idx}/{len(plan)}] {sid} ({slot:.1f}s) — depth + parallax, "
               f"{nframes} frames")
        try:
            spec_path = depth_parallax.build_shot(
                img, assets_dir, render_dur, drift_index=drift, fps=FPS)
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
    """Build outputs/<project>_animated.mp4 — the full-length bus music video.

    Reads the project's song (outputs/<project>_song.wav) and the shared bus
    scene kit (outputs/scene_kit_bus), authors one render spec per verse-section,
    renders each on the CPU via Blender (cached), then concatenates and muxes the
    song.  Yields progress lines; the final line is "DONE" or starts with
    "ERROR:".
    """
    if not _VALID_PROJECT_NAME.fullmatch(project or ""):
        yield f"ERROR: invalid project name {project!r}"
        return

    song = OUTPUTS_DIR / f"{project}_song.wav"
    kit = OUTPUTS_DIR / "scene_kit_bus"
    if not song.is_file():
        yield f"ERROR: no song at {song} — generate the song first"
        return

    # The bus scene kit is a hand-built SHOWCASE for one specific scene — it is
    # NOT a template for arbitrary projects.  Any project that has its own
    # storyboard images must animate THOSE, not the bus.  Only a project with no
    # images of its own (the bus showcase) falls back to the bus kit.
    own_images = sorted((OUTPUTS_DIR / f"{project}_storyboards").glob("*.png"))
    if own_images:
        yield from _build_from_own_images(project, song, len(own_images))
        return
    if not (kit / "manifest.json").is_file():
        yield (f"ERROR: project '{project}' has no storyboard images to animate "
               f"(generate storyboards first), and the bus showcase kit is missing.")
        return

    import build_bus_video as bbv
    import beat_timing
    import blender_render

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

    sections = bbv.plan_sections()
    total = sum(s["end"] - s["start"] for s in sections)
    yield f"Music video: {len(sections)} verse-sections, {total:.0f}s @ {FPS}fps (CPU render)"

    # One shared beat grid for the whole song (eighth notes), sliced per section.
    yield "Deriving the beat grid from the song..."
    beats = beat_timing.beat_grid(project, subdiv=2, audio_duration=total)
    if not beats:
        yield "  (no beat grid available — motion will still play, just not beat-locked)"

    build_dir = OUTPUTS_DIR / "_bus_build" / project
    build_dir.mkdir(parents=True, exist_ok=True)
    overlays = bbv.ensure_overlay_assets(OUTPUTS_DIR / "_bus_build")

    section_mp4s = []
    for idx, sec in enumerate(sections, 1):
        name = sec["name"]
        span = sec["end"] - sec["start"]
        nframes = _frame_count(sec)
        spec = bbv.build_section_spec(sec, beats, overlays)
        spec_json = json.dumps(spec, indent=2)
        spec_path = build_dir / f"{name}.json"
        mp4_path = build_dir / f"{name}.mp4"
        hash_path = build_dir / f"{name}.hash"
        want_hash = _sha(spec_json)

        # Resume: skip a section whose spec is unchanged and MP4 already present.
        if (mp4_path.is_file() and hash_path.is_file()
                and hash_path.read_text().strip() == want_hash):
            yield (f"[{idx}/{len(sections)}] {name} ({sec['start']:.0f}-{sec['end']:.0f}s, "
                   f"{sec['action']}) — cached, skipping")
            section_mp4s.append(mp4_path)
            continue

        spec_path.write_text(spec_json, encoding="utf-8")
        yield (f"[{idx}/{len(sections)}] {name} ({sec['start']:.0f}-{sec['end']:.0f}s, "
               f"{sec['action']}) — rendering {nframes} frames")

        frames_dir = build_dir / f"frames_{name}"
        rc = yield from _render_frames(blender, spec_path, frames_dir)
        if rc != 0:
            shutil.rmtree(frames_dir, ignore_errors=True)
            yield (f"ERROR: Blender exited {rc} on section '{name}' — render failed "
                   f"or the silent-failure guard tripped. No video written.")
            return
        try:
            _encode_section(ffmpeg, frames_dir, nframes, mp4_path)
        except RuntimeError as exc:
            yield f"ERROR: {exc}"
            return
        finally:
            shutil.rmtree(frames_dir, ignore_errors=True)   # reclaim disk
        hash_path.write_text(want_hash, encoding="utf-8")
        section_mp4s.append(mp4_path)
        yield f"    section '{name}' encoded ({nframes} frames = {span:.1f}s)"

    out_path = OUTPUTS_DIR / f"{project}_animated.mp4"
    yield f"Concatenating {len(section_mp4s)} sections and muxing the song..."
    try:
        _concat_and_mux(ffmpeg, section_mp4s, song, out_path, build_dir)
    except RuntimeError as exc:
        yield f"ERROR: {exc}"
        return

    size_mb = out_path.stat().st_size / (1024 * 1024)
    yield f"Wrote {out_path} ({size_mb:.1f} MB, {total:.0f}s, {FPS}fps, video+audio)"
    yield "DONE"


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Build the full-length bus music video.")
    p.add_argument("--project", default="wheels_hero")
    args = p.parse_args()
    for line in build_music_video(args.project):
        print(line, flush=True)
