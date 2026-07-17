#!/usr/bin/env python3
"""Make a project's musical beat grid reachable when authoring render specs.

A shot in the animation occupies an absolute slice of the song (e.g. 24.0-36.0s).
To make the motion land ON the beat, the spec author needs the beat times inside
that slice -- expressed RELATIVE to the shot start, because a spec's keyframe `t`
is measured from the shot's own t=0.  This module provides exactly that, without
re-deriving the beat grid: it reuses make_dailies' authoritative bar grid (read
from arrangement.json, the metronomic grid the music was generated on) and
subdivides each bar into its beats.

This is a small authoring helper, not a pipeline stage -- the assembly agent that
builds on-beat specs is expected to call `accents_in_window()` (or run the CLI).

CLI:
    python scripts/beat_timing.py --project test_win --start 24 --end 36
    python scripts/beat_timing.py --project test_win --start 24 --end 36 --subdiv 2
    python scripts/beat_timing.py --project test_win --shot SH030   # needs lyric sync
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"
OUTPUTS_DIR = REPO_ROOT / "outputs"

# Reuse the exact bar-grid + music resolution the animatic uses, so beats here
# and cuts there share one source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_dailies import (  # noqa: E402
    build_bar_grid, find_music, probe_duration, bar_times_from_arrangement,
)


def _beats_per_bar(project_dir: Path) -> int:
    """Beats per bar from arrangement.json (default 4/4)."""
    arr = project_dir / "arrangement.json"
    if arr.exists():
        try:
            with open(arr, "r", encoding="utf-8") as f:
                bars = json.load(f).get("bars") or []
            if bars:
                return int(bars[0].get("beats", 4))
        except (OSError, ValueError, TypeError):
            pass
    return 4


def bar_grid(project: str, audio_duration: float = 0.0):
    """Return (bar_times, tempo, bar_seconds) for a project.

    Prefers arrangement.json; falls back to analysing the music. `audio_duration`
    lets the grid be extended to cover a song longer than the arrangement (same
    behaviour as the animatic).  Returns ([], None, None) if nothing is available.
    """
    project_dir = PROJECTS_DIR / project
    music = find_music(project)
    if audio_duration <= 0 and music:
        audio_duration = probe_duration(music)
    bar_times, tempo, bar_seconds = bar_times_from_arrangement(project_dir, audio_duration)
    if not bar_times and music:
        bar_times, tempo, bar_seconds = build_bar_grid(project_dir, music, audio_duration)
    return (bar_times or []), tempo, bar_seconds


def beat_grid(project: str, subdiv: int = 1, audio_duration: float = 0.0):
    """Every beat time in the song (absolute seconds), ascending.

    `subdiv` further divides each beat: subdiv=1 = quarter notes (the beat),
    subdiv=2 = eighth notes, etc., for finer accents.
    """
    bar_times, tempo, bar_seconds = bar_grid(project, audio_duration)
    if not bar_times or len(bar_times) < 2:
        return []
    bpb = _beats_per_bar(PROJECTS_DIR / project)
    steps = max(1, bpb * max(1, subdiv))
    beats = []
    for a, b in zip(bar_times, bar_times[1:]):
        for k in range(steps):
            beats.append(a + (b - a) * k / steps)
    beats.append(bar_times[-1])
    return beats


def beats_in_window(project: str, start: float, end: float, subdiv: int = 1,
                    audio_duration: float = 0.0):
    """Beats that fall inside [start, end], both absolute and window-relative.

    Returns {"absolute": [...], "relative": [...]}.  The relative times are what
    a spec author drops keyframes on, since a shot's keyframe `t` starts at 0.
    """
    grid = beat_grid(project, subdiv, audio_duration)
    absolute = [t for t in grid if start - 1e-6 <= t <= end + 1e-6]
    relative = [round(t - start, 4) for t in absolute]
    return {"absolute": [round(t, 4) for t in absolute], "relative": relative}


def accents_in_window(project: str, start: float, end: float, subdiv: int = 1):
    """Convenience: just the window-relative beat times, for on-beat keyframes."""
    return beats_in_window(project, start, end, subdiv)["relative"]


def shot_windows(project: str):
    """Best-effort {shot_id: (start, duration)} from lyric sync.

    Requires faster-whisper (transcription); returns {} if lyric sync is
    unavailable or cannot align.  This is the bridge from "which shot" to "which
    seconds of the song", so accents_in_window can then be called per shot.
    """
    project_dir = PROJECTS_DIR / project
    music = find_music(project)
    if not music:
        return {}
    try:
        import lyric_sync
        from make_dailies import read_shotlist
        shots = read_shotlist(project_dir / "shotlist.csv")
        duration = probe_duration(music)
        bar_times, _t, _b = bar_grid(project, duration)
        plan = lyric_sync.plan(project_dir, shots, music, duration, bar_times,
                               OUTPUTS_DIR / f"{project}_transcript.json")
    except Exception as exc:  # noqa: BLE001 - never let authoring hard-fail
        print(f"[beat_timing] shot windows unavailable: {exc}", flush=True)
        return {}
    if not plan:
        return {}
    _shots, durations, report = plan
    return {r["shot_id"]: (round(r["start"], 4), round(r["duration"], 4))
            for r in report}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--project", required=True)
    p.add_argument("--start", type=float, help="window start (seconds into song)")
    p.add_argument("--end", type=float, help="window end (seconds)")
    p.add_argument("--shot", help="resolve the window from this shot via lyric sync")
    p.add_argument("--subdiv", type=int, default=1,
                   help="1=beats, 2=eighths, 4=sixteenths (default 1)")
    args = p.parse_args()

    if not (PROJECTS_DIR / args.project).exists():
        print(f"project not found: {args.project}")
        sys.exit(1)

    start, end = args.start, args.end
    if args.shot:
        windows = shot_windows(args.project)
        if args.shot not in windows:
            print(f"no lyric-sync window for shot {args.shot} "
                  f"(known: {', '.join(sorted(windows)) or 'none'})")
            sys.exit(1)
        start, dur = windows[args.shot]
        end = start + dur
        print(f"{args.shot}: {start:.2f}-{end:.2f}s ({dur:.2f}s)")

    if start is None or end is None:
        print("provide --start/--end, or --shot")
        sys.exit(1)

    bar_times, tempo, bar_seconds = bar_grid(args.project)
    print(f"grid: {tempo:.1f} BPM, {len(bar_times)} bars, "
          f"{bar_seconds:.3f}s/bar" if tempo else "grid: (none)")
    res = beats_in_window(args.project, start, end, args.subdiv)
    print(f"window {start:.2f}-{end:.2f}s: {len(res['absolute'])} accents "
          f"(subdiv={args.subdiv})")
    print(f"  absolute: {res['absolute']}")
    print(f"  relative: {res['relative']}")


if __name__ == "__main__":
    main()
