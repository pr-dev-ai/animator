"""
pipeline_api.py — thin wrappers around the existing animation pipeline scripts
so the Flask server can call them without reimplementing their logic.
"""

import csv
import glob
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Generator, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


def list_projects() -> list[str]:
    """Returns sorted list of project directory names in projects/."""
    projects_dir = REPO_ROOT / "projects"
    if not projects_dir.exists():
        return []
    return sorted(
        d.name
        for d in projects_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def check_comfyui_health() -> bool:
    """Returns True if ComfyUI is reachable at http://localhost:8188.

    Uses urllib.request with a 2-second timeout.  Returns False on any
    connection-level exception; returns True for HTTP-level errors (the
    server is running even if it returns a non-2xx status code).
    """
    try:
        with urllib.request.urlopen("http://localhost:8188", timeout=2):
            return True
    except urllib.error.HTTPError:
        # Server responded — it is reachable even if it returned an error code
        return True
    except Exception:
        return False


def create_project(name: str, type_: str) -> dict:
    """Creates a new project by calling scripts/create_project.py via subprocess.

    Returns {"name": name, "path": str(project_dir)}.
    Raises ValueError if the project already exists or the name is invalid.
    """
    # Perform a local fast-fail check before invoking the subprocess so the
    # caller receives a clear ValueError rather than parsing stderr.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError(
            f"Invalid project name {name!r} — only letters, numbers, "
            "underscores, and hyphens are allowed."
        )

    project_dir = REPO_ROOT / "projects" / name
    if project_dir.exists():
        raise ValueError(f"Project already exists: {project_dir}")

    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "create_project.py"),
        "--name",
        name,
        "--type",
        type_,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
    except Exception as exc:
        logger.error("Failed to launch create_project.py: %s", exc)
        raise ValueError(f"Failed to create project: {exc}") from exc

    if result.returncode != 0:
        msg = result.stderr.strip() or f"create_project.py exited with code {result.returncode}"
        logger.error("create_project.py failed: %s", msg)
        raise ValueError(msg)

    return {"name": name, "path": str(project_dir)}


def get_project_shots(project: str) -> list[dict]:
    """Reads projects/<project>/shotlist.csv.

    For each shot also checks whether voices/<project>/<shot_id>_*.wav exists.
    Returns a list of dicts with keys:
        shot_id, description, camera, duration, notes, has_wav, character
    character comes from dialogue.csv (matched by shot_id); defaults to
    "Character" when dialogue.csv is absent or the shot has no entry.
    """
    shotlist_csv = REPO_ROOT / "projects" / project / "shotlist.csv"
    dialogue_csv = REPO_ROOT / "projects" / project / "dialogue.csv"
    voices_dir = REPO_ROOT / "voices" / project

    if not shotlist_csv.exists():
        logger.error("shotlist.csv not found: %s", shotlist_csv)
        return []

    # Build a shot_id -> character mapping from dialogue.csv (best-effort)
    shot_characters: dict[str, str] = {}
    if dialogue_csv.exists():
        try:
            with open(dialogue_csv, "r", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    sid = row.get("shot_id", "").strip()
                    char = row.get("character", "").strip()
                    if sid and char:
                        shot_characters[sid] = char
        except Exception as exc:
            logger.error("Failed to read dialogue.csv: %s", exc)

    shots: list[dict] = []
    try:
        with open(shotlist_csv, "r", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                shot_id = row.get("shot_id", "").strip()
                if not shot_id:
                    continue

                character = shot_characters.get(shot_id, "Character")

                # Glob for any matching WAV file regardless of character suffix.
                # glob.escape() prevents shot_id values like "SH[010]" from being
                # mis-interpreted as character classes by the glob engine.
                wav_pattern = str(voices_dir / f"{glob.escape(shot_id)}_*.wav")
                has_wav = bool(glob.glob(wav_pattern))

                shots.append(
                    {
                        "shot_id": shot_id,
                        "description": row.get("description", "").strip(),
                        "camera": row.get("camera", "").strip(),
                        "duration": row.get("duration", "").strip(),
                        "notes": row.get("notes", "").strip(),
                        "has_wav": has_wav,
                        "character": character,
                    }
                )
    except Exception as exc:
        logger.error("Failed to read shotlist.csv: %s", exc)

    return shots


def import_audio(project: str, shot_id: str, character: str, wav_bytes: bytes) -> dict:
    """Saves WAV bytes to voices/<project>/<shot_id>_<character>.wav atomically.

    Creates the voices/<project>/ directory if needed.
    Returns {"path": relative_path_string}.
    Raises ValueError for empty wav_bytes.
    """
    if not wav_bytes:
        raise ValueError("wav_bytes must not be empty")

    voices_dir = REPO_ROOT / "voices" / project
    voices_dir.mkdir(parents=True, exist_ok=True)

    output_path = voices_dir / f"{shot_id}_{character}.wav"
    tmp_path = output_path.with_suffix(".wav.tmp")

    try:
        tmp_path.write_bytes(wav_bytes)
        tmp_path.replace(output_path)
    except Exception as exc:
        logger.error("Failed to save audio to %s: %s", output_path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    relative_path = output_path.relative_to(REPO_ROOT)
    logger.info("Imported audio: %s", relative_path)
    return {"path": str(relative_path)}


def save_storyboard_prompts(project: str, prompts: list[dict]) -> None:
    """Writes prompts to projects/<project>/prompts/storyboards.md.

    Each entry in *prompts* should be a dict with keys:
        shot_id, prompt, negative_prompt

    Format written:
        ## <shot_id>
        **Prompt:** <prompt>
        **Negative:** <negative_prompt>
    """
    prompts_dir = REPO_ROOT / "projects" / project / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    storyboards_md = prompts_dir / "storyboards.md"
    lines: list[str] = []
    for item in prompts:
        shot_id = item.get("shot_id", "")
        prompt = item.get("prompt", "")
        negative_prompt = item.get("negative_prompt", "")
        lines.append(f"## {shot_id}")
        lines.append(f"**Prompt:** {prompt}")
        lines.append(f"**Negative:** {negative_prompt}")
        lines.append("")

    # Write atomically so a crash mid-write cannot leave storyboards.md empty.
    tmp_md = storyboards_md.with_suffix(".md.tmp")
    tmp_md.write_text("\n".join(lines), encoding="utf-8")
    tmp_md.replace(storyboards_md)
    logger.info("Saved storyboard prompts: %s", storyboards_md)


def run_lipsync(project: str) -> Generator[str, None, None]:
    """Generator that runs gen_lipsync.py via subprocess and yields log lines.

    Yields each stdout/stderr line as it arrives.
    Final yield is "DONE" on success or "ERROR: <msg>" on failure.
    """
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "gen_lipsync.py"),
        "--project",
        project,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_ROOT),
        )
        try:
            for line in proc.stdout:
                yield line.rstrip()
            proc.wait()
        finally:
            # Ensure the subprocess is reaped even if the consumer closes the
            # generator early (GeneratorExit) or an exception occurs.
            if proc.returncode is None:
                proc.kill()
                proc.wait()
        if proc.returncode == 0:
            yield "DONE"
        else:
            yield f"ERROR: exit {proc.returncode}"
    except Exception as exc:
        logger.error("run_lipsync failed: %s", exc)
        yield f"ERROR: {exc}"


def build_animatic(project: str) -> Generator[str, None, None]:
    """Generator that runs make_dailies.py via subprocess and yields log lines.

    Yields each stdout/stderr line as it arrives.
    Final yield is "DONE" on success or "ERROR: <msg>" on failure.
    """
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "make_dailies.py"),
        "--project",
        project,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(REPO_ROOT),
        )
        try:
            for line in proc.stdout:
                yield line.rstrip()
            proc.wait()
        finally:
            # Ensure the subprocess is reaped even if the consumer closes the
            # generator early (GeneratorExit) or an exception occurs.
            if proc.returncode is None:
                proc.kill()
                proc.wait()
        if proc.returncode == 0:
            yield "DONE"
        else:
            yield f"ERROR: exit {proc.returncode}"
    except Exception as exc:
        logger.error("build_animatic failed: %s", exc)
        yield f"ERROR: {exc}"


def get_storyboard_images(project: str) -> list[str]:
    """Lists PNG/JPG filenames in outputs/<project>_storyboards/.

    Returns an empty list if the directory does not exist.
    """
    storyboards_dir = REPO_ROOT / "outputs" / f"{project}_storyboards"
    if not storyboards_dir.exists():
        return []
    return sorted(
        p.name
        for p in storyboards_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")
    )


def get_animatic_path(project: str) -> Optional[Path]:
    """Returns the Path to outputs/<project>_animatic.mp4, or None if missing."""
    animatic = REPO_ROOT / "outputs" / f"{project}_animatic.mp4"
    return animatic if animatic.exists() else None


def read_styleguide(project: str) -> str:
    """Reads projects/<project>/styleguide.md. Returns '' if the file is missing."""
    styleguide = REPO_ROOT / "projects" / project / "styleguide.md"
    if not styleguide.exists():
        return ""
    try:
        return styleguide.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to read styleguide.md: %s", exc)
        return ""
