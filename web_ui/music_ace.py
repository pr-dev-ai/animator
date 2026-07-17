"""
music_ace.py — real music generation with sung vocals via ACE-Step 1.5 (ComfyUI).

Unlike music_gen.py (a pure-numpy sine synth that plays block chords), this module
drives the ACE-Step 1.5 turbo model through ComfyUI's native nodes to produce a full
mix: real instrumentation AND AI vocals singing the project's own lyrics.

Model files (split, not the 10 GB AIO checkpoint) live under models/:
    diffusion_models/acestep_v1.5_turbo.safetensors   4.79 GB
    text_encoders/qwen_0.6b_ace15.safetensors         1.19 GB  (conditioning + lyrics)
    text_encoders/qwen_1.7b_ace15.safetensors         3.71 GB  (audio-code LLM)
    vae/ace_1.5_vae.safetensors                       0.34 GB

Both text encoders are required: ComfyUI's CLIPType.ACE branch only accepts a
DualCLIPLoader pair, so the split set totals the same ~10 GB as the AIO checkpoint.
The win is not disk, it is that the parts load and unload independently and the DiT
can be quantised to fp8 on its own.

restart_worker_first=True (the default) restarts the ComfyUI container before each
song.  This is load-bearing, not defensive: measured on this 16 GB box, three
consecutive generations WITHOUT a restart drove the WSL VM into a global OOM that
killed the ComfyUI process (anon-rss 7.35 GB) on the 3rd run — matching the unfixed
ACE-Step-1.5#142.  With a restart before each song, container RAM peaks flat at
~6.5-6.8 GB of 8.28 GB and 3/3 runs succeed.

Vocal quality varies noticeably by seed; the caller may want to offer a re-roll.

music_gen.py is retained as a zero-dependency fallback.
"""

import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Generator, Optional

from web_ui.pipeline_api import COMFYUI_URL

REPO_ROOT = Path(__file__).resolve().parent.parent

# Model filenames as registered by ComfyUI (see models/ tree above).
DIT_MODEL = "acestep_v1.5_turbo.safetensors"
TE_BASE = "qwen_0.6b_ace15.safetensors"
TE_LM = "qwen_1.7b_ace15.safetensors"
VAE_MODEL = "ace_1.5_vae.safetensors"

# fp8 weights for the DiT.  On Ampere (RTX 3050) fp8 is storage-only — weights are
# stored fp8 and upcast to bf16 for compute — but it still halves the DiT's memory
# footprint (~4.8 GB -> ~2.4 GB), which is what makes this fit in 6 GB VRAM.
DEFAULT_WEIGHT_DTYPE = "fp8_e4m3fn"

# Turbo is distilled: 8 steps at cfg 1.0.  More steps does not help.
TURBO_STEPS = 8
TURBO_CFG = 1.0
SHIFT = 3.0

DEFAULT_DURATION = 120.0
DEFAULT_TAGS = (
    "children's music, kids song, nursery rhyme, cheerful acoustic pop, "
    "bright acoustic guitar, gentle piano, light drums, upbeat, "
    "clear female vocals singing, playful, major key, simple melody"
)

CONTAINER_NAME = "comfyui"
_RESTART_TIMEOUT = 240
_GENERATION_TIMEOUT = 3600  # up to an hour per song is acceptable on this hardware

# TextEncodeAceStepAudio1.5's keyscale is a strict COMBO: a value outside this set is
# rejected by /prompt with HTTP 400 "Value not in list", failing the whole song.
_KEY_ROOTS = ["C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#",
              "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B"]
VALID_KEYSCALES = frozenset(
    f"{root} {quality}" for quality in ("major", "minor") for root in _KEY_ROOTS
)
DEFAULT_KEYSCALE = "C major"

# Chord -> (root, quality).  Claude writes chords like "Am7", "Cmaj7", "G7", "Dsus4";
# only the root and the major/minor quality are meaningful for a key signature.
_CHORD_RE = re.compile(r"^([A-G][#b]?)(.*)$")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ComfyUI worker lifecycle (RAM-leak mitigation)
# ---------------------------------------------------------------------------

def _comfyui_alive(timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(f"{COMFYUI_URL}/system_stats", timeout=timeout):
            return True
    except Exception:
        return False


def _wait_for_comfyui(timeout: int = _RESTART_TIMEOUT) -> bool:
    """Polls until ComfyUI answers, or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _comfyui_alive():
            return True
        time.sleep(3)
    return False


def _read_queue() -> Optional[dict]:
    """Returns ComfyUI's queue, or None if it could not be read."""
    try:
        with urllib.request.urlopen(f"{COMFYUI_URL}/queue", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _queue_busy() -> bool:
    """True if ComfyUI has a job running or pending (e.g. a storyboard image batch)."""
    queue = _read_queue()
    if queue is None:
        return False
    return bool(queue.get("queue_running") or queue.get("queue_pending"))


def _prompt_queued(prompt_id: str) -> bool:
    """True if prompt_id is still running or pending in ComfyUI's queue.

    Queue entries are [number, prompt_id, ...]; a restarted worker has an empty queue,
    which is how we notice our job died with it.
    """
    queue = _read_queue()
    if queue is None:
        return True  # can't tell — assume alive rather than abort on a blip
    for key in ("queue_running", "queue_pending"):
        for entry in queue.get(key, []):
            if len(entry) > 1 and entry[1] == prompt_id:
                return True
    return False


def restart_worker() -> Generator[str, None, None]:
    """Restarts the ComfyUI container to reclaim leaked RAM.

    ComfyUI is shared with storyboard image generation, so a restart would abort any
    in-flight job.  We skip the restart while the queue is busy and warn instead:
    a slightly leakier heap beats killing someone else's render mid-run.

    Yields log lines.  Tolerates a missing docker CLI: if the restart cannot be
    performed we warn and carry on rather than failing the whole song.
    """
    if _queue_busy():
        yield "  WARNING: ComfyUI is busy with another job — skipping restart (RAM may accumulate)"
        return

    yield "Restarting ComfyUI worker (ACE-Step RAM-leak mitigation)..."
    try:
        proc = subprocess.run(
            ["docker", "restart", CONTAINER_NAME],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            yield f"  WARNING: docker restart failed: {proc.stderr.strip()[:200]}"
            return
    except FileNotFoundError:
        yield "  WARNING: docker CLI not found — skipping restart (RAM may accumulate)"
        return
    except subprocess.TimeoutExpired:
        yield "  WARNING: docker restart timed out — continuing"
        return

    if _wait_for_comfyui():
        yield "  Worker back up with a clean heap"
    else:
        yield "  WARNING: worker did not come back in time"


# ---------------------------------------------------------------------------
# Project inputs
# ---------------------------------------------------------------------------

def _format_lyrics(raw: str) -> str:
    """Adds ACE-Step section tags to blank-line-separated stanzas.

    ACE-Step conditions on structure markers like [Verse 1] / [Chorus]; passing raw
    prose gives noticeably flabbier structure.  A stanza that repeats an earlier one
    is tagged [Chorus], everything else becomes a numbered verse.
    """
    stanzas = [s.strip() for s in raw.strip().split("\n\n") if s.strip()]
    if not stanzas:
        return raw.strip()

    def sig(stanza: str) -> str:
        return stanza.split("\n")[0].strip().lower().rstrip("!.,?")

    seen: set[str] = set()
    out: list[str] = []
    verse_no = 0
    for stanza in stanzas:
        key = sig(stanza)
        if key in seen:
            out.append(f"[Chorus]\n{stanza}")
        else:
            verse_no += 1
            seen.add(key)
            out.append(f"[Verse {verse_no}]\n{stanza}")
    return "\n\n".join(out)


def chord_to_keyscale(chord: str) -> str:
    """Maps a chord symbol to a keyscale the ACE-Step node will accept.

    Only the root and the minor/major quality matter.  Extensions (7, maj7, sus4, add9)
    are discarded rather than pasted into the keyscale, which would produce a value
    outside the node's fixed option list and get the workflow rejected with HTTP 400.
    Anything unparseable falls back to the default rather than failing the song.
    """
    match = _CHORD_RE.match(chord.strip())
    if not match:
        return DEFAULT_KEYSCALE
    root, rest = match.group(1), match.group(2)
    # Minor iff an 'm' immediately follows the root and is not part of "maj".
    quality = "minor" if re.match(r"^m(?!aj)", rest) else "major"
    keyscale = f"{root} {quality}"
    return keyscale if keyscale in VALID_KEYSCALES else DEFAULT_KEYSCALE


def _read_style(project: str) -> tuple[str, int, str]:
    """Returns (tags, bpm, keyscale) from chords.json / arrangement.json, with defaults."""
    project_dir = REPO_ROOT / "projects" / project
    bpm = 120
    tags = DEFAULT_TAGS
    keyscale = DEFAULT_KEYSCALE

    chords_file = project_dir / "chords.json"
    if chords_file.exists():
        try:
            cd = json.loads(chords_file.read_text(encoding="utf-8"))
            bpm = int(cd.get("tempo_bpm") or bpm)
            style = (cd.get("style") or "").strip()
            if style:
                tags = f"{style}, {DEFAULT_TAGS}"
        except Exception as exc:
            logger.debug("chords.json unreadable: %s", exc)

    arr_file = project_dir / "arrangement.json"
    if arr_file.exists():
        try:
            ad = json.loads(arr_file.read_text(encoding="utf-8"))
            bpm = int(ad.get("tempo_bpm") or bpm)
            first = (ad.get("bars") or [{}])[0].get("chord", "")
            if first:
                keyscale = chord_to_keyscale(first)
        except Exception as exc:
            logger.debug("arrangement.json unreadable: %s", exc)

    return tags, bpm, keyscale


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def build_workflow(
    tags: str,
    lyrics: str,
    duration: float,
    bpm: int = 120,
    keyscale: str = "C major",
    seed: int = 31,
    weight_dtype: str = DEFAULT_WEIGHT_DTYPE,
    language: str = "en",
) -> dict:
    """Builds the ACE-Step 1.5 split-file API workflow.

    Mirrors ComfyUI's official audio_ace_step_1_5_split template: the DiT is wrapped in
    ModelSamplingAuraFlow, the negative branch is a ConditioningZeroOut of the positive,
    and the latent length must agree with the encoder's duration.
    """
    return {
        "104": {"class_type": "UNETLoader", "inputs": {
            "unet_name": DIT_MODEL, "weight_dtype": weight_dtype}},
        "105": {"class_type": "DualCLIPLoader", "inputs": {
            "clip_name1": TE_BASE, "clip_name2": TE_LM,
            "type": "ace", "device": "default"}},
        "106": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_MODEL}},
        "78": {"class_type": "ModelSamplingAuraFlow", "inputs": {
            "shift": SHIFT, "model": ["104", 0]}},
        "94": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
            "clip": ["105", 0],
            "tags": tags,
            "lyrics": lyrics,
            "seed": seed,
            "bpm": bpm,
            "duration": duration,
            "timesignature": "4",
            "language": language,
            "keyscale": keyscale,
            "generate_audio_codes": True,
            "cfg_scale": 2.0,
            "temperature": 0.85,
            "top_p": 0.9,
            "top_k": 0,
            "min_p": 0.0,
        }},
        "47": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["94", 0]}},
        "98": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {
            "seconds": duration, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": TURBO_STEPS, "cfg": TURBO_CFG,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["78", 0], "positive": ["94", 0],
            "negative": ["47", 0], "latent_image": ["98", 0]}},
        "18": {"class_type": "VAEDecodeAudio", "inputs": {
            "samples": ["3", 0], "vae": ["106", 0]}},
        "107": {"class_type": "SaveAudio", "inputs": {
            "audio": ["18", 0], "filename_prefix": "audio/ace"}},
    }


def _submit(workflow: dict, client_id: str) -> str:
    payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"ComfyUI rejected the workflow: {detail}") from exc
    prompt_id = result.get("prompt_id", "")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI returned no prompt_id: {result}")
    return prompt_id


def _download(info: dict) -> bytes:
    qs = f"filename={urllib.parse.quote(info['filename'])}&type={info.get('type', 'output')}"
    if info.get("subfolder"):
        qs += f"&subfolder={urllib.parse.quote(info['subfolder'])}"
    with urllib.request.urlopen(f"{COMFYUI_URL}/view?{qs}", timeout=120) as resp:
        return resp.read()


def _flac_to_wav(data: bytes, dest: Path) -> float:
    """Decodes ComfyUI's FLAC to a WAV at dest (atomic).  Returns duration in seconds.

    ComfyUI has no WAV save node (flac/mp3/opus only); FLAC is lossless so this
    conversion is faithful.
    """
    import io
    import soundfile as sf

    audio, sr = sf.read(io.BytesIO(data))
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        # format must be explicit: soundfile infers it from the extension, and the
        # atomic tmp name ends in ".tmp".
        sf.write(str(tmp), audio, sr, subtype="PCM_16", format="WAV")
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return len(audio) / float(sr)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_song(
    project: str,
    duration: float = DEFAULT_DURATION,
    seed: int = 31,
    restart_worker_first: bool = True,
    weight_dtype: str = DEFAULT_WEIGHT_DTYPE,
) -> Generator[str, None, None]:
    """Generates a full song with sung vocals for a project via ACE-Step 1.5.

    Reads projects/<project>/lyrics.txt plus chords/arrangement for tempo and key,
    and writes outputs/<project>_song.wav.

    Yields plain log lines (server.py wraps these in SSE); the final line is "DONE"
    or starts with "ERROR:".
    """
    project_dir = REPO_ROOT / "projects" / project
    if not project_dir.is_dir():
        yield f"ERROR: Project '{project}' not found"
        return

    lyrics_file = project_dir / "lyrics.txt"
    if not lyrics_file.exists():
        yield "ERROR: No lyrics.txt — write lyrics in the Lyrics tab first"
        return
    raw_lyrics = lyrics_file.read_text(encoding="utf-8").strip()
    if not raw_lyrics:
        yield "ERROR: lyrics.txt is empty"
        return

    lyrics = _format_lyrics(raw_lyrics)
    tags, bpm, keyscale = _read_style(project)

    yield f"Model: ACE-Step 1.5 turbo ({weight_dtype}) — real instruments + sung vocals"
    yield f"Tempo {bpm} BPM | Key {keyscale} | Target {duration:.0f}s"
    sections = sum(1 for ln in lyrics.splitlines() if ln.startswith("["))
    yield f"Lyrics: {len(lyrics.splitlines())} lines across {sections} sections"

    if restart_worker_first:
        yield from restart_worker()

    if not _comfyui_alive():
        yield "ERROR: ComfyUI is not reachable at " + COMFYUI_URL
        return

    workflow = build_workflow(
        tags=tags, lyrics=lyrics, duration=duration,
        bpm=bpm, keyscale=keyscale, seed=seed, weight_dtype=weight_dtype,
    )

    try:
        prompt_id = _submit(workflow, str(uuid.uuid4()))
    except Exception as exc:
        yield f"ERROR: {exc}"
        return

    yield f"Queued (id {prompt_id[:8]}…) — generating, this can take several minutes..."

    started = time.time()
    info: Optional[dict] = None
    lost_polls = 0
    while time.time() - started < _GENERATION_TIMEOUT:
        time.sleep(5)
        try:
            with urllib.request.urlopen(
                f"{COMFYUI_URL}/history/{prompt_id}", timeout=10
            ) as resp:
                history = json.loads(resp.read())
        except Exception:
            continue

        # ACE-Step can exhaust RAM and get the worker OOM-killed mid-song (see
        # ACE-Step-1.5#142).  A restarted worker comes back with an empty queue and no
        # history, so the job is simply gone — fail fast instead of polling for an hour.
        if prompt_id not in history and not _prompt_queued(prompt_id):
            lost_polls += 1
            if lost_polls >= 3:
                yield ("ERROR: the ComfyUI worker died mid-generation (likely out of "
                       "memory) — the job is gone. Try a shorter duration.")
                return
        else:
            lost_polls = 0

        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                yield f"ERROR: ComfyUI failed — {json.dumps(status.get('messages', []))[:400]}"
                return
            for node_out in entry.get("outputs", {}).values():
                for audio in node_out.get("audio", []):
                    info = audio
            if info:
                break
            # Terminal but produced no audio (interrupted/cancelled).  Without this
            # the loop would spin until _GENERATION_TIMEOUT on an already-dead job.
            if status.get("completed"):
                yield "ERROR: ComfyUI finished without producing audio (job interrupted?)"
                return

        elapsed = int(time.time() - started)
        if elapsed and elapsed % 30 < 5:
            yield f"  Still generating… ({elapsed}s elapsed)"

    if not info:
        yield f"ERROR: Timed out after {_GENERATION_TIMEOUT}s waiting for audio"
        return

    out_dir = REPO_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{project}_song.wav"
    try:
        data = _download(info)
        actual = _flac_to_wav(data, out_path)
    except Exception as exc:
        yield f"ERROR: Could not save audio: {exc}"
        return

    took = time.time() - started
    yield f"Saved {out_path.name} ({out_path.stat().st_size // 1024} KB, {actual:.1f}s audio) in {took:.0f}s"
    yield "DONE"
