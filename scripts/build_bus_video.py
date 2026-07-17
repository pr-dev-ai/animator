#!/usr/bin/env python3
"""Author the full-length "Wheels on the Bus" music-video render specs.

The whole video reuses ONE side-view bus scene kit (outputs/scene_kit_bus): a
background plate, a bus body with transparent windows, one reusable circular
wheel placed at both mounts, and three kids' heads behind the windows.  What
changes verse to verse is the MOTION, not the art -- which is the trick that
makes a full song's worth of animation out of a single asset kit and sidesteps
any character-consistency problem.

The song is verse-structured, and each verse is a different action on the same
bus.  `plan_sections()` maps the sung timeline (from lyric_sync) onto a list of
sections; `build_section_spec()` turns one section into a blender_render v2 spec
with the shared bus + spinning wheels + bobbing kids, plus that verse's action
(wheel emphasis / kids up-and-down / horn honk / wiper swish / drive-off).

Motion lands on the beat: the caller passes the song's eighth-note grid and the
builder drops keyframes on those times.

Two small overlay assets (a soft honk-flash glow and a wiper blade) are
synthesised procedurally with PIL rather than generated on the GPU -- they are
simple shapes and the GPU is contended by the image/music pipeline.

The heavy render loop + assembly lives in web_ui/animation_api.build_music_video;
this module only builds specs and overlay assets, so it stays import-light and
unit-testable without Blender.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = REPO_ROOT / "outputs" / "scene_kit_bus"

FPS = 24
RES = [1152, 768]

# --- Bus geometry (from scene_kit_bus/manifest.json) ------------------------
# The bus body's animation anchor is body-local (530,200); with the body placed
# at frame (0,182) that anchor sits at frame (530,382) when the bus is at its
# natural resting placement.  Every wheel/kid offset below is measured against
# that natural anchor, so the offsets are placement-independent (parenting makes
# `pos` an offset from the parent anchor -- see blender_render_schema.md).
BUS_ANCHOR_NATURAL = (530, 382)
BUS_BODY_ANCHOR = [530, 200]          # anchor inside bus_body.png

# Where the bus anchor is parked on the canvas.  Centred horizontally (576) so a
# small centred camera zoom never reveals the background edge; dropped to BUS_Y
# so the wheels sit ON the asphalt instead of floating above it (Part B fix --
# the hero used 398 and still floated; BUS_Y grounds the tyres).
BUS_X = 576
BUS_Y = 452

# Wheel/kid frame anchors at natural placement -> offsets from the bus anchor.
WHEEL_MOUNTS = {"wheel_front": (240, 523), "wheel_rear": (783, 523)}
KID_FILES = ["kid_1_blonde.png", "kid_2_teal.png", "kid_3_red.png"]
KID_SIZES = [(255, 390), (325, 462), (300, 425)]
KID_WINDOWS = [(303, 274), (543, 274), (795, 274)]
KID_SCALE = 0.30

# Base of the front windshield (the driver's angled window at the far front of
# the side-view bus) in frame coords at natural placement -- where the wiper
# blade pivots during the wiper verses.  A side-view bus shows its windshield
# nearly edge-on at the very front-left, so the wiper lives there, small, rather
# than over a passenger window (which would read as an antenna).
WINDSHIELD_FRAME = (70, 388)


def _off(frame_pt):
    """Frame point -> offset from the bus's natural anchor (canvas px, y-down)."""
    return [frame_pt[0] - BUS_ANCHOR_NATURAL[0], frame_pt[1] - BUS_ANCHOR_NATURAL[1]]


# --- Section plan -----------------------------------------------------------
# Absolute song windows come from lyric_sync on wheels_hero_song.wav (see
# scripts/lyric_sync.py).  The transcript places the sung verses at:
#   wheels 15-30 | horn 36.7-46 / 68.7-77 / 99.6-104 | children 47.5-62 |
#   wipers 78-94 / 111-126.  There is a ~15s instrumental intro and a ~24s
#   instrumental-plus-refrain outro.  Sections tile [0,150) with no gaps so the
#   rendered frames concatenate to exactly the song length.
SECTIONS = [
    {"name": "intro",    "start": 0.0,   "end": 15.0,  "action": "cruise"},
    {"name": "wheels",   "start": 15.0,  "end": 31.0,  "action": "wheels"},
    {"name": "horn1",    "start": 31.0,  "end": 46.0,  "action": "horn",  "beep": (5.7, 10.0)},
    {"name": "children", "start": 46.0,  "end": 63.0,  "action": "children"},
    {"name": "horn2",    "start": 63.0,  "end": 78.0,  "action": "horn",  "beep": (5.7, 13.0)},
    {"name": "wipers1",  "start": 78.0,  "end": 94.0,  "action": "wipers"},
    {"name": "horn3",    "start": 94.0,  "end": 110.0, "action": "horn",  "beep": (5.6, 10.4)},
    {"name": "wipers2",  "start": 110.0, "end": 126.0, "action": "wipers"},
    {"name": "outro",    "start": 126.0, "end": 150.0, "action": "cruise"},
]

# Per-section centred camera zoom (pos stays [576,384] so edges never show).
# Varying the zoom gives each verse a slightly different framing without ever
# panning off-centre into the background edge.
SECTION_ZOOM = {
    "intro":    (1.06, 1.09), "wheels": (1.10, 1.06), "horn1": (1.05, 1.05),
    "children": (1.08, 1.11), "horn2":  (1.05, 1.05), "wipers1": (1.09, 1.07),
    "horn3":    (1.05, 1.05), "wipers2": (1.07, 1.09), "outro":  (1.08, 1.04),
}


def plan_sections():
    """Return the section list (name, start, end, action)."""
    return [dict(s) for s in SECTIONS]


# --- Procedural overlay assets ---------------------------------------------

def ensure_overlay_assets(out_dir):
    """Create the honk-glow and wiper-blade PNGs if missing; return their paths.

    Both are simple shapes drawn with PIL -- deliberately NOT SD-generated, to
    keep off the contended GPU.  Written once and reused.
    """
    from PIL import Image, ImageDraw
    import numpy as np

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    glow = out_dir / "honk_glow.png"
    wiper = out_dir / "wiper_blade.png"

    if not glow.exists():
        # Soft radial burst: warm yellow core fading to transparent, for the horn
        # honk flash.  Built in numpy for a smooth falloff.
        n = 256
        yy, xx = np.mgrid[0:n, 0:n]
        r = np.sqrt((xx - n / 2) ** 2 + (yy - n / 2) ** 2) / (n / 2)
        a = np.clip(1.0 - r, 0.0, 1.0) ** 1.8
        rgba = np.zeros((n, n, 4), dtype=np.uint8)
        rgba[..., 0] = 255
        rgba[..., 1] = 236
        rgba[..., 2] = 120
        rgba[..., 3] = (a * 255).astype(np.uint8)
        Image.fromarray(rgba, "RGBA").save(glow)

    if not wiper.exists():
        # Tapered dark blade, pivot at the bottom centre.  A hair of margin keeps
        # antialiasing from clipping the tip.
        w, h = 22, 150
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.polygon([(w / 2 - 6, h - 4), (w / 2 + 6, h - 4),
                   (w / 2 + 2, 6), (w / 2 - 2, 6)], fill=(30, 30, 34, 235))
        d.line([(w / 2, h - 6), (w / 2, 8)], fill=(90, 90, 96, 235), width=2)
        img.save(wiper)

    return {"glow": glow, "wiper": wiper}


# --- Keyframe helpers -------------------------------------------------------

def _accents(beats_abs, start, end, stride=1, pad=0.0):
    """Window-relative accent times inside [start+pad, end], every `stride`-th.

    `beats_abs` is the song's eighth-note grid (absolute seconds).  stride=1 ->
    eighths, stride=2 -> quarter-note beats.  Relative to the section start, so
    the values drop straight onto keyframe `t`.
    """
    hits = [round(b - start, 4) for b in beats_abs if start + pad - 1e-6 <= b <= end + 1e-6]
    return hits[::stride]


def _bus_bounce_keys(dur, quarters):
    """Bus body vertical bob + squash, returning to baseline at the section ends.

    A small lively bounce on every quarter-note beat: the anchor dips a few px
    and the body squashes a touch (scale y 0.985), popping back up between beats.
    Baseline (pos [BUS_X, BUS_Y], scale 1) is held at t=0 and t=dur so adjacent
    sections meet seamlessly at the cut.
    """
    keys = [{"t": 0.0, "pos": [BUS_X, BUS_Y], "scale": 1.0, "easing": "ease_in_out"}]
    for i, bt in enumerate(quarters):
        if bt <= 0.02 or bt >= dur - 0.02:
            continue
        # Down on the beat, back up halfway to the next beat.
        keys.append({"t": round(bt, 3), "pos": [BUS_X, BUS_Y + 4],
                     "scale": [1.006, 0.985], "easing": "ease_out"})
        nxt = quarters[i + 1] if i + 1 < len(quarters) else dur
        mid = bt + (nxt - bt) * 0.5
        if mid < dur - 0.02:
            keys.append({"t": round(mid, 3), "pos": [BUS_X, BUS_Y],
                         "scale": 1.0, "easing": "ease_in_out"})
    keys.append({"t": round(dur, 3), "pos": [BUS_X, BUS_Y], "scale": 1.0})
    return keys


def _horn_shake_keys(dur, quarters, beep_window):
    """Bus body honk motion: sharp scale-pop + horizontal shake on each beep beat.

    Within the beep window the bus jolts (scale up, nudge left/right) on every
    beat; outside it the bus just gently holds so the honks read as punctuation.
    """
    b0, b1 = beep_window
    keys = [{"t": 0.0, "pos": [BUS_X, BUS_Y], "scale": 1.0, "easing": "ease_in_out"}]
    beeps = [q for q in quarters if b0 - 1e-6 <= q <= b1 + 1e-6]
    for i, bt in enumerate(beeps):
        if bt <= 0.05 or bt >= dur - 0.05:
            continue
        shove = 7 if i % 2 == 0 else -7
        keys.append({"t": round(bt - 0.04, 3), "pos": [BUS_X, BUS_Y], "scale": 1.0,
                     "easing": "ease_out"})
        keys.append({"t": round(bt, 3), "pos": [BUS_X + shove, BUS_Y - 5],
                     "scale": [1.03, 1.04], "easing": "ease_out"})
        keys.append({"t": round(bt + 0.12, 3), "pos": [BUS_X, BUS_Y], "scale": 1.0,
                     "easing": "ease_in_out"})
    keys.append({"t": round(dur, 3), "pos": [BUS_X, BUS_Y], "scale": 1.0})
    return keys


def _kid_bob_keys(dur, quarters, off, amp, phase):
    """Kid vertical bob on the quarter beats, phase-offset per kid.

    `amp` sets how far the head travels (strong in the children verse, gentle
    elsewhere); baseline is held at the section ends for seamless cuts.
    """
    keys = [{"t": 0.0, "pos": list(off), "scale": KID_SCALE, "easing": "ease_in_out"}]
    for j, bt in enumerate(quarters):
        if bt <= 0.02 or bt >= dur - 0.02:
            continue
        up = ((j + phase) % 2 == 0)
        dy = -amp if up else amp * 0.4   # rise high, dip shallow (heads bob up)
        keys.append({"t": round(bt, 3), "pos": [off[0], off[1] + dy],
                     "scale": KID_SCALE, "easing": "ease_in_out"})
    keys.append({"t": round(dur, 3), "pos": list(off), "scale": KID_SCALE})
    return keys


def _wheel_keys(dur, revs):
    """Constant-offset wheels (parented) spinning `revs` turns over the section."""
    out = {}
    for name, mount in WHEEL_MOUNTS.items():
        off = _off(mount)
        out[name] = [
            {"t": 0.0, "pos": off, "rot": 0, "easing": "linear"},
            {"t": round(dur, 3), "pos": off, "rot": round(360 * revs)},
        ]
    return out


# --- Section spec builder ---------------------------------------------------

def build_section_spec(section, beats_abs, overlays):
    """Build one section's blender_render v2 spec (dict).

    `beats_abs` = the song's absolute eighth-note grid; `overlays` = the dict
    from ensure_overlay_assets.  All image paths are absolute (relative paths
    would resolve against the spec file, not the repo).
    """
    name = section["name"]
    start, end = section["start"], section["end"]
    dur = round(end - start, 4)
    action = section["action"]

    eighths = _accents(beats_abs, start, end, stride=1)
    quarters = _accents(beats_abs, start, end, stride=2)

    layers = []

    # Background: static plate.
    layers.append({
        "name": "background", "image": str(KIT_DIR / "background.png"), "z": 0,
        "anchor": [576, 384], "keyframes": [{"t": 0.0, "pos": [576, 384]}],
    })

    # Kids behind the body: bob strongly in the children verse, gently otherwise.
    strong = (action == "children")
    amp = 30 if strong else 8
    for i, (f, sz, win) in enumerate(zip(KID_FILES, KID_SIZES, KID_WINDOWS)):
        off = _off(win)
        anchor = [sz[0] // 2, int(sz[1] * 0.30)]
        layers.append({
            "name": f"kid_{i}", "image": str(KIT_DIR / f), "z": 1, "parent": "bus_body",
            "anchor": anchor,
            "keyframes": _kid_bob_keys(dur, quarters, off, amp, phase=i),
        })

    # Bus body: horn sections get the honk jolt, everything else the gentle bounce.
    if action == "horn":
        body_keys = _horn_shake_keys(dur, quarters, section["beep"])
    else:
        body_keys = _bus_bounce_keys(dur, quarters)
    layers.append({
        "name": "bus_body", "image": str(KIT_DIR / "bus_body.png"), "z": 2,
        "anchor": BUS_BODY_ANCHOR, "keyframes": body_keys,
    })

    # Wheels: always spinning (this is the "wheels go round" throughline), a
    # touch faster in the wheels verse to sell that lyric.
    revs = (dur * 0.75) * (1.7 if action == "wheels" else 1.0)
    for wname, keys in _wheel_keys(dur, revs).items():
        layers.append({
            "name": wname, "image": str(KIT_DIR / "wheel.png"), "z": 3,
            "parent": "bus_body", "anchor": [65, 65], "keyframes": keys,
        })

    # Per-verse extra elements ------------------------------------------------
    if action == "horn":
        _add_horn_flash(layers, dur, quarters, section["beep"], overlays)
    elif action == "wipers":
        _add_wipers(layers, dur, eighths, overlays)

    zoom0, zoom1 = SECTION_ZOOM.get(name, (1.07, 1.07))
    spec = {
        "fps": FPS, "duration": dur, "resolution": RES,
        "layers": layers,
        "camera": {"keyframes": [
            {"t": 0.0, "pos": [576, 384], "zoom": zoom0, "easing": "ease_in_out"},
            {"t": dur, "pos": [576, 384], "zoom": zoom1},
        ]},
    }
    return spec


def _add_horn_flash(layers, dur, quarters, beep_window, overlays):
    """A soft warm glow that pops at the front of the bus on each beep beat.

    Parented to the bus so it rides along; scales from nothing to a bright flash
    and back on each beep.  Placed just ahead of the front wheel / headlight.
    """
    b0, b1 = beep_window
    beeps = [q for q in quarters if b0 - 1e-6 <= q <= b1 + 1e-6]
    off = _off((150, 470))    # front headlight / horn area
    keys = [{"t": 0.0, "pos": off, "scale": 0.001, "easing": "ease_out"}]
    for bt in beeps:
        if bt <= 0.05 or bt >= dur - 0.05:
            continue
        keys.append({"t": round(bt - 0.05, 3), "pos": off, "scale": 0.001, "easing": "ease_out"})
        keys.append({"t": round(bt, 3), "pos": off, "scale": 1.15, "easing": "ease_out"})
        keys.append({"t": round(bt + 0.18, 3), "pos": off, "scale": 0.001, "easing": "ease_in"})
    keys.append({"t": round(dur, 3), "pos": off, "scale": 0.001})
    layers.append({
        "name": "honk_glow", "image": str(overlays["glow"]), "z": 4,
        "parent": "bus_body", "anchor": [128, 128], "keyframes": keys,
    })


def _add_wipers(layers, dur, eighths, overlays):
    """A wiper blade sweeping back and forth over the front windshield.

    Parented to the bus; the blade pivots at its base (bottom-centre of the PNG)
    and rotates between two angles on each eighth-note, so it swishes on the
    beat.  Starts and ends parked at the same angle for clean cuts.
    """
    off = _off(WINDSHIELD_FRAME)
    # Short blade (scale) pivoting at the windshield base, sweeping between two
    # left-of-vertical angles so it stays over the raked front glass and never
    # rises above the roofline.
    park, sweep, scale = -30, 12, 0.5
    keys = [{"t": 0.0, "pos": off, "rot": park, "scale": scale, "easing": "ease_in_out"}]
    for k, bt in enumerate(eighths):
        if bt <= 0.02 or bt >= dur - 0.02:
            continue
        ang = sweep if (k % 2 == 0) else park
        keys.append({"t": round(bt, 3), "pos": off, "rot": ang, "scale": scale,
                     "easing": "ease_in_out"})
    keys.append({"t": round(dur, 3), "pos": off, "rot": park, "scale": scale})
    # Anchor at the blade's bottom-centre so it pivots like a real wiper.
    layers.append({
        "name": "wiper", "image": str(overlays["wiper"]), "z": 4,
        "parent": "bus_body", "anchor": [11, 146], "keyframes": keys,
    })


# --- CLI: build a single section spec (debug/inspection) --------------------

def main():
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import beat_timing

    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--project", default="wheels_hero")
    p.add_argument("--section", help="section name (default: list sections)")
    p.add_argument("--out", help="write the section spec JSON here")
    args = p.parse_args()

    beats = beat_timing.beat_grid(args.project, subdiv=2, audio_duration=150.0)
    overlays = ensure_overlay_assets(REPO_ROOT / "outputs" / "_bus_build")

    if not args.section:
        for s in SECTIONS:
            print(f"{s['name']:9s} {s['start']:6.1f}-{s['end']:6.1f}  {s['action']}")
        return
    sec = next(s for s in SECTIONS if s["name"] == args.section)
    spec = build_section_spec(sec, beats, overlays)
    out = Path(args.out) if args.out else REPO_ROOT / "outputs" / "_bus_build" / f"{args.section}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2))
    print(f"wrote {out} ({len(spec['layers'])} layers, {spec['duration']}s)")


if __name__ == "__main__":
    main()
