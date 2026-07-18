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

# Body-motion styles -> (per-beat vertical dip px, waddle tilt deg).
_BODY_MOTION = {
    "still": (0, 0),
    "bob":   (22, 3),
    "sway":  (8, 6),
    "hop":   (40, 0),
    "slide": (10, 2),
}
_CAMERA = {   # (zoom0, zoom1, pan_dx over shot as fraction of width)
    "hold":     (1.04, 1.04, 0.0),
    "push_in":  (1.02, 1.12, 0.0),
    "pull_out": (1.12, 1.02, 0.0),
    "pan_left": (1.06, 1.06, -0.10),
    "pan_right":(1.06, 1.06, 0.10),
}


def _win(times, t0, t1):
    return [t for t in times if t0 <= t < t1]


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
    dip, tilt_amp = _BODY_MOTION.get(choreo.get("body_motion", "bob"), _BODY_MOTION["bob"])
    intensity = float(choreo.get("intensity", 0.6))
    dip = int(dip * (0.5 + intensity))          # scale motion by Claude's intensity

    # --- body: motion on beats ---
    body_kf = [{"t": 0.0, "pos": list(duck_pos), "rot": 0, "scale": duck_scale, "easing": "ease_in_out"}]
    for i, b in enumerate(beats):
        dy = dip if i % 2 == 0 else -int(dip * 0.45)
        tl = tilt_amp if i % 2 == 0 else -tilt_amp
        body_kf.append({"t": rel(b), "pos": [duck_pos[0], duck_pos[1] + dy], "rot": tl,
                        "scale": duck_scale, "easing": "ease_in_out"})
    body_kf.append({"t": round(dur, 3), "pos": list(duck_pos), "rot": 0, "scale": duck_scale})

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

    spec = {"fps": FPS, "duration": round(dur, 3), "resolution": list(CANVAS),
            "layers": layers, "camera": _camera_move(choreo, dur)}
    print(f"[scene_director] char={rig['name']} action={choreo.get('body_motion')} "
          f"cam={choreo.get('camera')} beats={len(beats)} lip={lip}")
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
