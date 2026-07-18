#!/usr/bin/env python3
"""Author a beat-driven 2.5D cutout-puppet spec for the duck rig, from a musicmap.

De-risk spike for real (non-slideshow) animation: a rigged character whose parts
articulate ON THE BEAT with the camera moving.  Motion:
  * body  — waddle bob (up/down) on every beat
  * wing  — flap (rotate about its pivot) on downbeats (bar boundaries)
  * beak  — opens on sung lyric WORDS, but ONLY when word confidence clears a
            threshold.  English (~0.79) fires word-sync; Hindi (~0.56) does not,
            so it falls back to a gentle beak bob on the beat.  Same rig code,
            language-conditional behaviour, baked in from the start.
  * camera— slow push-in; the duck plane pushes a touch more than the background
            for a parallax (depth) cue so it doesn't read flat.

Emits a scripts/blender_render.py spec.  Part images + pivots come from the duck
scene kit (outputs/duck_rig/), all in one 772x768 frame so they auto-align.
"""
import json
import sys
from pathlib import Path

KIT = Path(r"C:\pradeep\animator\outputs\duck_rig")
BG = Path(r"C:\pradeep\animator\outputs\test_win_storyboards\SH010.png")

# Pivots in the part-image frame (772x768), from duck_parts.py:
BODY_ANCHOR = (386, 417)
WING_ANCHOR = (386, 475)
BEAK_ANCHOR = (147, 359)

CANVAS = (1152, 768)
FPS = 24
WORD_CONF_MIN = 0.65   # English 0.79 qualifies; Hindi 0.56 falls back to beat-only

# Where the duck's body anchor sits on the canvas, and its scale.
DUCK_POS = (470, 500)
DUCK_SCALE = 0.9


def _win(times, t0, t1):
    return [t for t in times if t0 <= t < t1]


def build_duck_spec(musicmap_path: str, start: float, dur: float) -> dict:
    mm = json.loads(Path(musicmap_path).read_text())
    t1 = start + dur
    beats = _win(mm.get("beats", []), start, t1)
    downbeats = _win(mm.get("downbeats", []), start, t1)
    words = [w for w in mm.get("words", [])
             if start <= w["start"] < t1 and w.get("conf", 0) >= WORD_CONF_MIN]
    rel = lambda t: round(t - start, 3)

    # --- body: waddle bob + tilt on every beat (down+tilt on beat, up between) ---
    body_kf = [{"t": 0.0, "pos": list(DUCK_POS), "rot": 0, "scale": DUCK_SCALE, "easing": "ease_in_out"}]
    for i, b in enumerate(beats):
        dy = 26 if i % 2 == 0 else -12           # clear squash-down on the beat, lift between
        tilt = 4 if i % 2 == 0 else -4           # waddle rock
        body_kf.append({"t": rel(b), "pos": [DUCK_POS[0], DUCK_POS[1] + dy], "rot": tilt,
                        "scale": DUCK_SCALE, "easing": "ease_in_out"})
    body_kf.append({"t": round(dur, 3), "pos": list(DUCK_POS), "rot": 0, "scale": DUCK_SCALE})

    # --- wing: flap on downbeats (rotate up then settle) ---
    woff = [WING_ANCHOR[0] - BODY_ANCHOR[0], WING_ANCHOR[1] - BODY_ANCHOR[1]]
    wing_kf = [{"t": 0.0, "pos": woff, "rot": 0, "easing": "ease_in_out"}]
    for d in downbeats:
        wing_kf.append({"t": max(0.0, rel(d) - 0.12), "pos": woff, "rot": 0, "easing": "ease_out"})
        wing_kf.append({"t": rel(d), "pos": woff, "rot": -34, "easing": "ease_in"})       # up-flap
        wing_kf.append({"t": min(dur, rel(d) + 0.28), "pos": woff, "rot": 0, "easing": "ease_in_out"})
    wing_kf.append({"t": round(dur, 3), "pos": woff, "rot": 0})

    # --- beak: open on confident words, else gentle bob on beats ---
    boff = [BEAK_ANCHOR[0] - BODY_ANCHOR[0], BEAK_ANCHOR[1] - BODY_ANCHOR[1]]
    beak_kf = [{"t": 0.0, "pos": boff, "rot": 0, "easing": "ease_in_out"}]
    if words:
        for w in words:
            o, c = rel(w["start"]), rel(w["end"])
            beak_kf.append({"t": max(0.0, o - 0.05), "pos": boff, "rot": 0, "easing": "ease_out"})
            beak_kf.append({"t": o, "pos": boff, "rot": 24, "easing": "ease_in_out"})     # open
            beak_kf.append({"t": min(dur, c), "pos": boff, "rot": 0, "easing": "ease_in_out"})
        mode = f"word-sync ({len(words)} words, conf>={WORD_CONF_MIN})"
    else:
        for i, b in enumerate(beats):          # fallback: beat-only beak bob (Hindi path)
            beak_kf.append({"t": rel(b), "pos": boff, "rot": 9 if i % 2 == 0 else 0,
                            "easing": "ease_in_out"})
        mode = "beat-only fallback (low word confidence)"
    beak_kf.append({"t": round(dur, 3), "pos": boff, "rot": 0})

    spec = {
        "fps": FPS, "duration": round(dur, 3), "resolution": list(CANVAS),
        "layers": [
            {"name": "bg", "image": str(BG), "z": 0, "anchor": [576, 384],
             "keyframes": [{"t": 0.0, "pos": [576, 384], "scale": 1.0, "easing": "ease_in_out"},
                           {"t": round(dur, 3), "pos": [576, 384], "scale": 1.05}]},
            {"name": "duck_body", "image": str(KIT / "duck_full.png"), "z": 2,
             "anchor": list(BODY_ANCHOR), "keyframes": body_kf},
            {"name": "duck_wing", "image": str(KIT / "duck_wing.png"), "z": 3,
             "parent": "duck_body", "anchor": list(WING_ANCHOR), "keyframes": wing_kf},
            {"name": "duck_beak", "image": str(KIT / "duck_beak.png"), "z": 4,
             "parent": "duck_body", "anchor": list(BEAK_ANCHOR), "keyframes": beak_kf},
        ],
        # camera: gentle centred push-in (never reveals bg edge).
        "camera": {"keyframes": [
            {"t": 0.0, "pos": [576, 384], "zoom": 1.02, "easing": "ease_in_out"},
            {"t": round(dur, 3), "pos": [576, 384], "zoom": 1.10}]},
    }
    print(f"beats={len(beats)} downbeats={len(downbeats)} beak={mode}")
    return spec


if __name__ == "__main__":
    mm = sys.argv[1] if len(sys.argv) > 1 else r"C:\pradeep\animator\outputs\test_win_musicmap.json"
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 7.0
    out = sys.argv[4] if len(sys.argv) > 4 else r"C:\pradeep\animator\outputs\duck_rig\duck_spec.json"
    spec = build_duck_spec(mm, start, dur)
    Path(out).write_text(json.dumps(spec, indent=2))
    print("wrote", out)
