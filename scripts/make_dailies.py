#!/usr/bin/env python3
"""
Assemble storyboard frames + audio into an animatic MP4 using FFmpeg.

This is an "enhanced slideshow" renderer, not AI video:
  * Ken Burns motion (pans / zooms) via the zoompan filter, varied per shot.
  * Crossfades between shots via the xfade filter.
  * Optional beat-synced cuts that land on musical BAR boundaries (librosa).
  * Optional lyric-synced shots: when the project has a song, scripts/lyric_sync
    transcribes it and puts each shot on screen while the lyric it depicts is
    actually being sung, stretching the animatic across the whole song instead
    of stopping at sum(shotlist durations).  Falls back to the beat-synced
    shotlist durations below whenever that cannot be done confidently.
  * A properly timed audio track (dialogue placed at absolute offsets on a
    timeline, padded/trimmed to shot duration) so audio cannot drift out of
    sync with picture.

Progress is printed to stdout line-by-line and flushed; web_ui/pipeline_api.py
relays those lines into an SSE stream.
"""

import argparse
import csv
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Log to stdout (not stderr) so progress interleaves correctly with print().
# StreamHandler flushes on every emit, so lines arrive one at a time.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"
OUTPUTS_DIR = REPO_ROOT / "outputs"
VOICES_DIR = REPO_ROOT / "voices"

# Output format
FPS = 24
OUT_W = 1280
OUT_H = 720

# zoompan jitter workaround: pre-scale the still WAY above the output size
# before zoompan sees it.  vf_zoompan.c truncates the crop rect to whole input
# pixels, so the bigger the input, the finer the effective sub-pixel step.
# See https://ffmpeg.org/pipermail/ffmpeg-devel/2020-February/256883.html
# (fix proposed 2020, never merged).  6x output width => ~1/6 px quantisation.
# This is CPU/RAM hungry (~0.5 GB); callers must NOT run these in parallel.
PRESCALE_W = 7680
PRESCALE_H = 4320

# Default crossfade length between shots, seconds.
XFADE_DEFAULT = 0.5

# Ken Burns presets: (zoom_from, zoom_to, x_from, x_to, y_from, y_to).
# x/y are normalised pan positions in [0, 1] over the available travel.
# Cycled per shot so the motion never looks mechanical.
KEN_BURNS_PRESETS = [
    (1.00, 1.16, 0.50, 0.50, 0.50, 0.50),   # slow push in, centred
    (1.18, 1.02, 0.50, 0.50, 0.50, 0.50),   # pull back, centred
    (1.10, 1.22, 0.05, 0.95, 0.50, 0.50),   # push in while panning left->right
    (1.20, 1.06, 0.50, 0.50, 0.10, 0.90),   # pull back while drifting down
    (1.10, 1.22, 0.95, 0.05, 0.50, 0.50),   # push in while panning right->left
    (1.04, 1.20, 0.20, 0.75, 0.85, 0.20),   # push in, diagonal drift up-right
    (1.16, 1.00, 0.50, 0.50, 0.50, 0.50),   # gentle pull back, centred
    (1.20, 1.08, 0.80, 0.25, 0.25, 0.80),   # pull back, diagonal drift down-left
]


def log(msg: str):
    """Print a progress line to stdout and flush (SSE contract)."""
    print(msg, flush=True)


def _resolve_tool(name: str) -> str:
    """Find an ffmpeg-family binary, falling back to common Windows install dirs.

    A long-running server process may have been started before ffmpeg was added
    to PATH, so PATH alone is not enough.
    """
    found = shutil.which(name)
    if found:
        return found

    candidates = []
    local = Path.home() / "AppData" / "Local"
    # winget (Gyan.FFmpeg) shim + package dirs
    candidates.append(local / "Microsoft" / "WinGet" / "Links" / f"{name}.exe")
    pkgs = local / "Microsoft" / "WinGet" / "Packages"
    if pkgs.is_dir():
        candidates.extend(sorted(pkgs.glob(f"Gyan.FFmpeg*/**/bin/{name}.exe")))
    # chocolatey / manual installs
    candidates.append(Path("C:/ffmpeg/bin") / f"{name}.exe")
    candidates.append(Path("C:/ProgramData/chocolatey/bin") / f"{name}.exe")
    # linux/docker
    candidates.append(Path("/usr/bin") / name)

    for c in candidates:
        if c.is_file():
            return str(c)
    return name  # let the caller fail with a clear message


FFMPEG = _resolve_tool("ffmpeg")
FFPROBE = _resolve_tool("ffprobe")


def check_ffmpeg() -> bool:
    """Check that FFmpeg and FFprobe are usable."""
    for tool in (FFMPEG, FFPROBE):
        try:
            subprocess.run([tool, "-version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            logger.error(
                "%s not found. Install it (Windows: `winget install Gyan.FFmpeg`, "
                "Debian/Ubuntu: `sudo apt install ffmpeg`) and restart the app.",
                Path(tool).name,
            )
            return False
    return True


def run_ffmpeg(cmd: list, what: str):
    """Run an ffmpeg command, failing loudly with its stderr."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("FFmpeg failed (%s):\n%s", what, result.stderr[-4000:])
        sys.exit(1)
    return result


def read_shotlist(shotlist_csv: Path):
    """Read shot ids + durations from shotlist.csv, preserving order."""
    shots = []
    with open(shotlist_csv, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            shot_id = (row.get("shot_id") or "").strip()
            if not shot_id:
                continue
            try:
                duration = float((row.get("duration") or "3.0").strip())
            except ValueError:
                duration = 3.0
            if duration <= 0:
                duration = 3.0
            shots.append({"shot_id": shot_id, "duration": duration})
    return shots


def find_storyboard(project_name: str, shot_id: str):
    """Locate the storyboard still for a shot, if it exists."""
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = OUTPUTS_DIR / f"{project_name}_storyboards" / f"{shot_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def make_placeholder(temp_dir: Path, shot_id: str) -> Path:
    """Render a neutral placeholder still for a missing storyboard frame."""
    placeholder = temp_dir / f"placeholder_{shot_id}.png"
    run_ffmpeg([
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=0x1e2030:s={OUT_W}x{OUT_H}",
        "-frames:v", "1", "-y", str(placeholder),
    ], f"placeholder for {shot_id}")
    return placeholder


def probe_duration(path: Path):
    """Return the duration of a media file in seconds, or 0.0 if unknown."""
    result = subprocess.run([
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(path),
    ], capture_output=True, text=True)
    if result.returncode != 0:
        return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def probe_image_size(path: Path):
    """Return (width, height) of an image/video file via ffprobe."""
    result = subprocess.run([
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", str(path),
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    lines = result.stdout.strip().splitlines()
    if not lines or "x" not in lines[0]:
        # ffprobe can exit 0 while reporting nothing for a truncated/empty file.
        raise RuntimeError(f"Could not read image dimensions from {path} "
                           f"(is it a valid image?)")
    w, h = lines[0].split("x")
    return int(w), int(h)


def centre_crop_169(width: int, height: int):
    """Largest centred 16:9 rect inside a width x height image.

    Cropping to the output aspect BEFORE the big pre-scale matters: zoompan's
    crop rect inherits the INPUT aspect ratio, so feeding it a square image and
    asking for a 16:9 output would stretch the picture.  Cropping at native
    resolution first is also far cheaper than cropping after upscaling, and
    loses nothing (we are upscaling either way).
    """
    if width * 9 >= height * 16:
        # wider than 16:9 -> full height, crop the sides
        crop_h = height
        crop_w = (height * 16) // 9
    else:
        # taller than 16:9 -> full width, crop top/bottom
        crop_w = width
        crop_h = (width * 9) // 16
    crop_w -= crop_w % 2
    crop_h -= crop_h % 2
    return crop_w, crop_h, (width - crop_w) // 2, (height - crop_h) // 2


def find_music(project_name: str):
    """Locate the music bed for a project, if any.

    Order matters: _song.wav is the finished article (instruments *and* sung
    vocals), so it wins.  _instrumental.wav is the backing track alone and is
    only a fallback for projects that have no song yet — preferring it would
    silently drop the vocals from the animatic.
    """
    for suffix in ("_song.wav", "_instrumental.wav"):
        candidate = OUTPUTS_DIR / f"{project_name}{suffix}"
        if candidate.exists():
            return candidate
    return None


def read_dialogue(project_dir: Path, project_name: str):
    """Map shot_id -> dialogue WAV path, for shots that have recorded audio."""
    dialogue_csv = project_dir / "dialogue.csv"
    voices_dir = VOICES_DIR / project_name
    result = {}
    if not dialogue_csv.exists():
        return result
    with open(dialogue_csv, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            shot_id = (row.get("shot_id") or "").strip()
            character = (row.get("character") or "").strip()
            if not shot_id or shot_id in result:
                continue
            candidate = voices_dir / f"{shot_id}_{character}.wav"
            if candidate.exists():
                result[shot_id] = candidate
    return result


def bar_times_from_arrangement(project_dir: Path, audio_duration: float = 0.0):
    """Read the exact bar grid from arrangement.json, if the project has one.

    This is the authoritative source for music the app generated itself:
    web_ui/music_gen.py renders each bar back-to-back starting at t=0, so the
    downbeats are exact by construction.  Always prefer this over guessing the
    beat grid from the audio signal.

    The arrangement describes the *instrumental*, which is often shorter than
    the finished song: the test project's arrangement is 32 bars (64.0s) while
    the ACE-Step song built from it runs 120.0s.  A grid that stops at 64s would
    leave every later cut with nothing to snap to, so once the arrangement runs
    out we keep laying down bars of the same length until we cover the audio.
    That is sound for this app's metronomic beds -- the 120s song measures at
    119.68 BPM, i.e. the same grid -- but it does assume a steady tempo.
    """
    arrangement_json = project_dir / "arrangement.json"
    if not arrangement_json.exists():
        return None, None, None
    try:
        import json
        with open(arrangement_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        tempo = float(data.get("tempo_bpm", 0))
        bars = data.get("bars") or []
        if tempo <= 0 or not bars:
            return None, None, None
        beat_seconds = 60.0 / tempo
        bar_times = []
        t = 0.0
        for bar in bars:
            bar_times.append(t)
            t += float(bar.get("beats", 4)) * beat_seconds
        bar_times.append(t)  # end of the final bar is also a valid cut point
        arrangement_total = t
        bar_seconds = arrangement_total / len(bars)

        extended = 0
        if audio_duration > arrangement_total + bar_seconds * 0.5 and bar_seconds > 0:
            while t < audio_duration:
                t += bar_seconds
                bar_times.append(t)
                extended += 1

        note = (f", extended by {extended} bars to cover {audio_duration:.2f}s of audio"
                if extended else "")
        log(f"Bar grid from arrangement.json: {tempo:.1f} BPM, {len(bars)} bars, "
            f"{bar_seconds:.2f}s per bar ({arrangement_total:.2f}s total){note}")
        return bar_times, tempo, bar_seconds
    except Exception as exc:
        logger.warning("Could not read arrangement.json (%s); will analyse audio instead.", exc)
        return None, None, None


def build_bar_grid(project_dir: Path, music: Path, audio_duration: float):
    """Best available bar grid for `music`: arrangement first, then librosa."""
    bar_times, tempo, bar_seconds = bar_times_from_arrangement(project_dir, audio_duration)
    if not bar_times:
        bar_times, tempo, bar_seconds = detect_bar_times(music)
    return bar_times, tempo, bar_seconds


def _normalise_tempo(tempo: float, low: float = 70.0, high: float = 160.0):
    """Fold a tempo estimate into a musically sensible range.

    Beat trackers routinely land an octave out: our 120 BPM test bed reports as
    30 BPM because the only strong onsets are the chord changes (one per bar).
    Doubling/halving preserves the grid while fixing the octave.
    """
    if tempo <= 0:
        return 0.0
    guard = 0
    while tempo < low and guard < 8:
        tempo *= 2.0
        guard += 1
    while tempo >= high and guard < 8:
        tempo /= 2.0
        guard += 1
    return tempo


def detect_bar_times(music_path: Path, beats_per_bar: int = 4):
    """Detect downbeat (bar boundary) times in a music file using librosa.

    Fallback for audio with no arrangement.json (e.g. an imported song).
    Returns (bar_times, tempo, bar_seconds) or (None, None, None) on failure.

    Cutting on every beat would mean a cut every 0.5s at 120 BPM, which is
    unwatchable -- so we only ever cut on bar boundaries (downbeats).

    Limitation: this anchors a FIXED bar grid at the first detected beat, which
    assumes a steady tempo.  That holds for this app's metronomic music beds;
    a live recording that drifts would gradually slip off the grid.
    """
    try:
        import librosa
    except ImportError:
        logger.warning("librosa not installed; falling back to shotlist durations. "
                       "Install with: pip install librosa")
        return None, None, None

    try:
        import numpy as np

        log(f"Analysing music for beats: {music_path.name}")
        y, sr = librosa.load(str(music_path), sr=None, mono=True)
        duration = len(y) / sr

        tempo, beat_times = 0.0, []
        # Try median aggregation first (librosa's default, robust for real
        # percussive music), then mean.  A synthesised chord bed moves only a
        # few frequency bins per onset, so the median across bins is flat ZERO
        # and finds no beats at all -- mean aggregation still sees the change.
        # trim=False also matters: with sparse onsets the default trim=True
        # discards every detected beat.
        for aggregate in (np.median, np.mean):
            env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=aggregate)
            if not env.any():
                continue
            t, beat_frames = librosa.beat.beat_track(
                onset_envelope=env, sr=sr, units="frames", trim=False,
            )
            t = float(t.item() if hasattr(t, "item") else t)
            if t > 0 and len(beat_frames) >= 2:
                tempo = t
                beat_times = librosa.frames_to_time(beat_frames, sr=sr)
                break
    except Exception as exc:
        logger.warning("Beat detection failed (%s); falling back to shotlist durations.", exc)
        return None, None, None

    if len(beat_times) < 2 or tempo <= 0:
        logger.warning("No usable beat grid detected; falling back to shotlist durations.")
        return None, None, None

    raw_tempo = tempo
    tempo = _normalise_tempo(tempo)
    if tempo <= 0:
        return None, None, None

    bar_seconds = beats_per_bar * 60.0 / tempo
    if bar_seconds <= 0 or bar_seconds > duration:
        logger.warning("Implausible bar length (%.2fs); falling back to shotlist durations.",
                       bar_seconds)
        return None, None, None

    anchor = float(beat_times[0])
    n_bars = int((duration - anchor) / bar_seconds) + 1
    bar_times = [anchor + k * bar_seconds for k in range(max(2, n_bars))]

    octave_note = "" if abs(raw_tempo - tempo) < 0.1 else f" (raw estimate {raw_tempo:.1f}, octave-corrected)"
    log(f"Detected tempo ~{tempo:.1f} BPM{octave_note}, {len(beat_times)} beats, "
        f"{len(bar_times)} bars ({beats_per_bar}/4, {bar_seconds:.2f}s per bar)")
    return bar_times, tempo, bar_seconds


def snap_durations_to_bars(durations, bar_times, bar_seconds, min_duration=1.5):
    """Shift each shot boundary onto the nearest musical bar boundary.

    Keeps the running time close to the shotlist (each boundary moves by at most
    half a bar) while guaranteeing every shot stays at least `min_duration` long
    and boundaries stay strictly increasing.
    """
    if not bar_times:
        return list(durations), 0

    max_shift = bar_seconds * 0.5
    # Absolute end time of each shot, per the shotlist.
    boundaries = []
    running = 0.0
    for d in durations:
        running += d
        boundaries.append(running)

    snapped = []
    prev = 0.0
    used = set()
    moved = 0
    for target in boundaries:
        candidates = [
            t for t in bar_times
            if t >= prev + min_duration and t not in used
        ]
        chosen = None
        if candidates:
            best = min(candidates, key=lambda t: abs(t - target))
            if abs(best - target) <= max_shift:
                chosen = best
        if chosen is None:
            chosen = max(target, prev + min_duration)
        else:
            used.add(chosen)
            if abs(chosen - target) > 1e-6:
                moved += 1
        snapped.append(chosen)
        prev = chosen

    out = []
    prev = 0.0
    for b in snapped:
        out.append(round(b - prev, 5))
        prev = b
    return out, moved


def ken_burns_chain(preset_index: int, n_frames: int, crop, prescale):
    """Build the scale/crop/zoompan filter chain for one shot."""
    zf, zt, xf, xt, yf, yt = KEN_BURNS_PRESETS[preset_index % len(KEN_BURNS_PRESETS)]
    cw, ch, cx, cy = crop
    pw, ph = prescale

    # Progress 0 -> 1 across the shot. zoompan's `on` is the output frame index.
    last = max(1, n_frames - 1)
    p = f"(on/{last})"
    z_expr = f"{zf:.4f}+({zt - zf:.4f})*{p}"
    x_expr = f"(iw-iw/zoom)*({xf:.4f}+({xt - xf:.4f})*{p})"
    y_expr = f"(ih-ih/zoom)*({yf:.4f}+({yt - yf:.4f})*{p})"

    return (
        f"crop={cw}:{ch}:{cx}:{cy},"
        f"scale={pw}:{ph}:flags=lanczos,"
        # yuv444p before zoompan: under a chroma-subsampled format zoompan also
        # snaps the crop origin to EVEN input pixels, doubling the quantisation
        # step.  Full chroma removes that constraint; we convert back after.
        f"format=yuv444p,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d=1:s={OUT_W}x{OUT_H}:fps={FPS},"
        f"format=yuv420p"
    )


def render_shot(image: Path, duration: float, preset_index, out_path: Path):
    """Render one shot from a still image, with or without Ken Burns motion.

    `preset_index` selects a KEN_BURNS_PRESETS entry; pass None for a static
    (plain slideshow) render.

    Deliberately sequential / single ffmpeg process: the 7680x4320 pre-scale is
    ~0.5 GB of RAM and this box only has ~7.5 GB free.  Do not parallelise.
    """
    n_frames = max(2, int(round(duration * FPS)))
    crop = centre_crop_169(*probe_image_size(image))

    if preset_index is None:
        cw, ch, cx, cy = crop
        chain = (f"crop={cw}:{ch}:{cx}:{cy},"
                 f"scale={OUT_W}:{OUT_H}:flags=lanczos,format=yuv420p")
    else:
        chain = ken_burns_chain(preset_index, n_frames, crop,
                                (PRESCALE_W, PRESCALE_H))

    run_ffmpeg([
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", str(image),
        "-frames:v", str(n_frames),
        "-filter_threads", "2",          # keep the 7680-wide buffers bounded
        "-vf", chain,
        "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ], f"render for {image.name}")


def build_audio_track(total_duration: float, music: Path,
                      dialogue_clips, out_path: Path):
    """Build the audio track on an explicit timeline.

    dialogue_clips: list of (path, start_seconds, shot_duration).

    Every dialogue clip is trimmed to its shot duration and placed at its
    ABSOLUTE start offset with adelay, rather than being blindly concatenated.
    That is the fix for the drift bug: a clip whose length != its shot duration
    can no longer push everything after it out of sync.  The finished track is
    padded/trimmed to exactly `total_duration` so audio and video end together.
    """
    if not music and not dialogue_clips:
        return None

    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    parts = []
    labels = []
    idx = 0

    if music:
        cmd.extend(["-i", str(music)])
        # Duck the music under dialogue if there is any.
        gain = 0.35 if dialogue_clips else 1.0
        parts.append(
            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"atrim=0:{total_duration:.5f},asetpts=PTS-STARTPTS,"
            f"volume={gain}[music]"
        )
        labels.append("[music]")
        idx += 1

    for n, (path, start, shot_duration) in enumerate(dialogue_clips):
        cmd.extend(["-i", str(path)])
        delay_ms = int(round(start * 1000))
        parts.append(
            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            # Trim anything longer than its shot; shorter clips simply end early
            # and the mix supplies silence for the remainder of the shot.
            f"atrim=0:{shot_duration:.5f},asetpts=PTS-STARTPTS,"
            f"adelay={delay_ms}:all=1[d{n}]"
        )
        labels.append(f"[d{n}]")
        idx += 1

    if len(labels) == 1:
        mixed = labels[0]
    else:
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:"
                     f"dropout_transition=0[mix]")
        mixed = "[mix]"

    fade_start = max(0.0, total_duration - 0.6)
    parts.append(
        f"{mixed}apad,atrim=0:{total_duration:.5f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={fade_start:.5f}:d=0.6,"
        f"alimiter=limit=0.95[aout]"
    )

    cmd.extend([
        "-filter_complex", ";".join(parts),
        "-map", "[aout]",
        "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(out_path),
    ])
    run_ffmpeg(cmd, "audio timeline")
    return out_path


def assemble(clips, durations, xfade_duration: float, audio_track,
             output_path: Path):
    """Crossfade the rendered shot clips together and mux the audio.

    EVERY clip (including the last) is rendered `xfade_duration` longer than its
    shot duration, so that after the overlaps are consumed each shot occupies
    exactly its intended slot and the total equals sum(durations):
        total = offset(last) + len(last)
              = (sum(D[:-1]) - d) + (D[-1] + d) = sum(D)
    Shortening only the last clip makes the video exactly one crossfade short of
    the audio.
    """
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    for clip in clips:
        cmd.extend(["-i", str(clip)])

    audio_index = len(clips)
    if audio_track:
        cmd.extend(["-i", str(audio_track)])

    parts = []
    if len(clips) == 1:
        parts.append("[0:v]null[vout]")
    elif xfade_duration <= 0:
        # xfade with duration=0 is broken in ffmpeg: instead of a hard cut it
        # silently truncates the result (3s+4s clips -> 4.04s, not 7s).  With no
        # crossfade the clips are exactly their slots, so plain concat is both
        # correct and cheaper.
        parts.append("".join(f"[{i}:v]" for i in range(len(clips))) +
                     f"concat=n={len(clips)}:v=1:a=0[vout]")
    else:
        current = "[0:v]"
        elapsed = 0.0
        for i in range(1, len(clips)):
            elapsed += durations[i - 1]
            # Fade completes exactly on the shot boundary (= the downbeat when
            # beat-sync is on), so the incoming shot is fully visible on the beat.
            offset = max(0.0, elapsed - xfade_duration)
            label = "[vout]" if i == len(clips) - 1 else f"[v{i}]"
            parts.append(
                f"{current}[{i}:v]xfade=transition=fade:"
                f"duration={xfade_duration:.5f}:offset={offset:.5f}{label}"
            )
            current = label
    cmd.extend(["-filter_complex", ";".join(parts), "-map", "[vout]"])
    if audio_track:
        cmd.extend(["-map", f"{audio_index}:a", "-c:a", "aac", "-b:a", "192k"])
    cmd.extend([
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-movflags", "+faststart",
        str(output_path),
    ])
    run_ffmpeg(cmd, "crossfade assembly")


def create_animatic(project_name: str, output_path: Path, beat_sync: bool,
                    xfade_duration: float, motion: bool,
                    use_lyric_sync: bool = True):
    """Create animatic from storyboard images and audio."""
    project_dir = PROJECTS_DIR / project_name
    shotlist_csv = project_dir / "shotlist.csv"

    if not shotlist_csv.exists():
        logger.error("shotlist.csv not found: %s", shotlist_csv)
        sys.exit(1)

    shots = read_shotlist(shotlist_csv)
    if not shots:
        logger.error("No shots found in shotlist.csv")
        sys.exit(1)

    durations = [s["duration"] for s in shots]
    shotlist_total = sum(durations)
    log(f"Loaded {len(shots)} shots from shotlist.csv ({shotlist_total:.2f}s)")

    # ---- Beat sync (optional, degrades gracefully) ----
    music = find_music(project_name)
    if music:
        log(f"Music bed: {music.name}")
    else:
        log("No music bed found; audio will be dialogue-only (or silent).")

    music_duration = probe_duration(music) if music else 0.0

    bar_times = bar_seconds = None
    if beat_sync and music:
        # Prefer the exact grid the music was generated from; only analyse the
        # waveform when there is no arrangement (e.g. an imported song).
        bar_times, tempo, bar_seconds = build_bar_grid(
            project_dir, music, music_duration)

    # ---- Lyric sync: put each shot on the lyric it actually depicts ----
    # This supersedes duration-snapping when it succeeds, because it decides
    # both the order and the length of every shot from the vocal itself.  It
    # still snaps its cuts to `bar_times`, so cuts keep landing on downbeats.
    aligned = None
    if use_lyric_sync and music and music_duration > 0:
        try:
            import lyric_sync
            aligned = lyric_sync.plan(
                project_dir, shots, music, music_duration, bar_times,
                OUTPUTS_DIR / f"{project_name}_transcript.json",
            )
        except Exception as exc:
            # Never let alignment take the animatic down with it.
            logger.warning("Lyric sync failed (%s); falling back to beat sync.", exc)
            aligned = None

    if aligned:
        shots, durations, _report = aligned
    elif beat_sync and music and bar_times:
        durations, moved = snap_durations_to_bars(durations, bar_times, bar_seconds)
        log(f"Beat-sync: snapped {moved}/{len(durations)} cuts onto bar boundaries "
            f"(total {sum(durations):.2f}s vs shotlist {shotlist_total:.2f}s)")
    elif beat_sync and music:
        log("Beat-sync unavailable; using shotlist durations.")
    elif beat_sync:
        log("Beat-sync requested but no music found; using shotlist durations.")
    else:
        log("Beat-sync disabled; using shotlist durations.")

    # Crossfade must be shorter than the shortest shot, or xfade offsets go
    # negative / overlap and ffmpeg produces garbage.
    if len(shots) > 1:
        max_xfade = max(0.0, min(durations) * 0.4)
        if xfade_duration > max_xfade:
            log(f"Reducing crossfade {xfade_duration:.2f}s -> {max_xfade:.2f}s "
                f"(shortest shot is {min(durations):.2f}s)")
            xfade_duration = max_xfade
    else:
        xfade_duration = 0.0

    # Quantise every duration onto the frame grid.  Clips can only ever be a
    # whole number of frames, so a duration like the beat-detector's 1.9969s bar
    # would make the encoded video land up to half a frame away from the total
    # the audio track is built for.  Rounding here keeps video length, audio
    # length and the xfade offsets exactly consistent.
    durations = [max(1, round(d * FPS)) / FPS for d in durations]
    xfade_duration = round(xfade_duration * FPS) / FPS
    total_duration = sum(durations)

    dialogue_map = read_dialogue(project_dir, project_name)

    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        clips = []
        dialogue_clips = []
        start = 0.0

        for i, shot in enumerate(shots):
            shot_id = shot["shot_id"]
            duration = durations[i]

            image = find_storyboard(project_name, shot_id)
            if image is None:
                logger.warning("Storyboard not found for %s, using placeholder", shot_id)
                image = make_placeholder(temp_dir, shot_id)

            if shot_id in dialogue_map:
                dialogue_clips.append((dialogue_map[shot_id], start, duration))

            # Render EVERY clip `xfade_duration` longer than its slot, so the
            # crossfade overlap is consumed without stealing screen time.
            # Chaining xfade gives total = offset(last) + len(last)
            #   = (sum(D[:-1]) - d) + (D[-1] + d) = sum(D)
            # so the last clip needs the extra d as well, or the video ends up
            # exactly one crossfade shorter than the audio.
            render_duration = duration + xfade_duration

            log(f"[{i + 1}/{len(shots)}] Rendering {shot_id}: {image.name} "
                f"{duration:.2f}s @ {start:.2f}s"
                f"{' + dialogue' if shot_id in dialogue_map else ''}")

            clip = temp_dir / f"clip_{i:03d}.mp4"
            render_shot(image, render_duration, i if motion else None, clip)
            clips.append(clip)
            start += duration

        # ---- Audio on a real timeline ----
        audio_track = None
        if music or dialogue_clips:
            log(f"Building audio timeline ({total_duration:.2f}s, "
                f"{len(dialogue_clips)} dialogue clip(s))")
            audio_track = build_audio_track(
                total_duration, music, dialogue_clips,
                temp_dir / "audio.wav",
            )

        log(f"Crossfading {len(clips)} clips ({xfade_duration:.2f}s transitions)")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        assemble(clips, durations, xfade_duration, audio_track, output_path)

    log(f"Animatic created: {output_path} ({total_duration:.2f}s)")


def main():
    parser = argparse.ArgumentParser(description="Create animatic from storyboards and audio")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--no-beat-sync", action="store_true",
                        help="Do not snap cuts onto musical bar boundaries. Combine "
                             "with --no-lyric-sync for shotlist durations verbatim")
    parser.add_argument("--no-lyric-sync", action="store_true",
                        help="Do not transcribe the song to align shots with the "
                             "lyric they depict; use shotlist durations instead")
    parser.add_argument("--no-motion", action="store_true",
                        help="Disable Ken Burns motion (plain slideshow)")
    parser.add_argument("--xfade", type=float, default=XFADE_DEFAULT,
                        help=f"Crossfade duration in seconds (default {XFADE_DEFAULT})")
    args = parser.parse_args()

    if not check_ffmpeg():
        sys.exit(1)

    if not (PROJECTS_DIR / args.project).exists():
        logger.error("Project directory not found: %s", PROJECTS_DIR / args.project)
        sys.exit(1)

    output_path = OUTPUTS_DIR / f"{args.project}_animatic.mp4"
    create_animatic(
        args.project,
        output_path,
        beat_sync=not args.no_beat_sync,
        xfade_duration=max(0.0, args.xfade),
        motion=not args.no_motion,
        use_lyric_sync=not args.no_lyric_sync,
    )


if __name__ == "__main__":
    main()
