import os
import json
import logging
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


def generate_chords(lyrics_text: str) -> dict:
    """Suggest simple ukulele chords for a kids song.

    Args:
        lyrics_text: The full song lyrics as a string.

    Returns:
        dict with at least: chords (list[str]), chord_chart (str).

    Raises:
        RuntimeError: If the Claude API call fails.
    """
    check_api_key()
    if not lyrics_text or not lyrics_text.strip():
        raise ValueError("'lyrics_text' cannot be empty — generate or paste lyrics first")
    if len(lyrics_text.strip()) < 20:
        raise ValueError("Lyrics are too short — add more content before generating chords")

    client = _get_client()
    system_prompt = (
        "You are a music teacher specialising in simple ukulele arrangements for children. "
        "Suggest beginner-friendly chords that match the mood and rhythm of the lyrics."
    )
    user_prompt = (
        "Suggest simple ukulele chords for the following kids song lyrics. "
        "Return a JSON object with keys: "
        '"chords" (array of chord names used, e.g. ["C", "G", "Am", "F"]), '
        '"chord_chart" (string showing chord placements above lyric lines). '
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
        return json.loads(_strip_fences(raw_text))
    except json.JSONDecodeError:
        logger.warning("generate_chords: could not parse JSON; returning fallback")
        return {"chords": [], "chord_chart": raw_text}


def generate_storyboard_prompts(
    project_name: str, shotlist: list, style_guide: str
) -> list:
    """Generate Stable Diffusion image prompts for each shot in a storyboard.

    Args:
        project_name: Name of the animation project.
        shotlist: List of dicts, each with keys:
            shot_id (str), description (str), camera (str), duration (float).
        style_guide: High-level visual style description for the project.

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
        "Each prompt must be safe, age-appropriate, and visually descriptive for Stable Diffusion."
    )

    shotlist_text = json.dumps(shotlist, indent=2)
    user_prompt = (
        f'Project: "{project_name}"\n'
        f"Style guide: {style_guide}\n\n"
        "For each shot below, write a Stable Diffusion prompt that begins with:\n"
        f'"{sd_prefix}"\n\n'
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
            max_tokens=1200,
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
