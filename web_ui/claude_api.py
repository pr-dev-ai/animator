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


def generate_lyrics(theme: str, style: str, num_verses: int, language: str = "English") -> dict:  # noqa: D417
    """Generate kids song lyrics for the given theme and style.

    Args:
        theme: The subject or topic of the song (e.g. "dinosaurs", "friendship").
        style: Musical style descriptor (e.g. "upbeat", "lullaby").
        num_verses: How many verses the song should have.
        language: Language for the lyrics (e.g. "English", "Hindi").

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
        f'Write a kids song about "{theme}" in a {style} style with {num_verses} verses.{script_note} '
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


def _normalize_scenes(raw_scenes: list, song_duration: float | None) -> list[dict]:
    """Clean Claude's scene list: sequential ids, valid cameras/durations, scaled.

    - Assigns clean sequential shot_ids (SH010, SH020, …) regardless of what
      Claude returned, so downstream shot lookup is never ambiguous.
    - Clamps the count to [_MIN_SCENES, _MAX_SCENES].
    - When song_duration is given, rescales durations proportionally so they sum
      to approximately song_duration (the scenes then cover the whole song).
    """
    scenes: list[dict] = []
    for item in raw_scenes:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("description", "")).strip()
        lyric_ref = str(item.get("lyric_ref", "")).strip()
        if not desc and not lyric_ref:
            continue
        scenes.append(
            {
                "description": desc or lyric_ref,
                "camera": _coerce_camera(item.get("camera")),
                "duration": _coerce_duration(item.get("duration")),
                "lyric_ref": lyric_ref,
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

    # Keep a stable key order for the returned dicts.
    return [
        {
            "shot_id": s["shot_id"],
            "description": s["description"],
            "camera": s["camera"],
            "duration": s["duration"],
            "lyric_ref": s["lyric_ref"],
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
        "Every scene must be safe and age-appropriate for kids aged 3-8."
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
        "Return a JSON array where each element has keys:\n"
        '  "description" (string: what is visually happening in the scene),\n'
        '  "camera" (string: exactly one of "Wide", "Medium", "Close"),\n'
        '  "duration" (number: seconds of screen time for this scene),\n'
        '  "lyric_ref" (string: the exact lyric line or phrase this scene '
        "illustrates).\n\n"
        "Output only the JSON array with no additional text.\n\n"
        f"Lyrics:\n{lyrics_text}"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
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
        if not isinstance(parsed, list):
            raise json.JSONDecodeError("expected a JSON array", raw_text, 0)
    except json.JSONDecodeError:
        logger.warning("plan_scenes: could not parse JSON; using fallback")
        return _fallback_scene_list(lyrics_text, song_duration)

    scenes = _normalize_scenes(parsed, song_duration)
    if not scenes:
        logger.warning("plan_scenes: normalized scene list too small; using fallback")
        return _fallback_scene_list(lyrics_text, song_duration)
    return scenes


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
