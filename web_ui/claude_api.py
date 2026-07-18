import os
import json
import logging
import math
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

logger = logging.getLogger(__name__)
_client = None


def check_api_key() -> None:
    """Raise ValueError if the API key is missing or still the placeholder value."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env")
    if "your-key-here" in key or key == "sk-ant-...your-key-here...":
        raise ValueError("ANTHROPIC_API_KEY still contains the placeholder value — add your real key to .env")


def _extract_text(response) -> str:
    """Return the text content from a Claude response, skipping ThinkingBlocks."""
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    raise ValueError("No text block found in Claude response")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that Claude sometimes wraps around JSON."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop opening fence line (e.g. ```json or just ```)
        stripped = stripped[stripped.index("\n") + 1 :] if "\n" in stripped else stripped[3:]
        # Drop closing fence
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3].rstrip()
    return stripped


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        _client = Anthropic(api_key=key)
    return _client


_ROMAN_SCRIPT_LANGUAGES = {"hindi", "mandarin chinese", "japanese", "arabic", "korean", "thai"}


# Story templates shape the WHOLE song (structure + a suggested music style), so a
# single choice changes lyrics -> scenes -> video, not just the theme wording. The
# UI lists these; generate_lyrics injects the matching structure instruction.
SONG_TEMPLATES = {
    "nursery": {
        "label": "Nursery rhyme",
        "style": "nursery rhyme",
        "instruction": ("Write a simple, playful nursery rhyme with an easy, repeating "
                        "sing-along chorus and short rhyming lines."),
    },
    "lullaby": {
        "label": "Lullaby",
        "style": "lullaby",
        "instruction": ("Write a gentle bedtime lullaby: slow and soothing, with soft "
                        "imagery of the moon, stars, sleep and dreams, and a tender "
                        "repeating refrain."),
    },
    "counting": {
        "label": "Counting song",
        "style": "upbeat pop",
        "instruction": ("Write a counting song that counts in order from one to ten. Each "
                        "verse introduces the next number with a concrete, picturable "
                        "example (one sun, two shoes...). Keep a repeating chorus."),
    },
    "alphabet": {
        "label": "Alphabet song",
        "style": "upbeat pop",
        "instruction": ("Write an alphabet learning song that moves through letters in "
                        "order, giving each letter a simple word and image (A is for "
                        "apple...). Keep it rhythmic and easy to sing along."),
    },
    "moral": {
        "label": "Moral story",
        "style": "folk",
        "instruction": ("Write a short story-song with a clear beginning, middle and a "
                        "kind moral at the end (sharing, honesty, courage). Use the "
                        "verses to advance the story and a chorus that hints at the "
                        "lesson."),
    },
    "action": {
        "label": "Action & dance",
        "style": "upbeat pop",
        "instruction": ("Write a high-energy action song that calls out simple movements "
                        "(clap, jump, spin, stomp). Each verse names a new action with an "
                        "encouraging chorus."),
    },
}


def list_song_templates() -> list:
    """Public list of templates for the UI: [{id, label, style}, ...]."""
    return [{"id": k, "label": v["label"], "style": v["style"]}
            for k, v in SONG_TEMPLATES.items()]


def generate_lyrics(theme: str, style: str, num_verses: int, language: str = "English",
                    template: str | None = None) -> dict:  # noqa: D417
    """Generate kids song lyrics for the given theme and style.

    Args:
        theme: The subject or topic of the song (e.g. "dinosaurs", "friendship").
        style: Musical style descriptor (e.g. "upbeat", "lullaby").
        num_verses: How many verses the song should have.
        language: Language for the lyrics (e.g. "English", "Hindi").
        template: Optional story-template id (see SONG_TEMPLATES) that shapes the
            song's structure; falls back to a plain kids song when absent/unknown.

    Returns:
        dict with keys: title (str), lyrics_text (str), verses (list[str]).

    Raises:
        RuntimeError: If the Claude API call fails.
    """
    check_api_key()
    if not theme or not theme.strip():
        raise ValueError("'theme' is required to generate lyrics")
    if not 1 <= num_verses <= 10:
        raise ValueError(f"'num_verses' must be between 1 and 10, got {num_verses}")

    language = language.strip() or "English"
    use_roman = language.lower() in _ROMAN_SCRIPT_LANGUAGES
    tmpl = SONG_TEMPLATES.get((template or "").strip())
    style = (style or "").strip() or (tmpl["style"] if tmpl else "")
    template_note = f" {tmpl['instruction']}" if tmpl else ""

    client = _get_client()
    system_prompt = (
        "You are a children's songwriter. "
        "Write fun, educational, age-appropriate songs for kids aged 3-8."
    )
    script_note = (
        f" Write the lyrics in {language} but use Roman/English alphabet characters "
        "(transliteration — no native script like Devanagari, Kanji, etc.)."
        if use_roman
        else f" Write entirely in {language}."
    )
    user_prompt = (
        f'Write a kids song about "{theme}" in a {style} style with {num_verses} verses.'
        f"{template_note}{script_note} "
        "Return your answer as a JSON object with keys: "
        '"title" (string), "lyrics_text" (full song as one string), '
        '"verses" (array of verse strings, one element per verse). '
        "Output only the JSON object with no additional text."
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        raise RuntimeError(f"Claude API call failed in generate_lyrics: {exc}") from exc

    raw_text = _extract_text(response)
    logger.debug("generate_lyrics raw response: %s", raw_text)

    try:
        return json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        logger.warning("generate_lyrics: could not parse JSON; returning fallback")
        return {"title": "My Song", "lyrics_text": raw_text, "verses": []}


DEFAULT_TEMPO_BPM = 120
DEFAULT_STYLE = "kids pop"

# Sane musical bounds. music_gen.arrangement_to_wav already floors at 40 BPM;
# clamping here keeps an implausible model answer from reaching the synth.
_MIN_TEMPO_BPM = 40
_MAX_TEMPO_BPM = 208


def coerce_tempo(value, fallback: int = DEFAULT_TEMPO_BPM) -> int:
    """Return *value* as a BPM int clamped to a playable range, else *fallback*.

    Never raises: json.loads accepts non-standard ``Infinity``/``NaN``/``1e999``,
    and int(inf) raises OverflowError — so every conversion failure degrades to
    *fallback* rather than propagating to the caller.
    """
    try:
        tempo = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(_MIN_TEMPO_BPM, min(_MAX_TEMPO_BPM, tempo))


def coerce_style(value, fallback: str = DEFAULT_STYLE) -> str:
    """Return *value* as a non-empty style string, else *fallback*.

    Never raises: Claude may return a non-string (e.g. a list of adjectives),
    which would break a bare ``.strip()``.
    """
    if not isinstance(value, str):
        return fallback
    return value.strip() or fallback


def generate_chords(
    lyrics_text: str,
    style: str | None = None,
    tempo_bpm: int | None = None,
) -> dict:
    """Suggest simple ukulele chords, a tempo, and a style for a kids song.

    Args:
        lyrics_text: The full song lyrics as a string.
        style: Optional musical style (e.g. "upbeat", "lullaby").  When given
            it steers Claude's tempo choice and is echoed back verbatim, so the
            user's pick — not a model guess — is what reaches the synth.
        tempo_bpm: Optional explicit tempo.  When given it overrides Claude's
            suggestion entirely.

    Returns:
        dict with keys: chords (list[str]), chord_chart (str),
        tempo_bpm (int), style (str).

    Raises:
        RuntimeError: If the Claude API call fails.
    """
    check_api_key()
    if not lyrics_text or not lyrics_text.strip():
        raise ValueError("'lyrics_text' cannot be empty — generate or paste lyrics first")
    if len(lyrics_text.strip()) < 20:
        raise ValueError("Lyrics are too short — add more content before generating chords")

    requested_style = (style or "").strip()
    # An explicit tempo is user intent and always wins over the model's guess.
    forced_tempo = coerce_tempo(tempo_bpm, fallback=0) if tempo_bpm is not None else 0

    client = _get_client()
    system_prompt = (
        "You are a music teacher specialising in simple ukulele arrangements for children. "
        "Suggest beginner-friendly chords that match the mood and rhythm of the lyrics."
    )
    style_note = (
        f"The song should be in a {requested_style} style — pick a tempo that suits it.\n"
        if requested_style
        else "Infer the style and tempo from the mood and rhythm of the lyrics.\n"
    )
    user_prompt = (
        "Suggest simple ukulele chords for the following kids song lyrics.\n"
        f"{style_note}"
        "Return a JSON object with keys: "
        '"chords" (array of chord names used, e.g. ["C", "G", "Am", "F"]), '
        '"chord_chart" (string showing chord placements above lyric lines), '
        f'"tempo_bpm" (integer beats per minute between {_MIN_TEMPO_BPM} and '
        f"{_MAX_TEMPO_BPM}; kids songs are typically 90-140), "
        '"style" (short style descriptor, e.g. "upbeat kids pop", "gentle lullaby"). '
        "Output only the JSON object with no additional text.\n\n"
        f"Lyrics:\n{lyrics_text}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        raise RuntimeError(f"Claude API call failed in generate_chords: {exc}") from exc

    raw_text = _extract_text(response)
    logger.debug("generate_chords raw response: %s", raw_text)

    try:
        result = json.loads(_strip_fences(raw_text))
        if not isinstance(result, dict):
            raise json.JSONDecodeError("expected a JSON object", raw_text, 0)
    except json.JSONDecodeError:
        logger.warning("generate_chords: could not parse JSON; returning fallback")
        result = {"chords": [], "chord_chart": raw_text}

    # Guarantee tempo_bpm/style are always present and sane, whatever Claude
    # returned — generate_instrumental reads these straight off chords.json.
    result["tempo_bpm"] = forced_tempo or coerce_tempo(result.get("tempo_bpm"))
    result["style"] = requested_style or coerce_style(result.get("style"))
    return result


def generate_music_arrangement(
    chords: list[str], lyrics: str, tempo_bpm: int, style: str
) -> dict:
    """Generate a bar-by-bar chord arrangement for music synthesis.

    Returns:
        dict with keys: tempo_bpm (int), bars (list of {chord, beats}).
    """
    check_api_key()
    client = _get_client()
    chord_list = ", ".join(chords) if chords else "C, G, Am, F"
    user_prompt = (
        f"Create a bar-by-bar chord arrangement for a kids song.\n"
        f"Available chords: {chord_list}\n"
        f"Tempo: {tempo_bpm} BPM, Style: {style}\n"
        f"Lyrics:\n{lyrics}\n\n"
        "Generate 16–32 bars covering intro, verses, chorus, outro. "
        "Match chord changes to the lyric phrasing.\n"
        "Return a JSON object with keys:\n"
        '  "tempo_bpm": number,\n'
        '  "bars": array of {"chord": string, "beats": number (2 or 4)}\n'
        "Output only the JSON object, no other text."
    )
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        raise RuntimeError(f"Claude API call failed in generate_music_arrangement: {exc}") from exc

    raw = _extract_text(response)
    try:
        return json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        cycle = (chords or ["C", "G", "Am", "F"]) * 4
        return {"tempo_bpm": tempo_bpm, "bars": [{"chord": c, "beats": 4} for c in cycle]}


_VALID_CAMERAS = ("Wide", "Medium", "Close")

# Guard rails on the scene count Claude may return. A kids song broken roughly
# one-scene-per-line tops out well under 40; anything past that is a runaway
# answer, and fewer than 2 is not a storyboard. These only clamp the extremes —
# the count inside this band is entirely Claude's call.
_MIN_SCENES = 2
_MAX_SCENES = 40

# Fallback pacing when no song_duration is given: seconds of screen time per
# lyric line. Kids songs sit around 3–5s per sung line.
_SECONDS_PER_LINE = 4.0


def _coerce_camera(value) -> str:
    """Map an arbitrary camera string to one of _VALID_CAMERAS (default Medium)."""
    if not isinstance(value, str):
        return "Medium"
    v = value.strip().lower()
    if v.startswith("w"):
        return "Wide"
    if v.startswith("c") or "close" in v:
        return "Close"
    return "Medium"


def _coerce_duration(value, fallback: float = _SECONDS_PER_LINE) -> float:
    """Return *value* as a positive float duration in seconds, else *fallback*."""
    try:
        d = float(value)
    except (TypeError, ValueError):
        return fallback
    # Reject non-positive, NaN, and inf. json.loads accepts Infinity/NaN, and an
    # inf here would poison the duration rescale (factor -> 0, every scene 1.0s).
    if not math.isfinite(d) or d <= 0:
        return fallback
    return d


def _fallback_scene_list(lyrics_text: str, song_duration: float | None) -> list[dict]:
    """Build a sane, still-dynamic scene list without calling Claude.

    One scene per non-empty lyric line (so the count still reflects the song's
    length), clamped to [_MIN_SCENES, _MAX_SCENES]. Durations spread evenly over
    song_duration when given, else a per-line default.
    """
    lines = [ln.strip() for ln in lyrics_text.splitlines() if ln.strip()]
    # Drop obvious section markers like "Verse 1:" / "Chorus:" so they don't
    # become their own (empty) scenes.
    lines = [ln for ln in lines if not re.fullmatch(r"(verse|chorus|bridge|outro|intro)\s*\d*\s*:?", ln, re.IGNORECASE)]
    if not lines:
        lines = ["Opening scene", "Closing scene"]

    n = max(_MIN_SCENES, min(_MAX_SCENES, len(lines)))
    lines = lines[:n]
    if song_duration and song_duration > 0:
        per = round(song_duration / n, 1)
    else:
        per = _SECONDS_PER_LINE

    cameras = ("Wide", "Medium", "Medium", "Close")
    scenes: list[dict] = []
    for i, line in enumerate(lines):
        scenes.append(
            {
                "shot_id": f"SH{(i + 1) * 10:03d}",
                "description": line,
                "camera": cameras[i % len(cameras)],
                "duration": per,
                "lyric_ref": line,
            }
        )
    return scenes


def _normalize_scenes(raw_scenes: list, song_duration: float | None,
                      protagonist: str = "") -> list[dict]:
    """Clean Claude's scene list: sequential ids, valid cameras/durations, scaled.

    - Assigns clean sequential shot_ids (SH010, SH020, …) regardless of what
      Claude returned, so downstream shot lookup is never ambiguous.
    - Clamps the count to [_MIN_SCENES, _MAX_SCENES].
    - When song_duration is given, rescales durations proportionally so they sum
      to approximately song_duration (the scenes then cover the whole song).
    - Threads each scene's ``character`` (defaulting empty character scenes to the
      protagonist so the lead carries the video) and its lyric-matched ``setting``.
    """
    scenes: list[dict] = []
    for item in raw_scenes:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description", "")).strip()
        lyric_ref = str(item.get("lyric_ref", "")).strip()
        if not desc and not lyric_ref:
            continue
        character = str(item.get("character", "")).strip()
        scenes.append(
            {
                "description": desc or lyric_ref,
                "camera": _coerce_camera(item.get("camera")),
                "duration": _coerce_duration(item.get("duration")),
                "lyric_ref": lyric_ref,
                "character": character,
                "setting": str(item.get("setting", "")).strip()[:80],
            }
        )

    scenes = scenes[:_MAX_SCENES]
    if len(scenes) < _MIN_SCENES:
        return []  # too little to trust — caller falls back

    # Rescale durations to hit the target song length when we have one.
    if song_duration and song_duration > 0:
        total = sum(s["duration"] for s in scenes) or 1.0
        factor = song_duration / total
        for s in scenes:
            s["duration"] = max(1.0, round(s["duration"] * factor, 1))

    # Assign clean sequential ids last, overwriting anything Claude chose.
    for i, s in enumerate(scenes):
        s["shot_id"] = f"SH{(i + 1) * 10:03d}"

    # Normalise obvious "no character" markers to empty; trust Claude's assignment
    # otherwise (it puts the protagonist in most scenes, supporting where featured).
    for s in scenes:
        if s["character"].lower() in ("none", "scenery", "n/a", "-"):
            s["character"] = ""

    # Keep a stable key order for the returned dicts.
    return [
        {
            "shot_id": s["shot_id"],
            "description": s["description"],
            "camera": s["camera"],
            "duration": s["duration"],
            "lyric_ref": s["lyric_ref"],
            "character": s["character"],
            "setting": s["setting"],
        }
        for s in scenes
    ]


def plan_scenes(
    lyrics_text: str,
    style: str,
    language: str = "English",
    song_duration: float | None = None,
) -> list[dict]:
    """Let Claude decide how many scenes a music video needs and what each is.

    Reads the song's lyrics (and its length when known) and returns a scene list
    of Claude's chosen length — a short song gets fewer scenes, a long one more.
    The count is NOT fixed by a template; it tracks the lyric structure.

    Args:
        lyrics_text: The full song lyrics as a string.
        style: Visual/musical style descriptor (e.g. "upbeat", "lullaby").
        language: Language of the lyrics (for scene descriptions; default English).
        song_duration: Optional total song length in seconds. When given, scene
            durations are scaled to sum to approximately this value.

    Returns:
        List of scene dicts, each with keys:
            shot_id (str, sequential SH010/SH020/…), description (str),
            camera ("Wide"|"Medium"|"Close"), duration (float seconds),
            lyric_ref (str, the lyric line/phrase the scene illustrates).

    Never raises for a bad/empty Claude response — it degrades to a sane,
    still-song-shaped fallback scene list instead.
    """
    check_api_key()
    if not lyrics_text or not lyrics_text.strip():
        raise ValueError("'lyrics_text' cannot be empty — generate or paste lyrics first")

    lyrics_text = lyrics_text.strip()
    style = (style or "").strip() or "bright, cheerful kids animation"
    language = (language or "English").strip() or "English"

    client = _get_client()
    system_prompt = (
        "You are the director of a children's animated music video. "
        "Given a song's lyrics, you break it into a sequence of visual scenes for a "
        "storyboard. YOU decide how many scenes the video needs based on the song's "
        "structure — its verses, chorus, and lines. A short song needs only a few "
        "scenes; a long one needs more. Aim for roughly one scene per lyric line or "
        "couplet, but use your judgement so each scene shows one clear visual moment. "
        "Every scene must be safe and age-appropriate for kids aged 3-8.\n\n"
        "CAST CONSISTENCY (important): first decide ONE main character — the "
        "protagonist/hero who carries the WHOLE video from the first scene to the "
        "last — based on who the song is about. Give them a short, fixed description "
        "and reuse the EXACT SAME character name in every scene they appear in. The "
        "protagonist appears in MOST scenes. Add a few supporting characters (family, "
        "friends, animals the lyrics mention) and use them ONLY in the scenes where "
        "the lyrics feature them. Never invent a brand-new character for a scene that "
        "the protagonist could carry.\n\n"
        "SETTING MATCHES THE LYRICS: each scene's setting must be the place the lyric "
        "at that moment describes (a village lane, a school, a field, a home) — not "
        "generic scenery — and a place the scene's character would believably be."
    )

    duration_note = (
        f"The song is about {song_duration:.0f} seconds long — choose scene "
        "durations (in seconds) that add up to roughly that total so the scenes "
        "cover the whole song.\n"
        if song_duration and song_duration > 0
        else "Give each scene a sensible duration in seconds (kids-song lines "
        "run about 3-5 seconds).\n"
    )

    user_prompt = (
        f"Plan the scenes for a {style} children's music video.\n"
        f"Lyrics language: {language}.\n"
        f"{duration_note}"
        "Decide how many scenes best fit these lyrics — do NOT pad to a fixed "
        "number. Walk through the song in order, one scene per lyric line or "
        "couplet.\n\n"
        "Return a JSON OBJECT with keys:\n"
        '  "protagonist": {"name": a short DESCRIPTIVE character label reused verbatim '
        'in every scene — e.g. "village girl", "little boy", "grandmother" — NOT a '
        'proper name like "Priya", "description": one line}\n'
        '  "supporting": array of {"name","description"} (names also descriptive labels '
        'like "mother", "friend", "puppy")\n'
        '  "scenes": array where each element has keys:\n'
        '     "description" (what is visually happening in the scene),\n'
        '     "character" (the ONE character in this scene — the protagonist by '
        "default, or a supporting character's name when the lyric features them; use "
        'the EXACT name from the cast, or "" for a pure scenery/title moment),\n'
        '     "setting" (2-6 words for the location, matching THIS lyric line),\n'
        '     "camera" (exactly one of "Wide", "Medium", "Close"),\n'
        '     "duration" (number: seconds of screen time),\n'
        '     "lyric_ref" (the exact lyric line this scene illustrates).\n\n'
        "The protagonist's name must be identical everywhere they appear so the same "
        "character is drawn throughout. Output only the JSON object.\n\n"
        f"Lyrics:\n{lyrics_text}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        logger.warning("plan_scenes: Claude call failed (%s); using fallback", exc)
        return _fallback_scene_list(lyrics_text, song_duration)

    raw_text = _extract_text(response)
    logger.debug("plan_scenes raw response: %s", raw_text)

    try:
        parsed = json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        logger.warning("plan_scenes: could not parse JSON; using fallback")
        return _fallback_scene_list(lyrics_text, song_duration)
    # new cast-aware object form {protagonist, supporting, scenes}; fall back to a
    # bare array for older/degraded responses.
    if isinstance(parsed, dict):
        raw_list = parsed.get("scenes", [])
        proto = (parsed.get("protagonist") or {}).get("name", "") if isinstance(parsed.get("protagonist"), dict) else ""
    elif isinstance(parsed, list):
        raw_list, proto = parsed, ""
    else:
        logger.warning("plan_scenes: unexpected JSON shape; using fallback")
        return _fallback_scene_list(lyrics_text, song_duration)

    scenes = _normalize_scenes(raw_list, song_duration, protagonist=proto)
    if not scenes:
        logger.warning("plan_scenes: normalized scene list too small; using fallback")
        return _fallback_scene_list(lyrics_text, song_duration)
    return scenes


# --------------------------------------------------------------------------- #
# Per-scene camera/parallax motion, authored by Claude from the scene prompt.  #
# --------------------------------------------------------------------------- #

# The pan directions the depth-parallax renderer understands (scripts/
# depth_parallax.py _DRIFT_DIRS).  "in"/"out" are pure push (dolly) moves with no
# lateral pan; "hold" is an almost-static gentle shot.
_MOTION_DIRECTIONS = (
    "in", "out", "left", "right", "up", "down",
    "up-left", "up-right", "down-left", "down-right", "hold",
)

# When Claude is unavailable or a scene has no prompt we fall back to the
# renderer's tuned defaults: the rotating _DRIFTS palette (signalled by
# drift=None) at near_pan_frac 0.11 (intensity 0.5) and push 0.05 (push 0.25).
_DEFAULT_INTENSITY = 0.5
_DEFAULT_PUSH = 0.25

# Common phrasings Claude might emit, mapped onto a canonical direction.
_DRIFT_ALIASES = {
    "zoom in": "in", "push in": "in", "dolly in": "in", "forward": "in",
    "zoom out": "out", "push out": "out", "pull back": "out", "dolly out": "out",
    "back": "out", "backward": "out",
    "pan left": "left", "pan right": "right",
    "tilt up": "up", "pan up": "up", "tilt down": "down", "pan down": "down",
    "static": "hold", "none": "hold", "still": "hold", "hold still": "hold",
    "upleft": "up-left", "up left": "up-left", "leftup": "up-left",
    "upright": "up-right", "up right": "up-right", "rightup": "up-right",
    "downleft": "down-left", "down left": "down-left", "leftdown": "down-left",
    "downright": "down-right", "down right": "down-right", "rightdown": "down-right",
}


def _fallback_motion(reason: str = "default rotating drift (Claude unavailable)") -> dict:
    """The tuned-default motion: rotating palette drift, centred intensity/push.

    drift=None tells the renderer to cycle its fixed _DRIFTS palette by the
    shot's index, exactly as the general path did before Claude motion existed.
    """
    return {
        "drift": None,
        "intensity": _DEFAULT_INTENSITY,
        "push": _DEFAULT_PUSH,
        "reason": reason,
    }


def _coerce_drift(value):
    """Map an arbitrary drift string onto a canonical _MOTION_DIRECTIONS entry.

    Returns None (→ renderer default rotating drift) when the value is missing or
    unrecognisable, so a stray Claude answer degrades to the tuned default rather
    than a wrong hard direction.
    """
    if not isinstance(value, str):
        return None
    v = value.strip().lower().replace("_", "-")
    if v in _MOTION_DIRECTIONS:
        return v
    if v in _DRIFT_ALIASES:
        return _DRIFT_ALIASES[v]
    # Last resort: match on whole WORDS (not substrings, so "maintain"/"rising"
    # don't spuriously match "in"), longest direction first so "up-left" beats a
    # bare "up".  A hyphenated direction also matches its spaced form ("up left").
    words = set(re.split(r"[^a-z]+", v))
    for d in sorted(_MOTION_DIRECTIONS, key=len, reverse=True):
        if d in words:
            return d
        if "-" in d and d.replace("-", " ") in v:
            return d
    return None


def _coerce_unit(value, fallback: float) -> float:
    """Return *value* as a float clamped to [0, 1], else *fallback*."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(f):
        return fallback
    return max(0.0, min(1.0, f))


def _normalize_motion(item) -> dict:
    """Clean one Claude motion dict into {drift, intensity, push, reason}."""
    if not isinstance(item, dict):
        return _fallback_motion("unparseable motion entry")
    return {
        "drift": _coerce_drift(item.get("drift")),
        "intensity": _coerce_unit(item.get("intensity"), _DEFAULT_INTENSITY),
        "push": _coerce_unit(item.get("push"), _DEFAULT_PUSH),
        "reason": str(item.get("reason", "")).strip(),
    }


_MOTION_SYSTEM_PROMPT = (
    "You are the cinematographer for a children's animated music video. Each "
    "scene is a single flat storyboard illustration that will be brought to life "
    "with 2.5D depth parallax — the image is sliced into near/mid/far layers and "
    "the virtual camera drifts, so foreground and background move at different "
    "rates. Your job: read a scene's description and choose the ONE camera move "
    "that best fits what is happening in it.\n\n"
    "Motion vocabulary (pick exactly one 'drift' per scene):\n"
    "  in         slow dolly/push toward the scene — good for wide establishing "
    "shots settling into a place\n"
    "  out        pull back / reveal — good for endings or opening up a space\n"
    "  left,right lateral track — good for characters running, chasing, walking "
    "across, or motion travelling sideways\n"
    "  up         drift upward — good for things rising: flying up, climbing, "
    "growing tall, looking to the sky\n"
    "  down       drift downward — good for falling, landing, looking down, "
    "settling\n"
    "  up-left, up-right, down-left, down-right  diagonal drifts for combined "
    "motion\n"
    "  hold       almost static, gentle — good for a calm close-up of one "
    "character singing\n\n"
    "Also choose 'intensity' 0.0-1.0 (how far the camera travels: 0.1 barely "
    "moving for a calm close-up, ~0.5 normal, 0.9 energetic for action/chase) and "
    "'push' 0.0-1.0 (how much it also zooms: low for a held shot, moderate for an "
    "establishing settle). Keep moves gentle overall — this is a soft kids video."
)


def _request_scene_motions(shots: list[dict]) -> dict:
    """One Claude call returning {shot_id: motion} for every shot in *shots*.

    Economical by design — the whole video is planned in a single request. Never
    raises: any failure (API error, bad JSON, missing key) degrades that shot (or
    all shots) to the tuned-default fallback motion.
    """
    client = _get_client()
    lines = []
    for s in shots:
        dur = _coerce_duration(s.get("duration"), 4.0)
        cam = str(s.get("camera", "")).strip() or "Medium"
        lines.append(
            {
                "shot_id": str(s.get("shot_id", "")),
                "camera": cam,
                "duration": round(dur, 1),
                "description": str(s.get("prompt", "")).strip(),
            }
        )
    user_prompt = (
        "Choose the camera motion for each of these scenes. For every scene "
        "return an object with keys:\n"
        '  "shot_id" (string, echo the input id),\n'
        '  "drift" (string: one of in, out, left, right, up, down, up-left, '
        "up-right, down-left, down-right, hold),\n"
        '  "intensity" (number 0.0-1.0),\n'
        '  "push" (number 0.0-1.0),\n'
        '  "reason" (short string: why this move fits the scene).\n\n'
        "Return ONLY a JSON array, one object per scene, no other text.\n\n"
        f"Scenes:\n{json.dumps(lines, indent=2)}"
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=_MOTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw_text = _extract_text(response)
    logger.debug("author_scene_motions raw response: %s", raw_text)
    parsed = json.loads(_strip_fences(raw_text))
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array of motions")

    by_id: dict[str, dict] = {}
    for item in parsed:
        if isinstance(item, dict) and str(item.get("shot_id", "")).strip():
            by_id[str(item["shot_id"]).strip()] = _normalize_motion(item)
    return by_id


def author_scene_motions(shots: list[dict]) -> dict:
    """Author the camera/parallax motion for a whole shot list in ONE Claude call.

    Args:
        shots: list of dicts, each with keys shot_id (str), prompt (str, the
            scene's storyboard prompt/description), camera (str), duration
            (float seconds).

    Returns:
        dict mapping each shot_id to a motion dict
        {drift, intensity, push, reason} — see author_scene_motion. Every input
        shot_id is present in the result; a shot Claude omitted (or the whole
        call on failure) falls back to the renderer's tuned defaults.

    Never raises — a failed/garbled Claude response degrades to sane defaults so
    the animation build always proceeds.
    """
    result = {str(s.get("shot_id", "")): _fallback_motion() for s in shots}
    usable = [s for s in shots if str(s.get("prompt", "")).strip()]
    if not usable:
        return result  # nothing to describe → all defaults

    try:
        check_api_key()
        motions = _request_scene_motions(usable)
    except Exception as exc:  # noqa: BLE001
        logger.warning("author_scene_motions: Claude call failed (%s); using defaults", exc)
        return result

    for sid, motion in motions.items():
        if sid in result:
            result[sid] = motion
    return result


def author_scene_motion(prompt: str, camera: str, duration: float) -> dict:
    """Let Claude choose the camera/parallax motion that fits ONE scene's prompt.

    Reads the scene's storyboard description and returns motion params the
    depth-parallax renderer understands:
        {"drift": "in"|"out"|"left"|"right"|"up"|"down"|"up-left"|…|"hold"|None,
         "intensity": 0.0-1.0, "push": 0.0-1.0, "reason": str}
    where drift is the pan direction (None → renderer's default rotating drift),
    intensity scales how far the near layer pans, and push the zoom amount.

    Never raises: an empty prompt or any Claude failure returns the tuned-default
    fallback motion instead of throwing.
    """
    if not prompt or not prompt.strip():
        return _fallback_motion("no scene prompt — using default motion")
    shot_id = "SH_ONE"
    motions = author_scene_motions(
        [{"shot_id": shot_id, "prompt": prompt, "camera": camera, "duration": duration}]
    )
    return motions.get(shot_id, _fallback_motion())


_BODY_MOTIONS = ("still", "bob", "sway", "hop", "slide")
_CAMERAS = ("hold", "push_in", "pull_out", "pan_left", "pan_right")


def _parse_scene_objects(text: str) -> list:
    """Parse a JSON array of scene objects, salvaging a truncated response.

    A long shot list can overrun the model's token budget and cut the JSON off
    mid-array. Rather than lose every scene to a parse error, pull out as many
    complete top-level objects as decoded cleanly (the tail object is dropped)."""
    text = _strip_fences(text).strip()
    try:
        v = json.loads(text)
        if isinstance(v, list):
            return v
    except Exception:  # noqa: BLE001 — fall through to object-by-object salvage
        pass
    objs, dec = [], json.JSONDecoder()
    i = text.find("[")
    i = 0 if i < 0 else i + 1
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] == "]":
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except Exception:  # noqa: BLE001 — reached the truncated tail; stop
            break
        objs.append(obj)
    return objs


def _fallback_choreo(reason: str = "default (Claude unavailable)") -> dict:
    """Sane choreography when Claude can't direct a scene."""
    return {"character": "", "setting": "sunny park meadow", "sings": True,
            "body_motion": "bob", "camera": "push_in", "intensity": 0.6,
            "mood": "happy", "reason": reason}


def direct_scenes(shots: list[dict]) -> dict:
    """Direct a whole shot list in ONE Claude call: scene -> choreography.

    Each shot: {shot_id, prompt/description, camera, duration}. Returns
    {shot_id: {character, sings, body_motion, camera, intensity, mood, reason}}
    for the deterministic scene author (scripts/scene_director.py) to execute.

    Never raises: any failure degrades to sane default choreography so the build
    always proceeds. This is the "Claude directs, code executes" split — Claude
    makes the creative call, the renderer stays deterministic.
    """
    result = {str(s.get("shot_id", "")): _fallback_choreo() for s in shots}
    usable = [s for s in shots if str(s.get("prompt", s.get("description", ""))).strip()]
    if not usable:
        return result
    try:
        check_api_key()
        client = _get_client()
        # A cast-consistent shotlist already pins character + setting per scene; keep
        # them so the protagonist stays the same throughout and backgrounds match the
        # lyric. Only older shotlists leave these blank for Claude to derive.
        pre = {str(s.get("shot_id", "")): s for s in usable}
        has_cast = any(str(s.get("character", "")).strip() for s in usable)
        lines = [{"shot_id": str(s.get("shot_id", "")),
                  "duration": round(_coerce_duration(s.get("duration"), 4.0), 1),
                  "description": str(s.get("prompt", s.get("description", ""))).strip()[:400],
                  "character": str(s.get("character", "")).strip(),
                  "setting": str(s.get("setting", "")).strip()}
                 for s in usable]
        cast_note = (
            "Each scene ALREADY has an assigned character and setting (keep the same "
            "character across scenes for consistency) — do NOT change them; only "
            "decide the animation.\n" if has_cast else "")
        user_prompt = (
            "You are the animation director for a children's music video. For each "
            "scene below, decide how its main character should be animated. "
            f"{cast_note}Return an object per scene with keys:\n"
            '  "shot_id" (echo the id),\n'
            '  "character" (echo the scene\'s assigned character if given; otherwise '
            'the ONE main character to animate — an animal OR a person; use "" only '
            "for a true scenery/title moment),\n"
            '  "setting" (echo the scene\'s assigned setting if given; otherwise 2-5 '
            'words for the location, NO animals or characters),\n'
            '  "sings" (boolean: is this character singing/vocalising here? drives lip-sync),\n'
            f'  "body_motion" (one of {", ".join(_BODY_MOTIONS)}),\n'
            f'  "camera" (one of {", ".join(_CAMERAS)}),\n'
            '  "intensity" (number 0.0-1.0, how energetic),\n'
            '  "mood" (short string),\n'
            '  "reason" (short: why this fits the scene).\n\n'
            "Match the motion to the scene: a character singing a verse -> bob + sings true; "
            "running/chasing -> slide or hop; a calm establishing shot -> still + push_in. "
            "Return ONLY a JSON array, one object per scene.\n\n"
            f"Scenes:\n{json.dumps(lines, indent=2)}"
        )
        # Budget ~300 tokens per scene object so a long shot list isn't truncated
        # mid-array (which used to drop every scene to the empty-character fallback).
        max_tokens = max(2048, min(8000, 400 + 300 * len(lines)))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=max_tokens,
            messages=[{"role": "user", "content": user_prompt}],
        )
        parsed = _parse_scene_objects(_extract_text(response))
        if not parsed:
            raise ValueError("no scene objects parsed from response")
        for item in parsed:
            sid = str(item.get("shot_id", ""))
            if sid in result:
                ps = pre.get(sid, {})
                if has_cast:      # pinned by the storyboard — enforce, don't re-derive
                    character = str(ps.get("character", "")).strip()
                    setting = str(ps.get("setting", "")).strip() or str(item.get("setting", "")).strip()
                else:
                    character = str(item.get("character", "")).strip()
                    setting = str(item.get("setting", "")).strip()
                result[sid] = {
                    "character": character,
                    "setting": setting[:80],
                    "sings": bool(item.get("sings", True)),
                    "body_motion": item.get("body_motion") if item.get("body_motion") in _BODY_MOTIONS else "bob",
                    "camera": item.get("camera") if item.get("camera") in _CAMERAS else "push_in",
                    "intensity": max(0.0, min(1.0, _coerce_duration(item.get("intensity"), 0.6))),
                    "mood": str(item.get("mood", "happy"))[:40],
                    "reason": str(item.get("reason", ""))[:120],
                }
    except Exception as exc:  # noqa: BLE001
        logger.warning("direct_scenes: Claude call failed (%s); using defaults", exc)
    return result


def generate_storyboard_prompts(
    project_name: str, shotlist: list, style_guide: str, lyrics: str = ""
) -> list:
    """Generate Stable Diffusion image prompts for each shot in a storyboard.

    Args:
        project_name: Name of the animation project.
        shotlist: List of dicts, each with keys:
            shot_id (str), description (str), camera (str), duration (float).
        style_guide: High-level visual style description for the project.
        lyrics: Full song lyrics to ground each scene in the song content.

    Returns:
        List of dicts, each with keys:
            shot_id (str), prompt (str), negative_prompt (str).

    Raises:
        RuntimeError: If the Claude API call fails.
    """
    check_api_key()
    if not project_name or not project_name.strip():
        raise ValueError("'project_name' is required to generate storyboard prompts")
    if not shotlist:
        raise ValueError(
            f"Project '{project_name}' has no shots in shotlist.csv — add shots before generating prompts"
        )

    client = _get_client()

    sd_prefix = (
        "cartoon, flat color, children's illustration, 2d, cute, "
        "bright, bold outlines, simple shapes"
    )
    negative_prompt = (
        "realistic, photo, dark, scary, complex background, "
        "watermark, text, logo, adult"
    )

    system_prompt = (
        "You are a storyboard artist creating image prompts for a children's animated show. "
        "Each prompt must be safe, age-appropriate, and visually descriptive for Stable Diffusion. "
        "When song lyrics are provided, make each scene reflect the specific lyric content "
        "happening at that moment in the song."
    )

    shotlist_text = json.dumps(shotlist, indent=2)
    lyrics_section = f"\nSong lyrics (use these to make each scene match the song):\n{lyrics.strip()}\n" if lyrics.strip() else ""
    user_prompt = (
        f'Project: "{project_name}"\n'
        f"Style guide: {style_guide}\n"
        f"{lyrics_section}\n"
        "For each shot below, write a Stable Diffusion prompt that begins with:\n"
        f'"{sd_prefix}"\n\n'
        "The scene description must be specific to the lyrics/song content for that moment — "
        "not generic. Describe what characters are doing, what words/actions match the lyric line.\n\n"
        "Return a JSON array where each element has keys:\n"
        '  "shot_id" (string, matching the input),\n'
        '  "prompt" (string, the full SD prompt),\n'
        f'  "negative_prompt" (string, always include: "{negative_prompt}").\n\n'
        "Output only the JSON array with no additional text.\n\n"
        f"Shots:\n{shotlist_text}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:
        raise RuntimeError(
            f"Claude API call failed in generate_storyboard_prompts: {exc}"
        ) from exc

    raw_text = _extract_text(response)
    logger.debug("generate_storyboard_prompts raw response: %s", raw_text)

    try:
        return json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        logger.warning(
            "generate_storyboard_prompts: could not parse JSON; returning fallback"
        )
        return [
            {
                "shot_id": shot.get("shot_id", str(i)),
                "prompt": f"{sd_prefix}, {shot.get('description', '')}",
                "negative_prompt": negative_prompt,
            }
            for i, shot in enumerate(shotlist)
        ]
