"""
pipeline_api.py — thin wrappers around the existing animation pipeline scripts
so the Flask server can call them without reimplementing their logic.
"""

import csv
import glob
import json
import logging
import random
import re
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Generator, Optional

COMFYUI_URL = "http://localhost:8188"

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


# Art checkpoints preferred for children's-book / nursery-rhyme illustration,
# best first. Matched case-insensitively as a substring of the filename.
# v1-5-pruned-emaonly is a research BASE model, not an art model: it is the
# last-resort fallback only, hence its absence from this list.
_CHECKPOINT_PREFERENCE = (
    "childrensstories_v1toonanime",
    "childrensstories",
    "dreamshaper",
)

# Stock SD1.5 VAE renders muddy; this finetune restores contrast and detail.
_VAE_PREFERENCE = ("vae-ft-mse-840000-ema-pruned",)


def _rank(name: str, preference: tuple[str, ...]) -> int:
    """Index of the first matching preference entry, or len(preference) if none."""
    lowered = name.lower()
    for i, wanted in enumerate(preference):
        if wanted in lowered:
            return i
    return len(preference)


def _find_checkpoint() -> str:
    """Return the best available checkpoint name known to ComfyUI.

    Picks by explicit art-model preference (_CHECKPOINT_PREFERENCE) rather than
    alphabetical order, so the SD1.5 base model is only ever a last resort.
    Prefers .safetensors over .ckpt but skips zero-byte (corrupt/incomplete)
    files. Falls back to scanning the local models/checkpoints directory if
    ComfyUI is not yet responding.
    Raises ValueError if no usable model is found.
    """
    checkpoints_dir = REPO_ROOT / "models" / "checkpoints"

    def _is_valid(name: str) -> bool:
        """True if the file exists on the host volume and is non-empty."""
        p = checkpoints_dir / name
        try:
            return p.stat().st_size > 0
        except OSError:
            return True  # can't check from host — assume OK

    def _sort_key(name: str) -> tuple:
        # 1. named art-model preference, 2. .safetensors over .ckpt, 3. stable by name
        return (_rank(name, _CHECKPOINT_PREFERENCE), not name.endswith(".safetensors"), name)

    try:
        with urllib.request.urlopen(
            f"{COMFYUI_URL}/object_info/CheckpointLoaderSimple", timeout=4
        ) as resp:
            data = json.loads(resp.read())
            models: list[str] = data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
            valid = [m for m in models if _is_valid(m)]
            if valid:
                return sorted(valid, key=_sort_key)[0]
    except Exception:
        pass

    if checkpoints_dir.exists():
        local = [
            p.name
            for p in checkpoints_dir.iterdir()
            if p.suffix in (".safetensors", ".ckpt") and p.stat().st_size > 0
        ]
        if local:
            return sorted(local, key=_sort_key)[0]

    raise ValueError(
        "No checkpoint model found in models/checkpoints/. "
        "Download a Stable Diffusion model (e.g. v1-5-pruned-emaonly.safetensors) first."
    )


def _find_vae() -> str | None:
    """Return the preferred standalone VAE name, or None to use the checkpoint's own.

    Returning None is a normal outcome, not an error: every checkpoint ships a
    baked-in VAE, so the workflow stays valid when no better VAE is installed.
    """
    try:
        with urllib.request.urlopen(f"{COMFYUI_URL}/object_info/VAELoader", timeout=4) as resp:
            data = json.loads(resp.read())
            vaes: list[str] = data["VAELoader"]["input"]["required"]["vae_name"][0]
    except Exception:
        return None

    preferred = [v for v in vaes if _rank(v, _VAE_PREFERENCE) < len(_VAE_PREFERENCE)]
    if not preferred:
        return None
    # Sort by rank, not ComfyUI's list order, so the first _VAE_PREFERENCE entry
    # really does win when several preferred VAEs are installed.
    return min(preferred, key=lambda v: _rank(v, _VAE_PREFERENCE))


# Base render. 768x512 keeps one axis at SD1.5's native 512 — pushing both axes
# past 512 makes SD1.5 duplicate subjects (two heads, two buses).
_BASE_WIDTH, _BASE_HEIGHT = 768, 512
# Hires fix: re-sample an upscaled latent at partial denoise. This is the single
# biggest quality gain available here, and 1.5x fits in 6GB VRAM.
_HIRES_WIDTH, _HIRES_HEIGHT = 1152, 768
_HIRES_DENOISE = 0.45  # high enough to add detail, low enough to keep composition
# Sampler tuning. 15 steps (the previous value) is genuinely undercooked; 28 is
# where SD1.5 stops gaining. The hires pass only refines, so it needs far fewer.
_BASE_STEPS = 28
_HIRES_STEPS = 12
_CFG = 7.0


def _comfyui_workflow(
    prompt: str,
    negative: str,
    shot_id: str,
    checkpoint: str,
    vae: str | None = None,
    seed: int | None = None,
) -> dict:
    """SD 1.5 text-to-image workflow (clip skip 2 + hires fix) for the ComfyUI API.

    When *vae* is None the checkpoint's built-in VAE is used.
    When *seed* is None a random one is drawn. The same seed drives both the base
    and the hires pass (as in a standard hires fix), so logging it is enough to
    reproduce the image.
    """
    if seed is None:
        seed = random.randint(0, 2 ** 32 - 1)

    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": checkpoint}},
        # Clip skip 2: standard for cartoon/anime finetunes, which are trained
        # with it. Using the final layer (the default) is a large part of why
        # output looked flat.
        "8": {"class_type": "CLIPSetLastLayer",
              "inputs": {"clip": ["1", 1], "stop_at_clip_layer": -2}},
        "2": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["8", 0], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["8", 0], "text": negative}},
        "4": {"class_type": "EmptyLatentImage",
              "inputs": {"width": _BASE_WIDTH, "height": _BASE_HEIGHT, "batch_size": 1}},
        "5": {"class_type": "KSampler",
              "inputs": {
                  "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                  "latent_image": ["4", 0],
                  "seed": seed,
                  "steps": _BASE_STEPS, "cfg": _CFG,
                  "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0,
              }},
        "10": {"class_type": "LatentUpscale",
               "inputs": {
                   "samples": ["5", 0], "upscale_method": "nearest-exact",
                   "width": _HIRES_WIDTH, "height": _HIRES_HEIGHT, "crop": "disabled",
               }},
        "11": {"class_type": "KSampler",
               "inputs": {
                   "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                   "latent_image": ["10", 0],
                   "seed": seed,
                   "steps": _HIRES_STEPS, "cfg": _CFG,
                   "sampler_name": "dpmpp_2m", "scheduler": "karras",
                   "denoise": _HIRES_DENOISE,
               }},
        "6": {"class_type": "VAEDecode",
              "inputs": {"samples": ["11", 0], "vae": ["1", 2]}},
        "7": {"class_type": "SaveImage",
              "inputs": {"images": ["6", 0], "filename_prefix": shot_id}},
    }

    if vae is not None:
        workflow["9"] = {"class_type": "VAELoader", "inputs": {"vae_name": vae}}
        workflow["6"]["inputs"]["vae"] = ["9", 0]

    return workflow


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

    # JSON sidecar — used by generate_storyboard_images for machine-readable access.
    storyboards_json = prompts_dir / "storyboards.json"
    tmp_json = storyboards_json.with_suffix(".json.tmp")
    tmp_json.write_text(json.dumps(prompts, indent=2), encoding="utf-8")
    tmp_json.replace(storyboards_json)
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


def generate_storyboard_images(project: str) -> Generator[str, None, None]:
    """Generator that submits each storyboard prompt to ComfyUI and yields progress.

    Reads projects/<project>/prompts/storyboards.json (written by save_storyboard_prompts).
    Saves output images to outputs/<project>_storyboards/<shot_id>.png.
    Yields one log line per event; final line is "DONE" or starts with "ERROR:".
    """
    prompts_file = REPO_ROOT / "projects" / project / "prompts" / "storyboards.json"
    if not prompts_file.exists():
        yield "ERROR: No prompts found — generate prompts in the Storyboard tab first"
        return

    try:
        prompts = json.loads(prompts_file.read_text(encoding="utf-8"))
    except Exception as exc:
        yield f"ERROR: Could not read prompts: {exc}"
        return

    if not prompts:
        yield "ERROR: Prompt file is empty"
        return

    try:
        checkpoint = _find_checkpoint()
    except ValueError as exc:
        yield f"ERROR: {exc}"
        return
    yield f"Model: {checkpoint}"

    vae = _find_vae()
    yield f"VAE: {vae}" if vae else "VAE: checkpoint built-in"

    out_dir = REPO_ROOT / "outputs" / f"{project}_storyboards"
    out_dir.mkdir(parents=True, exist_ok=True)

    client_id = str(uuid.uuid4())
    total = len(prompts)

    for i, shot in enumerate(prompts):
        shot_id = shot.get("shot_id", f"shot_{i:03d}")
        prompt_text = shot.get("prompt", "")
        negative = shot.get("negative_prompt", "realistic, photo, dark, scary, watermark")

        yield f"[{i + 1}/{total}] Submitting {shot_id}..."

        # Build and submit workflow. The seed is drawn here rather than inside
        # the workflow so it can be logged — otherwise a good render is
        # impossible to reproduce.
        seed = random.randint(0, 2 ** 32 - 1)
        workflow = _comfyui_workflow(prompt_text, negative, shot_id, checkpoint, vae, seed)
        try:
            payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
            req = urllib.request.Request(
                f"{COMFYUI_URL}/prompt",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
            prompt_id: str = result.get("prompt_id", "")
            if not prompt_id:
                yield f"  ERROR: ComfyUI rejected prompt — {result}"
                continue
        except Exception as exc:
            yield f"  ERROR: Could not reach ComfyUI: {exc}"
            continue

        yield f"  Queued (id {prompt_id[:8]}…) — waiting for GPU..."

        # Poll history until the image is ready (max 5 minutes, 5s interval)
        image_info: dict | None = None
        for elapsed in range(0, 300, 5):
            time.sleep(5)
            try:
                with urllib.request.urlopen(
                    f"{COMFYUI_URL}/history/{prompt_id}", timeout=5
                ) as resp:
                    history = json.loads(resp.read())
                if prompt_id in history:
                    status_str = history[prompt_id].get("status", {}).get("status_str", "")
                    if status_str == "error":
                        yield f"  ERROR: ComfyUI reported an error for {shot_id}"
                        break
                    for node_out in history[prompt_id].get("outputs", {}).values():
                        imgs = node_out.get("images", [])
                        if imgs:
                            image_info = imgs[0]
                            break
                    if image_info:
                        break
            except Exception:
                pass
            if elapsed > 0 and elapsed % 30 == 0:
                yield f"  Still generating {shot_id}… ({elapsed}s elapsed)"

        if not image_info:
            yield f"  ERROR: Timed out waiting for {shot_id}"
            continue

        # Download the image and save as <shot_id>.png
        filename = image_info["filename"]
        subfolder = image_info.get("subfolder", "")
        img_type = image_info.get("type", "output")
        qs = f"filename={urllib.parse.quote(filename)}&type={img_type}"
        if subfolder:
            qs += f"&subfolder={urllib.parse.quote(subfolder)}"
        try:
            with urllib.request.urlopen(f"{COMFYUI_URL}/view?{qs}", timeout=30) as resp:
                img_bytes = resp.read()
            output_path = out_dir / f"{shot_id}.png"
            output_path.write_bytes(img_bytes)
            yield f"  Saved {shot_id}.png ({len(img_bytes) // 1024} KB, seed {seed})"
        except Exception as exc:
            yield f"  ERROR: Could not download {shot_id}: {exc}"

    yield "DONE"


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


# ---------------------------------------------------------------------------
# Music generation
# ---------------------------------------------------------------------------

def generate_instrumental(project: str) -> Generator[str, None, None]:
    """Generate a synthesized instrumental WAV for a project.

    Reads saved chords.json and lyrics.txt, asks Claude for a bar arrangement,
    then synthesizes audio with music_gen.py.  Saves to outputs/<project>_instrumental.wav.
    """
    from web_ui import claude_api
    from web_ui.music_gen import arrangement_to_wav

    project_dir = REPO_ROOT / "projects" / project
    if not project_dir.is_dir():
        yield f"ERROR: Project '{project}' not found"
        return

    # Load chords saved by the Lyrics tab
    chords_file = project_dir / "chords.json"
    chords: list[str] = ["C", "G", "Am", "F"]
    tempo_bpm: int = 120
    style: str = "kids pop"
    if chords_file.exists():
        try:
            cd = json.loads(chords_file.read_text(encoding="utf-8"))
            chords = cd.get("chords") or chords
            tempo_bpm = int(cd.get("tempo_bpm") or tempo_bpm)
            style = cd.get("style") or style
        except Exception:
            pass

    # Load lyrics
    lyrics = ""
    lyrics_file = project_dir / "lyrics.txt"
    if lyrics_file.exists():
        lyrics = lyrics_file.read_text(encoding="utf-8").strip()

    yield f"Chords: {', '.join(chords)}  |  Tempo: {tempo_bpm} BPM"
    yield "Asking Claude for bar-by-bar arrangement..."

    try:
        arrangement = claude_api.generate_music_arrangement(chords, lyrics, tempo_bpm, style)
    except Exception as exc:
        yield f"ERROR: {exc}"
        return

    bars = arrangement.get("bars", [])
    resolved_tempo = arrangement.get("tempo_bpm", tempo_bpm)
    yield f"Arrangement ready: {len(bars)} bars at {resolved_tempo} BPM"

    # Save arrangement JSON
    arr_path = project_dir / "arrangement.json"
    try:
        arr_path.write_text(json.dumps(arrangement, indent=2), encoding="utf-8")
    except Exception:
        pass

    yield "Synthesizing audio..."
    try:
        wav_bytes = arrangement_to_wav(bars, resolved_tempo)
    except Exception as exc:
        yield f"ERROR synthesising audio: {exc}"
        return

    out_dir = REPO_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{project}_instrumental.wav"
    out_path.write_bytes(wav_bytes)
    size_kb = len(wav_bytes) // 1024
    yield f"Saved instrumental: {out_path.name} ({size_kb} KB)"
    yield "DONE"


def mix_song(project: str, instrumental_vol: float = 0.7, vocal_vol: float = 1.0) -> str:
    """Mix instrumental + vocal WAV into a final song. Returns output path."""
    from web_ui.music_gen import mix_tracks

    out_dir = REPO_ROOT / "outputs"
    instr_path = out_dir / f"{project}_instrumental.wav"
    vocal_path = out_dir / f"{project}_vocals.wav"

    if not instr_path.exists():
        raise ValueError("No instrumental found — generate it first")
    if not vocal_path.exists():
        raise ValueError("No vocal recording found — upload your Audacity WAV first")

    mixed = mix_tracks(instr_path, vocal_path, instrumental_vol, vocal_vol)
    out_path = out_dir / f"{project}_song.wav"
    out_path.write_bytes(mixed)
    return str(out_path)
