# Kids Animation Studio — Architecture

A local web app that turns a theme into an animated kids' music video — lyrics,
sung song, storyboard, and a flat‑cartoon animated video — **fully on‑device except
the Claude API**. Target machine: Windows 11, RTX 3050 (6 GB VRAM), 16 GB RAM.

This document is the map of the whole system. It's written so a new session can
understand the pipeline and, in particular, **replace the video‑animation stage**
with a different approach without spelunking the whole codebase.

---

## 1. Stack & how to run

| Piece | What | Where |
|---|---|---|
| **Web UI / API** | Flask app, served by `waitress` on **:5000** | `web_ui/server.py` |
| **Frontend** | Single page, vanilla JS (no build step) | `web_ui/templates/index.html`, `web_ui/static/app.js`, `style.css` |
| **ComfyUI** | Local image gen **and** ACE‑Step music gen, on **:8188** | external (Docker or native) |
| **Claude API** | Lyrics, scene planning, scene direction | `web_ui/claude_api.py` |
| **Blender 5.2** | Headless CPU renderer for the animation | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` |
| **ffmpeg** | Encode / concat / mux | resolved by `scripts/blender_render.py` |

**Run:**
```bash
# ComfyUI must be up on :8188 first (curl -sf http://localhost:8188/system_stats)
.venv/Scripts/waitress-serve.exe --host=0.0.0.0 --port=5000 web_ui.server:app
```
The Flask server does **not** hot‑reload — restart it after changing `web_ui/*.py`.
Static files (`app.js`, `index.html`) reload on browser refresh.

**Python environments (Windows):**
- `.venv` — main. **numpy is pinned at 2.4.6** (librosa/numba/scipy) — do not break it.
- `.venv_musicmap` — torch + demucs, used as a subprocess by `scripts/musicmap.py`.

---

## 2. The pipeline (stages, in order)

The UI is a left‑rail stepper; each stage writes artifacts to `projects/<p>/` and
`outputs/`, which the next stage reads. Server routes in `web_ui/server.py`.

```
Project → Lyrics → Music → Storyboard → Animate (video)
```

1. **Project** — `projects/<name>/` scaffold. `POST /api/project/create`.
2. **Lyrics** — `claude_api.generate_lyrics(theme, style, verses, language, template)`.
   UI: a single **Song** picker (`SONG_IDEAS`, `/api/song-ideas`) that bundles
   theme+template+style, or "type your own". Writes `projects/<p>/lyrics.txt` +
   `language.txt` (`hi`/`en`). `POST /api/generate/lyrics`.
3. **Music** — sung song via **ACE‑Step 1.5** through ComfyUI (`web_ui/music_ace.py`).
   ~150 s coherent ceiling; a RAM leak means the worker restarts between songs.
   Output: `outputs/<p>_song.wav`. `POST /api/music/generate`.
   - **`scripts/musicmap.py`** then analyses the song (subprocess, `.venv_musicmap`):
     Demucs (vocal isolation) + librosa (beats/downbeats) + faster‑whisper (word
     timing) → `outputs/<p>_musicmap.json` `{beats, downbeats, words[{start,end,conf,word}]}`.
     Hindi word `conf` is ~0.3 (unreliable text; timing is still usable).
4. **Storyboard** — `claude_api.plan_scenes()` (see §4) → `shotlist.csv`, then
   `web_ui/puppet_video.prepare_assets()` builds the puppet assets + preview images.
   `POST /api/storyboards/generate`.
5. **Animate (video)** — `web_ui/puppet_video.build_music_video()` renders the video
   (see §3). `POST /api/animation/build`. Output: `outputs/<p>_animated.mp4`.

---

## 3. The animation subsystem (the part to replace)

This is the flat‑cartoon **cutout‑puppet** renderer. Data flow:

```
shotlist.csv  ─▶ claude_api.direct_scenes()  ─▶ per‑scene choreography
                                                {character, setting, sings, body_motion,
                                                 camera, intensity, mood}
                       │
   build_character_rig.ensure_rig(name)  ─▶ outputs/char_lib/<name[__culture]>/  (rig.json + PNGs)
   gen_background._gen_plate(setting)     ─▶ outputs/bg_plates/plate_<hash>.png
                       │
   scene_director.author_scene(...)  ─▶ a JSON render SPEC (keyframes)
                       │
   scripts/blender_render.py  ─▶ PNG frames  ─▶ ffmpeg encode  ─▶ per‑scene mp4
                       │
   puppet_video._concat_and_mux()  ─▶ concat all scenes + mux song  ─▶ outputs/<p>_animated.mp4
```

Key modules:

- **`web_ui/puppet_video.py`** — the orchestrator. `build_music_video(project)` and
  `prepare_assets(project)`. Reads `shotlist.csv` (via `_read_scenes`, which now also
  reads the `character` and `setting` columns), stretches scene durations to fill the
  song, resolves each character rig (cached), auto‑places the character
  (`_auto_place`: feet on a ground line, `CHAR_HEIGHT_FRAC` of the canvas), renders
  each scene (resumable via a per‑scene hash), then concats + muxes. Captions exist
  but default **off** (they drift from the vocals).

- **`scripts/build_character_rig.py`** — turns a character NAME into a rig:
  1. generate a few candidates via flat2DAnimerge (`_generate_one`), auto‑pick the
     cleanest single figure (`_score_candidate`), 2. `cut()` = rembg (isnet‑anime)
     cutout → `body.png`, keep only the largest blob (`_largest_component`),
     3. `articulate_rig()` splits `body.png` into `head.png` + `torso.png` at the
     neck and locates the mouth (via eye detection). Prompts are **age/gender/culture
     aware** (`_age`, `_gender`, `_prompt`, `_neg`) so grandmother≠child≠father.
     Culture (`Indian`) is applied only for Hindi projects.

- **`scripts/gen_background.py`** — one flat‑cartoon scenery plate per `setting`
  (no characters). Uses the same checkpoint.

- **`scripts/scene_director.py`** — **the creative→spec bridge** (`author_scene`).
  Turns one scene's choreography + rig + musicmap window into a render spec:
  body motion (perform‑in‑place sway/bob/squash + breathing; only `walk`/`run`
  travel), a **head** layer (nod/tilt, `_head_motion`), a ground **contact shadow**,
  and a background that overscans + slowly pans. `MOUTH_FLAP_ENABLED=False`,
  `FX_ENABLED=False` (overlay clouds/mouth were disabled — read as fake).

- **`scripts/blender_render.py`** — **the render engine.** Consumes the spec and
  emits frames. **This is the contract to build against** (see §5). Parented planes,
  per‑keyframe easing, ortho camera, silent‑black‑frame guard, audio mux.

### Current approach and its ceiling
The character is a single flat AI illustration, cut out and moved as parented
planes. Real limb articulation (arms/legs) was **not** solved — segmenting a flat
illustration into clean limbs is a research problem. What works: whole‑body
perform‑in‑place motion + squash/breathing, an independently nodding/tilting **head**
(the reliably‑segmentable part), a grounding shadow, and a panning background. This
reads as a "performing flat‑cartoon character," not a fully articulated one.

**A new animation approach would replace `scene_director` + `blender_render`** (spec
authoring + render), reusing everything upstream (rigs, plates, musicmap, cast‑
consistent storyboard). Or replace the character source entirely — see §7.

---

## 4. Cast‑consistent storyboard (why the video is coherent)

`claude_api.plan_scenes()` (model **claude‑sonnet‑5 + adaptive thinking**, the one
place a strong model is used — it's the creative backbone) plans the WHOLE video:
- ONE **protagonist** (a descriptive label like `village girl`, never a proper name —
  the rig builder keys on it and needs a role word) carries every scene;
- **supporting characters** (`mother`, `teacher`, `farmer`…) appear only where the
  lyrics feature them;
- each scene's **setting matches its lyric line**.

Persisted as extra `shotlist.csv` columns: `character`, `setting`.
`claude_api.direct_scenes()` (fast Haiku, mechanical) then RESPECTS those pins and
only chooses the animation. `SONG_TEMPLATES` shape lyric structure; `SONG_IDEAS`
is the consolidated UI picker.

---

## 5. Render SPEC and rig formats (the contract)

**`rig.json`** (in `outputs/char_lib/<name>/`):
```json
{ "name": "village girl", "frame": [W, H], "body_anchor": [x, y],
  "parts": {
    "body":  {"image": "torso.png", "anchor": [x,y], "z": 2},
    "head":  {"image": "head.png",  "anchor": [neck_x, neck_y], "z": 3, "parent": "body"},
    "mouth": {"image": "mouth.png", "anchor": [mx, my], "z": 4, "parent": "head"}
  } }
```
Anchors are in image pixels (origin top‑left, y down). A part's `anchor` is the pivot
that its rotation/scale turn about; `parent` nests parts (child transforms are in the
parent's local frame).

**Render spec** (what `blender_render.py` eats):
```json
{ "fps": 24, "duration": 5.2, "resolution": [1152, 768],
  "layers": [
    { "name": "bg",   "image": "<abs path>", "z": 0, "anchor": [576,384],
      "keyframes": [ {"t":0.0,"pos":[576,384],"scale":1.18,"easing":"ease_in_out"}, ... ] },
    { "name": "body", "image": "<abs>", "z": 2, "anchor": [x,y], "keyframes": [...] },
    { "name": "head", "image": "<abs>", "z": 3, "parent": "body", "anchor": [x,y], "keyframes": [...] }
  ],
  "camera": { "keyframes": [ {"t":0.0,"pos":[576,384],"zoom":1.02,"easing":"ease_in_out"}, ... ] } }
```
Keyframe fields: `t` (seconds), `pos` `[x,y]` (absolute canvas px, or an **offset from
the parent anchor** when the layer has a `parent`), `rot` (degrees, clockwise‑positive),
`scale` (scalar or `[sx,sy]`), `easing` (`linear|ease_in|ease_out|ease_in_out`).
Canvas is 1152×768, ortho camera. Run:
`blender --background --python scripts/blender_render.py -- --spec <spec.json> --frames <dir>`.

---

## 6. Constraints & gotchas (don't re‑litigate)

- **`pip install bpy` impossible** (Blender skips Python 3.12); use `blender --background`.
- **numpy pinned 2.4.6** in `.venv`; heavy ML deps (mediapipe, torch) go in a separate venv + subprocess.
- **ACE‑Step ~150 s** coherent‑song ceiling; RAM leak → restart worker between songs.
- **Image model = `flat2DAnimerge_v45Sharp`** (`models/checkpoints/`). The old anime
  checkpoint could NOT do flat cartoon via prompting — the *model* had to change.
  `scripts/cartoonify.py` exists but is unused (posterising flat art only muddies it).
- **flat2D quirks**: adds companion creatures / paint‑splatter / swirls (handled by
  negatives + `_largest_component`) and wild hair; leans chibi + front‑facing.
- Detecting facial landmarks on chibi cartoons is fragile — the mouth is anchored to
  **eye whites** with a fixed‑proportion fallback.
- GPU caps at 70 W; Docker AI toggles must stay as configured. See `.claude` memory.

---

## 7. Where a new video‑animation approach plugs in

Everything up to the storyboard is solid and reusable: cast‑consistent plan,
per‑character rigs (`outputs/char_lib/`), background plates (`outputs/bg_plates/`),
and the `musicmap` (beats/word timing). The **animation** is the swappable part.

Candidate insertion points for a new approach:
- **Replace `scene_director` + `blender_render`** — keep the cutout assets, author a
  richer spec / use a different renderer (e.g. a real 2D skeletal system).
- **Replace the character source** — generate rig‑friendly art (T‑pose / separated
  limbs), or vectorize → Blender Grease Pencil (spiked: `grease_pencil_import_svg`
  works in 5.2 but GP render darkens colours — unresolved), or drive an AI
  image‑to‑video model per shot.
- **Keep the orchestrator** (`puppet_video.build_music_video`) — it already handles
  scene timing, cast resolution, resumable per‑scene render, concat + mux. A new
  renderer only needs to turn (rig assets + plate + timing + musicmap window) into a
  per‑scene mp4.

See the `.claude` project memory (`animation-pipeline`, `animator-machine-constraints`,
`animator-repo-bugs-fixed`) for the running record of decisions and limits.
