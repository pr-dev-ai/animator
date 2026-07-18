#!/usr/bin/env python3
"""musicmap.py — the unified TIMING SPINE every animation stage reads from.

Why this exists
---------------
The app's animation is depth-parallax over stills; it reads as a slideshow
because the motion and cuts are not precisely locked to the music.  Before real
(cutout-puppet) animation is worth building, we need ONE accurate, unified timing
map of each song, so that every downstream decision — cut here, accent the motion
there, open the beak on this word — reads from the same source of truth.  If the
timing is wrong, even great animation feels like a slideshow.

`build_musicmap(project)` writes ``outputs/<project>_musicmap.json`` containing:

  * tempo, beats, downbeats (bar boundaries), beats_per_bar
  * a downsampled onset-energy curve
  * structural sections (intro / verse / chorus …) — heuristic labels
  * WORD-LEVEL lyric timings ({word, start, end, conf})
  * the isolated VOCAL stem (kept on disk for downstream lip-sync)

This module SUPERSEDES the two ad-hoc timing helpers:
  * scripts/beat_timing.py — ad-hoc beat grid from arrangement.json/librosa
  * scripts/lyric_sync.py   — per-shot lyric windows via plain Whisper
Both stay in place for now; see ``musicmap_compat`` for the thin bridge that lets
existing callers read beats/words out of the musicmap instead.

The three analysis components
-----------------------------
1. STEM SEPARATION (Demucs).  Isolate the vocal stem from the full mix and feed
   THAT to the aligner — word timing on a clean vocal is far better than on the
   full mix.  Demucs wants torch, which must not pollute the shared ``.venv``
   (numpy is pinned there for librosa/numba), so it runs in a SEPARATE venv
   (``.venv_musicmap``) via subprocess.  CPU is fine (~1.5x realtime) and avoids
   the box's 6 GB VRAM juggling.

2. BEATS / DOWNBEATS / TEMPO.  The design doc recommended ``madmom``; this repo
   already found (Unit 3) that madmom does NOT install on Windows (sdist-only,
   2018/2019 C/Cython that will not compile).  We try, in order, whatever real
   downbeat tracker is available in the isolated venv (madmom, then beat_this)
   and fall back to ``librosa`` beat tracking + a downbeat-phase heuristic.  The
   tool that actually produced the grid is recorded in ``tools.beats``.

3. WORD-LEVEL ALIGNMENT (faster-whisper).  ``faster-whisper`` is already a repo
   dependency (CTranslate2, no torch) and gives word timestamps directly.  Run
   on the Demucs vocal stem.  WhisperX would add wav2vec2 forced alignment for
   tighter (+/-50 ms) timing, but it needs per-language alignment models and a
   heavy torch stack; faster-whisper on the isolated vocal is already a large
   improvement over aligning on the full mix, and is what we ship.

Generator + log contract
-------------------------
Like ``web_ui/music_ace.generate_song`` and ``scripts/lyric_sync``, the public
entry point is a GENERATOR that yields plain progress lines (server.py wraps
these in SSE).  It also *returns* the finished musicmap dict (available via
``StopIteration.value``); ``run_build_musicmap`` drains the generator and hands
back the dict for programmatic callers.

Every heavy step is cached on disk keyed by the audio's size+mtime, so a run
killed by the ~10-minute background-job limit resumes instead of restarting.

CLI:
    python scripts/musicmap.py --project test_win
    python scripts/musicmap.py --project shubham-videos --repo-root C:/pradeep/animator
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)

# In production the code, the songs (outputs/) and the projects (projects/) all
# live in one repo.  REPO_ROOT is that repo; callers may override it (the git
# worktree this was developed in does not carry the untracked test projects, so
# --repo-root points the reader at the main checkout).
REPO_ROOT = Path(__file__).resolve().parent.parent

# The isolated venv that owns torch + Demucs (+ optionally madmom/beat_this).
# Kept OUT of the shared .venv so its numpy/torch cannot break librosa's pin.
MUSICMAP_VENV = REPO_ROOT / ".venv_musicmap"

DEFAULT_FPS_HINT = 24
BEATS_PER_BAR = 4

# Onset curve is downsampled to roughly this rate before it goes in the JSON, so
# the file stays small while still describing the energy envelope usefully.
ONSET_TARGET_HZ = 20.0

# librosa analysis sample rate.  22050 is plenty for beat/onset/structure work
# and halves the compute vs 44100.
ANALYSIS_SR = 22050


def log(msg: str):
    """Print a progress line to stdout and flush (SSE contract)."""
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Paths & small IO helpers
# ---------------------------------------------------------------------------

def _work_dir(out_root: Path, project: str) -> Path:
    """Per-project scratch/cache dir: outputs/_musicmap/<project>/."""
    d = out_root / "outputs" / "_musicmap" / project
    d.mkdir(parents=True, exist_ok=True)
    return d


def _audio_key(path: Path, **extra) -> dict:
    """Cache key from a file's identity + any extra knobs (model name, tool…).

    Regenerating the song (new size/mtime) or changing a knob invalidates the
    cache automatically, so we never reuse an analysis of different audio.
    """
    st = path.stat()
    key = {"file": path.name, "size": st.st_size, "mtime": int(st.st_mtime)}
    key.update(extra)
    return key


def _load_cache(cache_path: Path, key: dict) -> Optional[dict]:
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("key") == key:
        return data
    return None


def _save_cache(cache_path: Path, key: dict, payload: dict):
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, **payload}, f)
        tmp.replace(cache_path)
    except OSError as exc:
        logger.warning("Could not write cache %s (%s); continuing.", cache_path.name, exc)


def _audio_duration(path: Path) -> float:
    """Duration in seconds via soundfile (no ffprobe dependency)."""
    import soundfile as sf

    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate)


# ---------------------------------------------------------------------------
# 1. Stem separation (Demucs, in the isolated venv, via subprocess)
# ---------------------------------------------------------------------------

def _venv_python(venv: Path) -> Optional[Path]:
    """Path to a venv's python, or None if the venv is not present."""
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = venv / rel
        if cand.exists():
            return cand
    return None


def separate_vocals(song: Path, work_dir: Path, device: str = "cpu",
                    venv: Path = MUSICMAP_VENV) -> Generator[str, None, Optional[Path]]:
    """Isolate the vocal stem with Demucs (htdemucs) in the isolated venv.

    Returns the path to ``vocals.wav`` (cached), or None if Demucs is unavailable
    or fails — in which case the caller aligns on the full mix and records a null
    vocal stem.  Never raises: a missing stem degrades word-timing quality, it
    does not sink the whole musicmap.
    """
    dest = work_dir / "vocals.wav"
    key = _audio_key(song, step="demucs", model="htdemucs")
    marker = work_dir / "vocals.key.json"
    if dest.exists() and _load_cache(marker, key):
        yield f"  Using cached vocal stem: {dest.name}"
        return dest

    py = _venv_python(venv)
    if py is None:
        yield (f"  WARNING: isolated venv not found at {venv.name} — skipping stem "
               f"separation; will align on the full mix (worse word timing).")
        return None

    demucs_out = work_dir / "demucs"
    yield (f"  Separating vocals with Demucs (htdemucs, {device}) — this is the "
           f"slow step (~1.5x realtime on CPU)...")
    cmd = [
        str(py), "-m", "demucs",
        "--two-stems=vocals", "-n", "htdemucs",
        "--segment", "7",          # bound CPU RAM; htdemucs max segment is 7.8s
        "-d", device,
        "-o", str(demucs_out),
        str(song),
    ]
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError:
        yield "  WARNING: could not launch the isolated venv python — skipping stem."
        return None
    except subprocess.TimeoutExpired:
        yield "  WARNING: Demucs timed out — skipping stem, aligning on the full mix."
        return None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        yield f"  WARNING: Demucs failed ({'; '.join(tail)[:200]}) — aligning on full mix."
        return None

    # Demucs writes <out>/htdemucs/<songstem>/vocals.wav.
    produced = demucs_out / "htdemucs" / song.stem / "vocals.wav"
    if not produced.exists():
        hits = list(demucs_out.rglob("vocals.wav"))
        produced = hits[0] if hits else produced
    if not produced.exists():
        yield "  WARNING: Demucs produced no vocals.wav — aligning on the full mix."
        return None

    try:
        import shutil
        shutil.copyfile(produced, dest)
    except OSError as exc:
        yield f"  WARNING: could not stage the vocal stem ({exc}) — using full mix."
        return None

    took = time.time() - started
    yield f"  Vocal stem ready: {dest.name} ({took:.0f}s)"
    _save_cache(marker, key, {"path": str(dest)})
    return dest


def _is_nonsilent(path: Path) -> bool:
    """True if the WAV carries real signal (guards a silent/failed stem)."""
    try:
        import numpy as np
        import soundfile as sf
        audio, _sr = sf.read(str(path))
        return bool(np.abs(audio).max() > 1e-3)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 2. Beats / downbeats / tempo
# ---------------------------------------------------------------------------

def _detect_beats_isolated(song: Path, work_dir: Path,
                           venv: Path = MUSICMAP_VENV) -> Optional[dict]:
    """Try a real downbeat tracker (madmom, then beat_this) in the isolated venv.

    Returns {tempo, beats, downbeats, beats_per_bar, tool} or None if no such
    tracker is installed/usable.  Runs out-of-process because these pull torch /
    a conflicting numpy that must stay out of the shared venv.
    """
    py = _venv_python(venv)
    if py is None:
        return None
    runner = Path(__file__).resolve().parent / "_musicmap_beats.py"
    if not runner.exists():
        return None
    try:
        proc = subprocess.run(
            [str(py), str(runner), str(song)],
            capture_output=True, text=True, timeout=1800,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        logger.debug("isolated beat tracker unavailable: %s",
                     (proc.stderr or "").strip()[:300])
        return None
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if data.get("beats") and data.get("tool"):
        return data
    return None


def _downbeat_phase(beats, onset_env, onset_times, beats_per_bar=BEATS_PER_BAR):
    """Pick the bar phase (0..beats_per_bar-1) whose beats carry the most energy.

    A downbeat is where the bar restarts and the accent lands, so the phase whose
    beats sum to the greatest onset strength is the best guess for bar starts.
    """
    import numpy as np

    if len(beats) < beats_per_bar:
        return 0
    onset_times = np.asarray(onset_times)
    onset_env = np.asarray(onset_env)
    strengths = []
    for bt in beats:
        idx = int(np.searchsorted(onset_times, bt))
        idx = min(max(idx, 0), len(onset_env) - 1)
        strengths.append(onset_env[idx])
    strengths = np.asarray(strengths)
    best_phase, best_score = 0, -1.0
    for phase in range(beats_per_bar):
        score = strengths[phase::beats_per_bar].sum()
        if score > best_score:
            best_score, best_phase = score, phase
    return best_phase


def detect_beats_librosa(song: Path) -> Optional[dict]:
    """librosa beat tracking + a downbeat-phase heuristic (the fallback path).

    Returns {tempo, beats, downbeats, beats_per_bar, tool="librosa"} or None.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None

    try:
        y, sr = librosa.load(str(song), sr=ANALYSIS_SR, mono=True)
    except Exception as exc:
        logger.warning("librosa could not load %s (%s).", song.name, exc)
        return None

    # Mean aggregation (not the median default): synthesised/soft beds move few
    # spectral bins per onset, so the median across bins can be flat zero.
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.mean)
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, units="frames", trim=False,
    )
    tempo = float(tempo.item() if hasattr(tempo, "item") else tempo)
    if len(beat_frames) < 2 or tempo <= 0:
        return None

    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    onset_times = librosa.times_like(onset_env, sr=sr)
    phase = _downbeat_phase(beats, onset_env, onset_times)
    downbeats = beats[phase::BEATS_PER_BAR]

    return {
        "tempo": round(tempo, 2),
        "beats": [round(t, 4) for t in beats],
        "downbeats": [round(t, 4) for t in downbeats],
        "beats_per_bar": BEATS_PER_BAR,
        "tool": "librosa",
    }


def detect_beats(song: Path, work_dir: Path) -> Generator[str, None, Optional[dict]]:
    """Best available beat grid: isolated real tracker first, then librosa.

    Cached: the grid does not change between runs of the same audio.
    """
    key = _audio_key(song, step="beats")
    cache = work_dir / "beats.json"
    cached = _load_cache(cache, key)
    if cached and cached.get("beats"):
        yield f"  Using cached beat grid ({cached['tool']}, {len(cached['beats'])} beats)"
        return {k: cached[k] for k in
                ("tempo", "beats", "downbeats", "beats_per_bar", "tool")}

    yield "  Detecting beats / downbeats / tempo..."
    result = _detect_beats_isolated(song, work_dir)
    if result:
        yield f"  Beat tracker: {result['tool']} (real downbeat tracker)"
    else:
        yield "  No real downbeat tracker available (madmom/beat_this) — using librosa."
        result = detect_beats_librosa(song)
    if not result:
        yield "  WARNING: beat detection failed; musicmap will have no beat grid."
        return None

    yield (f"  {result['tool']}: {result['tempo']:.1f} BPM, {len(result['beats'])} "
           f"beats, {len(result['downbeats'])} downbeats")
    _save_cache(cache, key, result)
    return result


# ---------------------------------------------------------------------------
# 3. Onset envelope + structural sections (librosa, in-process)
# ---------------------------------------------------------------------------

def _onset_and_sections(song: Path) -> Optional[dict]:
    """Compute a downsampled onset curve and structural sections with librosa.

    Sections use beat-synchronous features + agglomerative boundaries; the
    intro/verse/chorus LABELS are a recurrence heuristic (a repeated section is
    called a chorus), not ground truth — honest but approximate.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None

    try:
        y, sr = librosa.load(str(song), sr=ANALYSIS_SR, mono=True)
    except Exception as exc:
        logger.warning("librosa could not load %s for onset/sections (%s).", song.name, exc)
        return None

    duration = len(y) / sr
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.mean)
    onset_times = librosa.times_like(onset_env, sr=sr)

    # Downsample the onset curve to ~ONSET_TARGET_HZ so the JSON stays small.
    frame_hz = 1.0 / (onset_times[1] - onset_times[0]) if len(onset_times) > 1 else ONSET_TARGET_HZ
    step = max(1, int(round(frame_hz / ONSET_TARGET_HZ)))
    peak = float(onset_env.max()) or 1.0
    ds_times = onset_times[::step]
    ds_strength = onset_env[::step] / peak
    onset_curve = {
        "times": [round(float(t), 3) for t in ds_times],
        "strength": [round(float(s), 4) for s in ds_strength],
    }

    sections = _segment_sections(y, sr, duration)
    return {"onset_env": onset_curve, "sections": sections}


def _segment_sections(y, sr, duration):
    """Structural segmentation with heuristic intro/verse/chorus labels."""
    import librosa
    import numpy as np

    try:
        # Beat-synchronous features so segment boundaries land on musical time.
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, trim=False)
        if len(beats) < 4:
            raise ValueError("too few beats to segment")
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        sync = np.vstack([
            librosa.util.sync(chroma, beats, aggregate=np.median),
            librosa.util.sync(mfcc, beats, aggregate=np.median),
        ])
        # Aim for ~one section per 15s, clamped to a sane range.
        k = int(np.clip(round(duration / 15.0), 3, 8))
        # agglomerative returns boundary COLUMN indices into `sync`, which has
        # one more column than there are beats; clip so they stay valid indices
        # into the beat-frame array before mapping to time.
        bounds = librosa.segment.agglomerative(sync, k)
        bound_beats = np.clip(np.concatenate([bounds, [len(beats) - 1]]),
                              0, len(beats) - 1)
        bound_times = librosa.frames_to_time(beats[bound_beats], sr=sr)
        # Segment edges in seconds, starting at 0 and ending at the song end.
        edges = np.concatenate([[0.0], bound_times])
        edges = np.unique(np.clip(edges, 0.0, duration))
        if edges[-1] < duration - 1e-3:
            edges = np.append(edges, duration)

        # Feature per segment (median chroma) for the recurrence labeller.
        seg_feats = []
        for a, b in zip(edges[:-1], edges[1:]):
            fa = librosa.time_to_frames(a, sr=sr)
            fb = max(fa + 1, librosa.time_to_frames(b, sr=sr))
            seg_feats.append(np.median(chroma[:, fa:fb], axis=1))
        labels = _label_sections(seg_feats)
        raw = [
            {"label": lbl, "start": float(a), "end": float(b)}
            for lbl, a, b in zip(labels, edges[:-1], edges[1:])
        ]
        return _merge_sections(raw, duration)
    except Exception as exc:
        logger.warning("Structural segmentation failed (%s); emitting one section.", exc)
        return [{"label": "song", "start": 0.0, "end": round(float(duration), 3)}]


def _merge_sections(sections, duration, min_len=4.0):
    """Merge fragment sections and collapse consecutive same-label runs.

    Agglomerative boundaries scatter a few sub-second slivers around the real
    structure; a section shorter than ``min_len`` is folded into its neighbour,
    and adjacent sections that ended up with the same label become one.  Keeps
    the output at the granularity a human would call sections, not frames.
    """
    if not sections:
        return sections
    # Fold short segments into a neighbour.  A short segment folds into the
    # previous one; a short LEADING segment (no previous) instead lends its start
    # to the next, so the first real section simply begins at 0.
    merged = []
    for s in sections:
        if merged and (s["end"] - s["start"] < min_len):
            merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    while len(merged) > 1 and (merged[0]["end"] - merged[0]["start"] < min_len):
        merged[1]["start"] = merged[0]["start"]
        merged.pop(0)
    merged[0]["start"] = 0.0
    merged[-1]["end"] = round(float(duration), 3)
    # Collapse consecutive same-label runs.
    out = [merged[0]]
    for s in merged[1:]:
        if s["label"] == out[-1]["label"]:
            out[-1]["end"] = s["end"]
        else:
            out.append(s)
    for s in out:
        s["start"] = round(float(s["start"]), 3)
        s["end"] = round(float(s["end"]), 3)
    return out


def _label_sections(seg_feats):
    """Heuristic labels: first=intro, last=outro, most-repeated cluster=chorus.

    Segments are grouped by cosine similarity of their median chroma.  The
    biggest recurring group is the chorus (a hook repeats); a lone group is a
    verse; the opening is the intro and the trailing one the outro.  These are
    approximations, not annotations — good enough to steer motion, not to publish.
    """
    import numpy as np

    n = len(seg_feats)
    if n <= 1:
        return ["song"] * n

    feats = [f / (np.linalg.norm(f) + 1e-9) for f in seg_feats]
    # Greedy clustering by cosine similarity.
    clusters = []          # list of representative vectors
    assign = []
    for f in feats:
        best_c, best_sim = -1, 0.0
        for ci, rep in enumerate(clusters):
            sim = float(np.dot(f, rep))
            if sim > best_sim:
                best_sim, best_c = sim, ci
        if best_sim >= 0.85:
            assign.append(best_c)
        else:
            clusters.append(f)
            assign.append(len(clusters) - 1)

    counts = {}
    for c in assign:
        counts[c] = counts.get(c, 0) + 1
    chorus_cluster = max(counts, key=counts.get) if counts else -1
    # Only call a cluster the chorus if it genuinely RECURS without swallowing the
    # whole song: in one key most segments share chroma, so a cluster that covers
    # >60% of segments is just tonal similarity, not a repeated hook — those are
    # verses.  A real chorus repeats (>=2) but stays a minority.
    chorus_frac = counts.get(chorus_cluster, 0) / n
    is_chorus_cluster = 2 <= counts.get(chorus_cluster, 0) and chorus_frac <= 0.6

    labels = []
    for i, c in enumerate(assign):
        if i == 0:
            labels.append("intro")
        elif i == n - 1 and n >= 4:
            labels.append("outro")
        elif is_chorus_cluster and c == chorus_cluster:
            labels.append("chorus")
        else:
            labels.append("verse")
    return labels


# ---------------------------------------------------------------------------
# 4. Word-level lyric alignment (faster-whisper on the vocal stem)
# ---------------------------------------------------------------------------

# base = multilingual (needed for Hindi); base.en is English-only and used when
# we know the song is English, matching scripts/lyric_sync's proven model.
def _asr_model_for(language: str) -> str:
    return "base.en" if language == "en" else "base"


def _read_language(project_dir: Path) -> str:
    lang_file = project_dir / "language.txt"
    if lang_file.exists():
        try:
            v = lang_file.read_text(encoding="utf-8").strip().lower()
            if v:
                return v
        except OSError:
            pass
    return "en"


def align_words(audio: Path, work_dir: Path, language: str,
                cache_tag: str) -> Generator[str, None, list]:
    """Word-level timestamps for the vocal via faster-whisper.

    ``cache_tag`` distinguishes a stem-based alignment from a full-mix one so the
    two never collide in the cache.  Returns [{word, start, end, conf}], possibly
    empty.  Never raises.
    """
    model_name = _asr_model_for(language)
    key = _audio_key(audio, step="align", model=model_name, lang=language, on=cache_tag)
    cache = work_dir / "words.json"
    cached = _load_cache(cache, key)
    if cached is not None and "words" in cached:
        yield f"  Using cached word alignment ({len(cached['words'])} words)"
        return cached["words"]

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        yield "  WARNING: faster-whisper not installed — no word timings."
        return []

    try:
        yield (f"  Aligning words with faster-whisper '{model_name}' (CPU, "
               f"lang={language}) on {audio.name}...")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(
            str(audio), word_timestamps=True,
            language=None if language == "auto" else language,
            vad_filter=False, beam_size=5,
        )
        words = []
        for seg in segments:
            for w in (seg.words or []):
                token = w.word.strip()
                if token:
                    words.append({
                        "word": token,
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                        "conf": round(float(w.probability), 3),
                    })
    except Exception as exc:
        yield f"  WARNING: word alignment failed ({exc})."
        return []

    yield f"  Aligned {len(words)} words"
    _save_cache(cache, key, {"words": words})
    return words


def _alignment_report(words, lyrics_txt: Path) -> str:
    """A one-line honest read on alignment coverage vs the known lyrics."""
    try:
        import re
        expected = re.sub(r"[^\w\s]", " ", lyrics_txt.read_text(encoding="utf-8").lower())
        n_expected = len(expected.split())
    except OSError:
        n_expected = 0
    n = len(words)
    monotonic = all(words[i]["start"] <= words[i + 1]["start"] + 1e-3
                    for i in range(len(words) - 1))
    mean_conf = sum(w["conf"] for w in words) / n if n else 0.0
    cov = f"{n}/{n_expected} words ({100 * n / n_expected:.0f}% of lyric tokens)" \
        if n_expected else f"{n} words"
    return f"{cov}; mean conf {mean_conf:.2f}; monotonic={monotonic}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_musicmap(project: str, *, repo_root: Optional[Path] = None,
                   out_root: Optional[Path] = None, device: str = "cpu",
                   fps_hint: int = DEFAULT_FPS_HINT
                   ) -> Generator[str, None, Optional[dict]]:
    """Build outputs/<project>_musicmap.json — the unified timing spine.

    Reads the song from ``<repo_root>/outputs/<project>_song.wav`` and the known
    lyrics from ``<repo_root>/projects/<project>/lyrics.txt``.  Writes the map
    (and the vocal stem + step caches) under ``<out_root>/outputs``.  Both roots
    default to this module's REPO_ROOT (the production case, where they coincide).

    Yields plain log lines; RETURNS the musicmap dict (via StopIteration.value).
    The final line is "DONE" on success or starts with "ERROR:".
    """
    repo_root = Path(repo_root) if repo_root else REPO_ROOT
    out_root = Path(out_root) if out_root else repo_root

    song = repo_root / "outputs" / f"{project}_song.wav"
    if not song.exists():
        yield f"ERROR: no song at {song}"
        return None
    project_dir = repo_root / "projects" / project
    lyrics_txt = project_dir / "lyrics.txt"
    language = _read_language(project_dir)

    work_dir = _work_dir(out_root, project)
    duration = _audio_duration(song)

    yield f"Building musicmap for '{project}'"
    yield f"  Song: {song}  ({duration:.1f}s, language={language})"

    # 1. Vocal stem (Demucs, isolated venv).
    vocals = yield from separate_vocals(song, work_dir, device=device)
    if vocals and not _is_nonsilent(vocals):
        yield "  WARNING: vocal stem is silent — falling back to the full mix."
        vocals = None
    align_target = vocals if vocals else song
    cache_tag = "stem" if vocals else "mix"

    # 2. Beats / downbeats / tempo (isolated tracker → librosa).
    beats = yield from detect_beats(song, work_dir)

    # 3. Onset envelope + structural sections (librosa, in-process).
    yield "  Computing onset envelope + structural sections..."
    extra = _onset_and_sections(song) or {}
    sections = extra.get("sections", [])
    onset_env = extra.get("onset_env", {"times": [], "strength": []})
    if sections:
        yield f"  Sections: {len(sections)} ({', '.join(s['label'] for s in sections)})"

    # 4. Word-level alignment (faster-whisper on the vocal stem).
    words = yield from align_words(align_target, work_dir, language, cache_tag)
    if lyrics_txt.exists():
        yield f"  Alignment quality: {_alignment_report(words, lyrics_txt)}"

    # 5. Assemble.
    tools = {
        "beats": beats["tool"] if beats else "none",
        "align": "faster-whisper",
        "stems": "demucs" if vocals else "none",
    }
    musicmap = {
        "song": os.path.relpath(song, out_root).replace("\\", "/"),
        "duration": round(duration, 3),
        "fps_hint": fps_hint,
        "tempo_bpm": beats["tempo"] if beats else None,
        "beats": beats["beats"] if beats else [],
        "downbeats": beats["downbeats"] if beats else [],
        "beats_per_bar": beats["beats_per_bar"] if beats else BEATS_PER_BAR,
        "onset_env": onset_env,
        "sections": sections,
        "words": words,
        "vocal_stem": (os.path.relpath(vocals, out_root).replace("\\", "/")
                       if vocals else None),
        "tools": tools,
    }

    out_path = out_root / "outputs" / f"{project}_musicmap.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(musicmap, f, indent=2, ensure_ascii=False)
    tmp.replace(out_path)

    yield (f"Saved {out_path} — {musicmap['tempo_bpm']} BPM, "
           f"{len(musicmap['beats'])} beats, {len(musicmap['downbeats'])} downbeats, "
           f"{len(sections)} sections, {len(words)} words")
    yield "DONE"
    return musicmap


def run_build_musicmap(project: str, **kwargs) -> Optional[dict]:
    """Drain build_musicmap's generator (printing logs); return the map dict."""
    gen = build_musicmap(project, **kwargs)
    try:
        while True:
            log(next(gen))
    except StopIteration as stop:
        return stop.value


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s",
                        stream=sys.stdout)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--project", required=True)
    p.add_argument("--repo-root", default=None,
                   help="repo holding outputs/ and projects/ (default: this repo)")
    p.add_argument("--out-root", default=None,
                   help="where to write outputs/ (default: --repo-root)")
    p.add_argument("--device", default="cpu", choices=["cpu", "cuda"],
                   help="Demucs device (default: cpu)")
    args = p.parse_args()
    result = run_build_musicmap(
        args.project,
        repo_root=Path(args.repo_root) if args.repo_root else None,
        out_root=Path(args.out_root) if args.out_root else None,
        device=args.device,
    )
    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
