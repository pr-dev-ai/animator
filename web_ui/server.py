"""Flask web UI server for the kids animation pipeline."""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB for audio uploads
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # disable static file caching during development


# ---------------------------------------------------------------------------
# CORS — allow all origins for local development
# ---------------------------------------------------------------------------


@app.after_request
def add_cors_headers(response: Response) -> Response:
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    # Prevent browsers from caching JS/CSS so edits are always picked up on reload
    if response.content_type and any(t in response.content_type for t in ("javascript", "css")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/", methods=["OPTIONS"])
@app.route("/api/<path:path>", methods=["OPTIONS"])
def handle_preflight(path: str = "") -> Response:
    return Response(status=204)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _ok(data: object) -> Response:
    return jsonify({"ok": True, "data": data})


def _err(message: str, status: int = 500) -> tuple[Response, int]:
    return jsonify({"ok": False, "error": message}), status


# ---------------------------------------------------------------------------
# Routes — static page
# ---------------------------------------------------------------------------


@app.route("/")
def index() -> str:
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — health
# ---------------------------------------------------------------------------


@app.route("/api/health")
def health() -> Response:
    try:
        from web_ui import pipeline_api  # lazy import

        projects = pipeline_api.list_projects()
        comfyui_ok = pipeline_api.check_comfyui_health()
        return jsonify({"ok": True, "comfyui": comfyui_ok, "projects": projects})
    except Exception as exc:
        logger.exception("Health check failed")
        return jsonify({"ok": False, "comfyui": False, "projects": [], "error": str(exc)}), 503


# ---------------------------------------------------------------------------
# Routes — projects
# ---------------------------------------------------------------------------


@app.route("/api/project/list")
def project_list() -> Response | tuple[Response, int]:
    try:
        from web_ui import pipeline_api  # lazy import

        projects = pipeline_api.list_projects()
        return _ok(projects)
    except Exception as exc:
        logger.exception("project_list failed")
        return _err(str(exc))


@app.route("/api/project/create", methods=["POST"])
def project_create() -> Response | tuple[Response, int]:
    try:
        from web_ui import pipeline_api  # lazy import

        body = request.get_json(force=True) or {}
        name: str = body.get("name", "").strip()
        type_: str = body.get("type", "kids").strip()
        if not name:
            return _err("'name' is required", 400)
        result = pipeline_api.create_project(name, type_)
        return _ok(result)
    except Exception as exc:
        logger.exception("project_create failed")
        return _err(str(exc))


@app.route("/api/project/<string:name>/shots")
def project_shots(name: str) -> Response | tuple[Response, int]:
    try:
        from web_ui import pipeline_api  # lazy import

        shots = pipeline_api.get_project_shots(name)
        return _ok(shots)
    except Exception as exc:
        logger.exception("project_shots failed for %s", name)
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Routes — config check
# ---------------------------------------------------------------------------


@app.route("/api/config/check")
def config_check() -> Response:
    """Fast pre-flight check: verifies API key is set before any Claude call."""
    try:
        from web_ui import claude_api  # lazy import

        claude_api.check_api_key()
        return _ok({"api_key": True})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "api_key": False}), 400
    except Exception as exc:
        logger.exception("config_check failed")
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Routes — Claude generation
# ---------------------------------------------------------------------------


@app.route("/api/generate/lyrics", methods=["POST"])
def generate_lyrics() -> Response | tuple[Response, int]:
    try:
        from web_ui import claude_api  # lazy import

        body = request.get_json(force=True) or {}
        theme: str = body.get("theme", "").strip()
        style: str = body.get("style", "").strip()
        language: str = body.get("language", "English").strip() or "English"
        raw_verses = body.get("verses", 3)

        # Validate before touching Claude
        if not theme:
            return _err("'theme' is required", 400)
        try:
            verses: int = int(raw_verses)
        except (TypeError, ValueError):
            return _err(f"'verses' must be a number, got {raw_verses!r}", 400)
        if not 1 <= verses <= 10:
            return _err("'verses' must be between 1 and 10", 400)

        result = claude_api.generate_lyrics(theme, style, verses, language)
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("generate_lyrics failed")
        return _err(str(exc))


@app.route("/api/generate/chords", methods=["POST"])
def generate_chords() -> Response | tuple[Response, int]:
    try:
        from web_ui import claude_api  # lazy import

        body = request.get_json(force=True) or {}
        lyrics: str = body.get("lyrics", "").strip()

        # Validate before touching Claude
        if not lyrics:
            return _err("'lyrics' is required — generate or paste lyrics first", 400)
        if len(lyrics) < 20:
            return _err("Lyrics are too short — add more content before generating chords", 400)

        project_for_chords: str = body.get("project", "").strip()
        result = claude_api.generate_chords(lyrics)
        # Persist to disk so instrumental generation can read it later
        if project_for_chords:
            chords_file = REPO_ROOT / "projects" / project_for_chords / "chords.json"
            if chords_file.parent.is_dir():
                import json as _json
                chords_file.write_text(_json.dumps(result), encoding="utf-8")
        return _ok(result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("generate_chords failed")
        return _err(str(exc))


@app.route("/api/generate/prompts", methods=["POST"])
def generate_prompts() -> Response | tuple[Response, int]:
    try:
        from web_ui import claude_api  # lazy import
        from web_ui import pipeline_api  # lazy import

        body = request.get_json(force=True) or {}
        project: str = body.get("project", "").strip()
        style: str = body.get("style", "")
        lyrics: str = body.get("lyrics", "").strip()

        # Validate before touching Claude
        if not project:
            return _err("'project' is required — select a project first", 400)
        project_dir = REPO_ROOT / "projects" / project
        if not project_dir.is_dir():
            return _err(f"Project '{project}' not found — create it in the Project tab first", 404)

        shots = pipeline_api.get_project_shots(project)
        if not shots:
            return _err(
                f"Project '{project}' has no shots in shotlist.csv — add shots before generating prompts",
                400,
            )

        style_guide_path = project_dir / "styleguide.md"
        style_guide: str = ""
        if style_guide_path.exists():
            style_guide = style_guide_path.read_text(encoding="utf-8")

        if style and style_guide:
            style_guide = f"Style: {style}\n\n{style_guide}"
        elif style:
            style_guide = style

        # Save lyrics to disk so they persist across server restarts
        if lyrics:
            lyrics_path = project_dir / "lyrics.txt"
            lyrics_path.write_text(lyrics, encoding="utf-8")
        elif (project_dir / "lyrics.txt").exists():
            lyrics = (project_dir / "lyrics.txt").read_text(encoding="utf-8").strip()

        prompts = claude_api.generate_storyboard_prompts(project, shots, style_guide, lyrics)
        pipeline_api.save_storyboard_prompts(project, prompts)
        return _ok(prompts)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("generate_prompts failed")
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Routes — audio
# ---------------------------------------------------------------------------


@app.route("/api/audio/import", methods=["POST"])
def audio_import() -> Response | tuple[Response, int]:
    try:
        from web_ui import pipeline_api  # lazy import

        shot_id: str = request.form.get("shot_id", "").strip()
        character: str = request.form.get("character", "").strip()
        project: str = request.form.get("project", "").strip()

        if not shot_id or not project:
            return _err("'shot_id' and 'project' are required", 400)

        wav_file = request.files.get("file")
        if wav_file is None:
            return _err("'file' is required", 400)

        wav_bytes: bytes = wav_file.read()
        result = pipeline_api.import_audio(project, shot_id, character, wav_bytes)
        return _ok(result)
    except Exception as exc:
        logger.exception("audio_import failed")
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Routes — music generation & mixing
# ---------------------------------------------------------------------------


@app.route("/api/music/generate")
def music_generate() -> Response | tuple[Response, int]:
    project = request.args.get("project", "").strip()
    if not project:
        return _err("'project' query param is required", 400)
    if not (REPO_ROOT / "projects" / project).is_dir():
        return _err(f"Project '{project}' not found", 404)
    try:
        from web_ui import pipeline_api
        return _sse_stream(pipeline_api.generate_instrumental, project)
    except Exception as exc:
        logger.exception("music_generate failed")
        return _err(str(exc))


@app.route("/api/music/vocals", methods=["POST"])
def music_vocals_import() -> Response | tuple[Response, int]:
    try:
        project: str = request.form.get("project", "").strip()
        if not project:
            return _err("'project' is required", 400)
        wav_file = request.files.get("file")
        if wav_file is None:
            return _err("'file' is required", 400)
        out_path = REPO_ROOT / "outputs" / f"{project}_vocals.wav"
        out_path.parent.mkdir(exist_ok=True)
        wav_file.save(str(out_path))
        size_kb = out_path.stat().st_size // 1024
        return _ok({"saved": out_path.name, "size_kb": size_kb})
    except Exception as exc:
        logger.exception("music_vocals_import failed")
        return _err(str(exc))


@app.route("/api/music/mix", methods=["POST"])
def music_mix() -> Response | tuple[Response, int]:
    try:
        from web_ui import pipeline_api
        body = request.get_json(force=True) or {}
        project: str = body.get("project", "").strip()
        ivol: float = float(body.get("instrumental_vol", 0.7))
        vvol: float = float(body.get("vocal_vol", 1.0))
        if not project:
            return _err("'project' is required", 400)
        out = pipeline_api.mix_song(project, ivol, vvol)
        size_kb = (REPO_ROOT / "outputs" / f"{project}_song.wav").stat().st_size // 1024
        return _ok({"file": f"{project}_song.wav", "size_kb": size_kb})
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        logger.exception("music_mix failed")
        return _err(str(exc))


@app.route("/api/music/instrumental/<string:project>")
def serve_instrumental(project: str) -> Response | tuple[Response, int]:
    path = REPO_ROOT / "outputs" / f"{project}_instrumental.wav"
    if not path.exists():
        return _err(f"No instrumental for '{project}'", 404)
    return send_file(path, mimetype="audio/wav")


@app.route("/api/music/vocals/<string:project>")
def serve_vocals(project: str) -> Response | tuple[Response, int]:
    path = REPO_ROOT / "outputs" / f"{project}_vocals.wav"
    if not path.exists():
        return _err(f"No vocal recording for '{project}'", 404)
    return send_file(path, mimetype="audio/wav")


@app.route("/api/music/song/<string:project>")
def serve_song(project: str) -> Response | tuple[Response, int]:
    path = REPO_ROOT / "outputs" / f"{project}_song.wav"
    if not path.exists():
        return _err(f"No final song for '{project}'", 404)
    return send_file(path, mimetype="audio/wav")


# ---------------------------------------------------------------------------
# Routes — SSE streaming (lipsync / animatic build)
# ---------------------------------------------------------------------------


def _sse_stream(generator_fn, project: str) -> Response:
    """Wrap a pipeline generator in a Server-Sent Events response."""

    def generate() -> Generator[str, None, None]:
        # Do NOT yield inside a finally block — Python raises RuntimeError if
        # GeneratorExit is injected (client disconnect) and a yield occurs in
        # the finally clause.  Instead, track whether we finished cleanly and
        # emit the terminal event only when we are not being closed by the GC/WSGI.
        try:
            for line in generator_fn(project):
                yield f"data: {line}\n\n"
            yield "data: DONE\n\n"
        except GeneratorExit:
            # Client disconnected — do not yield; just let the generator close.
            return
        except Exception as exc:
            logger.exception("SSE generator error for project %s", project)
            yield f"data: ERROR: {exc}\n\n"
            yield "data: DONE\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/lyrics/<string:project>")
def load_lyrics(project: str) -> Response | tuple[Response, int]:
    """Return previously saved lyrics for a project (no Claude call)."""
    try:
        lyrics_file = REPO_ROOT / "projects" / project / "lyrics.txt"
        if not lyrics_file.exists():
            return _ok(None)
        return _ok(lyrics_file.read_text(encoding="utf-8").strip())
    except Exception as exc:
        logger.exception("load_lyrics failed for %s", project)
        return _err(str(exc))


@app.route("/api/prompts/<string:project>")
def load_prompts(project: str) -> Response | tuple[Response, int]:
    """Return previously saved storyboard prompts for a project (no Claude call)."""
    try:
        prompts_file = REPO_ROOT / "projects" / project / "prompts" / "storyboards.json"
        if not prompts_file.exists():
            return _ok([])
        import json as _json
        return _ok(_json.loads(prompts_file.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.exception("load_prompts failed for %s", project)
        return _err(str(exc))


@app.route("/api/storyboards/generate")
def storyboards_generate() -> Response | tuple[Response, int]:
    project = request.args.get("project", "").strip()
    if not project:
        return _err("'project' query param is required", 400)
    project_dir = REPO_ROOT / "projects" / project
    if not project_dir.is_dir():
        return _err(f"Project '{project}' not found", 404)
    prompts_file = project_dir / "prompts" / "storyboards.json"
    if not prompts_file.exists():
        return _err("No prompts found — generate prompts first", 400)
    try:
        from web_ui import pipeline_api  # lazy import

        return _sse_stream(pipeline_api.generate_storyboard_images, project)
    except Exception as exc:
        logger.exception("storyboards_generate failed")
        return _err(str(exc))


@app.route("/api/lipsync/run")
def lipsync_run() -> Response | tuple[Response, int]:
    project = request.args.get("project", "").strip()
    if not project:
        return _err("'project' query param is required", 400)
    try:
        from web_ui import pipeline_api  # lazy import

        return _sse_stream(pipeline_api.run_lipsync, project)
    except Exception as exc:
        logger.exception("lipsync_run setup failed")
        return _err(str(exc))


@app.route("/api/animatic/build")
def animatic_build() -> Response | tuple[Response, int]:
    project = request.args.get("project", "").strip()
    if not project:
        return _err("'project' query param is required", 400)
    try:
        from web_ui import pipeline_api  # lazy import

        return _sse_stream(pipeline_api.build_animatic, project)
    except Exception as exc:
        logger.exception("animatic_build setup failed")
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Routes — file serving
# ---------------------------------------------------------------------------


@app.route("/api/animatic/<string:project>")
def serve_animatic(project: str) -> Response | tuple[Response, int]:
    try:
        mp4_path = REPO_ROOT / "outputs" / f"{project}_animatic.mp4"
        if not mp4_path.exists():
            return _err(f"Animatic not found for project '{project}'", 404)
        return send_file(mp4_path, mimetype="video/mp4")
    except Exception as exc:
        logger.exception("serve_animatic failed for %s", project)
        return _err(str(exc))


@app.route("/api/storyboards/<string:project>")
def list_storyboards(project: str) -> Response | tuple[Response, int]:
    try:
        storyboard_dir = REPO_ROOT / "outputs" / f"{project}_storyboards"
        if not storyboard_dir.exists():
            return _ok([])
        files = sorted(
            f.name
            for f in storyboard_dir.iterdir()
            if f.suffix.lower() in {".png", ".jpg", ".jpeg"}
        )
        return _ok(files)
    except Exception as exc:
        logger.exception("list_storyboards failed for %s", project)
        return _err(str(exc))


@app.route("/api/image/<string:project>/<path:filename>")
def serve_image(project: str, filename: str) -> Response | tuple[Response, int]:
    try:
        storyboard_dir = (REPO_ROOT / "outputs" / f"{project}_storyboards").resolve()
        # Resolve the requested path FIRST so symlinks and `..` components are
        # expanded before the containment check — prevents path traversal and
        # sibling-directory bypass via the `startswith` prefix ambiguity.
        image_path = (storyboard_dir / filename).resolve()
        # is_relative_to checks path boundaries correctly (Python 3.9+).
        if not image_path.is_relative_to(storyboard_dir):
            return _err("Forbidden", 403)
        if not image_path.exists():
            return _err(f"Image '{filename}' not found for project '{project}'", 404)
        return send_file(image_path, max_age=3600)
    except Exception as exc:
        logger.exception("serve_image failed for %s/%s", project, filename)
        return _err(str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ in ("__main__", "web_ui.server"):
    app.run(host="0.0.0.0", port=5000, debug=False)
