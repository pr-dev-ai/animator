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


_INDOOR_WORDS = ("classroom", "class room", "kitchen", "bedroom", "indoor", "inside",
                 "interior", "living room", "hall", "shop", "office", "temple inside")


def _fx_kind(choreo):
    text = f"{choreo.get('setting', '')} {choreo.get('mood', '')}".lower()
    if any(w in text for w in _NIGHT_WORDS):
        return "night"
    if any(w in text for w in _INDOOR_WORDS):   # indoor wins (e.g. 'village classroom')
        return "indoor"
    if any(w in text for w in _OUTDOOR_WORDS):
        return "outdoor"
    return "indoor"


_FESTIVE_WORDS = ("festival", "playground", "garden", "celebration", "fair", "wedding", "flower")
# Overlay clouds/birds read as fake pasted-on shapes. Off — background MOTION now
# comes from panning the real plate (its own painted sky/trees move naturally).
FX_ENABLED = False


def fx_layers(choreo, dur, W=CANVAS[0], H=CANVAS[1]):
    """Flat-cartoon overlay layers that put MOTION in the world, matched to the art.

    outdoor -> flat outlined clouds drift + a bird or two flies across; night ->
    twinkling stars; festive/garden -> drifting petals. All sprites are flat-styled
    (scripts/gen_fx) so they sit in the flat-cartoon look. Returns [] on failure so a
    missing asset never breaks a render.
    """
    if not FX_ENABLED:
        return []
    try:
        import gen_fx
    except Exception:  # noqa: BLE001
        return []
    text = f"{choreo.get('setting', '')} {choreo.get('mood', '')}".lower()
    kind = _fx_kind(choreo)
    layers = []

    def sample(fn, step=0.3):
        kf, t = [], 0.0
        while t <= dur + 1e-6:
            kf.append(fn(t)); t += step
        kf.append(fn(dur))
        return kf

    if kind == "night":
        star = str(gen_fx.ensure_fx("star"))
        for i, (rx, ry) in enumerate(_STAR_POS):
            x0, y0, ph = int(rx * W), int(ry * H), i * 0.8
            layers.append({"name": f"fx_star{i}", "image": star, "z": 1, "anchor": [20, 20],
                           "keyframes": sample(lambda t, x0=x0, y0=y0, ph=ph: {
                               "t": round(t, 3), "pos": [x0, y0],
                               "scale": round(0.5 + 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 1.5 + ph)), 3),
                               "easing": "ease_in_out"})})
    elif kind == "outdoor":
        cloud = str(gen_fx.ensure_fx("flatcloud"))
        for i, (rx, ry, drift, sc) in enumerate([(0.20, 0.16, 70, 1.0), (0.60, 0.26, 48, 0.78)]):
            x0, y0 = rx * W, ry * H
            layers.append({"name": f"fx_cloud{i}", "image": cloud, "z": 1, "anchor": [220, 110],
                           "keyframes": sample(lambda t, x0=x0, y0=y0, drift=drift, sc=sc: {
                               "t": round(t, 3),
                               "pos": [int(x0 + drift * (t / max(0.1, dur))),
                                       int(y0 + 5 * math.sin(2 * math.pi * t / 6.0))],
                               "scale": sc, "easing": "linear"}, step=0.5)})
        bird = str(gen_fx.ensure_fx("bird"))            # 2 birds glide across the sky
        for i, (ry, dir_, sc) in enumerate([(0.14, 1, 1.0), (0.22, -1, 0.75)]):
            y0 = ry * H
            layers.append({"name": f"fx_bird{i}", "image": bird, "z": 1, "anchor": [36, 19],
                           "keyframes": sample(lambda t, y0=y0, dir_=dir_, sc=sc: {
                               "t": round(t, 3),
                               "pos": [int((0.5 - dir_ * 0.65) * W + dir_ * 1.3 * W * (t / max(0.1, dur))),
                                       int(y0 + 10 * math.sin(2 * math.pi * t / 2.5))],
                               "scale": sc, "easing": "linear"}, step=0.4)})

    if any(w in text for w in _FESTIVE_WORDS):          # drifting petals
        petal = str(gen_fx.ensure_fx("petal"))
        for i in range(5):
            rx = _SPARKLE_POS[i % len(_SPARKLE_POS)][0]
            x0, ph = rx * W, i * 1.3
            layers.append({"name": f"fx_petal{i}", "image": petal, "z": 6, "anchor": [13, 13],
                           "keyframes": sample(lambda t, x0=x0, ph=ph: {
                               "t": round(t, 3),
                               "pos": [int(x0 + 40 * math.sin(2 * math.pi * t / 3.0 + ph)),
                                       int((-0.1 + 1.1 * (t / max(0.1, dur))) * H)],
                               "scale": round(0.8 + 0.2 * math.sin(2 * math.pi * t + ph), 3),
                               "rot": round(60 * math.sin(t + ph), 1), "easing": "linear"})})
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


def _body_motion(choreo, beats, dur, pos, scale, intensity, direction=1):
    """Per-action body motion. The character mostly PERFORMS IN PLACE (music-video /
    storyboard feel) — lively sway, bob, squash — and stays centred, instead of
    marching across every shot. Only an explicit 'walk'/'run' travels, and then only
    a modest span around centre with an alternating direction; 'hop' bounces in place.
    """
    style = (choreo.get("body_motion") or "bob").lower()
    x0, y0 = pos
    W = CANVAS[0]
    step = 0.42                                   # step/sway cadence (s)
    amp = 0.6 + intensity                         # scale motion by Claude's intensity
    travels = style in ("walk", "run")            # ONLY these cross the frame
    span = int(0.22 * W)                          # modest, centred travel distance

    kf = []
    t, i = 0.0, 0
    while t <= dur + 1e-6:
        ph = t / step
        if travels:                               # cross a small span, centred on x0
            frac = min(1.0, t / max(0.1, dur))
            x = int(x0 + direction * span * (frac - 0.5))
            bounce = -22 * amp * abs(math.sin(math.pi * ph))
            rock = 7 * math.sin(2 * math.pi * (t / (2 * step)))
        elif style == "hop":                      # jump up and down in place
            x = int(x0 + 10 * amp * math.sin(2 * math.pi * (t / (2 * step))))
            bounce = -46 * amp * abs(math.sin(math.pi * ph))
            rock = 4 * math.sin(2 * math.pi * (t / (2 * step)))
        else:                                     # perform in place: sway + bob
            x = int(x0 + 30 * amp * math.sin(2 * math.pi * (t / (2.2 * step))))
            bounce = -20 * amp * abs(math.sin(math.pi * ph))
            rock = 7 * math.sin(2 * math.pi * (t / (2 * step)))
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
    # settle to rest at centre (not marched to the edge)
    kf.append({"t": round(dur, 3), "pos": [x0, y0], "rot": 0, "scale": scale,
               "easing": "ease_in_out"})
    return kf


def _head_motion(hoff, dur, intensity):
    """Gentle nod + side tilt of the head, parented to the body — the difference
    between a character that's alive and one that's a sliding image. Scaled by the
    scene's intensity; settles to neutral at the end."""
    amp = 0.5 + intensity
    kf, t = [], 0.0
    while t <= dur + 1e-6:
        tilt = 6.0 * amp * math.sin(2 * math.pi * t / 1.5)
        bob = -4.0 * amp * abs(math.sin(math.pi * t / 0.42))
        kf.append({"t": round(t, 3), "pos": [hoff[0], hoff[1] + int(bob)],
                   "rot": round(tilt, 1), "easing": "ease_in_out"})
        t += 0.18
    kf.append({"t": round(dur, 3), "pos": hoff, "rot": 0})
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
    direction = 1 if int(start) % 2 == 0 else -1        # alternate walk direction
    body_kf = _body_motion(choreo, beats, dur, duck_pos, duck_scale, intensity, direction)

    # ground contact shadow: follows the character's x at the ground line so the
    # figure sits IN the scene instead of looking pasted on top of the plate.
    ground_y = int(0.87 * CANVAS[1])
    try:
        import gen_fx
        shadow_img = str(gen_fx.ensure_fx("shadow"))
        sh_scale = round(max(0.45, duck_scale * 1.15), 3)
        shadow_kf = [{"t": k["t"], "pos": [k["pos"][0], ground_y], "scale": sh_scale,
                      "easing": "ease_in_out"} for k in body_kf]
        shadow_layer = [{"name": "shadow", "image": shadow_img, "z": 1, "anchor": [170, 55],
                         "keyframes": shadow_kf}]
    except Exception:  # noqa: BLE001
        shadow_layer = []

    layers = [
        {"name": "bg", "image": str(bg_image), "z": 0, "anchor": [576, 384],
         "keyframes": _camera_bg(choreo, dur, direction)},
        *shadow_layer,
        {"name": "body", "image": str(rig_dir / parts["body"]["image"]),
         "z": parts["body"]["z"], "anchor": list(parts["body"]["anchor"]), "keyframes": body_kf},
    ]

    # --- head: independent nod/tilt so the character has real body movement ---
    if "head" in parts:
        hd = parts["head"]
        hoff = [hd["anchor"][0] - ba[0], hd["anchor"][1] - ba[1]]
        layers.append({"name": "head", "image": str(rig_dir / hd["image"]),
                       "z": hd["z"], "parent": hd.get("parent", "body"),
                       "anchor": list(hd["anchor"]), "keyframes": _head_motion(hoff, dur, intensity)})

    # --- mouth: a dark open-mouth shape that scales OPEN/SHUT for visible lip sync
    #     (rotating the cutout was too subtle). Rides the head; opens on vocals. ---
    if "mouth" in parts:
        m = parts["mouth"]
        mparent = m.get("parent", "body")
        pa = parts["head"]["anchor"] if (mparent == "head" and "head" in parts) else ba
        moff = [m["anchor"][0] - pa[0], m["anchor"][1] - pa[1]]
        try:
            import gen_fx
            mimg = str(gen_fx.ensure_fx("mouth_open"))
        except Exception:  # noqa: BLE001
            mimg = str(rig_dir / m["image"])
        OPEN, SHUT = 0.55, 0.05                   # scale of the open-mouth sprite
                                                  # (kept small so the mouth/face ratio reads right)
        mkf = [{"t": 0.0, "pos": moff, "scale": SHUT, "easing": "ease_in_out"}]
        if words:                                 # precise per-word (English)
            for w in words:
                o, c = rel(w["start"]), rel(w["end"])
                mkf += [{"t": max(0.0, o - 0.04), "pos": moff, "scale": SHUT, "easing": "ease_out"},
                        {"t": o, "pos": moff, "scale": OPEN, "easing": "ease_in_out"},
                        {"t": min(dur, c), "pos": moff, "scale": SHUT, "easing": "ease_in_out"}]
            lip = f"word-sync ({len(words)} words)"
        else:                                     # singing flap during vocal activity
            vwin = [(rel(w["start"]), rel(min(w.get("end", w["start"] + 0.35), t1)))
                    for w in musicmap.get("words", []) if start <= w.get("start", -1) < t1]
            t = 0.0
            while t <= dur + 1e-6:
                singing = (not vwin) or any(a - 0.06 <= t <= b + 0.06 for a, b in vwin)
                sc = (OPEN if int(t / 0.2) % 2 == 0 else SHUT) if singing else SHUT
                mkf.append({"t": round(t, 3), "pos": moff, "scale": round(sc, 3), "easing": "ease_in_out"})
                t += 0.1
            lip = f"singing flap ({len(vwin)} vocal windows)"
        mkf.append({"t": round(dur, 3), "pos": moff, "scale": SHUT})
        layers.append({"name": "mouth", "image": mimg, "z": m["z"], "parent": mparent,
                       "anchor": [38, 28], "keyframes": mkf})     # 38,28 = sprite centre
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


def _camera_bg(choreo, dur, direction=1):
    """Background keyframes that give the scene continuous motion instead of a frozen
    flat image: the plate is scaled up (so it can pan without revealing edges) and
    slowly drifts sideways + pushes in. The character stays at its canvas position,
    so the drift also reads as a gentle parallax between figure and world."""
    z0, z1, _ = _CAMERA.get(choreo.get("camera", "push_in"), _CAMERA["push_in"])
    base = 1.18                                    # overscan so a pan stays in-bounds
    s1 = round(base + max(0.0, z1 - z0) * 0.5, 3)
    dx = direction * 30                            # slow horizontal scene drift (px)
    return [{"t": 0.0, "pos": [576 - dx, 384], "scale": base, "easing": "ease_in_out"},
            {"t": round(dur, 3), "pos": [576 + dx, 384], "scale": s1}]


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
