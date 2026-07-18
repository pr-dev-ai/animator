#!/usr/bin/env python3
"""scene_director.py — turn a storyboard scene into a correct Blender animation script.

Two layers, matching the "Claude directs, code executes" pattern used across this
app (never Claude writing raw bpy):

  1. Claude DIRECTS  — web_ui.claude_api.direct_scene() reads a scene's description
     and returns a small choreography JSON: {character, sings, body_motion, camera,
     intensity, mood}.  Creative decision, structured + validated, not code.

  2. This module EXECUTES — author_scene() takes that choreography + a character rig
     manifest + the musicmap window + a background, and emits a precise
     scripts/blender_render.py spec: body motion keyed to BEATS, lip-sync mouth-opens
     keyed to sung WORDS (confidence-gated so Hindi falls back to beat-only), optional
     wing/flap, and a camera move per the direction.  Deterministic => "correct".

This generalises the duck de-risk (scripts/duck_rig.py) to any rigged character.
"""
import json
from pathlib import Path

CANVAS = (1152, 768)
FPS = 24
WORD_CONF_MIN = 0.65   # English ~0.79 fires lip-sync; Hindi ~0.56 -> beat-only

import math
_CAMERA = {   # (zoom0, zoom1, pan_dx over shot as fraction of width)
    "hold":     (1.04, 1.04, 0.0),
    "push_in":  (1.02, 1.12, 0.0),
    "pull_out": (1.12, 1.02, 0.0),
    "pan_left": (1.06, 1.06, -0.10),
    "pan_right":(1.06, 1.06, 0.10),
}


def _win(times, t0, t1):
    return [t for t in times if t0 <= t < t1]


# --- living-world overlay FX (drifting clouds / floating sparkles / twinkling stars) ---
_NIGHT_WORDS = ("night", "moon", "star", "dream", "sleep", "lullaby", "dark", "twinkl")
_OUTDOOR_WORDS = ("sky", "garden", "park", "meadow", "hill", "field", "outside", "outdoor",
                  "forest", "cloud", "mountain", "sea", "beach", "street", "road", "village",
                  "farm", "jungle", "rainbow")
# deterministic scatter (relative x,y in [0,1]) — no RNG, keeps the render cache stable
_SPARKLE_POS = [(0.16, 0.28), (0.74, 0.22), (0.42, 0.52), (0.86, 0.58), (0.26, 0.70), (0.60, 0.40)]
_STAR_POS = [(0.12, 0.16), (0.30, 0.11), (0.50, 0.19), (0.68, 0.13), (0.83, 0.24),
             (0.21, 0.32), (0.58, 0.33), (0.90, 0.38)]


def _fx_kind(choreo):
    text = f"{choreo.get('setting', '')} {choreo.get('mood', '')}".lower()
    if any(w in text for w in _NIGHT_WORDS):
        return "night"
    if any(w in text for w in _OUTDOOR_WORDS):
        return "outdoor"
    return "indoor"


# Soft cloud/sparkle sprites clash with the flat-cartoon art pass, so they're off
# for now; re-enable (with cartoon-styled FX) once the flat-art look is settled.
FX_ENABLED = False


def fx_layers(choreo, dur, W=CANVAS[0], H=CANVAS[1]):
    """Animated overlay layers that make the WORLD move (not just the character).

    Chosen by scene kind: outdoor -> drifting clouds; night -> twinkling stars +
    floating sparkles; indoor/unknown -> gentle floating sparkles (dreamy motes).
    All sprites are procedural + cached (scripts/gen_fx). Returns [] on failure so a
    missing FX asset never breaks a render.
    """
    if not FX_ENABLED:
        return []
    try:
        import gen_fx
    except Exception:  # noqa: BLE001
        return []
    kind = _fx_kind(choreo)
    layers = []

    def sample(fn, step=0.3):
        kf, t = [], 0.0
        while t <= dur + 1e-6:
            kf.append(fn(t)); t += step
        kf.append(fn(dur))
        return kf

    if kind == "outdoor":
        cloud = str(gen_fx.ensure_fx("cloud"))
        for i, (rx, ry, drift, sc) in enumerate([(0.22, 0.18, 90, 1.0), (0.62, 0.28, 60, 0.8)]):
            x0, y0 = rx * W, ry * H
            layers.append({"name": f"fx_cloud{i}", "image": cloud, "z": 1, "anchor": [240, 130],
                           "keyframes": sample(lambda t, x0=x0, y0=y0, drift=drift, sc=sc: {
                               "t": round(t, 3),
                               "pos": [int(x0 + drift * (t / max(0.1, dur))),
                                       int(y0 + 6 * math.sin(2 * math.pi * t / 6.0))],
                               "scale": sc, "easing": "linear"}, step=0.5)})
    elif kind == "night":
        star = str(gen_fx.ensure_fx("star"))
        for i, (rx, ry) in enumerate(_STAR_POS):
            x0, y0, ph = int(rx * W), int(ry * H), i * 0.8
            layers.append({"name": f"fx_star{i}", "image": star, "z": 1, "anchor": [20, 20],
                           "keyframes": sample(lambda t, x0=x0, y0=y0, ph=ph: {
                               "t": round(t, 3), "pos": [x0, y0],
                               "scale": round(0.5 + 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.5 + ph)), 3),
                               "easing": "ease_in_out"})})
        _add_sparkles(layers, gen_fx, dur, W, H, sample, count=3)
    else:
        _add_sparkles(layers, gen_fx, dur, W, H, sample, count=4)
    return layers


def _add_sparkles(layers, gen_fx, dur, W, H, sample, count):
    sp = str(gen_fx.ensure_fx("sparkle"))
    for i in range(count):
        rx, ry = _SPARKLE_POS[i % len(_SPARKLE_POS)]
        x0, y0, ph = rx * W, ry * H, i * 1.3
        layers.append({"name": f"fx_sparkle{i}", "image": sp, "z": 6, "anchor": [32, 32],
                       "keyframes": sample(lambda t, x0=x0, y0=y0, ph=ph: {
                           "t": round(t, 3),
                           "pos": [int(x0 + 16 * math.sin(2 * math.pi * t / 3.0 + ph)),
                                   int(y0 - 42 * (t / max(0.1, dur)))],
                           "scale": round(0.7 + 0.45 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.2 + ph)), 3),
                           "easing": "ease_in_out"})})


def _body_motion(choreo, beats, dur, pos, scale, intensity):
    """Rich per-action body motion — the difference between a cartoon and a sticker.

    LOCOMOTION (walk/hop/run/slide, or any non-singing character): the character
    TRAVELS across the scene with a walk-bounce, waddle rock and squash-and-stretch
    on each step.  PERFORMANCE (a singing character): stays put but performs —
    energetic side-sway, on-beat bounce, body squash and a waddle rock, so a singer
    reads as alive-in-place instead of vibrating.
    """
    style = (choreo.get("body_motion") or "bob").lower()
    sings = bool(choreo.get("sings"))
    x0, y0 = pos
    W = CANVAS[0]
    step = 0.42                                   # waddle/step cadence (s)
    amp = 0.6 + intensity                         # scale motion by Claude's intensity
    locomote = style in ("slide", "hop", "run", "walk") or not sings

    kf = []
    t, i = 0.0, 0
    while t <= dur + 1e-6:
        ph = t / step
        if locomote:
            # travel across; hop = bigger arc + forward leaps, walk/run = steady glide
            frac = min(1.0, t / max(0.1, dur))
            x = int(180 + (W - 360) * (frac if style != "run" else frac))
            if style == "hop":
                bounce = -44 * amp * abs(math.sin(math.pi * ph))
            else:
                bounce = -22 * amp * abs(math.sin(math.pi * ph))
            rock = 7 * math.sin(2 * math.pi * (t / (2 * step)))
        else:
            # perform in place: sway + on-beat bounce
            x = int(x0 + 26 * amp * math.sin(2 * math.pi * (t / (2 * step))))
            bounce = -18 * amp * abs(math.sin(math.pi * ph))
            rock = 6 * math.sin(2 * math.pi * (t / (2 * step)))
        down = (i % 2 == 0)
        # breathing: slow chest rise/fall (~2.4s), so even a near-still character
        # is never frozen; combines with the per-step squash below.
        breathe = 1.0 + 0.028 * math.sin(2 * math.pi * t / 2.4)
        sx = round(scale * (1.06 if down else 0.98) / breathe, 3)
        sy = round(scale * (0.94 if down else 1.04) * breathe, 3)
        kf.append({"t": round(t, 3), "pos": [x, int(y0 + bounce)], "rot": round(rock, 1),
                   "scale": [sx, sy], "easing": "ease_in_out"})
        t += step / 2
        i += 1
    # settle to rest at the end
    kf.append({"t": round(dur, 3), "pos": [x0 if not locomote else int(W - 180), y0],
               "rot": 0, "scale": scale, "easing": "ease_in_out"})
    return kf


def author_scene(choreo: dict, rig: dict, rig_dir: Path, musicmap: dict,
                 start: float, dur: float, bg_image: str,
                 duck_pos=(470, 500), duck_scale=0.9) -> dict:
    """Build a blender_render spec for one scene. Pure/deterministic."""
    rig_dir = Path(rig_dir).resolve()   # absolute: spec paths must not be relative
    bg_image = str(Path(bg_image).resolve())
    t1 = start + dur
    beats = _win(musicmap.get("beats", []), start, t1)
    downbeats = _win(musicmap.get("downbeats", []), start, t1)
    sings = bool(choreo.get("sings"))
    words = ([w for w in musicmap.get("words", [])
              if start <= w["start"] < t1 and w.get("conf", 0) >= WORD_CONF_MIN]
             if sings else [])
    rel = lambda t: round(t - start, 3)

    ba = rig["body_anchor"]
    parts = rig["parts"]
    intensity = float(choreo.get("intensity", 0.6))
    body_kf = _body_motion(choreo, beats, dur, duck_pos, duck_scale, intensity)

    layers = [
        {"name": "bg", "image": str(bg_image), "z": 0, "anchor": [576, 384],
         "keyframes": _camera_bg(choreo, dur)},
        {"name": "body", "image": str(rig_dir / parts["body"]["image"]),
         "z": parts["body"]["z"], "anchor": list(parts["body"]["anchor"]), "keyframes": body_kf},
    ]

    # --- mouth: lip-sync on words, else beat-bob (Hindi/low-conf/non-singing) ---
    if "mouth" in parts:
        m = parts["mouth"]
        moff = [m["anchor"][0] - ba[0], m["anchor"][1] - ba[1]]
        mkf = [{"t": 0.0, "pos": moff, "rot": 0, "easing": "ease_in_out"}]
        if words:
            for w in words:
                o, c = rel(w["start"]), rel(w["end"])
                mkf += [{"t": max(0.0, o - 0.05), "pos": moff, "rot": 0, "easing": "ease_out"},
                        {"t": o, "pos": moff, "rot": 24, "easing": "ease_in_out"},
                        {"t": min(dur, c), "pos": moff, "rot": 0, "easing": "ease_in_out"}]
            lip = f"word-sync ({len(words)} words)"
        elif sings:
            for i, b in enumerate(beats):
                mkf.append({"t": rel(b), "pos": moff, "rot": 12 if i % 2 == 0 else 0, "easing": "ease_in_out"})
            lip = "beat-only (sings, low word conf)"
        else:
            lip = "closed (not singing)"
        mkf.append({"t": round(dur, 3), "pos": moff, "rot": 0})
        layers.append({"name": "mouth", "image": str(rig_dir / m["image"]),
                       "z": m["z"], "parent": "body", "anchor": list(m["anchor"]), "keyframes": mkf})
    else:
        lip = "no mouth part"

    # --- wing/flap on downbeats when the action is lively ---
    if "wing" in parts and choreo.get("body_motion") in ("hop", "bob", "slide"):
        wp = parts["wing"]
        woff = [wp["anchor"][0] - ba[0], wp["anchor"][1] - ba[1]]
        wkf = [{"t": 0.0, "pos": woff, "rot": 0, "easing": "ease_in_out"}]
        for d in downbeats:
            wkf += [{"t": max(0.0, rel(d) - 0.12), "pos": woff, "rot": 0, "easing": "ease_out"},
                    {"t": rel(d), "pos": woff, "rot": -30, "easing": "ease_in"},
                    {"t": min(dur, rel(d) + 0.28), "pos": woff, "rot": 0, "easing": "ease_in_out"}]
        wkf.append({"t": round(dur, 3), "pos": woff, "rot": 0})
        layers.append({"name": "wing", "image": str(rig_dir / wp["image"]),
                       "z": wp["z"], "parent": "body", "anchor": list(wp["anchor"]), "keyframes": wkf})

    # living-world overlays (drifting clouds / sparkles / stars) behind & in front
    fx = fx_layers(choreo, dur)
    layers += fx

    spec = {"fps": FPS, "duration": round(dur, 3), "resolution": list(CANVAS),
            "layers": layers, "camera": _camera_move(choreo, dur)}
    print(f"[scene_director] char={rig['name']} action={choreo.get('body_motion')} "
          f"cam={choreo.get('camera')} beats={len(beats)} lip={lip} fx={_fx_kind(choreo)}({len(fx)})")
    return spec


def _camera_bg(choreo, dur):
    z0, z1, _ = _CAMERA.get(choreo.get("camera", "push_in"), _CAMERA["push_in"])
    # background gets a touch less push than the camera for a parallax cue
    return [{"t": 0.0, "pos": [576, 384], "scale": 1.0, "easing": "ease_in_out"},
            {"t": round(dur, 3), "pos": [576, 384], "scale": round(1.0 + (z1 - z0) * 0.4, 3)}]


def _camera_move(choreo, dur):
    z0, z1, pan = _CAMERA.get(choreo.get("camera", "push_in"), _CAMERA["push_in"])
    x1 = 576 + int(pan * CANVAS[0])
    return {"keyframes": [
        {"t": 0.0, "pos": [576, 384], "zoom": z0, "easing": "ease_in_out"},
        {"t": round(dur, 3), "pos": [x1, 384], "zoom": z1}]}


if __name__ == "__main__":
    import sys
    rig = json.loads(Path(sys.argv[1]).read_text())
    mm = json.loads(Path(sys.argv[2]).read_text())
    choreo = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {"sings": True, "body_motion": "bob", "camera": "push_in", "intensity": 0.6}
    spec = author_scene(choreo, rig, Path(sys.argv[1]).parent, mm, 3.0, 7.0,
                        r"C:\pradeep\animator\outputs\test_win_storyboards\SH010.png")
    Path(sys.argv[4] if len(sys.argv) > 4 else "scene_spec.json").write_text(json.dumps(spec, indent=2))
    print("wrote spec")
