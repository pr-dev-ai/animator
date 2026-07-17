#!/usr/bin/env python3
"""Align storyboard shots to the lyrics actually sung in a project's song.

Why this exists
---------------
The shot durations in shotlist.csv are template defaults (4.0s, 5.0s, ...) that
are written long before any song exists.  The animatic therefore used to run for
sum(shotlist durations) -- 54s for a 120s song -- and the picture had no
relationship to the lyric being sung underneath it: the duck shot could easily
play over the rabbit verse.

This module closes that loop:

  1. Transcribe the song with word-level timestamps (faster-whisper, CPU).
  2. Fuzzy-locate every line of the ground-truth lyrics.txt in that transcript.
     ASR on sung vocals is noisy ("Squirrels" -> "Skrulls", "Ducks go splash" ->
     "Duts go splashing"), so matching is fuzzy and never exact.
  3. Work out which lyric line each shot depicts.  The storyboard prompts quote
     the lyric verbatim (SH030's prompt contains "Ducks go splash in the pond"),
     which is a far stronger signal than shotlist.csv's `notes` column -- see
     shot_lyric_map() for why the notes are actively misleading.
  4. Anchor those shots to the time their lyric is sung, spread the unanchored
     shots through the gaps, and stretch the result across the whole song.

Everything degrades gracefully: any failure returns None and make_dailies.py
falls back to the old shotlist-duration behaviour.  The animatic must never
hard-fail because a transcription did not work out.
"""

import difflib
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Tunables ---------------------------------------------------------------

# Whisper model. base.en transcribes our 120s test song in ~26s on CPU and
# recovers the lyrics well enough to align (most lines match at ratio >= 0.85).
# Deliberately CPU-only: the GPU belongs to image/music generation.
ASR_MODEL = "base.en"

# Minimum similarity for a lyric line to count as "found" in the transcript.
# Calibrated against the test song: genuinely sung lines land at 0.78-1.00,
# while lines that are NOT sung at all top out at 0.67 against the nearest
# similar-sounding passage.  0.75 separates them with margin on both sides.
MIN_LINE_RATIO = 0.75

# Minimum similarity for a quote lifted out of a storyboard prompt to be
# considered "the same text as" a lyric line.  Prompts quote the lyric verbatim,
# so this can be strict; it only needs to absorb punctuation differences.
MIN_QUOTE_RATIO = 0.80

# Below this many anchored shots the alignment is not worth trusting; the caller
# falls back to shotlist durations rather than inventing a timeline from one
# lucky match.
MIN_ANCHORS = 2

# Shortest shot we will ever emit, seconds.
MIN_SHOT = 1.5


def log(msg: str):
    """Print a progress line to stdout and flush (SSE contract)."""
    print(msg, flush=True)


def _norm_words(text: str):
    """Lowercase and strip punctuation, returning a word list.

    Hyphenated singing ("Tweet-tweet-tweet") must split into separate words so
    it can line up with the transcript's separate tokens.
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).split()


# --- 1. Transcription -------------------------------------------------------

def transcribe_words(audio_path: Path, cache_path: Path):
    """Transcribe `audio_path`, returning [{"w","s","e"}] word dicts.

    Whisper is slow, so the result is cached next to the audio. The cache is
    keyed on the audio's size+mtime and the model name, so regenerating the song
    or changing model invalidates it automatically rather than silently reusing
    a transcript of different audio.

    Returns None if faster-whisper is unavailable or transcription fails.
    """
    try:
        stat = audio_path.stat()
        key = {
            "audio": audio_path.name,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "model": ASR_MODEL,
        }
    except OSError:
        return None

    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("key") == key and cached.get("words"):
                log(f"Using cached transcript ({len(cached['words'])} words): "
                    f"{cache_path.name}")
                return cached["words"]
        except (OSError, ValueError):
            pass  # unreadable/stale cache is not an error; just re-transcribe

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.warning(
            "faster-whisper not installed; cannot lyric-sync. "
            "Install with: pip install faster-whisper"
        )
        return None

    try:
        log(f"Transcribing {audio_path.name} with Whisper '{ASR_MODEL}' (CPU) "
            f"- this takes a minute (the very first run also downloads the "
            f"~150 MB model, which can take several)...")
        model = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
        # vad_filter=False: the VAD is tuned for speech and clips sung phrases
        # that ride over a loud instrumental bed.
        segments, _info = model.transcribe(
            str(audio_path), word_timestamps=True, language="en",
            vad_filter=False, beam_size=5,
        )
        words = []
        for seg in segments:
            for w in (seg.words or []):
                token = w.word.strip()
                if token:
                    words.append({"w": token,
                                  "s": round(float(w.start), 3),
                                  "e": round(float(w.end), 3)})
    except Exception as exc:
        logger.warning("Transcription failed (%s); falling back to shotlist durations.", exc)
        return None

    if not words:
        logger.warning("Transcript was empty; falling back to shotlist durations.")
        return None

    log(f"Transcribed {len(words)} words")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"key": key, "words": words}, f)
    except OSError as exc:
        logger.warning("Could not write transcript cache (%s); continuing.", exc)
    return words


# --- 2. Locate each lyric line in the transcript ----------------------------

def find_line_occurrences(words, lyric_lines):
    """Locate every occurrence of every lyric line in the transcript.

    Returns {line_index: [(start, end, ratio), ...]} ordered by time.

    A line can legitimately occur more than once: song generators commonly sing
    the lyrics through twice to fill the requested duration, which is exactly
    what our 120s test song does.  Conversely a line may not occur at all --
    that same song never sings the final stanza.  Both must be handled.

    Matching is fuzzy (difflib on the normalised character stream) because ASR
    mishears sung vowels constantly.  Windows are scored over a range of lengths
    around the target so that inserted/dropped words do not sink the match.
    """
    tokens = [_norm_words(w["w"]) for w in words]
    # Flatten to one normalised token per transcript word, keeping the index
    # alignment with `words` so we can recover timings.
    flat, index_map = [], []
    for i, toks in enumerate(tokens):
        for t in toks:
            flat.append(t)
            index_map.append(i)

    if not flat:
        return {}

    # Score every candidate window once per line, then greedily take the best
    # non-overlapping ones.
    claims = []  # (ratio, line_index, start_flat, end_flat)
    for li, line in enumerate(lyric_lines):
        target = _norm_words(line)
        if len(target) < 2:
            continue
        tgt = " ".join(target)
        n = len(target)
        matcher = difflib.SequenceMatcher()
        matcher.set_seq2(tgt)
        for start in range(len(flat)):
            for span in range(max(2, n - 2), n + 3):
                end = start + span
                if end > len(flat):
                    break
                window = " ".join(flat[start:end])
                matcher.set_seq1(window)
                # real_quick_ratio/quick_ratio are cheap upper bounds; skip the
                # expensive ratio() when the window cannot possibly qualify.
                if (matcher.real_quick_ratio() < MIN_LINE_RATIO or
                        matcher.quick_ratio() < MIN_LINE_RATIO):
                    continue
                r = matcher.ratio()
                if r >= MIN_LINE_RATIO:
                    claims.append((r, li, start, end))

    # Highest-confidence claims win their span outright.  This is what stops a
    # not-sung line from stealing a similar-sounding passage: the test song's
    # final stanza opens "Animals at the park, oh what a special day" and scores
    # 0.67 against the first stanza's "Animals at the park, what a happy sight"
    # region -- but the first stanza itself scores 0.88 there, claims the span
    # first, and the impostor is left correctly unanchored.
    claims.sort(key=lambda c: (-c[0], c[2]))
    taken = [False] * len(flat)
    found = {}
    for r, li, s, e in claims:
        if any(taken[i] for i in range(s, e)):
            continue
        for i in range(s, e):
            taken[i] = True
        t0 = words[index_map[s]]["s"]
        t1 = words[index_map[e - 1]]["e"]
        found.setdefault(li, []).append((t0, t1, r))

    for li in found:
        found[li].sort()
    return found


# --- 3. Work out which lyric each shot depicts ------------------------------

def _fuzzy_contains(needle_words, hay_words):
    """Best similarity of `needle_words` against any window of `hay_words`.

    A plain substring test is useless here (the prompt reformats punctuation),
    and pairing quote characters is worse than useless: the prompts open with
    "children's illustration", whose apostrophe silently swallows the real
    quotation marks and captures boilerplate instead of the lyric.  Sliding a
    fuzzy window over the words sidesteps quoting entirely.
    """
    n = len(needle_words)
    if n < 2 or not hay_words:
        return 0.0
    tgt = " ".join(needle_words)
    matcher = difflib.SequenceMatcher()
    matcher.set_seq2(tgt)
    best = 0.0
    for start in range(len(hay_words)):
        for span in range(max(2, n - 2), n + 3):
            end = start + span
            if end > len(hay_words):
                break
            matcher.set_seq1(" ".join(hay_words[start:end]))
            if matcher.real_quick_ratio() <= best or matcher.quick_ratio() <= best:
                continue
            best = max(best, matcher.ratio())
    return best


def shot_lyric_map(prompts_json: Path, lyric_lines):
    """Map shot_id -> lyric line index, by finding lyrics quoted in the prompts.

    The prompt generator writes the lyric a shot illustrates straight into the
    prompt, e.g. SH030's prompt contains
        ... singing 'Ducks go splash in the pond, swimming all around', ...
    which names the depicted lyric exactly.  That is the signal we use.

    We deliberately do NOT use shotlist.csv's `notes` column.  It looks helpful
    ("Song verse 1", "Song verse 2", ...) but it is template boilerplate written
    before the lyrics existed, and on the test project it directly contradicts
    the images: SH030 is annotated "Song verse 2" while the rendered image, and
    its prompt, are unmistakably the *third* stanza's duck.  Trusting the notes
    would re-introduce the very mis-sync this module exists to remove.
    """
    if not prompts_json.exists():
        return {}
    try:
        with open(prompts_json, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s (%s); no lyric anchors.", prompts_json.name, exc)
        return {}
    if not isinstance(entries, list):
        return {}

    mapping = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        shot_id = (entry.get("shot_id") or "").strip()
        prompt = entry.get("prompt") or ""
        if not shot_id or not prompt:
            continue
        hay = _norm_words(prompt)
        best_ratio, best_line = 0.0, None
        for li, line in enumerate(lyric_lines):
            r = _fuzzy_contains(_norm_words(line), hay)
            if r > best_ratio:
                best_ratio, best_line = r, li
        if best_line is not None and best_ratio >= MIN_QUOTE_RATIO:
            mapping[shot_id] = best_line
    return mapping


# --- 4. Build the timeline --------------------------------------------------

def _snap(t, bar_times):
    """Snap a time to the nearest bar boundary."""
    if not bar_times:
        return t
    return min(bar_times, key=lambda b: abs(b - t))


def _assign_anchors(shots, shot_lines, occurrences, song_duration, bar_times):
    """Pick one occurrence per anchored shot: {shot_index: snapped_time}.

    Shots are anchored to the EARLIEST unclaimed occurrence of their lyric.  If
    two shots depict the same line they take successive occurrences rather than
    stacking on the same instant.
    """
    used = set()
    anchors = {}
    for i, shot in enumerate(shots):
        li = shot_lines.get(shot["shot_id"])
        if li is None or li not in occurrences:
            continue
        for n, (t0, _t1, ratio) in enumerate(occurrences[li]):
            if (li, n) in used:
                continue
            used.add((li, n))
            # Snap to the bar so cuts still land musically.  Sung phrases here
            # anticipate the downbeat by up to ~0.8s (a pickup), so the nearest
            # bar is the phrase start the singer is aiming at.
            t = _snap(t0, bar_times)
            t = min(max(t, 0.0), song_duration)
            anchors[i] = {"time": t, "raw": t0, "line": li, "ratio": ratio}
            break
    return anchors


def _order_shots(shots, anchors, song_duration):
    """Return shot indices ordered by the time their lyric is sung.

    Anchored shots sort by anchor time.  This can legitimately reorder the
    shotlist: on the test project SH030 (ducks) is listed before SH050
    (squirrels), but the song sings the squirrel stanza first, so the squirrel
    shot must come first or the picture contradicts the vocal.  Shot order in a
    template shotlist is boilerplate; what the audience hears is not.

    Unanchored shots stay between whichever anchored shots surrounded them in
    the original shotlist, using the *time span* those neighbours ended up
    occupying (min/max, since the neighbours may have swapped places).
    """
    anchored = sorted(anchors, key=lambda i: anchors[i]["time"])
    if not anchored:
        return list(range(len(shots)))

    keys = {i: anchors[i]["time"] for i in anchored}

    for i in range(len(shots)):
        if i in anchors:
            continue
        # `anchored` is never empty here (plan() requires MIN_ANCHORS >= 2), so
        # at least one of these bounds always exists.
        prev_a = max((a for a in anchored if a < i), default=None)
        next_a = min((a for a in anchored if a > i), default=None)
        if prev_a is None:
            lo, hi = 0.0, anchors[next_a]["time"]
        elif next_a is None:
            lo, hi = anchors[prev_a]["time"], song_duration
        else:
            ta, tb = anchors[prev_a]["time"], anchors[next_a]["time"]
            lo, hi = min(ta, tb), max(ta, tb)
        # Nudge inside the window; the tiny epsilon*index keeps unanchored
        # siblings in their original relative order and off the anchor.
        keys[i] = lo + (hi - lo) * 0.5 + 1e-6 * i

    return sorted(range(len(shots)), key=lambda i: (keys[i], i))


def _distribute(order, shots, anchors, song_duration):
    """Turn an ordering + fixed anchor times into per-shot start times.

    Anchored shots start exactly on their anchor.  Runs of unanchored shots
    share the span between the surrounding anchors, split in proportion to their
    shotlist durations so the author's intended relative pacing survives.
    """
    n = len(order)
    starts = [None] * n
    fixed = [(p, anchors[i]["time"]) for p, i in enumerate(order) if i in anchors]

    # Virtual fixed points at both ends make the loop uniform: the timeline
    # always starts at 0 and always ends at the song's end.
    points = [(-1, 0.0)] + fixed + [(n, song_duration)]

    for (pa, ta), (pb, tb) in zip(points, points[1:]):
        first = pa if pa >= 0 else 0
        members = list(range(first, pb))
        if not members:
            continue
        # Never let the run overflow its span: the next anchor is a fixed point,
        # and overshooting it would make the monotonic pass below shove that
        # anchored shot off the lyric it was pinned to.  If the shots genuinely
        # cannot all fit at MIN_SHOT, keep them inside the span anyway and let
        # the caller's min-duration pass squeeze them -- a couple of short shots
        # is a far better failure than a silently mis-synced anchor.
        span = max(tb - ta, 0.0)
        weights = [shots[order[p]]["duration"] for p in members]
        total_w = sum(weights) or float(len(members))
        t = ta
        for p, w in zip(members, weights):
            starts[p] = min(t, tb)
            t += span * (w / total_w)

    for p in range(n):
        if starts[p] is None:
            starts[p] = starts[p - 1] if p else 0.0
    # The picture has to start at t=0 whatever the vocal says.  If the very
    # first shot was anchored to a later lyric it simply comes up early, which
    # is the harmless direction: the image is already there when its line lands.
    starts[0] = 0.0

    # Give every shot MIN_SHOT where there is room, but never at an anchor's
    # expense: anchored starts are hard points, so only unanchored shots move,
    # and only when the widening still leaves every shot up to the next anchor
    # its own MIN_SHOT.  Where the anchors are simply too close for that, the
    # proportional start stands -- a run of short shots is a much better outcome
    # than an anchor dragged off the lyric that justified it.
    anchored_pos = {p for p, i in enumerate(order) if i in anchors}
    for p in range(1, n):
        if p in anchored_pos:
            continue
        limit = _next_fixed(p, n, anchored_pos, starts, song_duration)
        widened = max(starts[p], starts[p - 1] + MIN_SHOT)
        if widened <= limit:
            starts[p] = widened

    return starts


def _next_fixed(p, n, anchored_pos, starts, song_duration):
    """Latest time shot `p` may start without starving the shots after it.

    The next anchor (or the song's end) is immovable, and every shot between
    here and it still needs MIN_SHOT.
    """
    for q in range(p + 1, n):
        if q in anchored_pos:
            return starts[q] - MIN_SHOT * (q - p)
    return song_duration - MIN_SHOT * (n - p)


def _enforce_increasing(starts):
    """Guarantee strictly increasing starts, so no shot can be <= 0 frames.

    Runs last, after snapping, as the final backstop.  It can nudge an anchor by
    a frame when anchors are pathologically close, which is a fair trade against
    emitting a negative duration.
    """
    for p in range(1, len(starts)):
        starts[p] = max(starts[p], starts[p - 1] + 1.0 / 24.0)
    return starts


def _snap_starts(order, anchors, starts, bar_times, song_duration):
    """Snap interpolated cuts onto bar boundaries.

    Anchored starts are already exactly on a bar (see _assign_anchors) and are
    left untouched.  The interpolated cuts in between are pulled onto the
    nearest bar as well, so that *every* cut lands on a downbeat rather than
    only the ones the vocal happened to pin down.  A candidate bar is only
    accepted if it still leaves room for this shot and every shot up to the next
    anchor to clear MIN_SHOT.
    """
    if not bar_times:
        return starts

    n = len(starts)
    anchored_pos = {p for p, i in enumerate(order) if i in anchors}
    out = list(starts)
    for p in range(1, n):
        if p in anchored_pos:
            continue
        lo = out[p - 1] + MIN_SHOT
        hi = _next_fixed(p, n, anchored_pos, out, song_duration)
        candidates = [b for b in bar_times if lo <= b <= hi]
        if candidates:
            out[p] = min(candidates, key=lambda b: abs(b - starts[p]))
        # else: no bar fits between here and the next anchor, so leave the cut
        # exactly where _distribute put it.  Forcing it onto `lo` would push it
        # past the next anchor and invert the timeline -- an off-grid cut is a
        # far cheaper price than a broken anchor or a negative duration.
    return out


def _durations_from_starts(starts, song_duration):
    """Convert start times into per-shot durations closing on the song's end."""
    n = len(starts)
    end = max(song_duration, starts[-1] + MIN_SHOT)
    return [starts[p + 1] - starts[p] for p in range(n - 1)] + [end - starts[-1]]


def plan(project_dir: Path, shots, music_path: Path, song_duration: float,
         bar_times, cache_path: Path):
    """Plan lyric-aligned shot order + durations.

    Returns (ordered_shots, durations, report) or None when the song cannot be
    aligned confidently, in which case the caller keeps shotlist durations.
    """
    lyrics_txt = project_dir / "lyrics.txt"
    prompts_json = project_dir / "prompts" / "storyboards.json"
    if not lyrics_txt.exists():
        log("No lyrics.txt; skipping lyric sync.")
        return None

    try:
        lyric_lines = [l.strip() for l in
                       lyrics_txt.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError as exc:
        logger.warning("Could not read lyrics.txt (%s); skipping lyric sync.", exc)
        return None
    if not lyric_lines:
        return None

    shot_lines = shot_lyric_map(prompts_json, lyric_lines)
    if len(shot_lines) < MIN_ANCHORS:
        log(f"Only {len(shot_lines)} shot(s) quote a lyric; skipping lyric sync.")
        return None

    words = transcribe_words(music_path, cache_path)
    if not words:
        return None

    occurrences = find_line_occurrences(words, lyric_lines)
    sung = sum(len(v) for v in occurrences.values())
    log(f"Lyric alignment: matched {len(occurrences)}/{len(lyric_lines)} lines "
        f"({sung} occurrence(s)) in the vocal")

    anchors = _assign_anchors(shots, shot_lines, occurrences, song_duration, bar_times)
    if len(anchors) < MIN_ANCHORS:
        log(f"Only {len(anchors)} shot(s) could be anchored to sung lyrics "
            f"(need {MIN_ANCHORS}); using shotlist durations.")
        return None

    order = _order_shots(shots, anchors, song_duration)
    starts = _distribute(order, shots, anchors, song_duration)
    starts = _snap_starts(order, anchors, starts, bar_times, song_duration)
    starts = _enforce_increasing(starts)
    durations = _durations_from_starts(starts, song_duration)

    ordered_shots = [shots[i] for i in order]
    report = []
    for p, i in enumerate(order):
        a = anchors.get(i)
        report.append({
            "shot_id": shots[i]["shot_id"],
            "start": starts[p],
            "duration": durations[p],
            "anchored": a is not None,
            "lyric": lyric_lines[a["line"]] if a else None,
            "ratio": a["ratio"] if a else None,
            "raw": a["raw"] if a else None,
        })

    log(f"Lyric-sync: anchored {len(anchors)}/{len(shots)} shots to sung lyrics; "
        f"timeline now spans {sum(durations):.2f}s (song is {song_duration:.2f}s)")
    for r in report:
        if r["anchored"]:
            log(f"  {r['shot_id']} @ {r['start']:6.2f}s ({r['duration']:5.2f}s) "
                f"<- vocal {r['raw']:.2f}s [{r['ratio']:.2f}] \"{r['lyric'][:44]}\"")
        else:
            log(f"  {r['shot_id']} @ {r['start']:6.2f}s ({r['duration']:5.2f}s) "
                f"<- no lyric anchor (interpolated)")

    return ordered_shots, durations, report


def main():
    """Debug entry point: print the lyric-sync plan for a project."""
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s",
                        stream=sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", required=True)
    args = parser.parse_args()

    # Imported lazily so this module stays importable without make_dailies.
    from make_dailies import (PROJECTS_DIR, OUTPUTS_DIR, read_shotlist,
                              find_music, probe_duration, build_bar_grid)

    project_dir = PROJECTS_DIR / args.project
    shots = read_shotlist(project_dir / "shotlist.csv")
    music = find_music(args.project)
    if not music:
        print("No music for project", args.project)
        return
    duration = probe_duration(music)
    bar_times, _tempo, _bar_seconds = build_bar_grid(project_dir, music, duration)
    result = plan(project_dir, shots, music, duration, bar_times,
                  OUTPUTS_DIR / f"{args.project}_transcript.json")
    if not result:
        print("No lyric-sync plan (would fall back to shotlist durations)")


if __name__ == "__main__":
    main()
